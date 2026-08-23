# PyInstaller spec — NetGeo desktop launcher (C1-a skeleton, onedir build).
#
# Bundles packaging/launcher.py + backend/app + the built frontend
# (frontend/dist) + app icons into one onedir bundle.
#
# ponytail: onedir (not onefile) — faster startup, easier to inspect; NSIS/
# AppImage wrapping is a later slice (C2/C3), not this one.
#
# Build: cd packaging && ../backend/.venv/bin/pyinstaller netgeo.spec
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(SPECPATH).resolve().parent
BACKEND = REPO_ROOT / "backend"
FRONTEND_DIST = REPO_ROOT / "frontend" / "dist"
ICONS = REPO_ROOT / "packaging" / "icons"

datas = [(str(BACKEND / "app"), "app")]
if FRONTEND_DIST.is_dir():
    datas.append((str(FRONTEND_DIST), "frontend_dist"))

icon_path = str(ICONS / "netgeo.ico") if sys.platform == "win32" else None

a = Analysis(
    [str(REPO_ROOT / "packaging" / "launcher.py")],
    pathex=[str(BACKEND)],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "uvicorn.lifespan.on",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.loops.auto",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="netgeo",
    console=True,
    icon=icon_path,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    name="netgeo",
)
