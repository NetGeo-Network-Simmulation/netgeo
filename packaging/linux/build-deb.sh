#!/usr/bin/env bash
# NetGeo Linux .deb package — wraps the same onedir bundle as the AppImage/
# tarball into a real Debian package: `apt install ./netgeo_*.deb` registers
# it in dpkg, pulls in the Qt/QtWebEngine runtime libs automatically, and
# gives a clean `apt remove netgeo`.
#
# ponytail: dpkg-deb is already present here and on the ubuntu-22.04 CI
# runner — no fpm/nfpm dependency needed for a single (amd64) architecture.
set -euo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." &>/dev/null && pwd)"
BUNDLE_DIR="${1:-$REPO_ROOT/packaging/dist-container/dist/netgeo}"
STAGE="$REPO_ROOT/packaging/linux/deb-build"
OUT_DIR="$REPO_ROOT/packaging"

if [ ! -x "$BUNDLE_DIR/netgeo" ]; then
    echo "error: onedir bundle not found at $BUNDLE_DIR" >&2
    echo "build it first: ./packaging/linux/build-in-container.sh" >&2
    exit 1
fi

VERSION="$(sed -n 's/^APP_VERSION = "\(.*\)"/\1/p' "$REPO_ROOT/backend/app/core/config.py")"
if [ -z "$VERSION" ]; then
    echo "error: could not read APP_VERSION from backend/app/core/config.py" >&2
    exit 1
fi

echo "Building .deb staging tree at $STAGE ..."
rm -rf "$STAGE"
mkdir -p "$STAGE/DEBIAN" "$STAGE/opt/netgeo" "$STAGE/usr/bin" \
    "$STAGE/usr/share/applications"

# Whole onedir bundle, unmodified (same layout as build-appimage.sh's AppDir).
cp -r "$BUNDLE_DIR"/. "$STAGE/opt/netgeo"/
ln -s /opt/netgeo/netgeo "$STAGE/usr/bin/netgeo"

sed "s|__NETGEO_EXEC__|/opt/netgeo/netgeo|" "$REPO_ROOT/packaging/linux/netgeo.desktop" \
    > "$STAGE/usr/share/applications/netgeo.desktop"

for size in 128 256 512; do
    dest_dir="$STAGE/usr/share/icons/hicolor/${size}x${size}/apps"
    mkdir -p "$dest_dir"
    cp "$REPO_ROOT/packaging/icons/netgeo-${size}.png" "$dest_dir/netgeo.png"
done

# Debian/Ubuntu names for the same Qt/QtWebEngine runtime libs listed under
# Fedora names in packaging/linux/rpm/netgeo.spec — see also the
# "Install Qt runtime libs" step in .github/workflows/desktop.yml, which
# installs these same packages to make PyInstaller's own Analysis pass.
cat > "$STAGE/DEBIAN/control" <<EOF
Package: netgeo
Version: $VERSION
Section: net
Priority: optional
Architecture: amd64
Depends: libgl1, libegl1, libxkbcommon0, libxcb-cursor0, libnss3, libxcomposite1, libxdamage1, libxrandr2, libxtst6, libasound2, libatspi2.0-0
Maintainer: NetGeo <noreply@netgeo.invalid>
Homepage: https://github.com/suryaex/netgeo
Description: Network simulation + GIS/digital-twin platform
 NetGeo simulates IP/routing networks with a GIS/digital-twin overlay for
 physical plant, rack layout, and RF planning.
EOF

# Cache refresh only — dpkg itself removes the files this package owns
# (opt/netgeo, the /usr/bin symlink, desktop entry, icons) on removal; it
# never touches $HOME, so ~/.config/netgeo/ (offline-map.mbtiles) is safe
# without any extra logic here.
cat > "$STAGE/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database /usr/share/applications || true
command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -f -t /usr/share/icons/hicolor >/dev/null 2>&1 || true
EOF
cp "$STAGE/DEBIAN/postinst" "$STAGE/DEBIAN/postrm"
chmod 755 "$STAGE/DEBIAN/postinst" "$STAGE/DEBIAN/postrm"

echo "Packing .deb ..."
OUT_FILE="$OUT_DIR/netgeo_${VERSION}_amd64.deb"
rm -f "$OUT_FILE"
dpkg-deb --root-owner-group --build "$STAGE" "$OUT_FILE"

echo "Built: $OUT_FILE ($(du -h "$OUT_FILE" | cut -f1))"
