"""BGP best-path tie-break — RFC 4271 §9.1.2.2 (a)-(j).

`_better`/`best_paths` is pure decision-process logic, independent of the
FSM (already covered by test_bgp_fsm.py) and of route reflection/community
propagation (test_bgp_v2.py). Most tests here poke a BgpProcess's `_Peer`
state directly (established + a controlled `rib_in` entry) rather than
driving a real handshake — the fastest, most deterministic way to pin one
attribute at a time.
"""
from __future__ import annotations

from ipaddress import IPv4Address, IPv4Network

from engine.netstack import Network
from engine.netstack.protocols.bgp import BgpAttrs, BgpProcess
from engine.netstack.routing import Router

PFX = "203.0.113.0/24"


def _hub(asn: int = 65000, router_id: str = "9.9.9.9", **kw) -> BgpProcess:
    return BgpProcess(Router("hub"), asn=asn, router_id=router_id, **kw)


def _peer(proc: BgpProcess, ip: str, remote_asn: int, router_id: str = "0.0.0.0"):
    proc.add_neighbor(ip, remote_asn)
    p = proc.peers[IPv4Address(ip)]
    p.state = "established"
    p.router_id = router_id
    return p


def _winner(proc: BgpProcess) -> IPv4Address:
    _attrs, peer_ip = proc.best_paths()[IPv4Network(PFX)]
    return peer_ip


def test_bgp_bestpath_prefers_lower_origin_igp_over_incomplete():
    proc = _hub()
    a = _peer(proc, "10.0.0.9", 65010)   # higher IP, wins on origin
    b = _peer(proc, "10.0.0.1", 65020)   # lower IP -- would win the old shortcut
    a.rib_in[IPv4Network(PFX)] = BgpAttrs(as_path=(65010,), next_hop="10.0.0.9", origin="igp")
    b.rib_in[IPv4Network(PFX)] = BgpAttrs(as_path=(65020,), next_hop="10.0.0.1", origin="incomplete")
    assert _winner(proc) == a.ip


def test_bgp_bestpath_prefers_ebgp_over_ibgp_when_tied():
    proc = _hub(asn=65000)
    # Tie everything but eBGP/iBGP status; give the iBGP peer the *lower*
    # address so a peer-IP-first shortcut would wrongly pick it instead.
    ibgp = _peer(proc, "10.0.0.1", 65000)   # iBGP, lower IP
    ebgp = _peer(proc, "10.0.0.9", 65001)   # eBGP, higher IP
    ibgp.rib_in[IPv4Network(PFX)] = BgpAttrs(as_path=(65099,), next_hop=str(ibgp.ip))
    ebgp.rib_in[IPv4Network(PFX)] = BgpAttrs(as_path=(65099,), next_hop=str(ebgp.ip))
    assert _winner(proc) == ebgp.ip


def test_bgp_bestpath_med_only_compared_same_neighbor_as():
    proc = _hub()
    from_as100 = _peer(proc, "10.0.0.1", 100, router_id="1.1.1.1")
    from_as200 = _peer(proc, "10.0.0.9", 200, router_id="9.9.9.9")
    # Different neighboring AS, different MED: as100's higher MED must NOT
    # be penalised against as200's lower one -- MED is skipped, and the
    # tie-break falls through to router-id, where as100 (lower rid) wins.
    from_as100.rib_in[IPv4Network(PFX)] = BgpAttrs(as_path=(100,), next_hop=str(from_as100.ip), med=50)
    from_as200.rib_in[IPv4Network(PFX)] = BgpAttrs(as_path=(200,), next_hop=str(from_as200.ip), med=1)
    assert _winner(proc) == from_as100.ip

    # With always_compare_med on, MED is compared across ASes too -- the
    # lower-MED as200 route now wins instead.
    proc.always_compare_med = True
    assert _winner(proc) == from_as200.ip


def test_bgp_bestpath_peer_ip_is_last_resort_not_first():
    proc = _hub()
    lo = _peer(proc, "10.0.0.1", 65010, router_id="5.5.5.5")
    hi = _peer(proc, "10.0.0.9", 65020, router_id="5.5.5.5")
    # Every criterion through (h) ties (same as_path length, origin, med,
    # both eBGP, same forced router-id) -- only peer address can decide,
    # and the lower one must win.
    lo.rib_in[IPv4Network(PFX)] = BgpAttrs(as_path=(65010,), next_hop=str(lo.ip))
    hi.rib_in[IPv4Network(PFX)] = BgpAttrs(as_path=(65020,), next_hop=str(hi.ip))
    assert _winner(proc) == lo.ip

    # Same tie, opposite insertion order -- result must not depend on it.
    proc2 = _hub()
    hi2 = _peer(proc2, "10.0.0.9", 65020, router_id="5.5.5.5")
    lo2 = _peer(proc2, "10.0.0.1", 65010, router_id="5.5.5.5")
    hi2.rib_in[IPv4Network(PFX)] = BgpAttrs(as_path=(65020,), next_hop=str(hi2.ip))
    lo2.rib_in[IPv4Network(PFX)] = BgpAttrs(as_path=(65010,), next_hop=str(lo2.ip))
    assert _winner(proc2) == lo2.ip


def _link(net: Network, a: Router, an: str, aip: str, b: Router, bn: str, bip: str):
    net.connect(f"{a.name}-{b.name}", net.add_iface(a, an, [aip]),
                net.add_iface(b, bn, [bip]))


def _dual_upstream_topology() -> Network:
    """hub (AS 65000) dual-homed to two eBGP upstreams offering the same
    prefix, tied on local-pref/AS-path-length/origin/MED -- only router-id
    (h) decides. Exercises the tie-break through a real convergence, not a
    hand-poked rib_in, so replay determinism covers the whole path."""
    net = Network(seed=41)
    hub = net.add_device(Router("hub"))
    up1 = net.add_device(Router("up1"))
    up2 = net.add_device(Router("up2"))
    _link(net, hub, "eth0", "10.0.1.2/30", up1, "eth0", "10.0.1.1/30")
    _link(net, hub, "eth1", "10.0.2.2/30", up2, "eth0", "10.0.2.1/30")
    phub = BgpProcess(hub, asn=65000, router_id="9.9.9.9", keepalive_interval=1.0)
    phub.add_neighbor("10.0.1.1", 65001)
    phub.add_neighbor("10.0.2.1", 65002)
    p1 = BgpProcess(up1, asn=65001, router_id="1.1.1.1", keepalive_interval=1.0)
    p1.add_neighbor("10.0.1.2", 65000)
    p1.advertise_network(PFX)
    p2 = BgpProcess(up2, asn=65002, router_id="2.2.2.2", keepalive_interval=1.0)
    p2.add_neighbor("10.0.2.2", 65000)
    p2.advertise_network(PFX)
    net.start()
    net.run(until=20.0)
    return net


def test_bgp_bestpath_replay_determinism():
    net1 = _dual_upstream_topology()
    net2 = _dual_upstream_topology()
    assert net1.ledger.seq == net2.ledger.seq
    assert net1.ledger.hash() == net2.ledger.hash()
    # up1 (router-id 1.1.1.1) beats up2 (2.2.2.2) once local-pref/as-path/
    # origin/MED all tie -- proves (h) actually decided this, not chance.
    hub_route = next(
        r for r in net1.devices["hub"].routes if str(r.prefix) == PFX
    )
    assert str(hub_route.next_hop) == "10.0.1.1"
