#!/usr/bin/env bash

for directory in "$@"; do
    "$HOME/.local/bin/the_watcher_terminal_select" "$directory"
done
