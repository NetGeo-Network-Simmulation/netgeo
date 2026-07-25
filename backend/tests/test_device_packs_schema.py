"""Device-pack schema v2 gate (NG-DL-01/02) — runs the repo validator in CI."""
from __future__ import annotations

import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "validate_packs.py"


def test_packs_valid_against_schema_v2():
    spec = importlib.util.spec_from_file_location("validate_packs", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    findings = mod.validate()
    assert not findings, "\n".join(findings)
