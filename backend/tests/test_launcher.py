"""Test packaging/launcher.py's WebKitGTK fallback (C1-b slice).

The one thing that matters here: when pywebview or its native backend
(WebKitGTK/Qt) isn't available, the launcher must not crash — it falls back
to the system browser and says why. Uses a stubbed/absent ``webview`` module
so this never needs real WebKitGTK installed in CI.
"""
from __future__ import annotations

import importlib.util
import os
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
    # topology UI broken. Re-measured 2026-09-18 a SECOND time (supersedes
    # 680x640 from earlier the same day): a leader review of that 680x640
    # screenshot found the floating nav rail overlapping the topology chips
    # row/search field/bottom dock/minimap — the 680x640 floor only measured
    # the rail's own content height against the window, never against the
    # OTHER floating chrome sharing its space. NavigationRail.tsx now
    # confines itself to a fixed band instead (RAIL_TOP_CLEAR/
    # RAIL_BOTTOM_CLEAR, theme/shell.ts) and degrades its own content rather
    # than overlap anything, so the rail no longer drives either floor.
    # Re-measured what's left the same way (Playwright, every pairwise
    # combination of rail/chips/search/dock/minimap/inspector asserted
    # non-intersecting, across Topology AND Physical Plant): width floor is
    # 949px (topology top-left panel / bottom dock / minimap all needing to
    # coexist with the 360px inspector without touching), height floor is
    # unchanged at 604px (already clear with room to spare). Plus
    # NATIVE_TITLE_BAR_HEIGHT (36px) on top of the height floor, since
    # min_size is the *whole* frameless window and the browser-based
    # Playwright measurement never renders a native title bar at all.
    assert calls["min_size"][0] >= 980
    assert calls["min_size"][1] >= 640
    # initial size stays a comfortable margin above the floor, not just
    # equal to it
    assert calls["width"] >= calls["min_size"][0]
    assert calls["height"] >= calls["min_size"][1]


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


# ---- _system_subprocess_env() -----------------------------------------------
# BUG (2026-09-18, Surya QA on the installed rpm): minimize/maximize buttons
# missing from the native title bar. Root cause confirmed by hand — pointing
# LD_LIBRARY_PATH at the frozen bundle's _internal/ dir (what PyInstaller's
# bootloader does for the whole process) makes the *system* `gsettings`
# binary load the bundle's own libglib-2.0/libgio-2.0 instead of the host's,
# so it can't reach the real dconf backend and silently returns a bogus
# default ('appmenu:close') instead of erroring — _button_layout()'s parser
# then sees only "close" as a known token. These tests operate on real
# os.environ dict semantics (no stand-in object pretending to be something
# it isn't), and the second one proves the fix is actually wired into the
# gsettings call site, not just present as an unused helper.


def test_system_subprocess_env_is_noop_outside_frozen_bundle(monkeypatch):
    monkeypatch.setattr(launcher, "FROZEN", False)
    assert launcher._system_subprocess_env() is None


def test_system_subprocess_env_restores_ld_library_path_from_orig(monkeypatch):
    monkeypatch.setattr(launcher, "FROZEN", True)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/opt/netgeo/_internal")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/usr/lib64:/lib64")
    env = launcher._system_subprocess_env()
    assert env["LD_LIBRARY_PATH"] == "/usr/lib64:/lib64"


def test_system_subprocess_env_drops_ld_library_path_when_orig_was_empty(monkeypatch):
    monkeypatch.setattr(launcher, "FROZEN", True)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/opt/netgeo/_internal")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "")
    env = launcher._system_subprocess_env()
    assert "LD_LIBRARY_PATH" not in env


def test_button_layout_passes_sanitized_env_to_gsettings(monkeypatch):
    """The fix must be wired into the actual subprocess.run() call, not just
    exist as an unused helper — this is the part a careless fix forgets."""
    monkeypatch.setattr(launcher, "FROZEN", True)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/opt/netgeo/_internal")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/usr/lib64")
    seen = {}

    def _capture(*a, **k):
        seen.update(k)
        return _FakeCompletedProcess(0, "'close,minimize,maximize:appmenu'\n")

    monkeypatch.setattr(launcher.subprocess, "run", _capture)
    launcher._button_layout()
    assert seen["env"]["LD_LIBRARY_PATH"] == "/usr/lib64"


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


# ---- _decide_gl_mode() / _hardware_gl_available() ---------------------------
# Leader review (2026-09-20, same day as cbb2ac4): the SwiftShader
# QTWEBENGINE_CHROMIUM_FLAGS were being forced UNCONDITIONALLY, so a host with
# working hardware GL was silently downgraded to CPU rendering for the rack 3D
# view and the MapLibre map. _decide_gl_mode() gates that behind a real EGL
# probe (or an explicit NETGEO_GL override).


def test_decide_gl_mode_hw_when_probe_succeeds(monkeypatch):
    monkeypatch.delenv("NETGEO_GL", raising=False)
    monkeypatch.setattr(launcher, "_hardware_gl_available", lambda: True)
    mode, reason = launcher._decide_gl_mode()
    assert mode == "hw"
    assert "probe" in reason


def test_decide_gl_mode_sw_when_probe_fails(monkeypatch):
    monkeypatch.delenv("NETGEO_GL", raising=False)
    monkeypatch.setattr(launcher, "_hardware_gl_available", lambda: False)
    mode, reason = launcher._decide_gl_mode()
    assert mode == "sw"
    assert "probe" in reason


def test_decide_gl_mode_env_override_sw_skips_probe(monkeypatch):
    """NETGEO_GL=sw must win even when the probe would say hardware GL is
    fine — and must not even call the probe (an explicit override is a
    promise, not a hint)."""
    monkeypatch.setenv("NETGEO_GL", "sw")
    monkeypatch.setattr(launcher, "_hardware_gl_available", lambda: (_ for _ in ()).throw(
        AssertionError("probe must not run when NETGEO_GL overrides")
    ))
    mode, reason = launcher._decide_gl_mode()
    assert mode == "sw"
    assert reason == "NETGEO_GL=sw"


def test_decide_gl_mode_env_override_hw_skips_probe(monkeypatch):
    monkeypatch.setenv("NETGEO_GL", "hw")
    monkeypatch.setattr(launcher, "_hardware_gl_available", lambda: (_ for _ in ()).throw(
        AssertionError("probe must not run when NETGEO_GL overrides")
    ))
    mode, reason = launcher._decide_gl_mode()
    assert mode == "hw"
    assert reason == "NETGEO_GL=hw"


def test_decide_gl_mode_ignores_unknown_env_value_and_probes(monkeypatch):
    """A typo'd NETGEO_GL value falls through to auto-probe rather than
    silently picking a side — same "unknown token dropped" spirit as
    _button_layout()."""
    monkeypatch.setenv("NETGEO_GL", "bogus")
    monkeypatch.setattr(launcher, "_hardware_gl_available", lambda: True)
    mode, reason = launcher._decide_gl_mode()
    assert mode == "hw"
    assert "probe" in reason


class _FakeGbmLib:
    """Stands in for `ctypes.CDLL(libgbm)`. Only gbm_create_device is used
    by _hardware_gl_available(). Real ctypes function pointers accept
    arbitrary attribute assignment (`.restype`/`.argtypes`), which a bound
    *method* does NOT support (`AttributeError: 'method' object has no
    __dict__` — caught while writing this test, the exact "fake-looking
    stub hides a real bug" trap). Plain function objects assigned directly
    into the instance `__dict__` DO support it, same as the real thing."""

    def __init__(self, device_handle):
        def gbm_create_device(_fd):
            return device_handle

        self.gbm_create_device = gbm_create_device


class _FakeEglLib:
    """Stands in for `ctypes.CDLL(libEGL)`. eglGetProcAddress's return
    value here is a plain sentinel int, never dereferenced as a real
    function pointer in tests — the real code always wraps it through
    `ctypes.CFUNCTYPE(...)(addr)`, which `_stub_gbm_probe` fakes out
    separately (calling a real CFUNCTYPE on a fake address would
    segfault, the same trap _FakeGbmLib's docstring names)."""

    def __init__(self, proc_addr=1, init_result=1):
        def eglGetProcAddress(_name):
            return proc_addr

        def eglInitialize(_display, _major, _minor):
            return init_result

        self.eglGetProcAddress = eglGetProcAddress
        self.eglInitialize = eglInitialize


def _stub_gbm_probe(
    monkeypatch,
    *,
    render_node="/dev/null",
    gbm_device=0x5,
    proc_addr=1,
    display_handle=0x1234,
    init_result=1,
):
    """Wires every seam `_hardware_gl_available()` touches (the two CDLL
    loads, the EGL extension-function bind) to fakes, so an individual
    test only has to override the one value it's exercising.

    `render_node` defaults to /dev/null rather than a fake path: os.open/
    os.close are left as the REAL functions (not monkeypatched) — os.open
    is used internally by unrelated stdlib code that also runs during
    these tests (ctypes.util.find_library shells out via tempfile, which
    calls os.open itself), so globally replacing it broke tempfile with a
    wrong-arity TypeError the first time this was written. /dev/null is
    always present and O_RDWR-openable by any user, so the real os.open
    call in _hardware_gl_available() just works."""
    monkeypatch.setattr(launcher, "_dri_render_node", lambda: render_node)

    def fake_cdll(name):
        return _FakeGbmLib(gbm_device) if "gbm" in name else _FakeEglLib(proc_addr, init_result)

    monkeypatch.setattr(launcher.ctypes, "CDLL", fake_cdll)

    def fake_cfunctype(*_types, **_kw):
        def _bind(_address):
            return lambda _platform, _device, _attribs: display_handle

        return _bind

    monkeypatch.setattr(launcher.ctypes, "CFUNCTYPE", fake_cfunctype)


def test_hardware_gl_available_true_when_gbm_initializes(monkeypatch):
    """The full happy path: render node found, gbm device created, EGL
    platform display bound and initialized."""
    _stub_gbm_probe(monkeypatch)
    assert launcher._hardware_gl_available() is True


def test_hardware_gl_available_false_when_no_render_node(monkeypatch):
    """No /dev/dri render node at all (headless box, container, or this
    user isn't in the `render` group) — must short-circuit before ever
    touching ctypes."""
    monkeypatch.setattr(launcher, "_dri_render_node", lambda: None)

    def _boom(*_a, **_k):
        raise AssertionError("must not dlopen without a render node")

    monkeypatch.setattr(launcher.ctypes, "CDLL", _boom)
    assert launcher._hardware_gl_available() is False


def test_hardware_gl_available_false_when_gbm_create_device_fails(monkeypatch):
    """gbm_create_device() returns NULL — the exact failure confirmed by
    hand against the real bundled _internal/libgbm.so.1 from the installed
    netgeo-1.2.125.1 rpm (see _hardware_gl_available()'s docstring):
    "MESA-LOADER: failed to open iris: ... wrong ELF class" /
    "did not find extension DRI_Mesa version 1", gbm_create_device fails."""
    _stub_gbm_probe(monkeypatch, gbm_device=0)
    assert launcher._hardware_gl_available() is False


def test_hardware_gl_available_false_when_no_platform_display_ext(monkeypatch):
    """eglGetProcAddress("eglGetPlatformDisplayEXT") itself returns NULL —
    no attempt to bind/call it should follow."""
    _stub_gbm_probe(monkeypatch, proc_addr=0)
    assert launcher._hardware_gl_available() is False


def test_hardware_gl_available_false_when_platform_display_is_null(monkeypatch):
    _stub_gbm_probe(monkeypatch, display_handle=0)
    assert launcher._hardware_gl_available() is False


def test_hardware_gl_available_false_when_eglinitialize_fails(monkeypatch):
    _stub_gbm_probe(monkeypatch, init_result=0)
    assert launcher._hardware_gl_available() is False


def test_hardware_gl_available_false_when_libgbm_missing(monkeypatch):
    """No libgbm/libEGL on the system at all (e.g. a bare container image)
    — ctypes.CDLL raising OSError must not propagate."""
    monkeypatch.setattr(launcher, "_dri_render_node", lambda: "/dev/dri/renderD128")

    def _raise(name):
        raise OSError(f"cannot open shared object file: {name}")

    monkeypatch.setattr(launcher.ctypes, "CDLL", _raise)
    assert launcher._hardware_gl_available() is False


def test_hardware_gl_available_closes_fd_even_on_failure(monkeypatch):
    """fd leak guard: os.close() must run through the `finally` even when
    gbm_create_device fails partway through. Wraps the REAL os.close (not
    a replacement — see _stub_gbm_probe's docstring on why globally
    replacing os.open/close breaks unrelated stdlib code) so the fd is
    still actually released."""
    closed = []
    real_close = launcher.os.close
    monkeypatch.setattr(launcher, "_dri_render_node", lambda: "/dev/null")
    monkeypatch.setattr(launcher.os, "close", lambda fd: (closed.append(fd), real_close(fd)))
    monkeypatch.setattr(launcher.ctypes, "CDLL", lambda name: _FakeGbmLib(0) if "gbm" in name else _FakeEglLib())
    assert launcher._hardware_gl_available() is False
    # ctypes.util.find_library (unmocked, runs for real above) also opens/
    # closes its own fds internally — assert ours is in there, not that
    # it's the only one.
    assert closed


def test_try_webview_sets_swiftshader_flags_when_gl_mode_sw(monkeypatch):
    monkeypatch.delenv("QTWEBENGINE_CHROMIUM_FLAGS", raising=False)
    monkeypatch.setattr(launcher, "_decide_gl_mode", lambda: ("sw", "test forced sw"))
    fake_webview = type(sys)("webview")
    fake_window = _FakeWindow()
    fake_webview.create_window = lambda title, url, **kwargs: fake_window
    fake_webview.start = lambda **k: None
    monkeypatch.setitem(sys.modules, "webview", fake_webview)

    launcher._try_webview("http://127.0.0.1:1")
    assert "--enable-unsafe-swiftshader" in os.environ["QTWEBENGINE_CHROMIUM_FLAGS"]


def test_try_webview_leaves_flags_unset_when_gl_mode_hw(monkeypatch):
    """The regression this whole slice fixes: a host with working hardware
    GL must NOT get the SwiftShader flags forced on it."""
    monkeypatch.delenv("QTWEBENGINE_CHROMIUM_FLAGS", raising=False)
    monkeypatch.setattr(launcher, "_decide_gl_mode", lambda: ("hw", "test forced hw"))
    fake_webview = type(sys)("webview")
    fake_window = _FakeWindow()
    fake_webview.create_window = lambda title, url, **kwargs: fake_window
    fake_webview.start = lambda **k: None
    monkeypatch.setitem(sys.modules, "webview", fake_webview)

    launcher._try_webview("http://127.0.0.1:1")
    assert "QTWEBENGINE_CHROMIUM_FLAGS" not in os.environ


def test_help_flag_documents_netgeo_gl(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["netgeo", "--help"])
    launcher.main()
    out = capsys.readouterr().out
    assert "NETGEO_GL" in out


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
