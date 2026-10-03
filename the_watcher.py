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
import json
import hashlib
import os
import re
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path


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

def database_relative_path(root, path):
    """
    Returns a normalized relative path for database usage
    """
    return path.relative_to(root).as_posix()

def natural_key(value):
    """Sort episode2 before episode10."""
    return [
        int(part) if part.isdigit() else part.casefold()
        for part in re.split(r"(\d+)", str(value))
    ]


def find_video_files(root):
    """Recursively find and naturally sort video files."""
    found = []

    for current_dir, dirs, files in os.walk(root):
        dirs.sort(key=natural_key)
        files.sort(key=natural_key)

        current_path = Path(current_dir)

        for filename in files:
            path = current_path / filename

            if path.suffix.casefold() in VIDEO_EXTENSIONS:
                found.append(path)

    return found


HASH_BYTES = 1_048_576

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



def load_database(database_path):
    """
    Load the watched-episode database.

    Old records are accepted for migration. Their device/inode fields are
    intentionally ignored by reconcile_database().
    """
    try:
        with database_path.open("r", encoding="utf-8") as file:
            data = json.load(file)

        if isinstance(data, dict) and isinstance(data.get("files"), dict):
            return data["files"]

    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass

    return {}


def save_database(database_path, records):
    """
    Save the database using only watched, sha256, and size fields.
    """
    cleaned_records = {}

    for relative_path, record in records.items():
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
        dir=database_path.parent,
        prefix=f"{database_path.name}.",
        suffix=".tmp",
        delete=False,
    ) as file:
        json.dump(data, file, indent=2)
        file.write("\n")
        temporary_path = Path(file.name)

    temporary_path.replace(database_path)

def reconcile_database(root, files, old_records):
    """
    Match current files with previous records.

    Exact relative paths are preferred. If a path is new, files are matched
    by first-1-MiB SHA-256 and size, but only when exactly one old record
    matches.

    Legacy device/inode identities are ignored and are not used as fallback
    identifiers.
    """
    records = {}

    # Build an index only from already-migrated SHA-256 identities.
    identity_index = {}

    for old_record in old_records.values():
        if not isinstance(old_record, dict):
            continue

        old_identity = old_record.get("identity")
        key = identity_key(old_identity)

        if key is not None:
            identity_index.setdefault(key, []).append(old_record)

    for path in files:
        relative_path = database_relative_path(root, path)
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

        records[relative_path] = {
            "watched": bool(
                isinstance(previous_record, dict)
                and previous_record.get("watched", False)
            ),
            "identity": identity,
        }

    return records

def player_command():
    """
    Get the player command from PLAYER.

    Examples:

        PLAYER=mpv
        PLAYER="mpv --fullscreen"
        PLAYER=vlc
    """
    configured = os.environ.get("PLAYER", "vlc")
    command = shlex.split(configured)

    return command if command else ["vlc"]


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


def is_watched(root, path, records):
    """
    Look a path up in the database to see if it has been marked watched
    """
    relative_path = database_relative_path(root, path)
    return records.get(relative_path, {}).get("watched", False)


def next_unwatched_index(root, files, records):
    """
    Finds the index of the next unwatched episode
    """
    for index, path in enumerate(files):
        if not is_watched(root, path, records):
            return index

    return 0

def conflicting_identity_keys(files):
    """
    Return identities shared by more than one currently discovered file.

    Files without a readable identity cannot be classified as conflicts.
    """
    counts = {}

    for path in files:
        identity = file_identity(path)
        key = identity_key(identity)

        if key is not None:
            counts[key] = counts.get(key, 0) + 1

    return {
        key
        for key, count in counts.items()
        if count > 1
    }


def display_status_line(screen, left_message, right_message):
    """Draw the status line, keeping the left message intact when possible."""
    height, width = screen.getmaxyx()

    if width <= 0:
        return

    # Avoid writing into curses' bottom-right cell.
    usable_width = width - 1

    # The left message has priority.
    left_display = left_message[:usable_width]
    left_width = len(left_display)

    try:
        screen.addnstr(
            height - 1,
            0,
            left_display,
            usable_width,
            curses.A_DIM,
        )

        # Leave at least one space between the left and right messages.
        right_capacity = usable_width - left_width - 1

        if right_capacity < 3:
            return

        # The right field includes one space on each side.
        max_message_length = right_capacity - 2
        right_display = right_message[:max_message_length]

        right_field = f" {right_display} "

        # Align the field against the right edge.
        right_start = usable_width - len(right_field)

        # This check is mostly defensive, but prevents overlap.
        if right_start <= left_width:
            return

        screen.addnstr(
            height - 1,
            right_start,
            right_field,
            len(right_field),
            curses.A_REVERSE,
        )

    except curses.error:
        pass



# pylint: disable-next=too-many-arguments,too-many-positional-arguments,too-many-locals
def draw_screen(screen, root, files, records, selected, message):
    """
    Issues curses commands to draw the TUI
    """
    screen.erase()

    height, width = screen.getmaxyx()

    title = f" Episodes: {root} "
    help_text = (
        " ↑/↓|j/k:🐢  PG↑/PG↓:🐇  →/n:👁  Enter:▶  "
        "Space: ✓/☐  Esc/q: quit"
    )

    try:
        screen.addnstr(0, 0, title, max(0, width - 1), curses.A_BOLD)
        screen.addnstr(1, 0, help_text, max(0, width - 1), curses.A_DIM)
    except curses.error:
        pass

    if not files:
        try:
            screen.addnstr(3, 0, "No video files found.", max(0, width - 1))
        except curses.error:
            pass

        screen.refresh()
        return

    conflicting_keys = conflicting_identity_keys(files)

    list_top = 3
    list_bottom = max(list_top, height - 2)
    visible_rows = list_bottom - list_top

    first_visible = max(0, selected - visible_rows // 2)
    first_visible = min(
        first_visible,
        max(0, len(files) - visible_rows),
    )

    for row, index in enumerate(
        range(
            first_visible,
            min(len(files), first_visible + visible_rows),
        )
    ):
        path = files[index]
        relative_name = str(path.relative_to(root))
        watched = is_watched(root, path, records)

        identity = records.get(relative_name, {}).get("identity")
        is_conflicting = identity_key(identity) in conflicting_keys

        marker = "✓" if watched else " "
        selection_prefix = "> " if index == selected else "  "
        conflict_prefix = "! " if is_conflicting else "  "

        text = (
            f"{selection_prefix}{conflict_prefix}"
            f"[{marker}] {relative_name}"
        )


        attributes = curses.A_REVERSE if index == selected else curses.A_NORMAL

        if watched:
            attributes |= curses.A_DIM

        try:
            screen.addnstr(
                list_top + row,
                0,
                text,
                max(0, width - 1),
                attributes,
            )
        except curses.error:
            pass

    watched_count = sum(
        is_watched(root, path, records)
        for path in files
    )

    left_message = f" 👉: {selected + 1}/{len(files)} ✓: {watched_count}/{len(files)}"
    right_message = ""

    if message:
        right_message = f"{message}"
    else:
        right_message = "Who watches The Watcher?"

    display_status_line(screen, left_message, right_message)

    screen.refresh()


def run_tui(screen, root, files, records, session_stats):
    """
    The main loop of the program that draws the screen and read input
    """
    curses.curs_set(0)
    screen.keypad(True)

    selected = next_unwatched_index(root, files, records)
    message = ""

    while True:
        draw_screen(
            screen,
            root,
            files,
            records,
            selected,
            message,
        )

        key = screen.getch()
        message = ""

        quit_keys = (27, ord("q"), ord("Q"))
        up_keys = (curses.KEY_UP, ord("k"), ord("K"))
        down_keys = (curses.KEY_DOWN, ord("j"), ord("J"))
        next_unwatched_keys = (curses.KEY_RIGHT, ord("n"), ord("L"))
        enter_keys = (curses.KEY_ENTER, 10, 13)


        match key:
            case _ if key in quit_keys:
                break

            case _ if key in up_keys:
                selected = max(0, selected - 1)

            case _ if key in down_keys:
                selected = min(len(files) - 1, selected + 1)

            case _ if key in next_unwatched_keys:
                selected = next_unwatched_index(root, files, records)
                message = "Reset selection to next to watch"

            case curses.KEY_HOME:
                selected = 0

            case curses.KEY_END:
                selected = len(files) - 1

            case curses.KEY_NPAGE:
                selected = min(len(files) - 1, selected + 10)

            case curses.KEY_PPAGE:
                selected = max(0, selected - 10)

            case _ if key in enter_keys:
                message = launch_file(files[selected])

            case 32:  # ord(" ")
                relative_path = str(files[selected].relative_to(root))
                record = records[relative_path]
                record["watched"] = not record["watched"]

                if record["watched"]:
                    session_stats["marked_watched"] += 1

                save_database(root / DATABASE_NAME, records)

                message = (
                    "Marked watched"
                    if record["watched"]
                    else "Marked unwatched"
                )


            case curses.KEY_RESIZE:
                pass

def startup_status(message):
    """Display a startup message immediately."""
    print(message, flush=True)


def main():
    """
    Main entry point where we gather the root and print usage if needed
    otherwise start running the TUI
    """
    if len(sys.argv) == 1:
        root = Path(os.getcwd()).resolve()
    elif len(sys.argv) == 2:
        root = Path(sys.argv[1]).expanduser().resolve()
    else:
        print(f"Usage: {sys.argv[0]} DIRECTORY")
        print()
        print("Example:")
        print(f"  {sys.argv[0]} /home/user/video/series_a")
        print()
        print("Use another media player:")
        print(f"  PLAYER=vlc {sys.argv[0]} /home/user/video/series_a")
        sys.exit(2)

    if not root.is_dir():
        print(f"Not a directory: {root}", file=sys.stderr)
        sys.exit(1)

    startup_status(f"Scanning for video files in {root}...")
    files = find_video_files(root)

    if not files:
        print(f"No supported video files found below: {root}")
        sys.exit(0)

    database_path = root / DATABASE_NAME

    startup_status(
        f"Loading watch history for {len(files)} video file(s)..."
    )
    old_records = load_database(database_path)

    startup_status(
        "Calculating file identities and updating watch history..."
    )
    records = reconcile_database(
        root,
        files,
        old_records,
    )

    startup_status("Saving watch history...")
    save_database(database_path, records)

    session_stats = {
        "marked_watched": 0,
    }

    try:
        curses.wrapper(
            run_tui,
            root,
            files,
            records,
            session_stats,
        )

    except KeyboardInterrupt:
        pass

    finally:
        count = session_stats["marked_watched"]
        print(f"Episodes marked watched this session: {count}")




if __name__ == "__main__":
    main()
