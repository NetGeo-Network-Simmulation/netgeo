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

datas = [(str(BACKEND / "app"), "app"), (str(ICONS), "icons")]
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
        # Native window (Qt backend): pywebview's platforms/qt.py imports
        # `qtpy`, which picks its real Qt binding (PySide6 here) at runtime
        # via its own probing, not a static import PyInstaller's analysis can
        # follow. Listing the concrete PySide6 submodules makes PyInstaller
        # treat them as reachable, which is what makes it run its own bundled
        # hook-PySide6.QtWebEngineCore.py (collects the QtWebEngineProcess
        # helper binary + resources/translations) — confirmed present in
        # pyinstaller==6.22.2's hooks/ dir. GTK/gi has no equivalent hook,
        # which is why that path was abandoned (see packaging/README.md).
        "qtpy",
        "qtpy.QtCore",
        "qtpy.QtGui",
        "qtpy.QtWidgets",
        "PySide6.QtCore",
        "PySide6.QtGui",
        "PySide6.QtWidgets",
        "PySide6.QtNetwork",
        "PySide6.QtPrintSupport",
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets",
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
