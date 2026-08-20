"""Determinism lint (NG-NFR-01): engine code must draw randomness only from
the simulation's seeded RNG, never from module-level `random`.

Allowed: the two seed plumbing sites that *create* `random.Random(seed)`.
"""
from __future__ import annotations

import re
from pathlib import Path

ENGINE = Path(__file__).resolve().parents[1] / "engine"

# Files allowed to `import random` — they own the seeded-RNG plumbing.
_ALLOWED = {"simulation.py", "netstack/network.py"}


def test_engine_has_no_unseeded_random():
    offenders = []
    for py in ENGINE.rglob("*.py"):
        rel = py.relative_to(ENGINE).as_posix()
        text = py.read_text()
        if re.search(r"^\s*(import random|from random import)", text, re.MULTILINE) and rel not in _ALLOWED:
            offenders.append(rel)
        # Even allowed files must only construct random.Random(seed) —
        # module-level draws (random.random(), random.choice...) are banned.
        for m in re.finditer(r"\brandom\.(\w+)", text):
            if m.group(1) != "Random":
                offenders.append(f"{rel}: random.{m.group(1)}")
    assert not offenders, f"unseeded randomness in engine/: {offenders}"
