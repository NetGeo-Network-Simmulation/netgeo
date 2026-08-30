"""NetGeo desktop launcher — all-in-one native entry point.

Runs the existing FastAPI backend (already Postgres/Redis-free — see
packaging/README.md) on a free localhost port, mounts the built frontend
(frontend/dist) as static files on that same app/port, and opens it in a
native pywebview window (WebKitGTK on Linux). Single process, single port.

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
import time
import webbrowser
from pathlib import Path

FROZEN = getattr(sys, "frozen", False)
if FROZEN:
    # PyInstaller bundle: app package + frontend_dist + icons were collected
    # as datas into sys._MEIPASS (see netgeo.spec) — no repo layout to walk.
    BUNDLE_DIR = Path(sys._MEIPASS)  # type: ignore[attr-defined]
    FRONTEND_DIST = BUNDLE_DIR / "frontend_dist"
    ICONS_DIR = BUNDLE_DIR / "icons"
else:
    REPO_ROOT = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(REPO_ROOT / "backend"))
    FRONTEND_DIST = REPO_ROOT / "frontend" / "dist"
    ICONS_DIR = REPO_ROOT / "packaging" / "icons"

import uvicorn  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from app.main import app  # noqa: E402

# Distro-specific WebKitGTK prerequisites (see docs/qa/2026-08-30-shell-dan-
# desktop.md §6 / 2026-08-30-format-installer-linux.md §D — package names
# verified there, not guessed here).
_WEBKIT_INSTALL_HINT = (
    "    Fedora:         sudo dnf install webkit2gtk4.1 python3-gobject gtk3\n"
    "    Ubuntu/Debian:  sudo apt install libwebkit2gtk-4.1-0 python3-gi "
    "gir1.2-webkit2-4.1 libgtk-3-0"
)


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


def _webview_unavailable(exc: Exception | None) -> str:
    detail = f" ({exc})" if exc else ""
    return (
        f"[netgeo-launcher] Jendela aplikasi asli tidak tersedia{detail} — "
        "membuka browser sistem sebagai gantinya.\n"
        "  Untuk jendela aplikasi asli, pasang WebKitGTK dulu:\n" + _WEBKIT_INSTALL_HINT
    )


def _try_webview(url: str) -> bool:
    """Open `url` in a native window (pywebview/WebKitGTK). Blocks on this
    (main) thread until the window closes; returns True if it ran that way.
    Returns False, without blocking, if pywebview or its native backend
    (WebKitGTK/Qt) isn't importable here — caller falls back to the system
    browser instead of crashing.
    """
    try:
        import webview
    except ImportError as exc:
        print(_webview_unavailable(exc), file=sys.stderr)
        return False

    try:
        # Matches packaging/linux/netgeo.desktop's StartupWMClass=netgeo so
        # GNOME groups the window under the NetGeo taskbar/dock icon instead
        # of a generic "python3" one.
        from gi.repository import GLib

        GLib.set_prgname("netgeo")
    except Exception:
        pass  # ponytail: cosmetic only (taskbar grouping) — never fatal

    icon_path = ICONS_DIR / "netgeo-256.png"
    webview.create_window("NetGeo", url)
    try:
        webview.start(icon=str(icon_path) if icon_path.is_file() else None)
    except Exception as exc:  # webview.errors.WebViewException when GTK/Qt missing
        print(_webview_unavailable(exc), file=sys.stderr)
        return False
    return True


def main() -> None:
    _mount_frontend()
    port = _free_port()
    url = f"http://127.0.0.1:{port}"
    print(f"[netgeo-launcher] serving on {url}")

    # ponytail: NETGEO_NO_BROWSER skips opening any window/browser for
    # headless/CI/test runs — unchanged behavior from before pywebview.
    if os.environ.get("NETGEO_NO_BROWSER") == "1":
        uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")
        return

    server_thread = threading.Thread(
        target=uvicorn.run,
        kwargs={"app": app, "host": "127.0.0.1", "port": port, "log_level": "info"},
        daemon=True,
    )
    server_thread.start()
    time.sleep(1.0)  # give uvicorn a moment to bind before opening the window

    if not _try_webview(url):
        webbrowser.open(url)
        # webview.start() blocks until the window closes; the browser
        # fallback has no such signal, so just keep the process (and its
        # background uvicorn thread) alive the same way uvicorn.run() used to.
        server_thread.join()


if __name__ == "__main__":
    main()
