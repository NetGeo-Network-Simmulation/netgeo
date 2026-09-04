"""Registry of NOS kinds emulation is allowed to spawn.

A kind not listed here does not exist as far as NetGeo is concerned — that is
the license guard, not a runtime blocklist (see test_kinds.py). EULA-gated
images (vJunos, SR Linux, cEOS-lab, cRPD, RouterOS CHR, VyOS rolling) get
added later with ``bundlable=False``: NetGeo validates they're already present
locally but never pulls them itself.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True, slots=True)
class KindSpec:
    image: str
    license_class: Literal["free", "eula"]
    bundlable: bool
    mem_limit_mb: int
    caps: list[str] = field(default_factory=list)


KINDS: dict[str, KindSpec] = {
    "frr": KindSpec(
        image="quay.io/frrouting/frr:10.7.0",
        license_class="free",
        bundlable=True,
        mem_limit_mb=96,
        caps=["NET_ADMIN", "NET_RAW", "SYS_ADMIN"],
    ),
}
