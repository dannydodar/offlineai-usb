#!/usr/bin/env bash
# Create a desktop shortcut whose path points at this USB installation.
set -u
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
DESKTOP_DIR="${XDG_DESKTOP_DIR:-}"
if [ -z "$DESKTOP_DIR" ] && command -v xdg-user-dir >/dev/null 2>&1; then
    DESKTOP_DIR="$(xdg-user-dir DESKTOP 2>/dev/null || true)"
fi
[ -n "$DESKTOP_DIR" ] || DESKTOP_DIR="$HOME/Desktop"
mkdir -p "$DESKTOP_DIR"
SHORTCUT="$DESKTOP_DIR/OfflineAI.desktop"
{
    printf '%s\n' '[Desktop Entry]'
    printf '%s\n' 'Type=Application'
    printf '%s\n' 'Name=OfflineAI'
    printf '%s\n' 'Comment=Local offline assistant'
    printf 'Exec=/bin/bash "%s/start_offlineai.sh"\n' "$ROOT"
    printf 'Path=%s\n' "$ROOT"
    printf '%s\n' 'Terminal=false'
    printf '%s\n' 'Categories=Utility;'
} > "$SHORTCUT"
chmod +x "$SHORTCUT"
echo "Desktop shortcut created at $SHORTCUT"
