#!/usr/bin/env python3
"""
the_watcher - track watched episodes
"""

import curses
import json
import os
import re
import shlex
import subprocess
import sys
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


def file_identity(path):
    """
    Return information useful for detecting a renamed file.

    A rename on the same filesystem preserves the device and inode.
    Size is included as an additional safeguard.
    """
    stat = path.stat()

    return {
        "device": stat.st_dev,
        "inode": stat.st_ino,
        "size": stat.st_size,
    }


def identity_key(identity):
    """
    Get some info about a file to identify it if the name changes
    """
    return (
        identity.get("device"),
        identity.get("inode"),
        identity.get("size"),
    )


def load_database(database_path):
    """
    Loads the (json) watched episodes database from the disk
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
    Saves the (json) watched episodes database to the disk
    """
    temporary_path = database_path.with_suffix(".tmp")

    data = {
        "files": records,
    }

    with temporary_path.open("w", encoding="utf-8") as file:
        json.dump(data, file, indent=2)
        file.write("\n")

    temporary_path.replace(database_path)


def reconcile_database(root, files, old_records):
    """
    Match current files with previous records.

    Exact relative paths are preferred. If a path is new, the file's
    device/inode/size are checked against old records to detect renames.
    """
    records = {}

    # Build an index of old files by their identity.
    identity_index = {}

    for _, old_record in old_records.items():
        old_identity = old_record.get("identity")

        if old_identity:
            key = identity_key(old_identity)
            identity_index.setdefault(key, []).append(old_record)

    for path in files:
        relative_path = str(path.relative_to(root))
        identity = file_identity(path)

        # First try the exact path.
        previous_record = old_records.get(relative_path)

        # If the path is new, look for a uniquely matching old file.
        if previous_record is None:
            candidates = identity_index.get(identity_key(identity), [])

            if len(candidates) == 1:
                previous_record = candidates[0]

        records[relative_path] = {
            "watched": bool(
                previous_record and previous_record.get("watched", False)
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
    Attempts to launch the user's configured 
    video player with the selected file as a parameter.
    """
    command = player_command()
    command.append(str(path))

    try:
        with subprocess.Popen(args=command, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT):
            pass
        return f"Opened with: {' '.join(command[:-1])}"

    except FileNotFoundError:
        return f"Player not found: {command[0]}"

    except OSError as error:
        return f"Could not start player: {error}"


def is_watched(root, path, records):
    """
    Look a path up in the database to see if it has been marked watched
    """
    relative_path = str(path.relative_to(root))
    return records.get(relative_path, {}).get("watched", False)


def next_unwatched_index(root, files, records):
    """
    Finds the index of the next unwatched episode
    """
    for index, path in enumerate(files):
        if not is_watched(root, path, records):
            return index

    return 0

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

        marker = "✓" if watched else " "
        prefix = "> " if index == selected else "  "
        text = f"{prefix}[{marker}] {relative_name}"

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

    status = (
        f" Selected: {selected + 1}/{len(files)}"
        f"   Watched: {watched_count}/{len(files)}"
    )

    if message:
        status += f"   {message}"
    else:
        status += "   Who watches the watcher?"

    try:
        screen.addnstr(
            height - 1,
            0,
            status,
            max(0, width - 1),
            curses.A_DIM,
        )
    except curses.error:
        pass

    screen.refresh()


def run_tui(screen, root, files, records):
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

        QUIT_KEYS = (27, ord("q"), ord("Q"))
        UP_KEYS = (curses.KEY_UP, ord("k"), ord("K"))
        DOWN_KEYS = (curses.KEY_DOWN, ord("j"), ord("J"))
        NEXT_UNWATCHED_KEYS = (curses.KEY_RIGHT, ord("n"), ord("L"))
        ENTER_KEYS = (curses.KEY_ENTER, 10, 13)


        match key:
            case _ if key in QUIT_KEYS:
                break

            case _ if key in UP_KEYS:
                selected = max(0, selected - 1)

            case _ if key in DOWN_KEYS:
                selected = min(len(files) - 1, selected + 1)

            case _ if key in NEXT_UNWATCHED_KEYS:
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

            case _ if key in ENTER_KEYS:
                message = launch_file(files[selected])

            case 32:  # ord(" ")
                relative_path = str(files[selected].relative_to(root))
                record = records[relative_path]
                record["watched"] = not record["watched"]

                save_database(root / DATABASE_NAME, records)

                message = (
                    "Marked watched"
                    if record["watched"]
                    else "Marked unwatched"
                )

            case curses.KEY_RESIZE:
                pass



def main():
    """
    Main entry point where we gather the root and print usage if needed 
    otherwise start running the TUI
    """
    if len(sys.argv) == 1:
        root = Path(os.getcwd())
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

    files = find_video_files(root)

    if not files:
        print(f"No supported video files found below: {root}")
        sys.exit(0)

    database_path = root / DATABASE_NAME
    old_records = load_database(database_path)

    records = reconcile_database(
        root,
        files,
        old_records,
    )

    save_database(database_path, records)

    try:
        curses.wrapper(
            run_tui,
            root,
            files,
            records,
        )

    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
