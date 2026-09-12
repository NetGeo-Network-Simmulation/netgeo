"""BGP-POLICY: simple config knobs that make local-pref, MED and origin
reachable from real config -- ``add_neighbor(local_pref_in=..., med_out=...)``
and ``advertise_network(..., origin=...)`` -- instead of only existing as
dead fields on ``BgpAttrs``. Every test drives real message flow (real
sessions, real UPDATEs); nothing pokes ``BgpAttrs``/``rib_in`` directly.

Not oracle: pure sim, no containers.
"""
from __future__ import annotations

from ipaddress import IPv4Network

import pytest

from engine.netstack import Network
from engine.netstack.protocols.bgp import BgpProcess
from engine.netstack.routing import Router


def _link(net: Network, a: Router, an: str, aip: str, b: Router, bn: str, bip: str):
    net.connect(f"{a.name}-{b.name}", net.add_iface(a, an, [aip]),
                net.add_iface(b, bn, [bip]))


def _bgp(net: Network, name: str) -> BgpProcess:
    return next(p for p in net.devices[name].processes if p.proto == "bgp")


def _route(net: Network, router: str, prefix: str):
    return next(
        (r for r in net.devices[router].routes if str(r.prefix) == prefix), None
    )


PREFIX = "203.0.113.0/24"


def test_local_pref_wins_and_never_leaves_the_as():
    """r prefers n1 (local_pref_in=200) over n2 (default 100), and the
    UPDATE r sends onward to a third eBGP peer m carries local-pref reset
    to the well-known default -- it never crosses out to eBGP as 200."""
    net = Network(seed=101)
    r = net.add_device(Router("r"))
    n1 = net.add_device(Router("n1"))
    n2 = net.add_device(Router("n2"))
    m = net.add_device(Router("m"))
    _link(net, r, "e0", "10.0.0.1/30", n1, "e0", "10.0.0.2/30")
    _link(net, r, "e1", "10.0.1.1/30", n2, "e0", "10.0.1.2/30")
    _link(net, r, "e2", "10.0.2.1/30", m, "e0", "10.0.2.2/30")

    pr = BgpProcess(r, asn=100, router_id="1.1.1.1", keepalive_interval=5.0)
    pr.add_neighbor("10.0.0.2", 200, local_pref_in=200)
    pr.add_neighbor("10.0.1.2", 300)
    pr.add_neighbor("10.0.2.2", 400)
    p1 = BgpProcess(n1, asn=200, router_id="2.2.2.2", keepalive_interval=5.0)
    p1.add_neighbor("10.0.0.1", 100)
    p1.advertise_network(PREFIX)
    p2 = BgpProcess(n2, asn=300, router_id="3.3.3.3", keepalive_interval=5.0)
    p2.add_neighbor("10.0.1.1", 100)
    p2.advertise_network(PREFIX)
    pm = BgpProcess(m, asn=400, router_id="4.4.4.4", keepalive_interval=5.0)
    pm.add_neighbor("10.0.2.1", 100)
    net.start()
    net.run(until=40.0)

    route = _route(net, "r", PREFIX)
    assert route is not None and str(route.next_hop) == "10.0.0.2"  # via n1

    # n2's route lost purely on local-pref (everything else about it ties).
    best_attrs, best_peer = pr.best_paths()[IPv4Network(PREFIX)]
    assert str(best_peer) == "10.0.0.2" and best_attrs.local_pref == 200

    # And it never leaves this AS: m sees the RFC default, not 200.
    rib_m = _bgp(net, "m").peers[next(iter(_bgp(net, "m").peers))].rib_in
    assert rib_m[IPv4Network(PREFIX)].local_pref == 100


def test_med_lower_wins_from_same_neighbor_as_and_does_not_cross_as():
    """n1 and n2 are both AS 200, each offering r a different MED via
    ``med_out``. r must prefer the lower one, and must not carry AS 200's
    MED across the boundary when re-advertising to a different AS (m)."""
    net = Network(seed=103)
    r = net.add_device(Router("r"))
    n1 = net.add_device(Router("n1"))
    n2 = net.add_device(Router("n2"))
    m = net.add_device(Router("m"))
    _link(net, r, "e0", "10.1.0.1/30", n1, "e0", "10.1.0.2/30")
    _link(net, r, "e1", "10.1.1.1/30", n2, "e0", "10.1.1.2/30")
    _link(net, r, "e2", "10.1.2.1/30", m, "e0", "10.1.2.2/30")

    pr = BgpProcess(r, asn=100, router_id="1.1.1.1", keepalive_interval=5.0)
    pr.add_neighbor("10.1.0.2", 200)
    pr.add_neighbor("10.1.1.2", 200)
    pr.add_neighbor("10.1.2.2", 500)
    p1 = BgpProcess(n1, asn=200, router_id="2.2.2.2", keepalive_interval=5.0)
    p1.add_neighbor("10.1.0.1", 100, med_out=10)
    p1.advertise_network(PREFIX)
    p2 = BgpProcess(n2, asn=200, router_id="3.3.3.3", keepalive_interval=5.0)
    p2.add_neighbor("10.1.1.1", 100, med_out=20)
    p2.advertise_network(PREFIX)
    pm = BgpProcess(m, asn=500, router_id="5.5.5.5", keepalive_interval=5.0)
    pm.add_neighbor("10.1.2.1", 100)
    net.start()
    net.run(until=40.0)

    route = _route(net, "r", PREFIX)
    assert route is not None and str(route.next_hop) == "10.1.0.2"  # via n1, med=10

    best_attrs, best_peer = pr.best_paths()[IPv4Network(PREFIX)]
    assert str(best_peer) == "10.1.0.2" and best_attrs.med == 10

    # r has no med_out configured toward m -> AS 200's MED does not leak.
    rib_m = _bgp(net, "m").peers[next(iter(_bgp(net, "m").peers))].rib_in
    assert rib_m[IPv4Network(PREFIX)].med == 0


def test_origin_breaks_the_tie_when_everything_else_matches():
    """n1 (default origin igp) beats n2 (origin incomplete): equal
    local-pref, equal 1-hop AS-path length, so origin (c) decides."""
    net = Network(seed=107)
    r = net.add_device(Router("r"))
    n1 = net.add_device(Router("n1"))
    n2 = net.add_device(Router("n2"))
    _link(net, r, "e0", "10.2.0.1/30", n1, "e0", "10.2.0.2/30")
    _link(net, r, "e1", "10.2.1.1/30", n2, "e0", "10.2.1.2/30")

    pr = BgpProcess(r, asn=100, router_id="1.1.1.1", keepalive_interval=5.0)
    pr.add_neighbor("10.2.0.2", 200)
    pr.add_neighbor("10.2.1.2", 300)
    p1 = BgpProcess(n1, asn=200, router_id="2.2.2.2", keepalive_interval=5.0)
    p1.add_neighbor("10.2.0.1", 100)
    p1.advertise_network(PREFIX)  # origin defaults to "igp"
    p2 = BgpProcess(n2, asn=300, router_id="3.3.3.3", keepalive_interval=5.0)
    p2.add_neighbor("10.2.1.1", 100)
    p2.advertise_network(PREFIX, origin="incomplete")
    net.start()
    net.run(until=30.0)

    route = _route(net, "r", PREFIX)
    assert route is not None and str(route.next_hop) == "10.2.0.2"  # via n1 (igp)

    best_attrs, best_peer = pr.best_paths()[IPv4Network(PREFIX)]
    assert str(best_peer) == "10.2.0.2" and best_attrs.origin == "igp"


def test_advertise_network_rejects_unknown_origin():
    net = Network(seed=109)
    r = net.add_device(Router("r"))
    p = BgpProcess(r, asn=100, router_id="1.1.1.1")
    with pytest.raises(ValueError):
        p.advertise_network(PREFIX, origin="bogus")
