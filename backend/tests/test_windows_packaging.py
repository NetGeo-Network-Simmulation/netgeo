"""Platform-policy checks without importing the server or native GUI libraries."""
import ast
import io
import os
import runpy
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

PACKAGING = Path(__file__).resolve().parents[2] / "packaging"


class WindowsPackagingTests(unittest.TestCase):
    def test_windows_auto_does_not_probe_linux_or_force_software(self):
        tree = ast.parse((PACKAGING / "launcher.py").read_text(encoding="utf-8"))
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                        and n.name == "_decide_gl_mode")

        def unexpected_probe():
            self.fail("Windows must not probe Linux DRM/GBM")

        scope = {"os": os, "sys": SimpleNamespace(platform="win32"),
                 "_hardware_gl_available": unexpected_probe}
        exec(compile(ast.Module(body=[function], type_ignores=[]), "launcher", "exec"), scope)  # noqa: S102
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(scope["_decide_gl_mode"]()[0], "hw")
            os.environ["NETGEO_GL"] = "sw"
            self.assertEqual(scope["_decide_gl_mode"]()[0], "sw")

    def test_windowed_hook_supplies_writable_streams(self):
        with patch.object(sys, "stdout", None), patch.object(sys, "stderr", None):
            runpy.run_path(str(PACKAGING / "windowed_stdio.py"))
            try:
                for stream in (sys.stdout, sys.stderr):
                    self.assertIsInstance(stream.isatty(), bool)
                    stream.write("startup diagnostic\n")
                    stream.flush()
            finally:
                sys.stdout.close()
                sys.stderr.close()

    def test_hook_preserves_existing_console_streams(self):
        stream = io.StringIO()
        with patch.object(sys, "stdout", stream), patch.object(sys, "stderr", stream):
            runpy.run_path(str(PACKAGING / "windowed_stdio.py"))
            self.assertIs(sys.stdout, stream)
            self.assertIs(sys.stderr, stream)


if __name__ == "__main__":
    unittest.main()
