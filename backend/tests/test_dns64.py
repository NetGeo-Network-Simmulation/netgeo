"""DNS query types (A/AAAA) + DNS64 synthesis (A7).

RFC 1035 §3.2.2 (QTYPE A=1), RFC 3596 (AAAA=28), RFC 2308 §2.2 (NODATA vs
NXDOMAIN), RFC 6147 (DNS64), RFC 6052 §2.2 (IPv4-embedded IPv6 address
format — this engine only synthesizes the well-known /96, matching its
NAT64 (RFC 6146) implementation in ``test_nat64.py``).
"""
from __future__ import annotations

from ipaddress import IPv4Address, IPv6Address, IPv6Network

import pytest
from app.models import Topology
from app.services.netlab import build_network
from engine.netstack import Network
from engine.netstack.device import Host
from engine.netstack.routing import NAT64_WELLKNOWN_PREFIX, Router, _nat64_embed


def _zone_topology() -> tuple[Network, Host, Router]:
    net = Network(seed=21)
    h = net.add_device(Host("pc"))
    r = net.add_device(Router("gw"))
    net.connect(
        "lan",
        net.add_iface(h, "eth0", ["192.168.1.10/24", "fd00:1::10/64"]),
        net.add_iface(r, "eth0", ["192.168.1.1/24", "fd00:1::1/64"]),
    )
    h.default_gateway = IPv4Address("192.168.1.1")
    h.dns_server = IPv4Address("192.168.1.1")
    h.dns_server6 = IPv6Address("fd00:1::1")
    return net, h, r


def test_a_and_aaaa_queries_return_the_right_family():
    net, h, r = _zone_topology()
    r.dns_zone["dual.lab"] = IPv4Address("203.0.113.9")
    r.dns_zone6["dual.lab"] = IPv6Address("2001:db8::9")
    net.start()

    a_answers: list = []
    aaaa_answers: list = []
    h.resolve(net, "dual.lab", qtype="A", callback=a_answers.append)
    h.resolve(net, "dual.lab", qtype="AAAA", callback=aaaa_answers.append)
    net.run_for(5.0)

    assert a_answers == [IPv4Address("203.0.113.9")]
    assert aaaa_answers == [IPv6Address("2001:db8::9")]
    assert h.dns_cache["dual.lab"] == IPv4Address("203.0.113.9")
    assert h.dns_cache6["dual.lab"] == IPv6Address("2001:db8::9")


def test_nodata_vs_nxdomain():
    net, h, r = _zone_topology()
    r.dns_zone["v4only.lab"] = IPv4Address("203.0.113.5")  # no AAAA
    net.start()

    got: dict = {}

    def cb(name):
        def _inner(ans, name=name):
            got[name] = ans
        return _inner

    h.resolve(net, "v4only.lab", qtype="AAAA", callback=cb("nodata"))
    h.resolve(net, "totally.missing", qtype="A", callback=cb("nxdomain"))
    net.run_for(5.0)

    # Both come back with answer=None from the resolver's point of view;
    # the distinction lives on the wire (DnsMessage.rcode) — verified via
    # the capture below.
    assert got["nodata"] is None
    assert got["nxdomain"] is None

    frames = net.capture.records(link_id="lan", limit=500)
    dns_frames = [f for f in frames if "dns" in f.layers]
    responses = [f for f in dns_frames if f.layers["dns"].get("op") == "response"]
    nodata = [f for f in responses if f.layers["dns"]["qname"] == "v4only.lab"]
    nxdomain = [f for f in responses if f.layers["dns"]["qname"] == "totally.missing"]
    assert nodata and nodata[0].layers["dns"]["rcode"] == "noerror"
    assert nodata[0].layers["dns"]["answer"] is None
    assert nxdomain and nxdomain[0].layers["dns"]["rcode"] == "nxdomain"


def test_dns64_synthesizes_exact_address():
    net, h, r = _zone_topology()
    r.dns_zone["v4only.lab"] = IPv4Address("192.0.2.33")
    r.enable_dns64()
    net.start()

    answers: list = []
    h.resolve(net, "v4only.lab", qtype="AAAA", callback=answers.append)
    net.run_for(5.0)

    assert answers == [IPv6Address("64:ff9b::c000:221")]


def test_dns64_leaves_a_real_aaaa_untouched():
    net, h, r = _zone_topology()
    r.dns_zone["dual.lab"] = IPv4Address("192.0.2.33")
    r.dns_zone6["dual.lab"] = IPv6Address("2001:db8::99")
    r.enable_dns64()
    net.start()

    answers: list = []
    h.resolve(net, "dual.lab", qtype="AAAA", callback=answers.append)
    net.run_for(5.0)

    assert answers == [IPv6Address("2001:db8::99")]  # not the synthesized address


def test_dns_query_over_ipv6_transport():
    net, h, r = _zone_topology()
    r.dns_zone["files.lab"] = IPv4Address("192.168.1.50")
    net.start()

    answers: list = []
    h.resolve(net, "files.lab", callback=answers.append)  # default qtype "A"
    net.run_for(5.0)
    assert answers == [IPv4Address("192.168.1.50")]

    frames = net.capture.records(link_id="lan", limit=500)
    dns_v6 = [f for f in frames if "dns" in f.layers and "ipv6" in f.layers]
    assert dns_v6, "DNS query/response must have travelled over an IPv6 packet"


def test_old_a_only_project_state_is_unaffected():
    """A project built before qtype/dns64 existed has no ``dns_zone6``/
    ``dns64`` keys in its intent — the loader must leave the new fields at
    their defaults and behave exactly as before."""
    topo = Topology.model_validate({
        "project": {"id": "p1", "name": "t"},
        "nodes": [
            {
                "id": "n1", "project_id": "p1", "name": "gw", "kind": "router",
                "interfaces": [
                    {"id": "i1", "node_id": "n1", "name": "eth0", "ip": ["192.168.1.1/24"]},
                ],
                "intent": {"dns_zone": {"nas.lab": "192.168.1.40"}},
            },
        ],
        "links": [],
    })
    net = build_network(topo)
    gw = net.devices["gw"]
    assert isinstance(gw, Router)
    assert gw.dns_zone == {"nas.lab": IPv4Address("192.168.1.40")}
    assert gw.dns_zone6 == {}
    assert gw.dns64_prefix is None


def test_dns_zone6_and_dns64_intent_round_trip():
    topo = Topology.model_validate({
        "project": {"id": "p1", "name": "t"},
        "nodes": [
            {
                "id": "n1", "project_id": "p1", "name": "gw", "kind": "router",
                "interfaces": [
                    {"id": "i1", "node_id": "n1", "name": "eth0", "ip": ["192.168.1.1/24"]},
                ],
                "intent": {
                    "dns_zone": {"nas.lab": "192.168.1.40"},
                    "dns_zone6": {"nas.lab": "2001:db8::40"},
                    "dns64": {"enabled": True},
                },
            },
        ],
        "links": [],
    })
    net = build_network(topo)
    gw = net.devices["gw"]
    assert isinstance(gw, Router)
    assert gw.dns_zone6 == {"nas.lab": IPv6Address("2001:db8::40")}
    assert gw.dns64_prefix == NAT64_WELLKNOWN_PREFIX


def test_enable_dns64_rejects_a_non_wellknown_prefix():
    _net, _h, r = _zone_topology()
    with pytest.raises(ValueError):
        r.enable_dns64(prefix=IPv6Network("2001:db8:64::/96"))


def test_dns64_end_to_end_ipv6_only_host_pings_through_nat64():
    """pc (v6-only, no A/dns_server at all) -- gw (DNS64 + NAT64) -- server
    (v4-only). pc resolves server's name via AAAA/DNS64, then pings the
    synthesized address straight through the existing NAT64 translator."""
    net = Network(seed=22)
    pc = net.add_device(Host("pc"))
    gw = net.add_device(Router("gw"))
    server = net.add_device(Host("server"))

    net.connect("lan", net.add_iface(pc, "eth0", ["fd00:1::10/64"]),
                net.add_iface(gw, "eth0", ["fd00:1::1/64"]))
    net.connect("wan", net.add_iface(gw, "eth1", ["203.0.113.2/30"]),
                net.add_iface(server, "eth0", ["203.0.113.1/30"]))

    pc.default_gateway6 = IPv6Address("fd00:1::1")
    pc.dns_server6 = IPv6Address("fd00:1::1")
    gw.dns_zone["server.lab"] = IPv4Address("203.0.113.1")
    gw.enable_dns64()
    gw.enable_nat64(inside=["eth0"], outside="eth1")

    net.start()
    answers: list = []
    pc.resolve(net, "server.lab", qtype="AAAA", callback=answers.append)
    net.run_for(5.0)
    assert answers == [_nat64_embed(IPv4Address("203.0.113.1"))]

    report = net.ping("pc", str(answers[0]), count=3)
    assert report.received == 3
    assert gw.nat64_rows()


def test_dns64_determinism_two_runs_match():
    def run_once() -> tuple:
        net = Network(seed=23)
        h = net.add_device(Host("pc"))
        r = net.add_device(Router("gw"))
        net.connect("lan", net.add_iface(h, "eth0", ["192.168.9.10/24"]),
                    net.add_iface(r, "eth0", ["192.168.9.1/24"]))
        h.default_gateway = IPv4Address("192.168.9.1")
        h.dns_server = IPv4Address("192.168.9.1")
        r.dns_zone["v4only.lab"] = IPv4Address("198.51.100.7")
        r.enable_dns64()
        net.start()
        answers: list = []
        h.resolve(net, "v4only.lab", qtype="AAAA", callback=answers.append)
        net.run_for(5.0)
        return tuple(answers)

    assert run_once() == run_once()
