#!/usr/bin/env bash

# check first if multiple items are selcted and bail
# then check if the item is not a directory and bail
# only a single directory should pass to the end

selected_paths="$NAUTILUS_SCRIPT_SELECTED_FILE_PATHS"

count=$(printf '%s\n' "$selected_paths" | wc -l)

[[ $count -ne 1 ]] && exit 0

folder=$(printf '%s' "$selected_paths")

[[ -d "$folder" ]] || exit 0

"$HOME/.local/bin/the_watcher_terminal_select" "folder"
