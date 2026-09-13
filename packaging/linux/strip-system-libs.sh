#!/usr/bin/env bash
# Removes bundled runtime libs whose ABI MUST match the host's mesa/GL
# driver and X11 stack, not the CI build host's.
#
# Root cause (proven, not re-diagnosed here): PyInstaller's Analysis
# follows PySide6/QtWebEngine's own .so dependencies (ldd) and copies
# libstdc++.so.6 + libgcc_s.so.1 + libX11.so.6 from the build host
# (ubuntu-22.04, GLIBCXX_3.4.30) straight into the onedir bundle. On a
# newer host (Fedora 44, GLIBCXX_3.4.36) Qt's platform plugin loads the
# BUNDLED old libstdc++/libgcc_s into the same process as the system mesa
# driver, which needs newer GLIBCXX symbols -> driver load fails -> Wayland
# "EGL not available", X11 "Could not initialize GLX" then crash. Deleting
# the bundled copies (host's dynamic linker then falls through to
# /usr/lib) was verified to produce a working native window with zero
# EGL/GLX errors.
#
# libX11.so.6 gets the same treatment: it's loaded into the same process
# as the system GLX/Xorg stack (Qt's xcb platform plugin + mesa's DRI
# loader), so a stale bundled copy is the same class of risk. libexpat.so.1
# is NOT touched here — it's pulled in for XML parsing, never dlopen'd
# alongside the GL driver, so there is no shared-ABI hazard to fix.
#
# Runs on the onedir bundle BEFORE it is wrapped into AppImage/.deb/.rpm/
# tarball (all four copy this same directory verbatim) — one strip here
# fixes all of them. .deb/.rpm declare Depends/Requires on the distro's
# libstdc++/libX11 packages (see build-deb.sh, rpm/netgeo.spec) so
# apt/dnf guarantee they exist; libgcc_s rides along as libstdc++'s own
# transitive dependency, so it isn't declared by name (that package name
# has changed across distro releases — libgcc1 vs libgcc-s1 — declaring
# libstdc++ is the stable way to pull in whichever is current).
#
# AppImage and the plain tarball have NO package manager: with these libs
# no longer bundled, both now require the host to already provide them.
# For libstdc++/libgcc_s/libX11 that's a safe bet — practically every
# Linux desktop ships them (X11 or XWayland, plus any C++ program at all)
# — but it is a real, new requirement, written down here rather than left
# implicit.
set -euo pipefail

BUNDLE_DIR="${1:?usage: strip-system-libs.sh <onedir bundle dir>}"
LIBDIR="$BUNDLE_DIR/_internal"
[ -d "$LIBDIR" ] || LIBDIR="$BUNDLE_DIR"  # older PyInstaller: flat layout

for lib in libstdc++.so.6 libgcc_s.so.1 libX11.so.6; do
    if [ -e "$LIBDIR/$lib" ]; then
        rm -f "$LIBDIR/$lib"
        echo "strip-system-libs: removed bundled $lib (must match system driver ABI)"
    fi
done
