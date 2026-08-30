"""Test packaging/launcher.py's WebKitGTK fallback (C1-b slice).

The one thing that matters here: when pywebview or its native backend
(WebKitGTK/Qt) isn't available, the launcher must not crash — it falls back
to the system browser and says why. Uses a stubbed/absent ``webview`` module
so this never needs real WebKitGTK installed in CI.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

LAUNCHER_PATH = Path(__file__).resolve().parents[2] / "packaging" / "launcher.py"
_spec = importlib.util.spec_from_file_location("netgeo_launcher", LAUNCHER_PATH)
launcher = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(launcher)


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


def test_try_webview_succeeds_when_backend_available(monkeypatch):
    """Sanity check the happy path too: start() returning normally -> True."""
    fake_webview = type(sys)("webview")
    calls = {}
    fake_webview.create_window = lambda title, url: calls.update(title=title, url=url)
    fake_webview.start = lambda **k: None
    monkeypatch.setitem(sys.modules, "webview", fake_webview)

    ok = launcher._try_webview("http://127.0.0.1:1")
    assert ok is True
    assert calls == {"title": "NetGeo", "url": "http://127.0.0.1:1"}
