# NetGeo desktop packaging (C1-a skeleton)

All-in-one native launcher for the D1 edition (see
`docs/design/13-DISTRIBUTION-PLAN.md`, local-only, not in this repo listing).
This is a **kerangka** — the simplest thing that runs, not a finished product.

## What's here

- `launcher.py` — runs the existing FastAPI app (`backend/app/main.py`)
  on a free localhost port, mounts `frontend/dist` as static files on the
  same port, opens the default browser. Single process, single port.
  Verified working both as a plain script and as the PyInstaller binary
  below (frontend served 200, `/api/health` 200, clean shutdown).
- `icons/` — `netgeo.ico` (16/32/48/64/128/256 multi-size, for Windows) and
  `netgeo-{128,256,512}.png` (Linux), rasterized from
  `frontend/public/netgeo.svg` with ImageMagick (`magick`).
- `netgeo.spec` — PyInstaller onedir spec bundling the launcher + `backend/app`
  (including its non-Python data files, `app/data/*.json`) + `frontend/dist` +
  the Windows icon. Builds and runs successfully on this machine
  (`pyinstaller netgeo.spec`, output in `packaging/dist/netgeo/`, gitignored).
- `requirements.txt` — pins `pyinstaller==6.22.2` (build-time only, not a
  runtime dependency — don't add it to `backend/requirements.txt`).
- `linux/` — per-user desktop integration: `netgeo.desktop` (XDG entry),
  `install.sh` / `uninstall.sh`.
- `windows/netgeo.iss` — Inno Setup script producing a per-user
  `netgeo-<version>-setup.exe` (Start Menu + optional Desktop shortcut,
  uninstaller, no admin rights required).
- `../.github/workflows/desktop.yml` — builds the unsigned onedir bundle on
  `windows-latest` + `ubuntu-latest`, then wraps it into an install-ready
  artifact per platform (Windows: Inno Setup `.exe`; Linux: tarball of the
  bundle + `linux/`), uploads both. Triggers: `workflow_dispatch` and `v*`
  tags only (not every push). Windows *signing* (separate from the
  installer) is a disabled (`if: false`) stub step — no credentials of any
  kind are in this repo.

## Installing

### Linux — tested, working (Fedora 44, 2026-08-28)

```
cd packaging && ../backend/.venv/bin/pyinstaller netgeo.spec   # build the bundle
cd linux && ./install.sh                                        # per-user, no root
```

Installs the bundle to `~/.local/share/netgeo/`, adds
`~/.local/share/applications/netgeo.desktop`, and hicolor icons
(128/256/512). Verified end-to-end on this machine: `.desktop` passes
`desktop-file-validate`, launching via its exact `Exec=` line serves `/`
(200, real built `index.html` + hashed JS asset also 200) and
`/api/health` (200), and `uninstall.sh` removes everything it installed
(idempotent, run twice cleanly) while leaving `~/.config/netgeo/` (the
user's projects/auth state — a different, unrelated directory) untouched.
A CI-built tarball (`netgeo-installer-linux-unsigned` artifact) has the
same `linux/install.sh` layout but has **not itself** been run from CI
output — only from a local build on this machine.

Uninstall: `packaging/linux/uninstall.sh` (or the copy under
`~/.local/share/netgeo/` is not kept — re-run the one from a checkout/tarball).

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

- No AppImage/`.deb`/`.rpm` for Linux, no macOS build.
- No code signing (Windows installer and binaries are unsigned; see the
  disabled step in `desktop.yml` and `docs/qa/code-signing-native-distribution`).
- No tray icon, no auto-start, no auto-update wiring.
- No onefile mode (onedir chosen for faster startup / easier debugging).
- `launcher.py` always picks a random free port; no `--port` flag, no config
  file, no single-instance lock.
