# NetGeo desktop packaging (C1-a skeleton)

All-in-one native launcher for the D1 edition (see
`docs/design/13-DISTRIBUTION-PLAN.md`, local-only, not in this repo listing).
This is a **kerangka** — the simplest thing that runs, not a finished product.

## What's here

- `launcher.py` — runs the existing FastAPI app (`backend/app/main.py`)
  on a free localhost port, mounts `frontend/dist` as static files on the
  same port, opens it in a native pywebview window (WebKitGTK on Linux).
  Single process, single port. Verified working both as a plain script and
  as the PyInstaller binary below (frontend served 200, `/api/health` 200,
  clean shutdown). See "System prerequisites" below for the native-window
  fallback behavior.
- `icons/` — `netgeo.ico` (16/32/48/64/128/256 multi-size, for Windows) and
  `netgeo-{128,256,512}.png` (Linux), rasterized from
  `frontend/public/netgeo.svg` with ImageMagick (`magick`).
- `netgeo.spec` — PyInstaller onedir spec bundling the launcher + `backend/app`
  (including its non-Python data files, `app/data/*.json`) + `frontend/dist` +
  `icons/` + the Windows icon. Builds and runs successfully on this machine
  (`pyinstaller netgeo.spec`, output in `packaging/dist/netgeo/`, gitignored).
- `requirements.txt` — pins `pyinstaller==6.22.2` (build-time only) and
  `pywebview==6.2.1` (runtime dependency of `launcher.py`; don't add either
  to `backend/requirements.txt`).
- `linux/` — per-user desktop integration: `netgeo.desktop` (XDG entry),
  `install.sh` / `uninstall.sh`, `build-in-container.sh` (portable Linux
  build via rootless Podman — see "Installing" below, **use this, not a
  direct `pyinstaller netgeo.spec` on this machine**), `build-appimage.sh`
  (wraps that bundle into a single-file `NetGeo-x86_64.AppImage` via
  `linuxdeploy` + `linuxdeploy-plugin-appimage`, downloaded on demand into
  `packaging/linux/.appimage-tools/`, gitignored).
- `windows/netgeo.iss` — Inno Setup script producing a per-user
  `netgeo-<version>-setup.exe` (Start Menu + optional Desktop shortcut,
  uninstaller, no admin rights required).
- `../.github/workflows/desktop.yml` — builds the unsigned onedir bundle on
  `windows-latest` + `ubuntu-22.04` (pinned, not `ubuntu-latest` — see
  comment in the workflow for why), then wraps it into an install-ready
  artifact per platform (Windows: Inno Setup `.exe`; Linux: tarball of the
  bundle + `linux/`), uploads both. Triggers: `workflow_dispatch` and `v*`
  tags only (not every push). Windows *signing* (separate from the
  installer) is a disabled (`if: false`) stub step — no credentials of any
  kind are in this repo.

## System prerequisites (Linux native window)

`launcher.py` opens NetGeo in a native window via `pywebview` (WebKitGTK).
That needs system packages — **not** installed by pip — that may not be on
a minimal machine:

```bash
# Fedora
sudo dnf install webkit2gtk4.1 python3-gobject gtk3

# Ubuntu / Debian
sudo apt install libwebkit2gtk-4.1-0 python3-gi gir1.2-webkit2-4.1 libgtk-3-0
```

`gir1.2-webkit2-4.1` (the introspection typelib) is the one most often
missed — without it PyGObject can import `gi` fine but can't reach WebKit2.

**If any of these are missing, NetGeo does not crash.** `launcher.py`
catches the failure, prints which packages to install, and opens the app in
the system's default browser instead — same URL, same app, just not in its
own window. This fallback path is exercised in
`backend/tests/test_launcher.py` (with `webview` stubbed/absent so CI never
needs WebKitGTK installed) and was verified live on this dev machine — see
"Installing" below for what was actually run.

## Installing

### Linux — DO NOT build the release bundle on this dev machine

**`pyinstaller netgeo.spec` run directly on this machine (Fedora 44, glibc
2.43) produces a binary that will not start on any older distro.** Proven,
not theoretical: a Fedora-built bundle shipped to a fresh Ubuntu 24.04 VM
(glibc 2.39) failed with
`GLIBC_ABI_GNU2_TLS' not found (required by libpython3.14.so.1.0)` — full
repro in `docs/qa/launcher-vm-ubuntu-2026-08-28.md` (local-only). Reason:
PyInstaller statically links the *build host's* glibc, and glibc only runs
forward (older glibc → newer distro is fine; newer glibc → older distro is
not).

**Fix: build inside `packaging/linux/build-in-container.sh`.** It runs the
same `pyinstaller netgeo.spec` inside a rootless Podman container based on
`ubuntu:22.04` (glibc 2.35, python3.11 — the newest available there), so the
resulting binary's glibc floor is 2.35 instead of whatever this machine
happens to be running. No new dependency: Podman is already installed and
already used on this machine (see vault `research/spike-frr-podman.md`).

```
./packaging/linux/build-in-container.sh          # → packaging/dist-container/dist/netgeo
cd packaging/linux && ./install.sh                # per-user, no root
```

`frontend/dist` is built on the host first (Node is here; not installed in
the container) and bind-mounted in. Named Podman volumes
(`netgeo-build-apt-cache`, `netgeo-build-pip-cache`) persist apt/pip
downloads across re-runs — this environment's egress was measured at
~115 KB/s, so re-downloading everything on every retry is otherwise brutal.

**Verified working, both directions, 2026-08-28:**
- Built in the container, run **on this machine (Fedora 44)**: `/` → 200,
  `/api/health` → 200, built JS asset → 200.
- Same binary, shipped to a fresh **Ubuntu 24.04.4 VM** (headless,
  1 vCPU/3.3 GB): `install.sh` as non-root, `NETGEO_NO_BROWSER=1`, `/` → 200,
  `/api/health` → `{"status":"ok","app":"NetGeo","version":"1.2.99","channel":"beta"}`,
  built JS asset (`/assets/index-BTJLwYj6.js`) → 200. `uninstall.sh` ran
  twice cleanly (idempotent), VM left with zero `netgeo` remnants.
- `objdump -T` across every bundled `.so` tops out at `GLIBC_2.35` — matches
  the container base exactly, nothing higher leaked in.

Not yet tested: Debian 12, Ubuntu 20.04 or older (glibc 2.31, below this
build's 2.35 floor — would need a still-older base image), any distro other
than Fedora/Ubuntu.

Uninstall: `packaging/linux/uninstall.sh` (or the copy under
`~/.local/share/netgeo/` is not kept — re-run the one from a checkout/tarball).

### Linux — AppImage (first installer format)

Decision + full comparison (AppImage vs Flatpak vs .deb/.rpm vs tarball):
`docs/qa/2026-08-30-format-installer-linux.md` (local-only). Short version:
single binary, no per-distro build, CI-friendly. WebKitGTK is **not**
bundled — see "System prerequisites" above; the same fallback applies.

```
./packaging/linux/build-in-container.sh   # → packaging/dist-container/dist/netgeo
./packaging/linux/build-appimage.sh       # → packaging/NetGeo-x86_64.AppImage
```

`build-appimage.sh` downloads `linuxdeploy` + `linuxdeploy-plugin-appimage`
(GitHub continuous releases, ~36 MB total, cached in
`packaging/linux/.appimage-tools/` — gitignored) the first time it runs,
hand-builds an `AppDir` around the onedir bundle (an `AppRun` script exec's
the existing `netgeo` binary in place — the onedir's own libs are already
glibc-2.35-pinned and self-contained, so `linuxdeploy`'s dependency-chasing
is skipped on purpose, it would just risk shadowing them with mismatched
system `.so`s), then runs the plugin's `--appimage-extract-and-run` to
squash it (this environment has no guaranteed FUSE mount for a nested
AppImage, rootless + no sudo — extract-and-run sidesteps that).

**Output size: 27 MB** (`NetGeo-x86_64.AppImage`, 28 023 288 bytes).

**Three questions the design doc left UNVERIFIED — closed here with a real
build + two real runs (Fedora 44 desktop, Ubuntu 24.04 headless VM):**

1. *Does PyInstaller/AppImage bundle `gi` + the WebKit2 typelib, or does it
   stay dependent on system `.so`s?* **Stays dependent — confirmed, not
   guessed.** `find packaging/dist-container/dist/netgeo -iname '*.typelib'`
   returns nothing; no `_gi*.so` extension is bundled either — PyInstaller's
   static analysis pulls in only the pure-Python `gi/__init__.py` stub (that
   .py file is portable, so it rides along), not the compiled introspection
   binary or any `.typelib` data file. Running the built binary reproduces
   this identically on both the Fedora host (which *has* `webkit2gtk4.1` +
   `python3-gobject` installed) and the headless Ubuntu VM (which doesn't):
   `ImportError: cannot import name '_gi' from partially initialized module
   'gi'`.
2. *Does Ubuntu 22.04's apt `python3-gi` (built for its default python3.10)
   actually import from a different-minor-version venv via
   `--system-site-packages`?* **No — confirmed false, root cause found: a
   CPython C-extension ABI mismatch, not a packaging omission.** Isolated
   in a throwaway container: `python3.10 -m venv --system-site-packages`
   imports `gi` and resolves `WebKit2-4.1.typelib` from the system path
   without issue (`OK 3.10 venv: <IntrospectionModule 'WebKit2' from
   '/usr/lib/x86_64-linux-gnu/girepository-1.0/WebKit2-4.1.typelib'>`) — the
   *same* apt packages, imported from a `python3.11 -m venv
   --system-site-packages` instead, fail with the identical `_gi`
   ImportError seen in the real bundle. `build-in-container.sh` builds with
   python3.11 (the newest on `ubuntu:22.04`'s own repos — see its header
   comment), one minor version off apt's `python3-gi`'s 3.10 build — that
   gap alone is fatal to the native window, independent of typelib
   bundling. Not changed here: switching the build to python3.10 to close
   this gap is a real, scoped fix, but was kept out of this slice per
   instructions not to force fragile bundling just to look done — the
   existing browser fallback already covers it correctly.
3. *Final AppImage size?* **27 MB**, see above.

**Verified working, both directions, 2026-08-30 (same two-machine pattern as
the tarball above):**
- **Fedora 44 (Wayland desktop, `webkit2gtk4.1`/`gtk3`/`python3-gobject`
  installed)**: the AppImage FUSE-mounted directly (no
  `--appimage-extract-and-run` needed — a real FUSE mount was available
  here), `netgeo-bundle/netgeo` started, `_try_webview` failed for the ABI
  reason above and printed the Fedora install hint, `webbrowser.open()`
  opened a **real Firefox tab** — `GET /` → 200, JS/CSS assets → 200,
  `GET /api/auth/setup` → 200.
- **Ubuntu 24.04 VM (headless, `superadmin@100.72.83.91`, no
  `webkit2gtk`/`libgtk-3-0`/`gir1.2-webkit2-4.1` installed, `python3-gi`
  present for its own python3.12)**: copied over scp, FUSE-mounted the same
  way, printed the correct **Ubuntu/Debian** install hint (not the Fedora
  one — the same `launcher.py` code path picks the right message, this
  isn't AppImage-specific), backend served `/api/health` → 200 and `/` →
  200 with no window (headless, expected).

Not tested: a distro with `webkit2gtk`/`gtk3`/`python3-gobject` present *and*
its default python3 at 3.11 (would need the ABI check above to actually
pass) — none was available in this session.

### Windows — written, NOT tested (no Windows machine reachable from here)

```
cd packaging && pyinstaller netgeo.spec
cd windows && iscc netgeo.iss
```

Produces `packaging/windows/dist-installer/netgeo-1.2.99-setup.exe`:
installs to `%LOCALAPPDATA%\NetGeo`, Start Menu shortcut + optional Desktop
shortcut, registers an uninstaller, `PrivilegesRequired=lowest` (no admin
prompt). CI now builds this on `windows-latest` (Inno Setup is preinstalled
on that runner image) and uploads it as `netgeo-installer-windows-unsigned`.
**Surya needs to test this on an actual Windows machine** — double-click,
confirm SmartScreen "Run anyway" flow, Start Menu entry, uninstall cleanup —
before it's considered verified.

## Postgres / Redis — investigated, NOT a blocker

`backend/app/core/config.py` declares `DATABASE_URL` and `REDIS_URL`, but
neither is read anywhere else in `app/` — grepped, zero hits outside
`config.py`. `app/store/__init__.py`'s `get_repo()` always returns the
in-memory `MemoryRepository`; `app/store/postgres.py` exists but is never
imported. The FastAPI `lifespan` hook in `main.py` opens no DB or Redis
connection. So the backend **already runs standalone**: no Docker, no
Postgres, no Redis needed for D1. Confirmed by running `launcher.py` (and
the PyInstaller binary) directly on this machine with no infra services up.

State persistence: `NETGEO_STATE_STORE` (default
`~/.config/netgeo/auth.json` sibling `state.json`) already persists
`MemoryRepository` to a JSON file across restarts when set — this is the
existing S2 PERSIST-01 mechanism, not something added in this slice.

Env var `NETGEO_NO_BROWSER=1` skips the auto-open-browser step (used for
non-interactive testing); unset/default behavior is unchanged.

## Not done (explicitly out of scope)

- No `.deb`/`.rpm` for Linux (AppImage now exists, see "Linux — AppImage" above), no macOS build.
- No code signing (Windows installer and binaries are unsigned; see the
  disabled step in `desktop.yml` and `docs/qa/code-signing-native-distribution`).
- No tray icon, no auto-start, no auto-update wiring.
- No onefile mode (onedir chosen for faster startup / easier debugging).
- `launcher.py` always picks a random free port; no `--port` flag, no config
  file, no single-instance lock.
