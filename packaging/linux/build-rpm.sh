#!/usr/bin/env bash
# NetGeo Linux .rpm package — same onedir bundle as the .deb/AppImage/
# tarball, packaged for dnf/rpm instead of apt/dpkg.
#
# ponytail: rpmbuild is available here and on Debian/Ubuntu CI via the
# `rpm` apt package (it ships rpmbuild despite the distro not using rpm
# itself) — no fpm/nfpm dependency needed.
set -euo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." &>/dev/null && pwd)"
BUNDLE_DIR="${1:-$REPO_ROOT/packaging/dist-container/dist/netgeo}"
TOPDIR="$REPO_ROOT/packaging/linux/rpm-build"
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

rm -rf "$TOPDIR"
mkdir -p "$TOPDIR"/{BUILD,RPMS,SOURCES,SPECS,SRPMS}
DESKTOP_FILE="$TOPDIR/netgeo.desktop"
sed "s|__NETGEO_EXEC__|/opt/netgeo/netgeo|" "$REPO_ROOT/packaging/linux/netgeo.desktop" \
    > "$DESKTOP_FILE"

echo "Building .rpm via rpmbuild ..."
rpmbuild -bb \
    --define "_topdir $TOPDIR" \
    --define "_app_version $VERSION" \
    --define "_bundle_dir $BUNDLE_DIR" \
    --define "_desktop_file $DESKTOP_FILE" \
    --define "_icons_dir $REPO_ROOT/packaging/icons" \
    --buildroot "$TOPDIR/BUILDROOT" \
    "$REPO_ROOT/packaging/linux/rpm/netgeo.spec"

BUILT_RPM="$(find "$TOPDIR/RPMS" -name '*.rpm' | head -1)"
if [ -z "$BUILT_RPM" ]; then
    echo "error: rpmbuild did not produce a .rpm under $TOPDIR/RPMS" >&2
    exit 1
fi

OUT_FILE="$OUT_DIR/netgeo-${VERSION}-1.x86_64.rpm"
cp "$BUILT_RPM" "$OUT_FILE"

echo "Built: $OUT_FILE ($(du -h "$OUT_FILE" | cut -f1))"
