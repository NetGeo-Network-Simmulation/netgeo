#!/usr/bin/env bash
# NetGeo Linux desktop bundle — portable build via Podman rootless.
#
# Why this exists: a PyInstaller onedir bundle links against the *build
# host's* glibc (via libpython*.so.1.0 and every compiled extension's
# .so), and glibc is forward-compatible only (an older glibc can run a
# binary built for it or an even older one, never a newer one). Building
# on this dev machine (Fedora 44, glibc 2.43) produced a binary that
# failed on Ubuntu 24.04 (glibc 2.39) with:
#   GLIBC_ABI_GNU2_TLS' not found (required by libpython3.14.so.1.0)
# See docs/qa/launcher-vm-ubuntu-2026-08-28.md for the full repro. Fix:
# build inside a container based on an OLD glibc so the resulting binary
# runs on anything with an equal-or-newer glibc.
#
# Base image chosen: ubuntu:22.04 (glibc 2.35). Covers: Ubuntu 22.04+,
# Debian 12+ (glibc 2.36), Fedora 37+ (glibc 2.36+) — everything with
# glibc >= 2.35. Older LTS (Ubuntu 20.04, glibc 2.31) is NOT covered;
# revisit with a still-older base (e.g. ubuntu:20.04) if that matters.
#
# Python version: ubuntu:22.04's own repos (main + universe) top out at
# python3.11 — no python3.12/3.13/3.14 package exists there (checked
# 2026-08-28, apt-cache madison: empty). The dev venv here runs 3.14, but
# nothing in backend/app requires it — backend/app/store/postgres.py (the
# only module with a stated 3.12 requirement) is never imported by the
# default app (see packaging/README.md "Postgres / Redis"). So this
# script builds with python3.11, the newest available on this base
# without adding a PPA.
#
# ponytail: reuses packaging/netgeo.spec unmodified (no second spec) and
# packaging/requirements.txt + backend/requirements.txt unmodified (no
# pinned-version fork for the container). frontend/dist is built on the
# host (Node already here) and bind-mounted in read-only — installing
# Node inside the container too would just double the work for nothing.
set -euo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." &>/dev/null && pwd)"
OUT_DIR="${1:-$REPO_ROOT/packaging/dist-container}"
IMAGE="docker.io/library/ubuntu:22.04"

if [ ! -d "$REPO_ROOT/frontend/dist" ]; then
    echo "Building frontend on host (Node) ..."
    (cd "$REPO_ROOT/frontend" && npm ci --silent && npm run build --silent)
fi

rm -rf "$OUT_DIR"
mkdir -p "$OUT_DIR"

echo "Building onedir bundle in Podman ($IMAGE, glibc 2.35, python3.11) ..."
# ponytail: bind-mount only the subpaths netgeo.spec's pathex/Analysis needs
# (backend/app + backend/engine, both imported by app/services/netlab.py;
# packaging/; frontend/dist) — not the whole repo. Smaller SELinux relabel
# (:Z) surface, and skips .venv/.git/node_modules (123M/221M+) entirely.
# Named volumes cache apt/pip downloads across re-runs of this script (this
# environment's egress is bandwidth-capped — ~115KB/s measured 2026-08-28 —
# so a cold full re-download per attempt is painfully slow otherwise).
podman run --rm --network=host \
    -v "$REPO_ROOT/backend/app:/repo-ro/backend/app:ro,Z" \
    -v "$REPO_ROOT/backend/engine:/repo-ro/backend/engine:ro,Z" \
    -v "$REPO_ROOT/backend/requirements.txt:/repo-ro/backend/requirements.txt:ro,Z" \
    -v "$REPO_ROOT/packaging:/repo-ro/packaging:ro,Z" \
    -v "$REPO_ROOT/frontend/dist:/repo-ro/frontend/dist:ro,Z" \
    -v "$OUT_DIR:/out:Z" \
    -v netgeo-build-apt-cache:/var/cache/apt/archives \
    -v netgeo-build-pip-cache:/root/.cache/pip \
    "$IMAGE" \
    bash -euc '
        export DEBIAN_FRONTEND=noninteractive
        # ponytail: -o Acquire::ForceIPv4=true — this environment resolves
        # archive.ubuntu.com to IPv6 addresses that hang (multi-minute
        # per-request timeouts) before falling back to working IPv4; forcing
        # IPv4 sidesteps it. Harmless on a host with working IPv6.
        apt-get -o Acquire::ForceIPv4=true update -qq
        apt-get -o Acquire::ForceIPv4=true install -y -qq python3.11 python3.11-venv libpython3.11 binutils \
            python3-gi gir1.2-webkit2-4.1 libwebkit2gtk-4.1-0 libgtk-3-0 >/dev/null
        # ponytail: --system-site-packages so the venv can see apt's python3-gi
        # (PyGObject has no pip wheel with the WebKit2 typelib bound in).
        # UNVERIFIED: apt ships python3-gi for Ubuntu 22.04s default python3
        # (3.10), this venv is python3.11 — whether the compiled bindings are
        # importable across that minor-version gap is not confirmed in this
        # slice. If not, the bundle still runs (launcher.py's browser
        # fallback), just without a native window — real verification is
        # AppImage-slice work, out of scope here.
        python3.11 -m venv --system-site-packages /build-venv
        /build-venv/bin/pip install -q --upgrade pip
        /build-venv/bin/pip install -q -r /repo-ro/backend/requirements.txt
        /build-venv/bin/pip install -q -r /repo-ro/packaging/requirements.txt
        mkdir -p /work/backend /work/frontend
        cp -r /repo-ro/backend/app /work/backend/app
        cp -r /repo-ro/backend/engine /work/backend/engine
        cp -r /repo-ro/packaging /work/packaging
        cp -r /repo-ro/frontend/dist /work/frontend/dist
        cd /work/packaging
        /build-venv/bin/pyinstaller netgeo.spec --distpath /out/dist --workpath /tmp/build --noconfirm
    '

echo "Bundle written to $OUT_DIR/dist/netgeo (built with python3.11 on glibc 2.35)."
