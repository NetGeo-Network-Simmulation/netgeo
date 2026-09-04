"""Deterministic /29 subnet allocation for point-to-point emulation links.

One link = one podman network = one /29 out of a private range. The subnet is
derived from a hash of the link id so the same link always gets the same
addresses on every call. This exists because netavark's own IPAM reassigns
addresses across container restarts (proven in the N-2/N-3 spike) — the
adaptor must hand podman an explicit subnet + IP per endpoint, never let it
pick.
"""
from __future__ import annotations

import hashlib

_BASE = (10 << 24) | (201 << 16)  # 10.201.0.0
_NUM_SUBNETS = 1 << 13  # 13 bits of subnet index leave 3 bits for the /29 host part


def link_subnet(link_id: str) -> tuple[str, str, str, str]:
    """Return ``(cidr, gateway, ip_a, ip_b)`` for ``link_id``'s dedicated /29.

    A /30 only has 2 usable host addresses (offset 0 = network, offset 3 =
    broadcast) — not enough for a bridge gateway plus two container IPs, so
    this needs a /29 (6 usable, offsets 1-6) instead.

    ponytail: hash-mod allocation, not a collision-proof registry — a clash
    across 2**13 slots is not worth guarding against at lab scale. Replace
    with a real allocation table if link counts ever get there.
    """
    idx = int.from_bytes(hashlib.sha256(link_id.encode()).digest()[:2], "big") % _NUM_SUBNETS
    base = _BASE + idx * 8

    def _ip(offset: int) -> str:
        n = base + offset
        return f"{(n >> 24) & 0xFF}.{(n >> 16) & 0xFF}.{(n >> 8) & 0xFF}.{n & 0xFF}"

    return f"{_ip(0)}/29", _ip(1), _ip(2), _ip(3)


if __name__ == "__main__":
    a = link_subnet("link-1")
    b = link_subnet("link-2")
    assert a != b
    assert link_subnet("link-1") == a  # deterministic
    print("ok", a, b)
