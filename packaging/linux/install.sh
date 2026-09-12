#!/usr/bin/env bash
# NetGeo Linux desktop integration — per-user install, no root required.
#
# Copies the PyInstaller onedir bundle to ~/.local/share/netgeo/, installs a
# .desktop launcher and hicolor icons, then refreshes the desktop caches.
# Safe to run more than once (idempotent — just overwrites).
#
# Offline map (optional, OFFLINE-MAP-3): NetGeo can read basemap tiles from a
# local MBTiles file instead of the internet (see backend/app/services/
# offline_maps.py). This script does not download any curated "region" —
# there is no hosting for that yet — it only offers to place a file you
# already have, or fetch one from a URL you type in yourself. See
# packaging/README.md for the full picture.
#
#   --offline-map=PATH   copy this local .mbtiles file into place
#   --offline-map-url=URL curl this URL (must point straight at a .mbtiles)
#   --no-offline-map     skip the offline-map step, no prompt (for scripts)
#
# With none of the three: an interactive terminal gets a short prompt: a
# non-interactive one (no tty, e.g. CI) silently skips — the safe default is
# always "no offline map installed", which means NetGeo keeps using its
# online tile providers.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"

OFFLINE_MAP_DEST="$HOME/.config/netgeo/offline-map.mbtiles"
OFFLINE_MAP_SRC=""
OFFLINE_MAP_URL=""
SKIP_OFFLINE_MAP=0

usage() {
    cat <<'EOF'
Usage: install.sh [options]

Options:
  --offline-map=PATH      Install this local .mbtiles file as NetGeo's
                           offline basemap (copied to
                           ~/.config/netgeo/offline-map.mbtiles).
  --offline-map-url=URL   Download the .mbtiles from this URL instead.
                           NetGeo contacts only the URL you give it here —
                           no built-in host, no curated region list.
  --no-offline-map        Skip the offline-map step entirely, no prompt.
                           Default behavior otherwise: interactive terminals
                           are asked; non-interactive runs skip silently.
  -h, --help              Show this help and exit.

Without an offline map installed, NetGeo uses its normal online map tiles.
EOF
}

for arg in "$@"; do
    case "$arg" in
        --offline-map=*) OFFLINE_MAP_SRC="${arg#*=}" ;;
        --offline-map-url=*) OFFLINE_MAP_URL="${arg#*=}" ;;
        --no-offline-map) SKIP_OFFLINE_MAP=1 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "error: unknown option: $arg" >&2; usage >&2; exit 1 ;;
    esac
done

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

install_offline_map() {
    if [ -n "$OFFLINE_MAP_SRC" ]; then
        if [ ! -f "$OFFLINE_MAP_SRC" ]; then
            echo "error: --offline-map file not found: $OFFLINE_MAP_SRC" >&2
            exit 1
        fi
        mkdir -p "$(dirname "$OFFLINE_MAP_DEST")"
        cp "$OFFLINE_MAP_SRC" "$OFFLINE_MAP_DEST"
        echo "Offline map installed: $OFFLINE_MAP_DEST"
        return
    fi

    if [ -n "$OFFLINE_MAP_URL" ]; then
        if ! command -v curl >/dev/null 2>&1; then
            echo "error: curl is required for --offline-map-url" >&2
            exit 1
        fi
        mkdir -p "$(dirname "$OFFLINE_MAP_DEST")"
        curl -fsSL "$OFFLINE_MAP_URL" -o "$OFFLINE_MAP_DEST"
        echo "Offline map downloaded to: $OFFLINE_MAP_DEST"
        return
    fi

    if [ "$SKIP_OFFLINE_MAP" -eq 1 ] || [ ! -t 0 ]; then
        return
    fi

    echo ""
    echo "Peta offline (opsional) — NetGeo memakai peta online secara default."
    echo "  1) Lewati (default) — pakai peta online"
    echo "  2) Pasang dari berkas .mbtiles lokal yang sudah kamu punya"
    echo "  3) Unduh dari URL yang kamu tentukan sendiri"
    read -r -p "Pilihan [1]: " choice || choice=""
    case "$choice" in
        2)
            read -r -p "Path ke berkas .mbtiles: " path || path=""
            if [ -n "$path" ] && [ -f "$path" ]; then
                mkdir -p "$(dirname "$OFFLINE_MAP_DEST")"
                cp "$path" "$OFFLINE_MAP_DEST"
                echo "Offline map installed: $OFFLINE_MAP_DEST"
            else
                echo "Dilewati (berkas tidak ditemukan) — memakai peta online."
            fi
            ;;
        3)
            read -r -p "URL berkas .mbtiles: " url || url=""
            if [ -n "$url" ] && command -v curl >/dev/null 2>&1; then
                mkdir -p "$(dirname "$OFFLINE_MAP_DEST")"
                if curl -fsSL "$url" -o "$OFFLINE_MAP_DEST"; then
                    echo "Offline map downloaded to: $OFFLINE_MAP_DEST"
                else
                    rm -f "$OFFLINE_MAP_DEST"
                    echo "Unduh gagal — memakai peta online."
                fi
            else
                echo "Dilewati — memakai peta online."
            fi
            ;;
        *)
            echo "Dilewati — memakai peta online."
            ;;
    esac
}

install_offline_map

echo "Installed. NetGeo should now appear in your application menu."
echo "Run directly with: $INSTALL_DIR/netgeo"
