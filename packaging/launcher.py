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
import signal
import socket
import subprocess
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
from app.main import app  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

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


class _WindowBridge:
    """js_api bridge for the frameless window's own title bar (frontend
    NativeTitleBar.tsx, called as window.pywebview.api.*).

    Frameless removes 100% of OS-drawn chrome, so drag/resize/minimize/
    maximize/close all have to be reimplemented here — and pywebview 6.2.1
    itself has real gaps doing that on Wayland (verified against the
    installed library, not assumed):
      - No edge-resize for a frameless window on either backend at all
        (grep platforms/gtk.py and platforms/qt.py: only `easy_drag`'s
        whole-window move exists; no begin_resize_drag/startSystemResize
        call anywhere in the library) — reimplemented below via GDK/Qt
        native calls.
      - Window.move()/gtk_window_move() is a documented Wayland no-op
        (absolute positioning isn't part of xdg-shell) — confirmed live
        2026-09-14: window.move() after a frameless open left the window
        at its original position. So dragging is reimplemented the same
        way, via begin_move_drag/startSystemMove, instead of the CSS
        `pywebview-drag-region` class the pywebview docs suggest (that
        mechanism is built on the broken move()).

    Every method here is invoked by pywebview on a throwaway Thread, not
    the GTK/Qt GUI thread (see webview/util.py js_bridge_call). Confirmed
    live that touching GTK off-thread silently no-ops/races (is_maximized()
    read stale after an off-thread maximize()); wrapping in GLib.idle_add
    fixed it.

    The Qt half used to marshal via `QMetaObject.invokeMethod(receiver, fn,
    QueuedConnection)` — WRONG (Surya QA, 2026-09-14 on 1.2.125.1: pressing
    maximize killed the whole native window and fell back to the browser).
    Verified against the bundled PySide6 6.11.2 binary (`strings` on the
    packaged QtCore.abi3.so, no Qt/PySide6 install exists on this dev
    machine to import and check directly): the ONLY `QMetaObject::
    invokeMethod` symbol compiled in is the classic overload taking a
    `const char*` method NAME, no functor overload exists at all. Passing a
    Python callable as the 2nd arg therefore matches no overload, raises,
    and used to be swallowed by a bare `except Exception: pass` that then
    ran `fn()` synchronously right there on the js_api thread — calling a
    Qt GUI method (showMaximized(), minimize(), destroy()) off the GUI
    thread is undefined behavior in Qt, which is what aborted the child
    process. Fixed with `QTimer.singleShot(0, context, fn)` instead: its
    C++ impl (`QTimer::singleShotImpl(..., QSlotObjectBase*)`, confirmed
    present in the same binary) runs `fn` on `context`'s thread via a
    queued connection — no method-name string, no functor overload needed.
    The direct `fn()` fallback is gone entirely: if scheduling itself fails,
    the operation is skipped, never run off-thread. minimize()/close() now
    route through this too (they used to call pywebview's Window.minimize()/
    destroy() directly, unmarshaled — verified in webview/platforms/qt.py:
    its module-level minimize(uid)/destroy_window(uid) call the Qt widget
    method with zero thread dispatch of their own, so those two were
    exactly as off-thread-unsafe as the broken toggle_maximize, just not
    yet the one Surya happened to click first). Still NOT verified against
    a real running Qt/PySide6 process at the time this was written — that
    gap is now closed (2026-09-14, same day): a scratch venv with the exact
    PySide6 6.11.2 + pywebview 6.2.1 pinned by packaging/requirements.txt
    (verified identical to what's PyInstaller-bundled at
    /opt/netgeo/_internal/PySide6, itself confirmed via `rpm -ql netgeo`)
    ran this exact `_try_webview()` unmodified, on this machine's real
    GNOME Wayland session, driving the real built frontend end-to-end.
    maximize/minimize/close/begin_move/begin_resize all confirmed working
    (QTimer.singleShot marshal fires, no SIGABRT, no off-thread Qt calls).

    Surya separately reported a persistent dark decoration bar (app icon +
    "NetGeo" text + a single unlabeled close button, no minimize/maximize,
    unresponsive to drag) on top of the INSTALLED v1.2.125.1 package — and
    proved with `QT_WAYLAND_DECORATION=bradient` that changing its
    appearance is possible, meaning *some* QtWaylandClient client-side-
    decoration plugin is genuinely being invoked on that build, not just a
    look-alike in our own page. That could not be reproduced here: polling
    `native.frameGeometry()` vs `native.geometry()` every 1-2s for 8s
    straight, through this exact unmodified `_try_webview()` (this fix
    included) against the real backend, the two stayed pixel-identical the
    entire time — Qt's own accounting of zero extra decoration pixels —
    and `native.grab()` shows NativeTitleBar rendering correctly (real
    `_button_layout()` result too: left side, close/minimize/maximize,
    matching Surya's actual `close,minimize,maximize:appmenu` gsetting).
    Same result driving raw PySide6 directly (no pywebview wrapper) and
    with a real QWebEngineView loaded with the real heavy app, ruling out
    "WebEngine itself breaks frameless" as well. So the Python-level
    flag-setting in this file (`frameless=True` -> `Qt.FramelessWindowHint`
    in webview/platforms/qt.py, unmodified pywebview code) is proven
    sufficient and correct in a clean environment with identical library
    versions — the open question is something specific to the PyInstaller-
    frozen runtime at /opt/netgeo (plugin resolution, a stale prior
    install's cached Wayland state, or something not yet identified) that
    a venv reproduction of the same source + same library versions does
    not exhibit. Not guess-fixed here for that reason: re-test against a
    freshly built package with this fix before assuming anything else is
    wrong at the Python level.
    """

    def __init__(self) -> None:
        self._window = None  # bound to the webview.Window via bind() once created

    def bind(self, window: object) -> None:
        self._window = window

    def _native(self):
        return self._window.native

    def _is_qt(self) -> bool:
        """Which pywebview platform backend is actually driving this window.

        NOT `type(native).__module__` (the previous check): `BrowserView`,
        the native widget class, is defined *inside pywebview's own*
        webview/platforms/qt.py (`class BrowserView(QMainWindow)`), so its
        `__module__` is always "webview.platforms.qt" — never "PySide6" —
        no matter which backend is active. That made the old check always
        False, so every window op fell through to the GTK branch, which
        needs `gi`, which is never bundled (confirmed live: Surya's drag
        attempt on the installed 1.2.125.1 package raised exactly
        `ModuleNotFoundError: No module named 'gi'` from inside begin_move).

        pywebview itself already tracks which backend won: `Window._initialize`
        (webview/window.py) stores the winning platform module on
        `self.gui`, and every built-in Window method dispatches through it
        (`self.gui.destroy_window(...)` etc) — that's the one place
        pywebview keeps this fact, so read it from there instead of
        re-deriving it from a widget class that happens to live in
        pywebview's own package regardless of backend. This can't go stale
        the way the old check did: as long as pywebview keeps using `.gui`
        to dispatch (its own core mechanism), this reads the true answer.
        """
        gui = getattr(self._window, "gui", None)
        return bool(gui) and gui.__name__.rsplit(".", 1)[-1] == "qt"

    def _run_on_gui_thread(self, fn) -> None:
        try:
            if self._is_qt():
                from PySide6.QtCore import QTimer
                from PySide6.QtWidgets import QApplication

                QTimer.singleShot(0, QApplication.instance(), fn)
            else:
                from gi.repository import GLib

                GLib.idle_add(fn)
        except Exception:
            pass  # ponytail: marshal failed to schedule (e.g. `gi` missing —
            # never bundled, so the GTK branch is only ever reachable on a
            # source run with system GTK installed) — skip the op, never run
            # it off-thread or let it raise into the js_api caller (see class
            # docstring: an off-thread Qt call is what used to abort the
            # process; an uncaught raise here is what used to print the
            # `gi` traceback to stderr and silently kill the window op).

    def button_layout(self) -> dict:
        """Called once by NativeTitleBar.tsx on mount — see _button_layout()."""
        return _button_layout()

    def minimize(self) -> None:
        self._run_on_gui_thread(lambda: self._window.minimize())

    def close(self) -> None:
        self._run_on_gui_thread(lambda: self._window.destroy())

    def toggle_maximize(self) -> None:
        def _do() -> None:
            native = self._native()
            if self._is_qt():
                native.showNormal() if native.isMaximized() else native.showMaximized()
            else:
                native.unmaximize() if native.is_maximized() else native.maximize()

        self._run_on_gui_thread(_do)

    def begin_move(self) -> None:
        def _do() -> None:
            native = self._native()
            if self._is_qt():
                handle = native.windowHandle()
                if handle is not None:
                    handle.startSystemMove()
            else:
                from gi.repository import Gdk

                seat = Gdk.Display.get_default().get_default_seat()
                _, x, y = seat.get_pointer().get_position()
                native.get_window().begin_move_drag(1, x, y, Gdk.CURRENT_TIME)

        self._run_on_gui_thread(_do)

    def begin_resize(self, edge: str) -> None:
        """`edge` is one of n/s/e/w/ne/nw/se/sw — from the title bar's
        invisible edge/corner hit-zones (NativeTitleBar.tsx)."""

        def _do() -> None:
            native = self._native()
            if self._is_qt():
                from PySide6.QtCore import Qt as QtNS

                edges = {
                    "n": QtNS.Edge.TopEdge,
                    "s": QtNS.Edge.BottomEdge,
                    "e": QtNS.Edge.RightEdge,
                    "w": QtNS.Edge.LeftEdge,
                    "ne": QtNS.Edge.TopEdge | QtNS.Edge.RightEdge,
                    "nw": QtNS.Edge.TopEdge | QtNS.Edge.LeftEdge,
                    "se": QtNS.Edge.BottomEdge | QtNS.Edge.RightEdge,
                    "sw": QtNS.Edge.BottomEdge | QtNS.Edge.LeftEdge,
                }
                handle = native.windowHandle()
                if handle is not None and edge in edges:
                    handle.startSystemResize(edges[edge])
            else:
                from gi.repository import Gdk

                edges = {
                    "n": Gdk.WindowEdge.NORTH,
                    "s": Gdk.WindowEdge.SOUTH,
                    "e": Gdk.WindowEdge.EAST,
                    "w": Gdk.WindowEdge.WEST,
                    "ne": Gdk.WindowEdge.NORTH_EAST,
                    "nw": Gdk.WindowEdge.NORTH_WEST,
                    "se": Gdk.WindowEdge.SOUTH_EAST,
                    "sw": Gdk.WindowEdge.SOUTH_WEST,
                }
                if edge not in edges:
                    return
                seat = Gdk.Display.get_default().get_default_seat()
                _, x, y = seat.get_pointer().get_position()
                native.get_window().begin_resize_drag(edges[edge], 1, x, y, Gdk.CURRENT_TIME)

        self._run_on_gui_thread(_do)


def _button_layout() -> dict:
    """Read GNOME's `button-layout` gsetting (org.gnome.desktop.wm.
    preferences) so NativeTitleBar.tsx places its own minimize/maximize/
    close buttons on the side/order the user's actual desktop theme uses
    (Surya 2026-09-14: "harus bisa menyesuaikan posisinya dengan tema yang
    ada" — his MacTahoe-Dark theme puts them on the LEFT).

    Format is "LEFT:RIGHT", each side a comma list, e.g.
    'close,minimize,maximize:appmenu' -> left side, order close/minimize/
    maximize; ':minimize,maximize,close' -> right side, same order as our
    hardcoded default. Unknown tokens (appmenu, spacer, icon, ...) are
    dropped; we only care about the three real buttons.

    Falls back to the universal default (right side, minimize/maximize/
    close — same as NativeTitleBar's old hardcoded layout, so nothing
    regresses) when gsettings is missing (non-GNOME desktop, container) or
    the key is unset/malformed. Never raises — a cosmetic read must not
    block window creation.
    """
    default = {"side": "right", "order": ["minimize", "maximize", "close"]}
    try:
        result = subprocess.run(
            ["gsettings", "get", "org.gnome.desktop.wm.preferences", "button-layout"],
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return default
    if result.returncode != 0:
        return default
    value = result.stdout.strip().strip("'\"")
    if ":" not in value:
        return default
    known = {"close", "minimize", "maximize"}
    left_raw, right_raw = value.split(":", 1)
    left = [t for t in left_raw.split(",") if t in known]
    right = [t for t in right_raw.split(",") if t in known]
    if left and not right:
        return {"side": "left", "order": left}
    if right and not left:
        return {"side": "right", "order": right}
    if left and right:
        # Rare (both sides configured) — take whichever side has more
        # buttons; a tie keeps the right (matches the universal default).
        return {"side": "left", "order": left} if len(left) > len(right) else {"side": "right", "order": right}
    return default


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
    # ponytail: forces Qt's own compositor (QRhi) to a CPU rasterizer instead
    # of probing EGL/GLX/Vulkan/GBM. Confirmed empirically (2026-09-13, this
    # bug) on Fedora 44 Wayland with no EGL surface available: without this,
    # Qt fails RHI init and QtWebEngine never renders (or the process aborts
    # outright); with it, the window opens and WebEngine renders normally
    # (falls back to Chromium's --disable-gpu-compositing on its own).
    # setdefault so a machine that *does* have working GL can override back.
    os.environ.setdefault("QT_QUICK_BACKEND", "software")
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
    bridge = _WindowBridge()
    window = webview.create_window(
        "NetGeo",
        url,
        js_api=bridge,
        # Frameless + our own title bar (Surya 2026-09-14: "tetap frameless+
        # title bar sendiri biar bisa full rounded") — see _WindowBridge for
        # why drag/resize/maximize are reimplemented instead of left to
        # pywebview's own (Wayland-broken) defaults.
        frameless=True,
        easy_drag=False,  # would else make the WHOLE window draggable, breaking map/canvas clicks
        transparent=True,  # required for the CSS border-radius rounded corners to actually show
        background_color="#0F0F0E",  # theme/tokens.ts --ng-bg-0 — avoids a white flash before CSS paints
        # Surya QA, 2026-09-14: default pywebview size (800x600) left the
        # topology UI "berantakan" — rail overlapping the filter chips/
        # search field, top-toolbar buttons pushed off-screen. Measured
        # live with Playwright against this same built frontend.dist (not
        # guessed): layout is intact at 1040x700, and breaks (search field
        # clipped to "…ce or IP…", OSPF chip clipped off, rail overlapping
        # the filter row) at both 1000x720 (width-driven) and 1040x650
        # (height-driven — the left rail's fixed content height no longer
        # fits). min_size below adds a small margin over that measured
        # floor; initial size is a comfortable common laptop resolution
        # well above it, not a fix for the clipping itself (still real at
        # the floor — see class docstring / packaging/README.md for that
        # open item).
        width=1440,
        height=900,
        min_size=(1100, 720),
    )
    bridge.bind(window)
    try:
        # Corners must square off when maximized (GNOME/Windows/macOS
        # convention — CSS side, theme/globals.css `.ng-native-frame--
        # maximized`). These two events fire for ANY state change — our own
        # toggle button, a WM keybinding, drag-to-edge snap — not just
        # clicks inside NativeTitleBar, so they're the authoritative signal
        # (verified in webview/platforms/qt.py changeEvent + gtk.py
        # window-state-event: both call events.maximized/restored.set()
        # from the native window's own state-change callback, which is
        # already running on the GUI thread — no _run_on_gui_thread
        # marshaling needed for the evaluate_js() call itself).
        window.events.maximized += lambda: window.evaluate_js("window.dispatchEvent(new Event('netgeo:maximize'))")
        window.events.restored += lambda: window.evaluate_js("window.dispatchEvent(new Event('netgeo:restore'))")
    except Exception:
        pass  # ponytail: best-effort corner-rounding sync only, never fatal
    try:
        webview.start(icon=str(icon_path) if icon_path.is_file() else None)
    except Exception as exc:  # webview.errors.WebViewException when GTK/Qt missing
        print(_webview_unavailable(exc), file=sys.stderr)
        return False
    return True


def _relaunch_argv() -> list[str]:
    """Command to re-invoke this same launcher as a child process (frozen
    PyInstaller build: the bundled exe itself; source run: the interpreter
    plus this file)."""
    if FROZEN:
        return [sys.executable]
    return [sys.executable, str(Path(__file__).resolve())]


def _run_webview_in_subprocess(url: str) -> bool:
    """Try the native window in a child process, not this one.

    Qt can die with SIGABRT when it can't build a usable GL/EGL/GLX/Vulkan
    surface (confirmed on this bug: happens on plain Wayland with no EGL,
    and again — worse — when QT_QPA_PLATFORM=xcb is forced). A SIGABRT
    kills the process before any Python try/except runs, so it cannot be
    caught in-process. Running the window in a child process means only the
    child dies; this (parent) process — and the uvicorn thread it's about
    to start — is untouched and can fall back to the browser normally.
    """
    try:
        result = subprocess.run(_relaunch_argv() + ["--window-child", url])
    except OSError as exc:
        print(_webview_unavailable(exc), file=sys.stderr)
        return False
    if result.returncode == 0:
        return True
    if result.returncode < 0:
        sig = signal.Signals(-result.returncode).name
        reason = f"jendela asli dihentikan paksa oleh sinyal {sig} (kemungkinan driver grafis tidak sanggup membuat context GL/EGL/GLX/Vulkan)"
    else:
        reason = f"jendela asli keluar dengan kode {result.returncode}"
    print(_webview_unavailable(RuntimeError(reason)), file=sys.stderr)
    return False


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
    if "--window-child" in args:
        # ponytail: internal-only re-exec target for _run_webview_in_
        # subprocess — undocumented on purpose, never typed by a user.
        # Isolated here (before mounting/serving anything) so this process
        # does nothing but try the window and report success via exit code.
        url = args[args.index("--window-child") + 1]
        sys.exit(0 if _try_webview(url) else 1)

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
        opened_native = _run_webview_in_subprocess(url)  # isolates a Qt SIGABRT away from this process

    if not opened_native:
        webbrowser.open(url)
        # webview.start() blocks until the window closes; the browser
        # fallback has no such signal, so just keep the process (and its
        # background uvicorn thread) alive the same way uvicorn.run() used to.
        server_thread.join()


if __name__ == "__main__":
    main()
