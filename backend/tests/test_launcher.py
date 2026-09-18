"""Test packaging/launcher.py's WebKitGTK fallback (C1-b slice).

The one thing that matters here: when pywebview or its native backend
(WebKitGTK/Qt) isn't available, the launcher must not crash — it falls back
to the system browser and says why. Uses a stubbed/absent ``webview`` module
so this never needs real WebKitGTK installed in CI.
"""
from __future__ import annotations

import importlib.util
import socket
import sys
import time
from pathlib import Path

LAUNCHER_PATH = Path(__file__).resolve().parents[2] / "packaging" / "launcher.py"
_spec = importlib.util.spec_from_file_location("netgeo_launcher", LAUNCHER_PATH)
launcher = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(launcher)


# ---- _wait_until_ready() ---------------------------------------------------
# 2026-09-18: replaces a blind `time.sleep(1.0)` before opening the native
# window (docs/qa/native-lag-2026-09-18.md — /api/health measured ready at a
# median ~0.24s, so the fixed sleep wasted ~0.76s of first-paint every launch).


def test_wait_until_ready_true_once_port_accepts_connections():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    alive_thread = type("T", (), {"is_alive": lambda self: True})()
    try:
        assert launcher._wait_until_ready(port, alive_thread, timeout=2.0, interval=0.02) is True
    finally:
        srv.close()


def test_wait_until_ready_false_on_timeout_when_nothing_listens():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()  # port freed, nothing accepts on it
    alive_thread = type("T", (), {"is_alive": lambda self: True})()
    assert launcher._wait_until_ready(port, alive_thread, timeout=0.15, interval=0.05) is False


def test_wait_until_ready_false_fast_when_server_thread_dead():
    dead_thread = type("T", (), {"is_alive": lambda self: False})()
    started = time.monotonic()
    assert launcher._wait_until_ready(1, dead_thread, timeout=5.0) is False
    assert time.monotonic() - started < 1.0  # must not wait out the full cap


def test_webview_unavailable_message_names_distro_packages():
    msg = launcher._webview_unavailable(ImportError("no module named webview"))
    assert "webkit2gtk4.1" in msg  # Fedora
    assert "gir1.2-webkit2-4.1" in msg  # Ubuntu/Debian — the one most forgotten
    assert "libwebkit2gtk-4.1-0" in msg


def test_try_webview_falls_back_when_module_missing(monkeypatch, capsys):
    """`import webview` raising ImportError must not propagate — fall back."""
    monkeypatch.setitem(sys.modules, "webview", None)  # forces ImportError on import
    ok = launcher._try_webview("http://127.0.0.1:1")
    assert ok is False
    assert "webkit2gtk4.1" in capsys.readouterr().err


def test_try_webview_falls_back_when_backend_start_fails(monkeypatch, capsys):
    """webview imports fine but start() fails (no WebKitGTK/Qt) — still no crash."""
    fake_webview = type(sys)("webview")
    fake_webview.create_window = lambda *a, **k: None

    def _start(*a, **k):
        raise RuntimeError("You must have either QT or GTK with Python extensions installed")

    fake_webview.start = _start
    monkeypatch.setitem(sys.modules, "webview", fake_webview)

    ok = launcher._try_webview("http://127.0.0.1:1")
    assert ok is False
    assert "webkit2gtk4.1" in capsys.readouterr().err


def test_main_version_flag_prints_and_exits_without_serving(monkeypatch, capsys):
    """`netgeo --version` must not bind a port or start uvicorn."""
    monkeypatch.setattr(sys, "argv", ["netgeo", "--version"])
    monkeypatch.setattr(launcher, "_mount_frontend", lambda: (_ for _ in ()).throw(
        AssertionError("--version must exit before mounting/serving anything")
    ))
    launcher.main()
    out = capsys.readouterr().out
    assert "NetGeo" in out
    assert "serving on" not in out


def test_main_help_flag_prints_and_exits_without_serving(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["netgeo", "--help"])
    monkeypatch.setattr(launcher, "_mount_frontend", lambda: (_ for _ in ()).throw(
        AssertionError("--help must exit before mounting/serving anything")
    ))
    launcher.main()
    out = capsys.readouterr().out
    assert "Usage: netgeo" in out
    assert "serving on" not in out


def test_no_window_reason_flag_and_env(monkeypatch):
    """--no-window and NETGEO_NO_WINDOW=1 both trigger the headless-by-request
    path (distribution variant #4); neither present -> None (try the window)."""
    monkeypatch.delenv("NETGEO_NO_WINDOW", raising=False)
    assert launcher._no_window_reason(["--no-window"]) == "--no-window"
    assert launcher._no_window_reason([]) is None

    monkeypatch.setenv("NETGEO_NO_WINDOW", "1")
    assert launcher._no_window_reason([]) == "NETGEO_NO_WINDOW=1"


def test_help_flag_documents_no_window(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["netgeo", "--help"])
    launcher.main()
    out = capsys.readouterr().out
    assert "--no-window" in out
    assert "NETGEO_NO_WINDOW" in out


def test_run_webview_in_subprocess_true_on_clean_exit(monkeypatch):
    """Child process exits 0 (window opened and closed normally) -> True."""
    monkeypatch.setattr(launcher, "_relaunch_argv", lambda: [sys.executable, "-c", "pass"])
    assert launcher._run_webview_in_subprocess("http://127.0.0.1:1") is True


def test_run_webview_in_subprocess_false_and_no_raise_on_sigabrt(monkeypatch, capsys):
    """A child that dies from a signal (the real bug: Qt SIGABRT when no
    GL/EGL/GLX/Vulkan surface is available) must not crash the parent — it
    reports False and names the signal, same as any other native failure."""
    abort_script = "import os, signal; os.kill(os.getpid(), signal.SIGABRT)"
    monkeypatch.setattr(launcher, "_relaunch_argv", lambda: [sys.executable, "-c", abort_script])
    ok = launcher._run_webview_in_subprocess("http://127.0.0.1:1")
    assert ok is False
    assert "SIGABRT" in capsys.readouterr().err


def test_window_child_flag_runs_try_webview_and_exits(monkeypatch):
    """`--window-child <url>` must call _try_webview(url) and exit via its
    result, without mounting the frontend or starting uvicorn."""
    monkeypatch.setattr(sys, "argv", ["netgeo", "--window-child", "http://x"])
    monkeypatch.setattr(launcher, "_try_webview", lambda url: url == "http://x")
    monkeypatch.setattr(launcher, "_mount_frontend", lambda: (_ for _ in ()).throw(
        AssertionError("--window-child must not mount/serve anything")
    ))
    try:
        launcher.main()
    except SystemExit as exc:
        assert exc.code == 0
    else:
        raise AssertionError("--window-child must call sys.exit()")


class _FakeEvent:
    """Stands in for pywebview's `webview.window.Event` — only the `+=`
    subscribe contract matters here (`window.events.maximized += handler`).
    Recording every subscribed handler (not just the count) lets a test
    fire them and assert on the resulting effect, exactly like the real
    window.events.maximized.set() would."""

    def __init__(self) -> None:
        self.handlers: list = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self


class _FakeWindow:
    def __init__(self) -> None:
        self.events = type("Events", (), {"maximized": _FakeEvent(), "restored": _FakeEvent()})()
        self.evaluate_js_calls: list[str] = []

    def evaluate_js(self, script: str) -> None:
        self.evaluate_js_calls.append(script)


def test_try_webview_succeeds_when_backend_available(monkeypatch):
    """Sanity check the happy path too: start() returning normally -> True."""
    fake_webview = type(sys)("webview")
    calls = {}
    fake_window = _FakeWindow()

    def _create_window(title, url, **kwargs):
        calls.update(title=title, url=url, **kwargs)
        return fake_window

    fake_webview.create_window = _create_window
    fake_webview.start = lambda **k: None
    monkeypatch.setitem(sys.modules, "webview", fake_webview)

    ok = launcher._try_webview("http://127.0.0.1:1")
    assert ok is True
    assert calls["title"] == "NetGeo"
    assert calls["url"] == "http://127.0.0.1:1"
    # frameless + transparent are the 2026-09-14 decision (custom rounded
    # title bar) — keep this sharp, not just "didn't throw".
    assert calls["frameless"] is True
    assert calls["transparent"] is True
    # BUG 4 (Surya QA, 2026-09-14): pywebview's 800x600 default left the
    # topology UI broken (rail over the filter row, search field and
    # toolbar buttons clipped). Measured live with Playwright against the
    # built frontend.dist: intact at 1040x700, broken at 1000x720 and at
    # 1040x650 — min_size must sit above that measured floor on both axes.
    assert calls["width"] >= 1100
    assert calls["height"] >= 720
    assert calls["min_size"][0] >= 1100
    assert calls["min_size"][1] >= 720


def test_try_webview_subscribes_maximize_restore_for_corner_rounding(monkeypatch):
    """The window must wire up events.maximized/restored so the frontend's
    `.ng-native-frame--maximized` (square corners while maximized, matching
    GNOME/Windows/macOS convention) can react to ANY state change, not just
    clicks inside NativeTitleBar — a WM keybinding or drag-to-edge snap
    included. Firing the subscribed handler must dispatch the matching JS
    event via evaluate_js()."""
    fake_webview = type(sys)("webview")
    fake_window = _FakeWindow()
    fake_webview.create_window = lambda title, url, **kwargs: fake_window
    fake_webview.start = lambda **k: None
    monkeypatch.setitem(sys.modules, "webview", fake_webview)

    ok = launcher._try_webview("http://127.0.0.1:1")
    assert ok is True
    assert len(fake_window.events.maximized.handlers) == 1
    assert len(fake_window.events.restored.handlers) == 1

    fake_window.events.maximized.handlers[0]()
    fake_window.events.restored.handlers[0]()
    assert "netgeo:maximize" in fake_window.evaluate_js_calls[0]
    assert "netgeo:restore" in fake_window.evaluate_js_calls[1]


# ---- _button_layout() -------------------------------------------------------
# Surya 2026-09-14: NativeTitleBar's window buttons must sit on the side/
# order the user's actual desktop uses (his MacTahoe-Dark theme puts them on
# the LEFT), read from GNOME's button-layout gsetting.


class _FakeCompletedProcess:
    def __init__(self, returncode: int, stdout: str) -> None:
        self.returncode = returncode
        self.stdout = stdout


def test_button_layout_left_side_from_real_example(monkeypatch):
    """Surya's own `gsettings get` output, verbatim from the task."""
    monkeypatch.setattr(
        launcher.subprocess,
        "run",
        lambda *a, **k: _FakeCompletedProcess(0, "'close,minimize,maximize:appmenu'\n"),
    )
    assert launcher._button_layout() == {"side": "left", "order": ["close", "minimize", "maximize"]}


def test_button_layout_right_side(monkeypatch):
    monkeypatch.setattr(
        launcher.subprocess, "run", lambda *a, **k: _FakeCompletedProcess(0, "':minimize,maximize,close'\n")
    )
    assert launcher._button_layout() == {"side": "right", "order": ["minimize", "maximize", "close"]}


def test_button_layout_falls_back_when_gsettings_missing(monkeypatch):
    """Non-GNOME desktop / container: gsettings isn't even installed."""

    def _raise(*a, **k):
        raise FileNotFoundError("gsettings not found")

    monkeypatch.setattr(launcher.subprocess, "run", _raise)
    assert launcher._button_layout() == {"side": "right", "order": ["minimize", "maximize", "close"]}


def test_button_layout_falls_back_on_nonzero_exit_and_malformed_value(monkeypatch):
    monkeypatch.setattr(launcher.subprocess, "run", lambda *a, **k: _FakeCompletedProcess(1, ""))
    assert launcher._button_layout() == {"side": "right", "order": ["minimize", "maximize", "close"]}

    monkeypatch.setattr(launcher.subprocess, "run", lambda *a, **k: _FakeCompletedProcess(0, "'garbage'\n"))
    assert launcher._button_layout() == {"side": "right", "order": ["minimize", "maximize", "close"]}


# ---- _WindowBridge thread marshaling ----------------------------------------
# Surya QA, 2026-09-14 on 1.2.125.1: pressing maximize killed the native
# window entirely (fell back to the browser). Root cause: QMetaObject.
# invokeMethod(receiver, python_callable, ...) matches no overload in the
# bundled PySide6 (verified via `strings` on QtCore.abi3.so — only the
# classic method-NAME-string overload is compiled in), so the marshal always
# failed and a since-removed `except: fn()` fallback ran the Qt call
# directly on the js_api thread — undefined behavior in Qt, which aborted
# the process. These tests can't exercise real PySide6 (not installed on
# this dev machine) — they lock the *marshal call shape* and the *no direct-
# call-on-failure* contract via a stubbed PySide6.


def _make_qt_bridge():
    bridge = launcher._WindowBridge()
    # Realistic shape: pywebview's own `BrowserView(QMainWindow)` is defined
    # *inside* webview/platforms/qt.py, so its __module__ is always
    # "webview.platforms.qt" — never "PySide6.*" — no matter which backend
    # is active (a prior fake here used "PySide6.QtWidgets", which is not
    # what pywebview 6.x actually produces and masked the BUG 1 regression
    # below). `.gui` is the platform module pywebview's own
    # Window._initialize() records (webview/window.py) — what _is_qt() reads.
    fake_native = type("FakeNative", (), {"__module__": "webview.platforms.qt"})()
    fake_gui = type(sys)("webview.platforms.qt")
    fake_window = type("FakeWindow", (), {"native": fake_native, "gui": fake_gui})()
    bridge.bind(fake_window)
    return bridge


def test_is_qt_reads_window_gui_not_native_class_module():
    """BUG 1 regression (Surya QA, 2026-09-14 on the installed 1.2.125.1
    package): dragging raised `ModuleNotFoundError: No module named 'gi'`
    from inside begin_move. Root cause was `_is_qt()` checking
    `type(native).__module__`, which for pywebview's real BrowserView is
    "webview.platforms.qt" (pywebview's own package) — never "PySide6" — so
    the check was always False and every op fell through to the GTK branch.
    This fake reproduces that exact real shape; it fails against the old
    `type(native).__module__.split(".")[0] in ("PySide6", ...)` check and
    passes against the fixed `.gui`-based one."""
    bridge = _make_qt_bridge()
    assert bridge._is_qt() is True

    gtk_bridge = launcher._WindowBridge()
    fake_gui = type(sys)("webview.platforms.gtk")
    gtk_bridge.bind(type("FakeWindow", (), {"gui": fake_gui})())
    assert gtk_bridge._is_qt() is False


def test_run_on_gui_thread_uses_qtimer_singleshot_not_invokemethod(monkeypatch):
    singleshot_calls = []
    fake_qtcore = type(sys)("PySide6.QtCore")
    fake_qtcore.QTimer = type("QTimer", (), {"singleShot": staticmethod(lambda *a: singleshot_calls.append(a))})
    fake_app = object()
    fake_qtwidgets = type(sys)("PySide6.QtWidgets")
    fake_qtwidgets.QApplication = type("QApplication", (), {"instance": staticmethod(lambda: fake_app)})
    monkeypatch.setitem(sys.modules, "PySide6.QtCore", fake_qtcore)
    monkeypatch.setitem(sys.modules, "PySide6.QtWidgets", fake_qtwidgets)

    bridge = _make_qt_bridge()
    ran = []
    bridge._run_on_gui_thread(lambda: ran.append(True))

    assert len(singleshot_calls) == 1
    msec, context, fn = singleshot_calls[0]
    assert msec == 0
    assert context is fake_app
    assert ran == []  # scheduled, not executed synchronously on this thread
    fn()
    assert ran == [True]  # the scheduled callable is the real op


def test_run_on_gui_thread_skips_op_when_marshal_fails(monkeypatch):
    """If scheduling onto the GUI thread itself fails (e.g. PySide6 not
    importable), the window operation must be skipped — never run directly
    on the calling thread. That direct-call fallback is exactly what
    aborted the process on a real maximize click."""
    monkeypatch.setitem(sys.modules, "PySide6.QtCore", None)  # forces ImportError
    bridge = _make_qt_bridge()
    ran = []
    bridge._run_on_gui_thread(lambda: ran.append(True))
    assert ran == []


def test_minimize_and_close_are_marshaled_not_called_directly():
    """minimize()/close() must go through _run_on_gui_thread, same as
    toggle_maximize/begin_move/begin_resize — pywebview's own Window.
    minimize()/destroy() do zero thread marshaling internally (verified in
    webview/platforms/qt.py: the module-level minimize(uid)/destroy_window(
    uid) call the Qt widget method directly), so calling them off the GUI
    thread was exactly as unsafe as the unmarshaled toggle_maximize used to
    be — just not the button Surya happened to click first."""
    bridge = launcher._WindowBridge()
    marshaled = []
    bridge._run_on_gui_thread = lambda fn: marshaled.append(fn)

    def _boom():
        raise AssertionError("must not call the window method directly")

    fake_window = type("FakeWindow", (), {"minimize": _boom, "destroy": _boom})()
    bridge.bind(fake_window)

    bridge.minimize()
    bridge.close()
    assert len(marshaled) == 2
