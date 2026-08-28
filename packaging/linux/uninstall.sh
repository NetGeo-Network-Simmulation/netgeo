#!/usr/bin/env bash
# NetGeo Linux desktop integration — uninstall.
#
# Removes only what install.sh created: the binary bundle, the .desktop
# entry, and the hicolor icons. Does NOT touch ~/.config/netgeo/ (user
# project data / state — see NETGEO_STATE_STORE in backend/app/core/config.py)
# so uninstalling never deletes a user's saved projects. Safe to run more
# than once (idempotent — missing files are simply skipped).
set -uo pipefail

INSTALL_DIR="$HOME/.local/share/netgeo"
DESKTOP_FILE="$HOME/.local/share/applications/netgeo.desktop"
ICON_BASE="$HOME/.local/share/icons/hicolor"

rm -rf "$INSTALL_DIR"
rm -f "$DESKTOP_FILE"
for size in 128 256 512; do
    rm -f "$ICON_BASE/${size}x${size}/apps/netgeo.png"
done

command -v update-desktop-database >/dev/null 2>&1 && \
    update-desktop-database "$HOME/.local/share/applications" || true
command -v gtk-update-icon-cache >/dev/null 2>&1 && \
    gtk-update-icon-cache -f -t "$ICON_BASE" >/dev/null 2>&1 || true

echo "Uninstalled. User project data in ~/.config/netgeo/ was left untouched."
