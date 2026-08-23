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
- `../.github/workflows/desktop.yml` — builds the unsigned bundle on
  `windows-latest` + `ubuntu-latest`, uploads as a workflow artifact.
  Triggers: `workflow_dispatch` and `v*` tags only (not every push).
  Windows signing is a disabled (`if: false`) stub step with a comment
  naming the SignPath action and secrets it will need later — no
  credentials of any kind are in this repo.

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

## Not done in this slice (out of scope for C1-a)

- No NSIS `.exe` installer, no AppImage/.deb/.rpm — that's C2/C3.
- No macOS build.
- No onefile mode (onedir chosen for faster startup / easier debugging).
- Signing is a stub only — see the disabled step in `desktop.yml`.
- No auto-update wiring for the desktop binary.
- `launcher.py` always picks a random free port; no `--port` flag, no config
  file, no tray icon, no single-instance lock.
