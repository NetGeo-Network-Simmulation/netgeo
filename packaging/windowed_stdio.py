"""Supply streams before imports in PyInstaller's Windows GUI executable.

Windowed builds set stdout/stderr to None. Uvicorn and other dependencies
expect writable streams with isatty(); discard console output when no console
exists, including in the re-executed native window child.
"""
import os
import sys

for name in ("stdout", "stderr"):
    if getattr(sys, name) is None:
        # Streams must remain open for the lifetime of the process.
        target = os.environ.get("NETGEO_DIAGNOSTIC_LOG", os.devnull)
        setattr(sys, name, open(target, "a", encoding="utf-8", buffering=1))  # noqa: SIM115
