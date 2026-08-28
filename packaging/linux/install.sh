#!/usr/bin/env bash
# NetGeo Linux desktop integration — per-user install, no root required.
#
# Copies the PyInstaller onedir bundle to ~/.local/share/netgeo/, installs a
# .desktop launcher and hicolor icons, then refreshes the desktop caches.
# Safe to run more than once (idempotent — just overwrites).
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
# ponytail: the onedir bundle ships one directory up from packaging/linux/
# (packaging/dist/netgeo/) in a dev checkout, but a distributed tarball puts
# it right next to this script — check both, first match wins.
if [ -d "$SCRIPT_DIR/../dist/netgeo" ]; then
    BUNDLE_SRC="$SCRIPT_DIR/../dist/netgeo"
elif [ -d "$SCRIPT_DIR/../netgeo" ]; then
    BUNDLE_SRC="$SCRIPT_DIR/../netgeo"
else
    echo "error: could not find the netgeo onedir bundle (expected packaging/dist/netgeo)" >&2
    echo "build it first: cd packaging && pyinstaller netgeo.spec" >&2
    exit 1
fi

INSTALL_DIR="$HOME/.local/share/netgeo"
DESKTOP_DIR="$HOME/.local/share/applications"
ICON_BASE="$HOME/.local/share/icons/hicolor"

echo "Installing NetGeo to $INSTALL_DIR ..."
rm -rf "$INSTALL_DIR"
mkdir -p "$INSTALL_DIR"
cp -r "$BUNDLE_SRC"/. "$INSTALL_DIR"/
chmod +x "$INSTALL_DIR/netgeo"

mkdir -p "$DESKTOP_DIR"
sed "s|__NETGEO_EXEC__|$INSTALL_DIR/netgeo|" "$SCRIPT_DIR/netgeo.desktop" \
    > "$DESKTOP_DIR/netgeo.desktop"

for size in 128 256 512; do
    src="$SCRIPT_DIR/../icons/netgeo-${size}.png"
    if [ -f "$src" ]; then
        dest_dir="$ICON_BASE/${size}x${size}/apps"
        mkdir -p "$dest_dir"
        cp "$src" "$dest_dir/netgeo.png"
    fi
done

command -v update-desktop-database >/dev/null 2>&1 && \
    update-desktop-database "$DESKTOP_DIR" || true
command -v gtk-update-icon-cache >/dev/null 2>&1 && \
    gtk-update-icon-cache -f -t "$ICON_BASE" >/dev/null 2>&1 || true

echo "Installed. NetGeo should now appear in your application menu."
echo "Run directly with: $INSTALL_DIR/netgeo"
