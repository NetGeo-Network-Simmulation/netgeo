"""BGP v2 tests — NG-SIM-06: route reflection, communities, prefix filtering.

Ends with a runnable reduction of the `isp-nasional-bgp-multias-rr` scenario:
AS 65001 (RR + 2 PE clients, iBGP full to RR only) with an eBGP upstream
(AS 64512), an IX route-server (AS 65530) and a corporate customer (AS 65101).
"""
from __future__ import annotations

from ipaddress import IPv4Network

from engine.netstack import Network
from engine.netstack.protocols.bgp import NO_EXPORT, BgpProcess
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


def _rr_triangle(reflector: bool) -> Network:
    """rr + pe1 + pe2 in AS 65000; iBGP sessions only PE<->RR (hub).
    ce (AS 65100) feeds 198.51.100.0/24 into pe1 via eBGP."""
    net = Network(seed=9)
    rr = net.add_device(Router("rr"))
    pe1 = net.add_device(Router("pe1"))
    pe2 = net.add_device(Router("pe2"))
    ce = net.add_device(Router("ce"))
    _link(net, pe1, "eth0", "10.0.1.1/30", rr, "eth0", "10.0.1.2/30")
    _link(net, pe2, "eth0", "10.0.2.1/30", rr, "eth1", "10.0.2.2/30")
    _link(net, ce, "eth0", "192.0.2.1/30", pe1, "eth1", "192.0.2.2/30")
    net.add_iface(ce, "lan", ["198.51.100.1/24"])
    # Static reachability for iBGP next-hops across the hub.
    pe1.add_static_route("10.0.2.0/30", "10.0.1.2")
    pe2.add_static_route("10.0.1.0/30", "10.0.2.2")
    pe2.add_static_route("192.0.2.0/30", "10.0.2.2")
    rr.add_static_route("192.0.2.0/30", "10.0.1.1")

    prr = BgpProcess(rr, asn=65000, router_id="1.1.1.1", keepalive_interval=5.0)
    prr.add_neighbor("10.0.1.1", 65000, rr_client=reflector)
    prr.add_neighbor("10.0.2.1", 65000, rr_client=reflector)
    p1 = BgpProcess(pe1, asn=65000, router_id="2.2.2.2", keepalive_interval=5.0)
    p1.add_neighbor("10.0.1.2", 65000)
    p1.add_neighbor("192.0.2.1", 65100)
    p2 = BgpProcess(pe2, asn=65000, router_id="3.3.3.3", keepalive_interval=5.0)
    p2.add_neighbor("10.0.2.2", 65000)
    pce = BgpProcess(ce, asn=65100, router_id="9.9.9.9", keepalive_interval=5.0)
    pce.add_neighbor("192.0.2.2", 65000)
    pce.advertise_network("198.51.100.0/24")
    net.start()
    net.run(until=60.0)
    return net


def test_without_rr_ibgp_split_horizon_blocks():
    net = _rr_triangle(reflector=False)
    # pe1 learns from CE, advertises to RR (iBGP). RR must NOT pass it on.
    assert _route(net, "rr", "198.51.100.0/24") is not None
    assert _route(net, "pe2", "198.51.100.0/24") is None


def test_route_reflector_reaches_all_clients():
    net = _rr_triangle(reflector=True)
    r = _route(net, "pe2", "198.51.100.0/24")
    assert r is not None and r.source == "ibgp"
    # Reflection never bounces the route back to its originator: pe1 keeps
    # exactly its eBGP-learned copy.
    r1 = _route(net, "pe1", "198.51.100.0/24")
    assert r1 is not None and r1.source == "ebgp"
    # Originator id recorded on the reflected path.
    attrs = _bgp(net, "pe2").peers[next(iter(_bgp(net, "pe2").peers))].rib_in[
        IPv4Network("198.51.100.0/24")
    ]
    assert attrs.originator == "2.2.2.2"


def test_communities_propagate_and_no_export():
    net = Network(seed=11)
    a = net.add_device(Router("a"))     # AS 65001
    b = net.add_device(Router("b"))     # AS 65002
    c = net.add_device(Router("c"))     # AS 65003
    _link(net, a, "eth0", "10.1.0.1/30", b, "eth0", "10.1.0.2/30")
    _link(net, b, "eth1", "10.2.0.1/30", c, "eth0", "10.2.0.2/30")
    net.add_iface(a, "lan", ["203.0.113.0/24".replace("0/24", "1/24")])
    pa = BgpProcess(a, asn=65001, router_id="1.1.1.1", keepalive_interval=5.0)
    pa.add_neighbor("10.1.0.2", 65002)
    pa.advertise_network("203.0.113.0/24", communities=("65001:100", NO_EXPORT))
    pa.advertise_network("198.18.0.0/24", communities=("65001:200",))
    pb = BgpProcess(b, asn=65002, router_id="2.2.2.2", keepalive_interval=5.0)
    pb.add_neighbor("10.1.0.1", 65001)
    pb.add_neighbor("10.2.0.2", 65003)
    pc = BgpProcess(c, asn=65003, router_id="3.3.3.3", keepalive_interval=5.0)
    pc.add_neighbor("10.2.0.1", 65002)
    net.start()
    net.run(until=40.0)

    # b received both, with communities intact.
    rib_b = _bgp(net, "b").peers[next(iter(_bgp(net, "b").peers))].rib_in
    assert rib_b[IPv4Network("203.0.113.0/24")].communities == ("65001:100", NO_EXPORT)
    # no-export crossed one eBGP hop into b but must stop there.
    assert _route(net, "b", "203.0.113.0/24") is not None
    assert _route(net, "c", "203.0.113.0/24") is None
    # The ordinary prefix reaches c with the AS path prepended.
    rib_c = _bgp(net, "c").peers[next(iter(_bgp(net, "c").peers))].rib_in
    assert rib_c[IPv4Network("198.18.0.0/24")].as_path == (65002, 65001)


def test_prefix_list_in_and_out():
    net = Network(seed=13)
    a = net.add_device(Router("a"))
    b = net.add_device(Router("b"))
    _link(net, a, "eth0", "10.1.0.1/30", b, "eth0", "10.1.0.2/30")
    pa = BgpProcess(a, asn=65001, router_id="1.1.1.1", keepalive_interval=5.0)
    pa.add_neighbor(
        "10.1.0.2", 65002,
        # outbound: never announce the 198.18.x lab block
        prefix_list_out=[{"action": "deny", "prefix": "198.18.0.0/15", "le": 32},
                         {"action": "permit", "prefix": "0.0.0.0/0", "le": 32}],
    )
    for p in ("203.0.113.0/24", "198.18.5.0/24"):
        pa.advertise_network(p)
    pb = BgpProcess(b, asn=65002, router_id="2.2.2.2", keepalive_interval=5.0)
    pb.add_neighbor(
        "10.1.0.1", 65001,
        # inbound: only /24s from 203.0.113.0/24 exactly
        prefix_list_in=[{"action": "permit", "prefix": "203.0.113.0/24"}],
    )
    net.start()
    net.run(until=30.0)
    assert _route(net, "b", "203.0.113.0/24") is not None
    assert _route(net, "b", "198.18.5.0/24") is None  # blocked twice over


def test_isp_nasional_rr_scenario_reduced():
    """Reduced `isp-nasional-bgp-multias-rr`: hub-spoke iBGP via RR, upstream
    default via eBGP, IX prefix, customer prefix — everything converges and
    the customer prefix is reachable AS-wide."""
    net = Network(seed=17)
    rr = net.add_device(Router("rr1"))          # AS 65001 route reflector
    edge = net.add_device(Router("pe-edge"))    # AS 65001, upstream + IX
    agg = net.add_device(Router("pe-agg"))      # AS 65001, customer-facing
    up = net.add_device(Router("upstream"))     # AS 64512
    ix = net.add_device(Router("ix-rs"))        # AS 65530
    ce = net.add_device(Router("ce-corp"))      # AS 65101

    _link(net, edge, "rr", "10.65.0.1/30", rr, "e0", "10.65.0.2/30")
    _link(net, agg, "rr", "10.65.1.1/30", rr, "e1", "10.65.1.2/30")
    _link(net, edge, "up", "100.64.0.2/30", up, "e0", "100.64.0.1/30")
    _link(net, edge, "ix", "185.1.1.2/24", ix, "e0", "185.1.1.1/24")
    _link(net, ce, "wan", "192.0.2.2/30", agg, "cust", "192.0.2.1/30")
    net.add_iface(up, "lan", ["8.8.8.0/24".replace("8.0/24", "8.1/24")])
    net.add_iface(ix, "lan", ["185.99.0.1/24"])
    net.add_iface(ce, "lan", ["203.0.113.1/24"])

    # iBGP next-hop reachability through the hub links.
    edge.add_static_route("10.65.1.0/30", "10.65.0.2")
    agg.add_static_route("10.65.0.0/30", "10.65.1.2")
    agg.add_static_route("100.64.0.0/30", "10.65.1.2")
    agg.add_static_route("185.1.1.0/24", "10.65.1.2")
    rr.add_static_route("100.64.0.0/30", "10.65.0.1")
    rr.add_static_route("185.1.1.0/24", "10.65.0.1")
    rr.add_static_route("192.0.2.0/30", "10.65.1.1")
    edge.add_static_route("192.0.2.0/30", "10.65.0.2")

    prr = BgpProcess(rr, asn=65001, router_id="1.1.1.1", keepalive_interval=5.0)
    prr.add_neighbor("10.65.0.1", 65001, rr_client=True)
    prr.add_neighbor("10.65.1.1", 65001, rr_client=True)
    pe = BgpProcess(edge, asn=65001, router_id="2.2.2.2", keepalive_interval=5.0)
    pe.add_neighbor("10.65.0.2", 65001)
    pe.add_neighbor("100.64.0.1", 64512)
    pe.add_neighbor("185.1.1.1", 65530)
    pa = BgpProcess(agg, asn=65001, router_id="3.3.3.3", keepalive_interval=5.0)
    pa.add_neighbor("10.65.1.2", 65001)
    pa.add_neighbor("192.0.2.2", 65101)
    pa.advertise_network("192.0.2.0/30")   # customer link net, for return traffic
    pup = BgpProcess(up, asn=64512, router_id="8.8.8.8", keepalive_interval=5.0)
    pup.add_neighbor("100.64.0.2", 65001)
    pup.advertise_network("8.8.8.0/24")
    pix = BgpProcess(ix, asn=65530, router_id="185.99.0.1", keepalive_interval=5.0)
    pix.add_neighbor("185.1.1.2", 65001)
    pix.advertise_network("185.99.0.0/24")
    pce = BgpProcess(ce, asn=65101, router_id="9.9.9.9", keepalive_interval=5.0)
    pce.add_neighbor("192.0.2.1", 65001)
    pce.advertise_network("203.0.113.0/24", communities=("65001:65101",))

    net.start()
    net.run(until=90.0)

    # Customer prefix propagates: agg (eBGP) -> RR -> edge (reflected) -> upstream/IX.
    assert _route(net, "pe-agg", "203.0.113.0/24").source == "ebgp"
    assert _route(net, "rr1", "203.0.113.0/24").source == "ibgp"
    assert _route(net, "pe-edge", "203.0.113.0/24").source == "ibgp"
    assert _route(net, "upstream", "203.0.113.0/24").source == "ebgp"
    # Upstream + IX prefixes reach the customer-facing PE via reflection.
    assert _route(net, "pe-agg", "8.8.8.0/24") is not None
    assert _route(net, "pe-agg", "185.99.0.0/24") is not None
    # AS path seen at the upstream: 65001 then customer 65101.
    pup_rib = _bgp(net, "upstream").peers[next(iter(_bgp(net, "upstream").peers))].rib_in
    assert pup_rib[IPv4Network("203.0.113.0/24")].as_path == (65001, 65101)
    # End-to-end data path: customer LAN pings the upstream service.
    rep = net.ping("ce-corp", "8.8.8.1", count=3)
    assert rep.received == 3, rep.as_dict()


def test_bgp_v2_replay_determinism():
    a, b = _rr_triangle(reflector=True), _rr_triangle(reflector=True)
    assert a.ledger.seq == b.ledger.seq
    assert a.ledger.hash() == b.ledger.hash()
