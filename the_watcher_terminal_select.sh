#!/bin/sh

SCRIPT="$HOME/.local/bin/the_watcher"
DIRECTORY="$1"

# Prefer the user's configured terminal.
if command -v xdg-terminal-exec >/dev/null 2>&1; then
    exec xdg-terminal-exec -- python3 "$SCRIPT" "$DIRECTORY"
fi

# Common fallback terminals.
if command -v konsole >/dev/null 2>&1; then
    exec konsole -e python3 "$SCRIPT" "$DIRECTORY"
fi

if command -v gnome-terminal >/dev/null 2>&1; then
    exec gnome-terminal -- python3 "$SCRIPT" "$DIRECTORY"
fi

if command -v xfce4-terminal >/dev/null 2>&1; then
    exec xfce4-terminal --command="python3 \"$SCRIPT\" \"$DIRECTORY\""
fi

if command -v x-terminal-emulator >/dev/null 2>&1; then
    exec x-terminal-emulator -e python3 "$SCRIPT" "$DIRECTORY"
fi

if command -v xterm >/dev/null 2>&1; then
    exec xterm -e python3 "$SCRIPT" "$DIRECTORY"
fi

# No suitable terminal was found; exit without displaying an error.
exit 0
