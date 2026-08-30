#!/usr/bin/env bash
# NetGeo Linux AppImage — wraps the glibc-2.35-floor onedir bundle
# (packaging/linux/build-in-container.sh) into a single-file AppImage.
#
# WebKitGTK is NOT bundled (see docs/qa/2026-08-30-format-installer-linux.md
# §F, confirmed empirically in this slice: PyInstaller's static import scan
# picks up the `gi` Python module, but GObject-Introspection typelibs are
# data files resolved from system paths at runtime, not Python imports —
# nothing in this pipeline collects them). It stays a documented system
# prerequisite; launcher.py already falls back to the system browser with
# an install hint when WebKitGTK is missing.
#
# ponytail: the onedir bundle is already self-contained (its own glibc-
# pinned .so files) — we don't need linuxdeploy's dependency-chasing
# (--executable) on top of it, that would just risk overwriting bundled
# libs with mismatched system ones. AppDir is hand-built; linuxdeploy-
# plugin-appimage only squashes it (that's its whole job — see its own
# docs, it doesn't deploy dependencies itself, `linuxdeploy` core does).
set -euo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." &>/dev/null && pwd)"
BUNDLE_DIR="${1:-$REPO_ROOT/packaging/dist-container/dist/netgeo}"
TOOLS_DIR="${APPIMAGE_TOOLS_DIR:-$REPO_ROOT/packaging/linux/.appimage-tools}"
OUT_DIR="$REPO_ROOT/packaging"
APPDIR="$REPO_ROOT/packaging/linux/AppDir"

if [ ! -x "$BUNDLE_DIR/netgeo" ]; then
    echo "error: onedir bundle not found at $BUNDLE_DIR" >&2
    echo "build it first: ./packaging/linux/build-in-container.sh" >&2
    exit 1
fi

mkdir -p "$TOOLS_DIR"
LINUXDEPLOY="$TOOLS_DIR/linuxdeploy-x86_64.AppImage"
PLUGIN="$TOOLS_DIR/linuxdeploy-plugin-appimage-x86_64.AppImage"
for pair in \
    "$LINUXDEPLOY https://github.com/linuxdeploy/linuxdeploy/releases/download/continuous/linuxdeploy-x86_64.AppImage" \
    "$PLUGIN https://github.com/linuxdeploy/linuxdeploy-plugin-appimage/releases/download/continuous/linuxdeploy-plugin-appimage-x86_64.AppImage"
do
    dest="${pair%% *}"; url="${pair#* }"
    if [ ! -f "$dest" ]; then
        echo "Fetching $(basename "$dest") ..."
        curl -L -o "$dest" -s "$url"
        chmod +x "$dest"
    fi
done

# --appimage-extract-and-run: this environment has no guarantee of a FUSE
# mount available (rootless, no sudo) — extract-and-run sidesteps that
# instead of failing outright.
RUN_FLAG="--appimage-extract-and-run"

echo "Building AppDir at $APPDIR ..."
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/bin" "$APPDIR/usr/share/applications" \
    "$APPDIR/usr/share/icons/hicolor/256x256/apps"

# Whole onedir bundle, unmodified — the "netgeo" executable expects its
# sibling support files (_internal/ or flat .so/.pyz, PyInstaller-version-
# dependent) to sit right next to it.
cp -r "$BUNDLE_DIR" "$APPDIR/usr/bin/netgeo-bundle"

cat > "$APPDIR/AppRun" <<'EOF'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/usr/bin/netgeo-bundle/netgeo" "$@"
EOF
chmod +x "$APPDIR/AppRun"

sed "s|__NETGEO_EXEC__|AppRun|" "$REPO_ROOT/packaging/linux/netgeo.desktop" \
    > "$APPDIR/usr/share/applications/netgeo.desktop"
cp "$APPDIR/usr/share/applications/netgeo.desktop" "$APPDIR/netgeo.desktop"
cp "$REPO_ROOT/packaging/icons/netgeo-256.png" \
    "$APPDIR/usr/share/icons/hicolor/256x256/apps/netgeo.png"
cp "$REPO_ROOT/packaging/icons/netgeo-256.png" "$APPDIR/netgeo.png"

echo "Packing AppImage ..."
cd "$OUT_DIR"
rm -f NetGeo-x86_64.AppImage
ARCH=x86_64 "$PLUGIN" $RUN_FLAG --appdir "$APPDIR"
mv ./*-x86_64.AppImage NetGeo-x86_64.AppImage 2>/dev/null || true

echo "Built: $OUT_DIR/NetGeo-x86_64.AppImage ($(du -h "$OUT_DIR/NetGeo-x86_64.AppImage" | cut -f1))"
