#!/usr/bin/env bash

set -u

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

SERVICE_MENU_DIR="$HOME/.local/share/kio/servicemenus"
BIN_DIR="$HOME/.local/bin"

MAIN_PROGRAM="the_watcher.py"
MAIN_PROGRAM_BIN="the_watcher"
TERMINAL_SELECTOR="the_watcher_terminal_select.sh"
TERMINAL_SELECTOR_BIN="the_watcher_terminal_select"
DESKTOP_FILE="the_watcher.desktop"
GNOME_SCRIPT="the_watcher_gnome.sh"
GNOME_SCRIPT_NAME="Track watched episodes here"
NAUTILUS_DIR="$HOME/.local/share/nautilus/scripts"

usage() {
    cat <<EOF
Usage:
  $(basename "$0") install
  $(basename "$0") uninstall
  $(basename "$0") status
  $(basename "$0") help

Commands:
  install     Install the episode tracker and Dolphin service menu
  uninstall   Remove the installed files, but leave directories intact
  status      Detect if the episode tracker and service menu are installed
  help        Show this help message

Status:
$(status_check)
EOF
}

require_source_file() {
    local filename="$1"
    local path="$SCRIPT_DIR/$filename"

    if [[ ! -f "$path" ]]; then
        echo "Error: source file not found: $path" >&2
        exit 1
    fi
}

install_files() {
    require_source_file "$MAIN_PROGRAM"
    require_source_file "$TERMINAL_SELECTOR"
    require_source_file "$DESKTOP_FILE"
    require_source_file "$GNOME_SCRIPT"

    mkdir -p "$SERVICE_MENU_DIR"
    mkdir -p "$BIN_DIR"
    mkdir -p "$NAUTILUS_DIR"

    install -m 755 \
        "$SCRIPT_DIR/$MAIN_PROGRAM" \
        "$BIN_DIR/$MAIN_PROGRAM_BIN"

    install -m 755 \
        "$SCRIPT_DIR/$TERMINAL_SELECTOR" \
        "$BIN_DIR/$TERMINAL_SELECTOR_BIN"

    install -m 755 \
        "$SCRIPT_DIR/$GNOME_SCRIPT" \
        "$NAUTILUS_DIR/$GNOME_SCRIPT_NAME"

    # Replace the placeholder in the desktop file with the actual
    # absolute path to ~/.local/bin.
    sed "s|__INSTALL_BIN__|$BIN_DIR|g" \
        "$SCRIPT_DIR/$DESKTOP_FILE" \
        >"$SERVICE_MENU_DIR/$DESKTOP_FILE"

    chmod 755 "$SERVICE_MENU_DIR/$DESKTOP_FILE"

    echo "Installed episode tracker."
    echo
    echo "Python program:"
    echo "  $BIN_DIR/$MAIN_PROGRAM_BIN"
    echo
    echo "Terminal selector:"
    echo "  $BIN_DIR/$TERMINAL_SELECTOR_BIN"
    echo
    echo "Dolphin service menu:"
    echo "  $SERVICE_MENU_DIR/$DESKTOP_FILE"
    echo
    echo "Nautilus Scripts Menu:"
    echo "  $NAUTILUS_DIR/$GNOME_SCRIPT_NAME"
}

status_check() {
    if [[ -e "$SERVICE_MENU_DIR/$DESKTOP_FILE" ]]; then
        echo "  Service Menu for KDE is installed"
    else
        echo "  Service Menu for KDE is NOT installed"
    fi

    if [[ -e "$NAUTILUS_DIR/$GNOME_SCRIPT_NAME" ]]; then
        echo "  GNOME Nautilus Scripts Menu entry is installed"
    else
        echo "  GNOME Nautilus Scripts Menu entry is NOT installed"
    fi

    if [[ -e "$BIN_DIR/$MAIN_PROGRAM_BIN" ]]; then
        echo "  the_watcher is installed to $BIN_DIR"
    else
        echo "  the_watcher is NOT installed to $BIN_DIR"
    fi

    if [[ -e "$BIN_DIR/$TERMINAL_SELECTOR_BIN" ]]; then
        echo "  the_watcher_terminal_select is installed to $BIN_DIR"
    else
        echo "  the_watcher_terminal_select is NOT installed to $BIN_DIR"
    fi
}

uninstall_files() {
    local removed=0

    if [[ -e "$SERVICE_MENU_DIR/$DESKTOP_FILE" ]]; then
        rm -- "$SERVICE_MENU_DIR/$DESKTOP_FILE"
        echo "Removed $SERVICE_MENU_DIR/$DESKTOP_FILE"
        removed=1
    fi

    if [[ -e "$BIN_DIR/$MAIN_PROGRAM_BIN" ]]; then
        rm -- "$BIN_DIR/$MAIN_PROGRAM_BIN"
        echo "Removed $BIN_DIR/$MAIN_PROGRAM_BIN"
        removed=1
    fi

    if [[ -e "$BIN_DIR/$TERMINAL_SELECTOR_BIN" ]]; then
        rm -- "$BIN_DIR/$TERMINAL_SELECTOR_BIN"
        echo "Removed $BIN_DIR/$TERMINAL_SELECTOR_BIN"
        removed=1
    fi

    if [[ "$removed" -eq 0 ]]; then
        echo "Nothing was installed."
    else
        echo
        echo "Uninstalled. Installation directories were left intact."
    fi
}

case "${1:-help}" in
install)
    install_files
    ;;

status)
    status_check
    ;;

uninstall)
    uninstall_files
    ;;

help | -h | --help)
    usage
    ;;

*)
    echo "Unknown command: $1" >&2
    echo
    usage
    exit 2
    ;;
esac
