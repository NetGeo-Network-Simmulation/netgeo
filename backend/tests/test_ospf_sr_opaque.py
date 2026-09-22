"""OSPF opaque LSA infrastructure (RFC 5250) + SR's RI/Extended-Prefix use of
it (RFC 8665) — A5.1.

Three routers in a line (r1-r2-r3, single area 0), each with SR attached
directly to OspfProcess (no LDP anywhere in this file — that coexistence is
covered separately in test_sr.py/test_sr_ospf_native.py). Verifies the opaque
LSA machinery itself: origination, flooding to every router's LSDB, exact TLV
contents, and seq stability once converged.
"""
from __future__ import annotations

from engine.netstack import Network
from engine.netstack.protocols.ospf import OpaqueLsa, OspfProcess
from engine.netstack.protocols.sr import SrProcess
from engine.netstack.routing import Router

SRGB = 16000
SRGB_RANGE = 8000


def _lab() -> Network:
    net = Network(seed=21)
    r1 = net.add_device(Router("r1"))
    r2 = net.add_device(Router("r2"))
    r3 = net.add_device(Router("r3"))

    net.connect("l12", net.add_iface(r1, "eth0", ["10.0.12.1/30"]),
                net.add_iface(r2, "eth0", ["10.0.12.2/30"]))
    net.connect("l23", net.add_iface(r2, "eth1", ["10.0.23.1/30"]),
                net.add_iface(r3, "eth0", ["10.0.23.2/30"]))
    net.add_iface(r1, "lo0", ["10.255.0.1/32"])
    net.add_iface(r2, "lo0", ["10.255.0.2/32"])
    net.add_iface(r3, "lo0", ["10.255.0.3/32"])

    ospf1 = OspfProcess(r1, router_id="10.255.0.1", hello_interval=1.0, ifaces=["eth0", "lo0"])
    ospf2 = OspfProcess(r2, router_id="10.255.0.2", hello_interval=1.0,
                         ifaces=["eth0", "eth1", "lo0"])
    ospf3 = OspfProcess(r3, router_id="10.255.0.3", hello_interval=1.0, ifaces=["eth0", "lo0"])
    SrProcess(r1, ospf1, node_sid=101, srgb_base=SRGB, srgb_range=SRGB_RANGE)
    SrProcess(r2, ospf2, node_sid=102, srgb_base=SRGB, srgb_range=SRGB_RANGE)
    SrProcess(r3, ospf3, node_sid=103, srgb_base=SRGB, srgb_range=SRGB_RANGE)

    net.start()
    net.run(until=30.0)
    return net


def _ospf(net: Network, name: str) -> OspfProcess:
    return next(p for p in net.devices[name].processes if p.proto == "ospf")


def _opaque(net: Network, name: str) -> list[OpaqueLsa]:
    proc = _ospf(net, name)
    return [
        lsa
        for area in proc.my_areas()
        for lsa in proc.lsdb.get(area, {}).values()
        if isinstance(lsa, OpaqueLsa)
    ]


def test_ri_lsa_originated_once_per_router():
    net = _lab()
    for name in ("r1", "r2", "r3"):
        ri = [l for l in _opaque(net, name) if l.opaque_type == 4]
        rids = {l.router_id for l in ri}
        assert rids == {"10.255.0.1", "10.255.0.2", "10.255.0.3"}, (name, rids)
        for l in ri:
            assert l.tlvs == {"sr_algorithm": [0], "srgb_base": SRGB, "srgb_range": SRGB_RANGE}


def test_extended_prefix_lsa_per_router_loopback():
    net = _lab()
    expect = {
        "10.255.0.1": ("10.255.0.1/32", 101),
        "10.255.0.2": ("10.255.0.2/32", 102),
        "10.255.0.3": ("10.255.0.3/32", 103),
    }
    for name in ("r1", "r2", "r3"):
        ext = [l for l in _opaque(net, name) if l.opaque_type == 7]
        by_rid = {l.router_id: l for l in ext}
        assert set(by_rid) == set(expect), (name, set(by_rid))
        for rid, (prefix, sid) in expect.items():
            tlvs = by_rid[rid].tlvs
            assert tlvs["prefix"] == prefix
            assert tlvs["sid_index"] == sid
            assert tlvs["flags"] == {"N": True, "NP": False}


def test_opaque_lsas_flood_to_every_lsdb():
    """Every router's LSDB holds every OTHER router's opaque LSAs too — not
    just its own — proving OSPF's generic flooding (not a per-router-local
    stash) carries opaque type 10 the same as every other LSA type."""
    net = _lab()
    for name in ("r1", "r2", "r3"):
        keys = {l.key for l in _opaque(net, name)}
        # 3 routers * (1 RI + 1 Extended-Prefix) = 6 distinct opaque LSAs.
        assert len(keys) == 6, (name, keys)


def test_opaque_lsa_type_is_area_scope_type_10():
    """Sanity: the opaque LSAs live in the area LSDB keyed the same way as
    every other type-10-area-scope entry (RFC 5250 sec 4) -- confirms they
    aren't accidentally being treated as link- or AS-scope."""
    net = _lab()
    ospf1 = _ospf(net, "r1")
    for area in ospf1.my_areas():
        for lsa in ospf1.lsdb.get(area, {}).values():
            if isinstance(lsa, OpaqueLsa):
                assert lsa.opaque_type in (4, 7)
                assert lsa.key.startswith(f"opq|{lsa.opaque_type}|")


def test_opaque_lsa_seq_stable_once_converged():
    """No config change after start -- seq must stop incrementing once every
    router has converged (SrProcess's own no-op-on-unchanged guard)."""
    net = _lab()
    before = {l.key: l.seq for l in _opaque(net, "r2")}
    net.run_for(10.0)
    after = {l.key: l.seq for l in _opaque(net, "r2")}
    assert before == after


def test_show_ip_ospf_database_opaque_area():
    from engine.netstack.cli import CliSession

    net = _lab()
    sess = CliSession(net, net.devices["r1"])
    out = sess.execute("show ip ospf database opaque-area")
    assert "srgb_base" in out
    for rid in ("10.255.0.1", "10.255.0.2", "10.255.0.3"):
        assert rid in out


def test_replay_determinism():
    a, b = _lab(), _lab()
    assert a.ledger.seq == b.ledger.seq
    assert a.ledger.hash() == b.ledger.hash()
