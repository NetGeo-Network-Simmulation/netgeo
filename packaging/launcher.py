"""NetGeo desktop launcher — all-in-one native entry point (C1-a skeleton).

Runs the existing FastAPI backend (already Postgres/Redis-free — see
packaging/README.md) on a free localhost port, mounts the built frontend
(frontend/dist) as static files on that same app/port, and opens the
default browser. Single process, single port.

ponytail: reuses app.main.app as-is (no edits to the normal dev/Docker
entrypoint) and mounts the frontend in-process instead of adding a second
static file server — one process, one port, nothing new to run.

Run:    python packaging/launcher.py
Smoke:  curl http://127.0.0.1:<port>/api/health
"""
from __future__ import annotations

import os
import socket
import sys
import threading
import webbrowser
from pathlib import Path

FROZEN = getattr(sys, "frozen", False)
if FROZEN:
    # PyInstaller bundle: app package + frontend_dist were collected as datas
    # into sys._MEIPASS (see netgeo.spec) — no repo layout to walk.
    BUNDLE_DIR = Path(sys._MEIPASS)  # type: ignore[attr-defined]
    FRONTEND_DIST = BUNDLE_DIR / "frontend_dist"
else:
    REPO_ROOT = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(REPO_ROOT / "backend"))
    FRONTEND_DIST = REPO_ROOT / "frontend" / "dist"

import uvicorn  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from app.main import app  # noqa: E402


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _mount_frontend() -> None:
    if FRONTEND_DIST.is_dir():
        app.mount("/", StaticFiles(directory=str(FRONTEND_DIST), html=True), name="frontend")
    else:
        print(
            f"[netgeo-launcher] WARNING: {FRONTEND_DIST} not found — "
            "build it first (cd frontend && npm run build). Running API-only.",
            file=sys.stderr,
        )


def main() -> None:
    _mount_frontend()
    port = _free_port()
    url = f"http://127.0.0.1:{port}"
    # ponytail: NETGEO_NO_BROWSER skips the auto-open for headless/CI/test
    # runs (a real browser.open() in a non-interactive session can hang).
    # Default (unset) behavior is unchanged: browser opens automatically.
    if os.environ.get("NETGEO_NO_BROWSER") != "1":
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    print(f"[netgeo-launcher] serving on {url}")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")


if __name__ == "__main__":
    main()
