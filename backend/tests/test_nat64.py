"""NAT64: stateful IPv6-to-IPv4 translation (RFC 6146), well-known prefix
64:ff9b::/96 (RFC 6052).

Unlike NPTv6 (stateless, 1:1, same address family both sides), NAT64 crosses
families and is stateful/PAT-style — a ``Nat64Binding`` per flow, same shape
as NAT44's ``NatBinding``, expiring on idle via the same sequence-guard timer
idiom used elsewhere in this engine (fragmentation reassembly, DHCP leases).

DNS64 (RFC 6147) is NOT implemented — see the slice notes; this engine's DNS
support (``routing.py``'s ``dns_zone``/``DnsMessage``) has no query-type
concept (no A vs AAAA) and runs over IPv4 UDP only, so there is nothing to
synthesize an AAAA record *from*.
"""
from __future__ import annotations

from ipaddress import IPv4Address, IPv6Address

from app.models import Topology
from app.services.netlab import build_network
from engine.netstack import Network
from engine.netstack.device import Host
from engine.netstack.frames import PROTO_UDP, Ipv6Packet, UdpSegment
from engine.netstack.routing import (
    NAT64_MAX_PORT,
    NAT64_SESSION_TIMEOUT,
    NAT64_WELLKNOWN_PREFIX,
    Router,
    _nat64_embed,
    _nat64_extract,
)


def test_nat64_embed_extract_round_trip():
    v4 = IPv4Address("203.0.113.1")
    v6 = _nat64_embed(v4)
    assert v6 in NAT64_WELLKNOWN_PREFIX
    assert _nat64_extract(v6) == v4


def test_nat64_extract_rejects_addresses_outside_the_prefix():
    assert _nat64_extract(IPv6Address("2001:db8::1")) is None


def _nat64_topology() -> tuple[Network, Host, Router, Host]:
    """pc (v6-only) -- gw -- server (v4-only). gw translates pc's traffic to
    the synthesized 64:ff9b::<server-v4> address into real IPv4 toward
    server, and back."""
    net = Network(seed=11)
    pc = net.add_device(Host("pc"))
    gw = net.add_device(Router("gw"))
    server = net.add_device(Host("server"))

    net.connect("lan", net.add_iface(pc, "eth0", ["fd00:1::10/64"]),
                net.add_iface(gw, "eth0", ["fd00:1::1/64"]))
    net.connect("wan", net.add_iface(gw, "eth1", ["203.0.113.2/30"]),
                net.add_iface(server, "eth0", ["203.0.113.1/30"]))

    pc.default_gateway6 = IPv6Address("fd00:1::1")
    gw.enable_nat64(inside=["eth0"], outside="eth1")
    return net, pc, gw, server


def test_nat64_ping_round_trips_and_hides_the_inside_v6_address():
    net, _pc, gw, _server = _nat64_topology()
    dst6 = _nat64_embed(IPv4Address("203.0.113.1"))

    report = net.ping("pc", str(dst6), count=3)
    assert report.received == 3

    rows = gw.nat64_rows()
    assert rows and rows[0]["proto"] == "icmp"
    assert rows[0]["outside"].startswith("203.0.113.2:")

    wan_frames = net.capture.records(link_id="wan", limit=500)
    echo_requests = [
        r for r in wan_frames
        if r.layers.get("icmp", {}).get("type") == 8
    ]
    assert echo_requests
    for r in echo_requests:
        assert r.layers["ipv4"]["src"] == "203.0.113.2"
        assert r.layers["ipv4"]["dst"] == "203.0.113.1"
    # The inside IPv6 address never touches the v4 wire (different address
    # family already guarantees this — asserted anyway as a regression net).
    assert not any("fd00:1::10" in str(r.layers) for r in wan_frames)


def test_nat64_udp_translates_source_port():
    net, pc, gw, _server = _nat64_topology()
    dst6 = _nat64_embed(IPv4Address("203.0.113.1"))

    net.start()
    pc.send_ip6(net, Ipv6Packet(
        src=IPv6Address("fd00:1::10"), dst=dst6,
        proto=PROTO_UDP, payload=UdpSegment(src_port=40000, dst_port=9999, payload_len=4),
    ))
    net.run_for(2.0)

    rows = gw.nat64_rows()
    assert len(rows) == 1
    assert rows[0]["proto"] == "udp"
    assert rows[0]["inside"] == "fd00:1::10:40000"
    assert rows[0]["outside"].startswith("203.0.113.2:")

    wan_frames = net.capture.records(link_id="wan", limit=500)
    udp_frames = [r for r in wan_frames if "udp" in r.layers]
    assert udp_frames
    assert udp_frames[0].layers["ipv4"]["src"] == "203.0.113.2"
    assert udp_frames[0].layers["udp"]["dst_port"] == 9999


def test_nat64_session_expires_after_idle_timeout():
    """``Network.ping`` advances the scheduler by ``count*interval + 5.0``
    seconds by default, which would blow straight past the idle timeout —
    step the clock manually instead (run_after=False + run_for)."""
    net, _pc, gw, _server = _nat64_topology()
    dst6 = _nat64_embed(IPv4Address("203.0.113.1"))

    net.ping("pc", str(dst6), count=1, run_after=False)
    net.run_for(2.0)
    assert len(gw.nat64_rows()) == 1

    net.run_for(NAT64_SESSION_TIMEOUT + 1.0)
    assert gw.nat64_rows() == []


def test_nat64_pool_exhaustion_drops_new_sessions_without_a_binding():
    net, _pc, gw, _server = _nat64_topology()
    dst6 = _nat64_embed(IPv4Address("203.0.113.1"))
    gw._nat64_next_key = NAT64_MAX_PORT + 1  # simulate an exhausted pool

    net.ping("pc", str(dst6), count=1, run_after=False)
    net.run_for(2.0)

    assert gw.nat64_rows() == []
    assert net.drops.get("nat64_pool_exhausted") == 1


def test_nat64_intent_round_trip_through_topology_build():
    topo = Topology.model_validate({
        "project": {"id": "p1", "name": "t"},
        "nodes": [
            {
                "id": "n1", "project_id": "p1", "name": "gw", "kind": "router",
                "interfaces": [
                    {"id": "i1", "node_id": "n1", "name": "eth0", "ip": ["fd00:1::1/64"]},
                    {"id": "i2", "node_id": "n1", "name": "eth1", "ip": ["203.0.113.2/30"]},
                ],
                "intent": {"nat64": {"inside": ["eth0"], "outside": "eth1"}},
            },
        ],
        "links": [],
    })
    net = build_network(topo)
    gw = net.devices["gw"]
    assert isinstance(gw, Router)
    assert gw.nat64_inside == {"eth0"}
    assert gw.nat64_outside == "eth1"
