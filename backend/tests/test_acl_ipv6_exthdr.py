"""ACL classification against an IPv6 extension-header chain (RFC 8200 /
RFC 7112).

``Ipv6Packet.proto``/``.payload`` always carry the resolved upper-layer
protocol in this structured simulation (no byte-level next-header chain to
walk) -- see the class docstring. What is new here: ``ext_headers`` records
which extension headers sit in front of that upper-layer header on the
wire, and ``Router._acl_permits`` refuses to let *any* rule (including a
trailing bare ``permit``) match when that chain is too long or contains a
header type it doesn't recognize -- an RFC 7112-style evasion attempt is
denied outright rather than risking a wrong permit. This only engages when
an ACL is actually configured; a link with no ACL has nothing to evade.
"""
from __future__ import annotations

from ipaddress import IPv6Address

from engine.netstack import Network
from engine.netstack.device import Host
from engine.netstack.frames import (
    ETH_IPV6,
    IPV6_EXT_DEST_OPTS,
    IPV6_EXT_FRAGMENT,
    IPV6_EXT_HOP_BY_HOP,
    PROTO_TCP,
    EthernetFrame,
    Ipv6Packet,
    TcpSegment,
)
from engine.netstack.iface import Interface
from engine.netstack.routing import AclRule, Router

R1_IP = IPv6Address("2001:db8:a::1")


def _host_router_pair() -> tuple[Network, Router, Interface]:
    net = Network(seed=21)
    a = net.add_device(Host("a"))
    r1 = net.add_device(Router("r1"))
    ia = net.add_iface(a, "eth0", ["2001:db8:a::10/64"])
    ir = net.add_iface(r1, "eth0", ["2001:db8:a::1/64"])
    net.connect("l1", ia, ir)
    return net, r1, ir


def _tcp80_frame(iface, ext_headers: tuple[int, ...] = ()) -> EthernetFrame:
    return EthernetFrame(
        src_mac="02:00:00:00:00:0a",
        dst_mac=iface.mac,
        ethertype=ETH_IPV6,
        payload=Ipv6Packet(
            src=IPv6Address("2001:db8:a::10"),
            dst=R1_IP,
            proto=PROTO_TCP,
            payload=TcpSegment(src_port=54321, dst_port=80),
            ext_headers=ext_headers,
        ),
    )


def _fragment_frame(iface) -> EthernetFrame:
    """A synthetic non-first IPv6 fragment: Fragment ext header present,
    upper-layer proto still known (RFC 8200 -- the Fragment header's own
    Next Header field carries it), but no L4 header travels with this
    fragment, so ``payload`` is None (no ports available)."""
    return EthernetFrame(
        src_mac="02:00:00:00:00:0a",
        dst_mac=iface.mac,
        ethertype=ETH_IPV6,
        payload=Ipv6Packet(
            src=IPv6Address("2001:db8:a::10"),
            dst=R1_IP,
            proto=PROTO_TCP,
            payload=None,
            ext_headers=(IPV6_EXT_FRAGMENT,),
        ),
    )


def test_acl_permits_through_a_short_known_ext_header_chain():
    net, r1, ir = _host_router_pair()
    r1.acl_in["eth0"] = [AclRule(action="permit", proto="tcp", dst_port=80)]
    r1.on_frame(net, ir, _tcp80_frame(ir, (IPV6_EXT_HOP_BY_HOP, IPV6_EXT_DEST_OPTS)))
    assert net.stats()["drops"].get("acl_deny_in", 0) == 0


def test_acl_denies_oversized_ext_header_chain_even_though_a_rule_would_permit():
    net, r1, ir = _host_router_pair()
    r1.acl_in["eth0"] = [AclRule(action="permit")]  # would permit anything
    chain = (IPV6_EXT_HOP_BY_HOP,) * 9  # over MAX_IPV6_EXT_HEADERS (8)
    r1.on_frame(net, ir, _tcp80_frame(ir, chain))
    assert net.stats()["drops"].get("acl_deny_in", 0) == 1


def test_acl_denies_unrecognized_ext_header_type_even_though_a_rule_would_permit():
    net, r1, ir = _host_router_pair()
    r1.acl_in["eth0"] = [AclRule(action="permit")]
    r1.on_frame(net, ir, _tcp80_frame(ir, (253,)))  # not a recognized ext header type
    assert net.stats()["drops"].get("acl_deny_in", 0) == 1


def test_ext_header_guard_never_fires_without_a_configured_acl():
    """No ACL configured -> nothing to evade -- an oversized/unknown chain
    must still be forwarded exactly like today, proving the guard is scoped
    to configured ACLs, not a blanket IPv6 extension-header policy."""
    net, r1, ir = _host_router_pair()
    assert not r1.acl_in.get("eth0")
    r1.on_frame(net, ir, _tcp80_frame(ir, (253,) * 20))
    assert net.stats()["drops"].get("acl_deny_in", 0) == 0


def test_acl_port_rule_cannot_match_a_non_first_fragment():
    """A rule that requires dst_port=80 can never see a port on a non-first
    fragment (no L4 header travels with it) -- it must fall through, not
    match by accident, so an implicit-deny ACL denies it."""
    net, r1, ir = _host_router_pair()
    r1.acl_in["eth0"] = [AclRule(action="permit", proto="tcp", dst_port=80)]
    r1.on_frame(net, ir, _fragment_frame(ir))
    assert net.stats()["drops"].get("acl_deny_in", 0) == 1


def test_acl_proto_only_rule_still_matches_a_non_first_fragment():
    """A proto-only rule (no dst_port) keeps working on a fragment: the
    upper-layer protocol is still known from the Fragment header's own
    Next Header field, unlike the (unavailable) L4 ports."""
    net, r1, ir = _host_router_pair()
    r1.acl_in["eth0"] = [AclRule(action="permit", proto="tcp")]
    r1.on_frame(net, ir, _fragment_frame(ir))
    assert net.stats()["drops"].get("acl_deny_in", 0) == 0
