#!/usr/bin/env python3
"""
the_watcher - track watched episodes
"""

try:
    import curses
except ImportError as error:
    raise SystemExit(
        "This program requires a curses-compatible terminal library. "
        "On Windows, install it with: pip install windows-curses"
    ) from error
import argparse
import json
import hashlib
import os
import re
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path
from contextlib import contextmanager


VIDEO_EXTENSIONS = {
    ".3gp",
    ".avi",
    ".asf",
    ".divx",
    ".flv",
    ".m2ts",
    ".m4v",
    ".mkv",
    ".mov",
    ".mp4",
    ".mpeg",
    ".mpg",
    ".mts",
    ".ogm",
    ".ogv",
    ".rm",
    ".rmvb",
    ".ts",
    ".webm",
    ".wmv",
    ".vob",
}

DATABASE_NAME = ".episode_watches.json"

HASH_BYTES = 1_048_576

def natural_key(value):
    """Sort episode2 before episode10."""
    return [
        int(part) if part.isdigit() else part.casefold()
        for part in re.split(r"(\d+)", str(value))
    ]

def file_identity(path):
    """
    Return a content-based identity for a file.

    Only the first 1 MiB is hashed. The complete file is not read.
    """
    try:
        digest = hashlib.sha256()

        with path.open("rb") as file:
            remaining = HASH_BYTES

            while remaining:
                chunk = file.read(min(64 * 1024, remaining))

                if not chunk:
                    break

                digest.update(chunk)
                remaining -= len(chunk)

        # Use stat only for size. Device and inode are deliberately not used.
        size = path.stat().st_size

        return {
            "sha256": digest.hexdigest(),
            "size": size,
        }

    except OSError:
        return None

def identity_key(identity):
    """
    Return the content identity used to match renamed files.

    Identities from old databases containing device/inode fields are ignored.
    """
    if not isinstance(identity, dict):
        return None

    sha256 = identity.get("sha256")
    size = identity.get("size")

    if not isinstance(sha256, str) or not isinstance(size, int):
        return None

    return sha256, size

def launch_file(path):
    """
    Launches the passed file using either the system configured media player
    or the PLAYER environment variable if it is set
    """
    try:
        configured = os.environ.get("PLAYER")

        if sys.platform == "win32":
            os.startfile(str(path))  # pylint: disable=no-member
            return f"Opened: {path}"

        if sys.platform == "darwin":
            command = ["open", str(path)]
        else:
            command = ["xdg-open", str(path)]

        if configured:
            command = shlex.split(configured, posix= os.name != "nt")
            command.append(str(path))

        with subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,):
            pass
        return f"Opened: {path}"

    except FileNotFoundError:
        return "No system file opener was found"

    except OSError as error:
        return f"Could not start player: {error}"

class WatchedDatabase:
    """
    Manages the watched database
    """

    def __init__(self, in_root, istartup_status):
        self.root = in_root
        istartup_status(f"Scanning for video files in {self.root}...", 0)
        self.files = self.find_video_files()

        if not self.files:
            istartup_status(f"No supported video files found below: {self.root}", 1)

        self.database_path = self.root / DATABASE_NAME

        old_records = self.load_database()

        istartup_status("Checking for new files and renames...", 0)
        self.records = self.reconcile_database(old_records)

        self.conflicting_keys = self.conflicting_identity_keys()

        if len(old_records) == 0:
            istartup_status("Creating watch database...", 0)
        else:
            istartup_status("Updating watch database...", 0)

        self.save_database()

    def database_relative_path(self, path):
        """
        Returns a normalized relative path for database usage
        """
        if isinstance(path, Path):
            return path.relative_to(self.root).as_posix()

        return Path(path).as_posix()

    def find_video_files(self):
        """Recursively find and naturally sort video files."""
        found = []

        for current_dir, dirs, files in os.walk(self.root):
            dirs.sort(key=natural_key)
            files.sort(key=natural_key)

            current_path = Path(current_dir)

            for filename in files:
                path = current_path / filename

                if path.suffix.casefold() in VIDEO_EXTENSIONS:
                    found.append(path)

        return found

    def load_database(self):
        """
        Load the watched-episode database.

        Old records are accepted for migration. Their device/inode fields are
        intentionally ignored by reconcile_database().
        """
        try:
            with self.database_path.open("r", encoding="utf-8") as file:
                data = json.load(file)

            if isinstance(data, dict) and isinstance(data.get("files"), dict):
                return data["files"]

        except (FileNotFoundError, json.JSONDecodeError, OSError):
            pass

        return {}


    def save_database(self):
        """
        Save the database using only watched, sha256, and size fields.
        """
        cleaned_records = {}

        for relative_path, record in self.records.items():
            if not isinstance(record, dict):
                continue

            identity = record.get("identity")
            cleaned_identity = {}

            if isinstance(identity, dict):
                sha256 = identity.get("sha256")
                size = identity.get("size")

                if isinstance(sha256, str) and isinstance(size, int):
                    cleaned_identity = {
                        "sha256": sha256,
                        "size": size,
                    }

            cleaned_records[relative_path] = {
                "watched": bool(record.get("watched", False)),
                "identity": cleaned_identity,
            }

        data = {"files": cleaned_records}

        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=self.database_path.parent,
            prefix=f"{self.database_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as file:
            json.dump(data, file, indent=2)
            file.write("\n")
            temporary_path = Path(file.name)

        temporary_path.replace(self.database_path)

    def reconcile_database(self, old_records):
        """
        Match current files with previous records.

        Exact relative paths are preferred. If a path is new, files are matched
        by first-1-MiB SHA-256 and size, but only when exactly one old record
        matches.

        Legacy device/inode identities are ignored and are not used as fallback
        identifiers.
        """
        irecords = {}

        # Build an index only from already-migrated SHA-256 identities.
        identity_index = {}

        for old_record in old_records.values():
            if not isinstance(old_record, dict):
                continue

            old_identity = old_record.get("identity")
            key = identity_key(old_identity)

            if key is not None:
                identity_index.setdefault(key, []).append(old_record)

        for path in self.files:
            relative_path = self.database_relative_path(path)
            identity = file_identity(path)

            # First preserve the status associated with the exact old path.
            previous_record = old_records.get(relative_path)

            # If hashing/statting failed, retain the exact-path watched state if
            # available, but do not attempt content matching.
            if previous_record is None and identity is not None:
                candidates = identity_index.get(identity_key(identity), [])

                # Ambiguous partial-content matches are deliberately ignored.
                if len(candidates) == 1:
                    previous_record = candidates[0]

            irecords[relative_path] = {
                "watched": bool(
                    isinstance(previous_record, dict)
                    and previous_record.get("watched", False)
                ),
                "identity": identity,
            }

        return irecords

    def is_watched(self, path):
        """
        Look a path up in the database to see if it has been marked watched
        """
        relative_path = self.database_relative_path(path)
        return self.records.get(relative_path, {}).get("watched", False)

    def next_unwatched_index(self):
        """
        Finds the index of the next unwatched episode
        """
        for index, path in enumerate(self.files):
            if not self.is_watched(path):
                return index

        return 0

    def conflicting_identity_keys(self):
        """
        Return identities shared by more than one currently discovered file.

        Files without a readable identity cannot be classified as conflicts.
        """
        counts = {}

        for path in self.files:
            identity = file_identity(path)
            key = identity_key(identity)

            if key is not None:
                counts[key] = counts.get(key, 0) + 1

        return {
            key
            for key, count in counts.items()
            if count > 1
        }

class TheWatcherCursesTUI:
    """
        Provides a Text User Interface (via curses)
    """
    def __init__(self, idatabase, iscreen, iuse_emoji):
        self.database = idatabase
        self.screen = iscreen
        self.selected = idatabase.next_unwatched_index()
        self.title = f" Episodes: {self.database.root} "
        self.use_emoji = iuse_emoji
        self.help_text_emoji = (
            " ↑/↓|j/k:🐢  PG↑/PG↓:🐇  →/n:👁  Enter:▶  "
            "Space: ✓/☐  Esc/q: quit"
        )
        self.help_text = (
            " ↑/↓|j/k: slow  PG↑/PG↓: fast  →/n: next unwatched  Enter: play  "
            "Space: toggle watched  Esc/q: quit"
        )

    def display_status_line(self, message, height, width):
        """Draw the status line, keeping the left message intact when possible."""

        watched_count = sum(
            self.database.is_watched(path)
            for path in self.database.files
        )

        file_count = len(self.database.files)

        if self.use_emoji:
            left_message = f" 👉: {self.selected + 1}/{file_count} ✓: {watched_count}/{file_count}"
        else:
            left_message = (
                f" selected: {self.selected + 1}/{file_count} "
                f"watched: {watched_count}/{file_count}"
            )

        right_message = ""

        if message:
            right_message = f"{message}"
        else:
            right_message = "Who watches The Watcher?"

        if width <= 0:
            return

        # Avoid writing into curses' bottom-right cell.
        usable_width = width - 1

        # The left message has priority.
        left_display = left_message[:usable_width]

        try:
            self.screen.addnstr(
                height - 1,
                0,
                left_display,
                usable_width,
                curses.A_DIM,
            )

            # Leave at least one space between the left and right messages.
            right_capacity = usable_width - len(left_display) - 1

            if right_capacity < 3:
                return

            # The right field includes one space on each side.
            max_message_length = right_capacity - 2
            right_display = right_message[:max_message_length]

            right_field = f" {right_display} "

            # Align the field against the right edge.
            right_start = usable_width - len(right_field)

            # This check is mostly defensive, but prevents overlap.
            if right_start <= len(left_display):
                return

            self.screen.addnstr(
                height - 1,
                right_start,
                right_field,
                len(right_field),
                curses.A_REVERSE,
            )

        except curses.error:
            pass

    def draw_file_lines(self, height, width):
        """
        Responsible for drawing the lines that show the files in the directory
        """

        list_bottom = max(3, height - 2) # files to select start at row 3
        visible_rows = list_bottom - 3

        first_visible = min(
            max(0, self.selected - visible_rows // 2),
            max(0, len(self.database.files) - visible_rows),
        )
        if self.use_emoji:
            check = "✓"
        else:
            check = "*"

        for row, index in enumerate(
            range(
                first_visible,
                min(len(self.database.files), first_visible + visible_rows),
            )
        ):
            relative_name = self.database.database_relative_path(self.database.files[index])
            watched = self.database.is_watched(relative_name)

            is_conflicting = identity_key(
                self.database.records.get(relative_name, {}).get("identity")
            ) in self.database.conflicting_keys

            selection_prefix = "> " if index == self.selected else "  "

            text = (
                f"{selection_prefix}{"!" if is_conflicting else " "} "
                f"[{check if watched else " "}] {relative_name}"
            )

            attributes = curses.A_REVERSE if index == self.selected else curses.A_NORMAL

            if watched:
                attributes |= curses.A_DIM

            try:
                self.screen.addnstr(
                    3 + row, # starts at row 3
                    0,
                    text,
                    max(0, width - 1),
                    attributes,
                )
            except curses.error:
                pass

    def draw_screen(self, message, height, width):
        """
        Issues curses commands to draw the TUI
        """
        self.screen.erase()

        try:
            self.screen.addnstr(0, 0, self.title, max(0, width - 1), curses.A_BOLD)
            if self.use_emoji:
                self.screen.addnstr(1, 0, self.help_text_emoji, max(0, width - 1), curses.A_DIM)
            else:
                self.screen.addnstr(1, 0, self.help_text, max(0, width - 1), curses.A_DIM)
        except curses.error:
            pass

        if not self.database.files:
            try:
                self.screen.addnstr(3, 0, "No video files found.", max(0, width - 1))
            except curses.error:
                pass

            self.screen.refresh()
            return

        self.draw_file_lines(height, width)
        self.display_status_line(message, height, width)
        self.screen.refresh()

    def handle_input(self, session_stats):
        """
        Handles input from curses
        """
        key = self.screen.getch()
        message = ""

        quit_keys = (27, ord("q"), ord("Q"))
        up_keys = (curses.KEY_UP, ord("k"), ord("K"))
        down_keys = (curses.KEY_DOWN, ord("j"), ord("J"))
        next_unwatched_keys = (curses.KEY_RIGHT, ord("n"), ord("L"))
        enter_keys = (curses.KEY_ENTER, 10, 13)

        match key:
            case _ if key in quit_keys:
                message = "!!quit!!"

            case _ if key in up_keys:
                self.selected = max(0, self.selected - 1)

            case _ if key in down_keys:
                self.selected = min(len(self.database.files) - 1, self.selected + 1)

            case _ if key in next_unwatched_keys:
                self.selected = self.database.next_unwatched_index()
                message = "Reset selection to next to watch"

            case curses.KEY_HOME:
                self.selected = 0

            case curses.KEY_END:
                self.selected = len(self.database.files) - 1

            case curses.KEY_NPAGE:
                self.selected = min(len(self.database.files) - 1, self.selected + 10)

            case curses.KEY_PPAGE:
                self.selected = max(0, self.selected - 10)

            case _ if key in enter_keys:
                message = launch_file(self.database.files[self.selected])

            case 32:  # ord(" ")
                relative_path = self.database.database_relative_path(
                    self.database.files[self.selected]
                )
                record = self.database.records[relative_path]
                record["watched"] = not record["watched"]

                self.database.save_database()

                if record["watched"]:
                    session_stats["marked_watched"] += 1
                    self.selected = min(len(self.database.files) - 1, self.selected + 1)

                message = (
                    "Marked watched"
                    if record["watched"]
                    else "Marked unwatched"
                )

            case curses.KEY_RESIZE:
                pass

        return message

    def run_tui(self, session_stats):
        """
        The main loop of the program that draws the screen and read input
        """
        curses.curs_set(0)
        self.screen.keypad(True)

        message = ""

        while True:
            height, width = self.screen.getmaxyx()
            self.draw_screen(message, height, width)

            message = self.handle_input(session_stats)
            if message == "!!quit!!":
                break

def startup_status(message):
    """Display a startup message immediately."""
    print(message, flush=True)

@contextmanager
def curses_screen():
    """
    Slightly better for this usecase than curses.wrapper IMO
    """
    stdscr = curses.initscr()

    try:
        curses.noecho()
        curses.cbreak()
        stdscr.keypad(True)
        curses.start_color()

        try:
            curses.curs_set(0)
        except curses.error:
            pass

        yield stdscr

    finally:
        stdscr.keypad(False)
        curses.echo()
        curses.nocbreak()
        curses.curs_set(1)
        curses.endwin()

def main():
    """
    Main entry point where we gather the root and print usage if needed
    otherwise start running the TUI
    """

    parser = argparse.ArgumentParser(
        prog="the_watcher",
        description="Track watched episodes in a directory tree",
        epilog="Copyright (c) 2026 Ebben Feagan. Licensed under the MIT License.",
    )
    emoji_group = parser.add_mutually_exclusive_group()

    emoji_group.add_argument(
        "--no-emoji",
        dest="no_emoji",
        action="store_true",
        help="Disable emoji output",
    )

    emoji_group.add_argument(
        "--emoji",
        dest="no_emoji",
        action="store_false",
        help="Enable emoji output",
    )

    parser.set_defaults(no_emoji=os.name == "nt")

    parser.add_argument(
        "path",
        nargs="?",
        default=".",
        help="The (optional) path to scan for episodes, default is the current directory"
    )
    args = parser.parse_args()

    root = Path(args.path).expanduser().resolve()

    if not root.is_dir():
        print(f"Not a directory: {root}", file=sys.stderr)
        sys.exit(1)

    def report_status(message, exit_code):
        startup_status(message)
        if exit_code > 0:
            sys.exit(exit_code)

    database = WatchedDatabase(root, report_status)

    session_stats = {
        "marked_watched": 0,
    }

    try:
        with curses_screen() as stdscr:
            tui = TheWatcherCursesTUI(database, stdscr, not args.no_emoji)
            tui.run_tui(session_stats)

    except KeyboardInterrupt:
        pass

    finally:
        count = session_stats["marked_watched"]
        print(f"Episodes marked watched this session: {count}")

if __name__ == "__main__":
    main()
