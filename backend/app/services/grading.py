"""Auto-grading engine (NG-EDU-02).

Grades a student's live topology against an activity's checks. Each check
becomes a :class:`GradeItem` with a pass/fail verdict and a *human-readable*
reason (the AC hinges on that reason string). The score is the weighted
fraction of passing checks.

Pure and deterministic: the engine :class:`Network` is built once via
:mod:`app.services.netlab` (seed 0) and settled so switching/OSPF converge
before inspection; ping checks push real traffic through that same lab. Nothing
here touches the store or the shared :class:`LabManager` cache — grading a
project never perturbs its live lab.
"""
from __future__ import annotations

from ipaddress import ip_address

from app.models import GradeCheck, GradeItem, GradeReport, Topology
from app.services import netlab

# OSPF neighbour state that counts as "up" (a formed adjacency). The engine
# emits "init" | "2-way" | "full" (see protocols/ospf.py) — only "full"
# counts, since a DROther/DROther pair legitimately stops at 2-way.
_OSPF_UP = "full"


def grade(topo: Topology, checks: list[GradeCheck]) -> GradeReport:
    """Run every check against ``topo``'s live model state and total the score.

    ``score_pct = 100 * earned_weight / total_weight``; an empty/zero-weight
    check set scores 100.0 by definition.
    """
    net = netlab.build_network(topo, seed=0)
    net.start()
    # Settle so protocols/switching converge (same budget the live lab uses).
    net.run(until=net.now + netlab._settle_time(net))

    items: list[GradeItem] = []
    for chk in checks:
        passed, reason = _run_check(net, chk)
        items.append(
            GradeItem(
                label=chk.label or _auto_label(chk),
                passed=passed,
                weight=chk.weight,
                reason=reason,
            )
        )

    total = sum(c.weight for c in checks)
    earned = sum(i.weight for i in items if i.passed)
    score = 100.0 if total <= 0 else round(100.0 * earned / total, 6)
    return GradeReport(
        items=items,
        score_pct=score,
        earned_weight=round(earned, 6),
        total_weight=round(total, 6),
    )


def _auto_label(chk: GradeCheck) -> str:
    """A stable label when the author did not supply one."""
    k = str(chk.kind)
    if k == "node_exists":
        return f"{chk.node} exists"
    if k == "iface_ip":
        return f"{chk.node} {chk.iface} = {chk.cidr}"
    if k == "vlan_present":
        return f"VLAN {chk.vlan} on {chk.node}"
    if k == "ospf_neighbor":
        return f"OSPF neighbor on {chk.node}" + (f" ↔ {chk.peer}" if chk.peer else "")
    if k == "ping":
        return f"ping {chk.node}→{chk.dst}"
    return k


# ---------------------------------------------------------------------------
# Check dispatch — each returns (passed, human-readable reason).
# ---------------------------------------------------------------------------

def _run_check(net, chk: GradeCheck) -> tuple[bool, str]:
    kind = str(chk.kind)
    handler = _HANDLERS.get(kind)
    if handler is None:  # defensive — enum keeps this unreachable
        return False, f"unknown check kind '{kind}'"
    return handler(net, chk)


def _check_node_exists(net, chk: GradeCheck) -> tuple[bool, str]:
    if net.find_device(chk.node) is not None:
        return True, f"device '{chk.node}' exists"
    return False, f"device '{chk.node}' not found"


def _check_iface_ip(net, chk: GradeCheck) -> tuple[bool, str]:
    dev = net.find_device(chk.node)
    if dev is None:
        return False, f"device '{chk.node}' not found"
    iface = dev.interfaces.get(chk.iface)
    if iface is None:
        return False, f"interface '{chk.iface}' not found on {chk.node}"
    have = [str(i) for i in iface.ips]
    if chk.cidr in have:
        return True, f"{chk.node} {chk.iface} has {chk.cidr}"
    got = ", ".join(have) if have else "no IPv4 address"
    return False, f"{chk.node} {chk.iface} expected {chk.cidr}, has {got}"


def _check_vlan_present(net, chk: GradeCheck) -> tuple[bool, str]:
    dev = net.find_device(chk.node)
    if dev is None:
        return False, f"device '{chk.node}' not found"
    vid = chk.vlan
    for iface in dev.interfaces.values():
        if iface.vlan_mode == "access" and iface.access_vlan == vid:
            return True, f"VLAN {vid} present on {chk.node} (access {iface.name})"
        if iface.vlan_mode == "trunk" and iface.trunk_vlans and vid in iface.trunk_vlans:
            return True, f"VLAN {vid} present on {chk.node} (trunk {iface.name})"
    return False, f"VLAN {vid} not present on {chk.node}"


def _ospf_proc(dev):
    for proc in getattr(dev, "processes", []):
        if getattr(proc, "proto", "") == "ospf":
            return proc
    return None


def _check_ospf_neighbor(net, chk: GradeCheck) -> tuple[bool, str]:
    dev = net.find_device(chk.node)
    if dev is None:
        return False, f"device '{chk.node}' not found"
    proc = _ospf_proc(dev)
    if proc is None:
        return False, f"OSPF not running on {chk.node}"
    up = [r for r in proc.neighbor_rows() if str(r.get("state")) == _OSPF_UP]
    if not up:
        return False, f"no OSPF neighbor is up on {chk.node}"
    if not chk.peer:
        return True, f"OSPF neighbor up on {chk.node} ({len(up)} adjacency)"
    peer = net.find_device(chk.peer)
    if peer is None:
        return False, f"peer device '{chk.peer}' not found"
    peer_proc = _ospf_proc(peer)
    peer_rid = peer_proc.router_id if peer_proc else None
    peer_ips = {str(i.ip) for i in peer.all_ips()}
    for r in up:
        if (peer_rid and r.get("router_id") == peer_rid) or r.get("ip") in peer_ips:
            return True, f"OSPF neighbor up on {chk.node} with {chk.peer}"
    return False, f"OSPF neighbor with {chk.peer} not up on {chk.node}"


def _resolve_ip(net, ref: str) -> str | None:
    """A ping destination is a literal IP or a node name (first IPv4 wins)."""
    try:
        ip_address(ref)
        return ref
    except ValueError:
        pass
    dev = net.find_device(ref)
    if dev is None:
        return None
    ips = dev.all_ips()
    return str(ips[0].ip) if ips else None


def _check_ping(net, chk: GradeCheck) -> tuple[bool, str]:
    if net.find_device(chk.node) is None:
        return False, f"source device '{chk.node}' not found"
    dst = _resolve_ip(net, chk.dst)
    if dst is None:
        return False, f"cannot resolve ping destination '{chk.dst}' to an IP"
    report = net.ping(chk.node, dst, count=3)  # auto-runs the sim
    if report.received > 0:
        return True, f"ping {chk.node}→{chk.dst} succeeded ({report.received}/{report.sent})"
    return False, f"ping {chk.node}→{chk.dst} failed (0/{report.sent} replies)"


_HANDLERS = {
    "node_exists": _check_node_exists,
    "iface_ip": _check_iface_ip,
    "vlan_present": _check_vlan_present,
    "ospf_neighbor": _check_ospf_neighbor,
    "ping": _check_ping,
}
