"""ACL support for IPv6 (RFC 4291 / RFC 8200 / RFC 4443 / RFC 4861).

Engine level: ``AclRule`` gains v6-scoped src6/dst6 + icmp_type, gated at both
IPv6 ingress (``Router._on_ipv6``) and egress (``Router._forward6``) — mirrors
the existing IPv4 acl_in/acl_out pipeline. Key behaviour under test: denying
ICMPv6 types 135/136 genuinely blocks Neighbor Discovery (unlike IPv4, where
ARP isn't an IP protocol an ACL could ever touch), address-family scoped
rules never cross-match the other family, and the v6 fields survive the
declarative-intent round trip used by ``app.services.netlab``.
"""
from __future__ import annotations

from ipaddress import IPv4Network, IPv6Address, IPv6Network

from app.models import Topology
from app.services.netlab import build_network
from engine.netstack import Network
from engine.netstack.device import Host
from engine.netstack.routing import AclRule, Router


def _host_router_pair() -> tuple[Network, Host, Router]:
    """a -- r1, one link, dual-stack addressing on both ends."""
    net = Network(seed=11)
    a = net.add_device(Host("a"))
    r1 = net.add_device(Router("r1"))
    ia = net.add_iface(a, "eth0", ["10.0.0.10/24", "2001:db8:a::10/64"])
    ir = net.add_iface(r1, "eth0", ["10.0.0.1/24", "2001:db8:a::1/64"])
    net.connect("l1", ia, ir)
    return net, a, r1


def _routed6_two_dsts() -> Network:
    """a -- r1 -- r2 -- {b1, b2}: two v6 destinations behind the same r1
    egress interface, so an egress ACL on that one interface can be proven
    to hit only the denied prefix."""
    net = Network(seed=12)
    a = net.add_device(Host("a"))
    b1 = net.add_device(Host("b1"))
    b2 = net.add_device(Host("b2"))
    r1 = net.add_device(Router("r1"))
    r2 = net.add_device(Router("r2"))

    ia = net.add_iface(a, "eth0", ["2001:db8:a::10/64"])
    net.connect("la", ia, net.add_iface(r1, "eth0", ["2001:db8:a::1/64"]))
    net.connect(
        "lx",
        net.add_iface(r1, "eth1", ["2001:db8:12::1/64"]),
        net.add_iface(r2, "eth0", ["2001:db8:12::2/64"]),
    )
    ib1 = net.add_iface(b1, "eth0", ["2001:db8:b1::10/64"])
    net.connect("lb1", ib1, net.add_iface(r2, "eth1", ["2001:db8:b1::1/64"]))
    ib2 = net.add_iface(b2, "eth0", ["2001:db8:b2::10/64"])
    net.connect("lb2", ib2, net.add_iface(r2, "eth2", ["2001:db8:b2::1/64"]))

    a.default_gateway6 = IPv6Address("2001:db8:a::1")
    b1.default_gateway6 = IPv6Address("2001:db8:b1::1")
    b2.default_gateway6 = IPv6Address("2001:db8:b2::1")
    r1.add_static_route6("2001:db8:b1::/64", "2001:db8:12::2")
    r1.add_static_route6("2001:db8:b2::/64", "2001:db8:12::2")
    r2.add_static_route6("2001:db8:a::/64", "2001:db8:12::1")
    return net


def test_acl_deny_icmpv6_nd_blocks_neighbor_resolution():
    """Blocking ICMPv6 ND (135/136) is not like blocking ICMPv4 (ARP isn't
    IP-layer): it genuinely kills link-layer resolution, and that failure
    must be observable, not silently excepted."""
    net, a, r1 = _host_router_pair()
    r1.acl_in["eth0"] = [
        AclRule(action="permit", proto="icmpv6", icmp_type=128),
        AclRule(action="permit", proto="icmpv6", icmp_type=129),
        AclRule(action="deny", proto="icmpv6", icmp_type=135),
        AclRule(action="deny", proto="icmpv6", icmp_type=136),
        AclRule(action="permit"),
    ]
    rep = net.ping("a", "2001:db8:a::1", count=1)
    assert rep.received == 0
    assert net.stats()["drops"].get("acl_deny_in", 0) >= 1
    assert IPv6Address("2001:db8:a::1") not in a.nd_cache  # resolution never completed


def test_acl_permits_icmpv6_when_nd_not_denied():
    """Control case: without an ND-denying rule, echo (and the ND it rides
    on) works normally — proves the rules above are what blocks it, not the
    mere presence of an ACL."""
    net, a, r1 = _host_router_pair()
    r1.acl_in["eth0"] = [
        AclRule(action="permit", proto="icmpv6", icmp_type=128),
        AclRule(action="permit", proto="icmpv6", icmp_type=129),
        AclRule(action="permit"),
    ]
    rep = net.ping("a", "2001:db8:a::1", count=2)
    assert rep.received == 2
    assert IPv6Address("2001:db8:a::1") in a.nd_cache


def test_acl_deny_ipv6_prefix_blocks_only_that_prefix_at_egress():
    net = _routed6_two_dsts()
    r1 = net.devices["r1"]
    assert isinstance(r1, Router)
    r1.acl_out["eth1"] = [
        AclRule(action="deny", dst6=IPv6Network("2001:db8:b1::/64")),
        AclRule(action="permit"),
    ]
    denied = net.ping("a", "2001:db8:b1::10", count=2)
    assert denied.received == 0
    assert net.stats()["drops"].get("acl_deny_out", 0) >= 1

    allowed = net.ping("a", "2001:db8:b2::10", count=2)
    assert allowed.received == 2


def test_acl_address_family_rules_do_not_cross_match():
    # A v4-only rule (bare "src", no proto) must never match v6 traffic.
    net, _a, r1 = _host_router_pair()
    r1.acl_in["eth0"] = [
        AclRule(action="deny", src=IPv4Network("10.0.0.10/32")),
        AclRule(action="permit"),
    ]
    assert net.ping("a", "10.0.0.1", count=1).received == 0
    assert net.ping("a", "2001:db8:a::1", count=1).received == 1

    # A v6-only rule (bare "src6", no proto) must never match v4 traffic.
    net2, _a2, r2 = _host_router_pair()
    r2.acl_in["eth0"] = [
        AclRule(action="deny", src6=IPv6Network("2001:db8:a::10/128")),
        AclRule(action="permit"),
    ]
    assert net2.ping("a", "2001:db8:a::1", count=1).received == 0
    assert net2.ping("a", "10.0.0.1", count=1).received == 1


def test_aclrule_ipv6_fields_round_trip_through_intent():
    """as_dict() -> declarative intent -> _apply_intent (netlab.py) must
    reproduce the same rule, the way an archive export/import round trip
    would exercise it."""
    original = AclRule(
        action="deny",
        proto="icmpv6",
        src6=IPv6Network("2001:db8:a::/64"),
        dst6=IPv6Network("2001:db8:b::/64"),
        icmp_type=135,
    )
    d = original.as_dict()

    topo = Topology.model_validate({
        "project": {"id": "p1", "name": "t"},
        "nodes": [
            {
                "id": "n1", "project_id": "p1", "name": "r1", "kind": "router",
                "interfaces": [
                    {"id": "i1", "node_id": "n1", "name": "eth0",
                     "ip": ["2001:db8:a::1/64"]},
                ],
                "intent": {"acl": {"eth0": {"in": [{
                    "action": d["action"], "proto": d["proto"],
                    "src6": d["src6"], "dst6": d["dst6"],
                    "icmp_type": d["icmp_type"],
                }]}}},
            },
        ],
        "links": [],
    })
    net = build_network(topo)
    r1 = net.devices["r1"]
    assert isinstance(r1, Router)
    rebuilt = r1.acl_in["eth0"][0]
    assert rebuilt == original
    assert rebuilt.as_dict() == d
