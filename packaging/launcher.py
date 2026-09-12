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
    "gir1.2-webkit2-4.1 libgtk-3-0\n"
    "    (packaged builds bundle Qt/QtWebEngine already, so this GTK path is "
    "only relevant when running from source without packaging/requirements.txt "
    "installed, or on a machine missing base X11/OpenGL libs)"
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
    # ponytail: prefer the bundled Qt/PySide6 backend over GTK — WebKitGTK's
    # typelibs are a system prerequisite PyInstaller cannot cleanly bundle
    # (no hook collects .typelib/girepository data at all), while Qt/
    # QtWebEngine ships as a self-contained pip wheel that PyInstaller's own
    # bundled hooks (hook-PySide6.QtWebEngineCore.py et al.) already know how
    # to collect in full (helper binary, resources, translations). setdefault
    # so a source-run dev with only system GTK installed can still force it
    # back with PYWEBVIEW_GUI=gtk.
    os.environ.setdefault("PYWEBVIEW_GUI", "qt")
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


def _no_window_reason(args: list[str]) -> str | None:
    """Return why the native window should be skipped *by request*
    (`--no-window` flag or `NETGEO_NO_WINDOW=1` env var, same on/off
    convention as NETGEO_NO_BROWSER), or None to try the window normally.

    Kept separate from `_try_webview`'s own fallback so the log line can
    say "skipped, you asked" instead of "skipped, it broke" — variant #4
    of the five distribution forms (see docs/design/13-DISTRIBUTION-PLAN.md
    local-only note / vault netgeo-distribusi-lima-bentuk) made a deliberate
    choice, not a WebKitGTK/Qt failure.
    """
    if "--no-window" in args:
        return "--no-window"
    if os.environ.get("NETGEO_NO_WINDOW") == "1":
        return "NETGEO_NO_WINDOW=1"
    return None


def _print_version() -> None:
    from app.core.config import APP_VERSION

    print(f"NetGeo {APP_VERSION}")


def _print_help() -> None:
    print("NetGeo desktop launcher")
    print()
    print("Usage: netgeo [--version] [--help] [--no-window]")
    print()
    print("  --version    print the app version and exit")
    print("  --help       show this message and exit")
    print("  --no-window  run the backend, open the UI in the system browser")
    print("               instead of a native window (distribution variant #4)")
    print()
    print("Env vars:")
    print("  NETGEO_NO_WINDOW=1    same as --no-window (for systemd/containers)")
    print("  NETGEO_NO_BROWSER=1   run the backend headless, no window/browser")
    print("  PYWEBVIEW_GUI=gtk|qt  force the native-window backend")


def main() -> None:
    # ponytail: handled before touching the server/frontend at all — a user
    # running `netgeo --version` should get an instant answer, not a bound
    # port and a spawned window.
    args = sys.argv[1:]
    if "--version" in args:
        _print_version()
        return
    if "--help" in args or "-h" in args:
        _print_help()
        return

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

    reason = _no_window_reason(args)
    if reason:
        print(f"[netgeo-launcher] headless: native window skipped by request ({reason}) — opening system browser.")
        opened_native = False
    else:
        opened_native = _try_webview(url)  # False also logs *why* it failed, via _webview_unavailable

    if not opened_native:
        webbrowser.open(url)
        # webview.start() blocks until the window closes; the browser
        # fallback has no such signal, so just keep the process (and its
        # background uvicorn thread) alive the same way uvicorn.run() used to.
        server_thread.join()


if __name__ == "__main__":
    main()
