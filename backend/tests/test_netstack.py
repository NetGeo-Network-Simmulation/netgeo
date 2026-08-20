"""Netstack engine tests — L2/L3 realism, protocols, services, determinism.

Each test builds a small lab with the pure-Python engine API (no HTTP layer),
runs the deterministic kernel, and asserts on observable network behaviour:
ping/traceroute results, tables (ARP/MAC/RIB), captures and drop counters.
"""
from __future__ import annotations

from ipaddress import IPv4Address, IPv4Network

from engine.netstack import Network
from engine.netstack.device import Host
from engine.netstack.protocols.bgp import BgpProcess
from engine.netstack.protocols.ospf import OspfProcess
from engine.netstack.routing import AclRule, DhcpPool, Router
from engine.netstack.switching import Switch

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def lan_pair() -> tuple[Network, Host, Host, Switch]:
    """h1 -- sw -- h2, one /24."""
    net = Network(seed=42)
    h1 = net.add_device(Host("h1"))
    h2 = net.add_device(Host("h2"))
    sw = net.add_device(Switch("sw1"))
    i_h1 = net.add_iface(h1, "eth0", ["10.0.0.1/24"])
    i_h2 = net.add_iface(h2, "eth0", ["10.0.0.2/24"])
    s1 = net.add_iface(sw, "gi0/1")
    s2 = net.add_iface(sw, "gi0/2")
    net.connect("l1", i_h1, s1, delay=0.0001)
    net.connect("l2", i_h2, s2, delay=0.0001)
    return net, h1, h2, sw


def routed_chain() -> Network:
    """h1 -- r1 -- r2 -- h2 with static routes both ways."""
    net = Network(seed=1)
    h1 = net.add_device(Host("h1"))
    h2 = net.add_device(Host("h2"))
    r1 = net.add_device(Router("r1"))
    r2 = net.add_device(Router("r2"))
    net.connect("lan1", net.add_iface(h1, "eth0", ["192.168.1.10/24"]),
                net.add_iface(r1, "eth0", ["192.168.1.1/24"]))
    net.connect("core", net.add_iface(r1, "eth1", ["10.0.12.1/30"]),
                net.add_iface(r2, "eth1", ["10.0.12.2/30"]))
    net.connect("lan2", net.add_iface(r2, "eth0", ["192.168.2.1/24"]),
                net.add_iface(h2, "eth0", ["192.168.2.10/24"]))
    h1.default_gateway = IPv4Address("192.168.1.1")
    h2.default_gateway = IPv4Address("192.168.2.1")
    r1.add_static_route("192.168.2.0/24", "10.0.12.2")
    r2.add_static_route("192.168.1.0/24", "10.0.12.1")
    return net


# ---------------------------------------------------------------------------
# L2: ARP, switching, VLANs, STP
# ---------------------------------------------------------------------------

def test_arp_and_ping_same_lan():
    net, h1, h2, _sw = lan_pair()
    report = net.ping("h1", "10.0.0.2", count=4)
    assert report.sent == 4
    assert report.received == 4
    assert report.loss_pct == 0.0
    # RTT must be positive and include serialization + propagation.
    assert all(r > 0 for r in report.rtts_ms)
    # ARP was resolved and cached on both hosts.
    assert IPv4Address("10.0.0.2") in h1.arp_table
    assert IPv4Address("10.0.0.1") in h2.arp_table


def test_switch_learns_macs_and_stops_flooding():
    net, h1, h2, sw = lan_pair()
    net.ping("h1", "10.0.0.2", count=2)
    macs = {row["mac"] for row in sw.mac_table_rows()}
    assert str(h1.interfaces["eth0"].mac) in macs
    assert str(h2.interfaces["eth0"].mac) in macs


def test_capture_records_arp_and_icmp():
    net, *_ = lan_pair()
    net.ping("h1", "10.0.0.2", count=1)
    records = net.capture.records(limit=500)
    infos = " | ".join(r.info for r in records)
    assert "ARP who-has" in infos
    assert "echo-request" in infos
    assert "echo-reply" in infos
    # Structured layers for the inspector.
    icmp_recs = [r for r in records if "icmp" in r.layers]
    assert icmp_recs and icmp_recs[0].layers["ipv4"]["src"]


def test_vlan_isolation_blocks_cross_vlan_traffic():
    net = Network(seed=7)
    h1 = net.add_device(Host("h1"))
    h2 = net.add_device(Host("h2"))
    sw = net.add_device(Switch("sw1"))
    i1 = net.add_iface(h1, "eth0", ["10.0.0.1/24"])
    i2 = net.add_iface(h2, "eth0", ["10.0.0.2/24"])
    p1 = net.add_iface(sw, "gi0/1")
    p2 = net.add_iface(sw, "gi0/2")
    p1.access_vlan = 10
    p2.access_vlan = 20
    net.connect("l1", i1, p1)
    net.connect("l2", i2, p2)
    report = net.ping("h1", "10.0.0.2", count=2)
    assert report.received == 0  # ARP can never cross VLANs


def test_vlan_trunk_carries_tagged_traffic():
    net = Network(seed=7)
    h1 = net.add_device(Host("h1"))
    h2 = net.add_device(Host("h2"))
    sw1 = net.add_device(Switch("sw1"))
    sw2 = net.add_device(Switch("sw2"))
    i1 = net.add_iface(h1, "eth0", ["10.0.0.1/24"])
    i2 = net.add_iface(h2, "eth0", ["10.0.0.2/24"])
    a1 = net.add_iface(sw1, "gi0/1")
    t1 = net.add_iface(sw1, "gi0/24")
    a2 = net.add_iface(sw2, "gi0/1")
    t2 = net.add_iface(sw2, "gi0/24")
    a1.access_vlan = 10
    a2.access_vlan = 10
    t1.vlan_mode = "trunk"
    t2.vlan_mode = "trunk"
    net.connect("acc1", i1, a1)
    net.connect("acc2", i2, a2)
    net.connect("trunk", t1, t2)
    report = net.ping("h1", "10.0.0.2", count=3)
    assert report.received == 3
    # Learned entries live in VLAN 10.
    assert any(row["vlan"] == 10 for row in sw1.mac_table_rows())


def test_stp_blocks_redundant_path_and_traffic_survives():
    """Triangle of switches — without STP this would be a broadcast storm."""
    net = Network(seed=3)
    h1 = net.add_device(Host("h1"))
    h2 = net.add_device(Host("h2"))
    sws = [net.add_device(Switch(f"sw{i}")) for i in (1, 2, 3)]
    # Inter-switch triangle.
    ports: dict[tuple[int, int], object] = {}
    for a, b in ((0, 1), (1, 2), (0, 2)):
        pa = net.add_iface(sws[a], f"gi0/{b + 10}")
        pb = net.add_iface(sws[b], f"gi0/{a + 10}")
        net.connect(f"isl-{a}{b}", pa, pb)
        ports[(a, b)] = (pa, pb)
    net.connect("h1", net.add_iface(h1, "eth0", ["10.0.0.1/24"]),
                net.add_iface(sws[0], "gi0/1"))
    net.connect("h2", net.add_iface(h2, "eth0", ["10.0.0.2/24"]),
                net.add_iface(sws[1], "gi0/1"))

    net.start()
    net.run_for(12.0)  # let STP elect and settle

    blocked = [
        i.qualified_name
        for sw in sws
        for i in sw.interfaces.values()
        if i.stp_state == "blocking"
    ]
    assert len(blocked) == 1, f"expected exactly one blocked port, got {blocked}"

    report = net.ping("h1", "10.0.0.2", count=3)
    assert report.received == 3


# ---------------------------------------------------------------------------
# L3: static routing, ICMP semantics, traceroute
# ---------------------------------------------------------------------------

def test_static_routing_end_to_end():
    net = routed_chain()
    report = net.ping("h1", "192.168.2.10", count=4)
    assert report.received == 4
    r1 = net.devices["r1"]
    assert isinstance(r1, Router)
    assert r1.forwarded > 0


def test_traceroute_reveals_each_hop():
    net = routed_chain()
    tr = net.traceroute("h1", "192.168.2.10")
    assert tr.reached
    addrs = [h["address"] for h in tr.as_dict()["hops"]]
    assert addrs == ["192.168.1.1", "10.0.12.2", "192.168.2.10"]


def test_ttl_expiry_generates_time_exceeded():
    net = routed_chain()
    net.start()
    h1 = net.devices["h1"]
    ident = h1.ping(net, IPv4Address("192.168.2.10"), count=1, ttl=1)
    net.run_for(5.0)
    rep = net.pings[ident]
    assert rep.received == 0
    assert rep.errors and "time-exceeded from 192.168.1.1" in rep.errors[0]


def test_no_route_generates_unreachable():
    net = routed_chain()
    report = net.ping("h1", "203.0.113.99", count=1)  # no route on r1
    assert report.received == 0
    assert report.errors and "unreachable" in report.errors[0]


def test_mtu_violation_generates_frag_needed():
    net = Network(seed=1)
    h1 = net.add_device(Host("h1"))
    r1 = net.add_device(Router("r1"))
    h2 = net.add_device(Host("h2"))
    net.connect("a", net.add_iface(h1, "eth0", ["10.0.1.1/24"]),
                net.add_iface(r1, "eth0", ["10.0.1.254/24"]))
    net.connect("b", net.add_iface(r1, "eth1", ["10.0.2.254/24"]),
                net.add_iface(h2, "eth0", ["10.0.2.1/24"]), mtu=576)
    h1.default_gateway = IPv4Address("10.0.1.254")
    h2.default_gateway = IPv4Address("10.0.2.254")
    net.start()
    ident = h1.ping(net, IPv4Address("10.0.2.1"), count=1, size=1400)
    net.run_for(5.0)
    rep = net.pings[ident]
    assert rep.received == 0
    assert rep.errors and "unreachable(code=4)" in rep.errors[0]
    assert net.drops.get("mtu_exceeded", 0) == 1


# ---------------------------------------------------------------------------
# Link realism: latency, loss, determinism, queueing
# ---------------------------------------------------------------------------

def test_latency_includes_serialization_and_propagation():
    net = Network(seed=0)
    h1 = net.add_device(Host("h1"))
    h2 = net.add_device(Host("h2"))
    # 10 Mbps, 5 ms one-way: 84B echo ≈ 0.07ms ser; RTT ≥ 10ms.
    net.connect(
        "slow",
        net.add_iface(h1, "eth0", ["10.9.0.1/30"]),
        net.add_iface(h2, "eth0", ["10.9.0.2/30"]),
        bandwidth_bps=10_000_000,
        delay=0.005,
    )
    report = net.ping("h1", "10.9.0.2", count=2)
    assert report.received == 2
    assert min(report.rtts_ms) >= 10.0
    # First echo also waits behind ARP resolution (~one extra RTT) — realistic.
    assert report.rtts_ms[0] > report.rtts_ms[1]
    assert max(report.rtts_ms) < 25.0


def test_loss_is_deterministic_for_a_seed():
    def run(seed: int) -> tuple[int, int]:
        net = Network(seed=seed)
        h1 = net.add_device(Host("h1"))
        h2 = net.add_device(Host("h2"))
        net.connect(
            "lossy",
            net.add_iface(h1, "eth0", ["10.9.0.1/30"]),
            net.add_iface(h2, "eth0", ["10.9.0.2/30"]),
            loss=0.3,
        )
        rep = net.ping("h1", "10.9.0.2", count=20, interval=0.1)
        return rep.sent, rep.received

    a = run(1)
    b = run(1)
    c = run(999)
    assert a == b                      # bit-for-bit reproducible
    assert 0 < a[1] < a[0]             # some but not all survive 30% loss
    assert 0 < c[1] < c[0]
    assert a[1] != c[1]                # different seed, different fate


def test_queue_overflow_tail_drops():
    net = Network(seed=5)
    h1 = net.add_device(Host("h1"))
    h2 = net.add_device(Host("h2"))
    i1 = net.add_iface(h1, "eth0", ["10.9.0.1/30"])
    i2 = net.add_iface(h2, "eth0", ["10.9.0.2/30"])
    i1.queue_depth = 2
    # Slow link so a burst overwhelms the 2-frame queue.
    net.connect("thin", i1, i2, bandwidth_bps=64_000)
    rep = net.ping("h1", "10.9.0.2", count=10, interval=0.0)
    assert net.drops.get("queue_overflow", 0) > 0
    assert rep.received < 10


def test_jitter_varies_rtt_but_stays_deterministic():
    def rtts(seed: int) -> list[float]:
        net = Network(seed=seed)
        h1 = net.add_device(Host("h1"))
        h2 = net.add_device(Host("h2"))
        net.connect(
            "jittery",
            net.add_iface(h1, "eth0", ["10.9.0.1/30"]),
            net.add_iface(h2, "eth0", ["10.9.0.2/30"]),
            delay=0.001,
            jitter=0.004,
        )
        return net.ping("h1", "10.9.0.2", count=5).rtts_ms

    a = rtts(11)
    assert len({round(r, 6) for r in a}) > 1   # jitter spreads RTTs
    assert a == rtts(11)                           # deterministic


# ---------------------------------------------------------------------------
# OSPF
# ---------------------------------------------------------------------------

def ospf_triangle() -> tuple[Network, dict[str, Router]]:
    """r1--r2--r3 triangle, a LAN behind r1 and r3."""
    net = Network(seed=9)
    routers = {name: net.add_device(Router(name)) for name in ("r1", "r2", "r3")}
    h1 = net.add_device(Host("h1"))
    h3 = net.add_device(Host("h3"))
    net.connect("r1r2", net.add_iface(routers["r1"], "eth0", ["10.0.12.1/30"]),
                net.add_iface(routers["r2"], "eth0", ["10.0.12.2/30"]))
    net.connect("r2r3", net.add_iface(routers["r2"], "eth1", ["10.0.23.2/30"]),
                net.add_iface(routers["r3"], "eth1", ["10.0.23.3/30"]))
    net.connect("r1r3", net.add_iface(routers["r1"], "eth1", ["10.0.13.1/30"]),
                net.add_iface(routers["r3"], "eth0", ["10.0.13.3/30"]))
    net.connect("lan1", net.add_iface(routers["r1"], "eth2", ["192.168.1.1/24"]),
                net.add_iface(h1, "eth0", ["192.168.1.10/24"]))
    net.connect("lan3", net.add_iface(routers["r3"], "eth2", ["192.168.3.1/24"]),
                net.add_iface(h3, "eth0", ["192.168.3.10/24"]))
    h1.default_gateway = IPv4Address("192.168.1.1")
    h3.default_gateway = IPv4Address("192.168.3.1")
    for name, rid in (("r1", "1.1.1.1"), ("r2", "2.2.2.2"), ("r3", "3.3.3.3")):
        OspfProcess(routers[name], router_id=rid, hello_interval=0.5)
    return net, routers


def test_ospf_full_adjacency_and_route_install():
    net, routers = ospf_triangle()
    net.start()
    net.run_for(10.0)
    p1 = routers["r1"].processes[0]
    states = {row["state"] for row in p1.neighbor_rows()}
    assert states == {"full"}
    ospf_routes = [r for r in routers["r1"].routes if r.source == "ospf"]
    prefixes = {str(r.prefix) for r in ospf_routes}
    assert "192.168.3.0/24" in prefixes
    report = net.ping("h1", "192.168.3.10", count=3)
    assert report.received == 3


def test_ospf_reroutes_around_link_failure():
    net, routers = ospf_triangle()
    net.start()
    net.run_for(10.0)
    # Direct path r1->r3 must be preferred initially (one hop).
    r = routers["r1"].lookup(IPv4Address("192.168.3.10"))
    assert r is not None and r.source == "ospf"
    assert str(r.next_hop) == "10.0.13.3"

    net.set_link_state("r1r3", up=False)
    net.run_for(15.0)  # dead interval (4 * 0.5s) + SPF + settle
    r_after = routers["r1"].lookup(IPv4Address("192.168.3.10"))
    assert r_after is not None and r_after.source == "ospf"
    assert str(r_after.next_hop) == "10.0.12.2"  # detour via r2
    report = net.ping("h1", "192.168.3.10", count=3)
    assert report.received == 3


# ---------------------------------------------------------------------------
# BGP
# ---------------------------------------------------------------------------

def test_bgp_session_and_route_exchange():
    net = Network(seed=2)
    r1 = net.add_device(Router("r1"))
    r2 = net.add_device(Router("r2"))
    net.connect("peering", net.add_iface(r1, "eth0", ["203.0.113.1/30"]),
                net.add_iface(r2, "eth0", ["203.0.113.2/30"]))
    net.add_iface(r1, "lo0", ["198.51.100.1/24"])
    net.add_iface(r2, "lo0", ["198.51.101.1/24"])
    p1 = BgpProcess(r1, asn=65001, router_id="1.1.1.1", keepalive_interval=1.0, hold_time=4.0)
    p2 = BgpProcess(r2, asn=65002, router_id="2.2.2.2", keepalive_interval=1.0, hold_time=4.0)
    p1.add_neighbor("203.0.113.2", 65002)
    p2.add_neighbor("203.0.113.1", 65001)
    p1.advertise_network("198.51.100.0/24")
    p2.advertise_network("198.51.101.0/24")
    net.start()
    net.run_for(10.0)

    assert all(row["state"] == "established" for row in p1.summary_rows())
    r = r1.lookup(IPv4Address("198.51.101.7"))
    assert r is not None and r.source == "ebgp"
    assert str(r.next_hop) == "203.0.113.2"
    # AS-path is recorded in the adj-rib-in.
    path = p1.peers[IPv4Address("203.0.113.2")].rib_in[IPv4Network("198.51.101.0/24")].as_path
    assert path == (65002,)

    report = net.ping("r1", "198.51.101.1", count=2)
    assert report.received == 2


def test_bgp_as_path_grows_across_transit():
    net = Network(seed=2)
    r1 = net.add_device(Router("r1"))
    r2 = net.add_device(Router("r2"))
    r3 = net.add_device(Router("r3"))
    net.connect("ab", net.add_iface(r1, "eth0", ["10.0.1.1/30"]),
                net.add_iface(r2, "eth0", ["10.0.1.2/30"]))
    net.connect("bc", net.add_iface(r2, "eth1", ["10.0.2.2/30"]),
                net.add_iface(r3, "eth0", ["10.0.2.3/30"]))
    net.add_iface(r1, "lo0", ["198.51.100.1/24"])
    procs = {
        "r1": BgpProcess(r1, asn=65001, router_id="1.1.1.1", keepalive_interval=1.0),
        "r2": BgpProcess(r2, asn=65002, router_id="2.2.2.2", keepalive_interval=1.0),
        "r3": BgpProcess(r3, asn=65003, router_id="3.3.3.3", keepalive_interval=1.0),
    }
    procs["r1"].add_neighbor("10.0.1.2", 65002)
    procs["r2"].add_neighbor("10.0.1.1", 65001)
    procs["r2"].add_neighbor("10.0.2.3", 65003)
    procs["r3"].add_neighbor("10.0.2.2", 65002)
    procs["r1"].advertise_network("198.51.100.0/24")
    net.start()
    net.run_for(15.0)

    rib = procs["r3"].peers[IPv4Address("10.0.2.2")].rib_in
    path = rib[IPv4Network("198.51.100.0/24")].as_path
    assert path == (65002, 65001)
    route = r3.lookup(IPv4Address("198.51.100.9"))
    assert route is not None and route.source == "ebgp" and route.metric == 2


# ---------------------------------------------------------------------------
# Services: DHCP, DNS, NAT, ACL
# ---------------------------------------------------------------------------

def test_dhcp_full_dora_cycle():
    net = Network(seed=4)
    h = net.add_device(Host("h1"))
    r = net.add_device(Router("gw"))
    net.connect("lan", net.add_iface(h, "eth0"),
                net.add_iface(r, "eth0", ["192.168.88.1/24"]))
    r.add_dhcp_pool(DhcpPool(
        network=IPv4Network("192.168.88.0/24"),
        gateway=IPv4Address("192.168.88.1"),
        dns=IPv4Address("192.168.88.1"),
    ))
    net.start()
    h.dhcp_discover(net)
    net.run_for(5.0)

    assert h.interfaces["eth0"].ips, "host must be bound"
    ip = h.interfaces["eth0"].ips[0]
    assert ip.ip in IPv4Network("192.168.88.0/24")
    assert h.default_gateway == IPv4Address("192.168.88.1")
    assert h.dns_server == IPv4Address("192.168.88.1")
    assert any(e["event"] == "dhcp.bound" for e in net.events_log)
    # And the address actually works.
    report = net.ping("h1", "192.168.88.1", count=2)
    assert report.received == 2


def test_dns_resolution_against_router_zone():
    net = Network(seed=4)
    h = net.add_device(Host("h1"))
    r = net.add_device(Router("gw"))
    net.connect("lan", net.add_iface(h, "eth0", ["192.168.88.10/24"]),
                net.add_iface(r, "eth0", ["192.168.88.1/24"]))
    h.default_gateway = IPv4Address("192.168.88.1")
    h.dns_server = IPv4Address("192.168.88.1")
    r.dns_zone["files.lab"] = IPv4Address("192.168.88.40")
    net.start()

    answers: list = []
    h.resolve(net, "files.lab", callback=answers.append)
    h.resolve(net, "missing.lab", callback=answers.append)
    net.run_for(5.0)
    assert IPv4Address("192.168.88.40") in answers
    assert None in answers
    assert h.dns_cache["files.lab"] == IPv4Address("192.168.88.40")


def test_nat_translates_source_and_restores_replies():
    net = Network(seed=6)
    inside = net.add_device(Host("pc"))
    gw = net.add_device(Router("gw"))
    server = net.add_device(Host("server"))
    net.connect("lan", net.add_iface(inside, "eth0", ["192.168.1.10/24"]),
                net.add_iface(gw, "eth0", ["192.168.1.1/24"]))
    net.connect("wan", net.add_iface(gw, "eth1", ["203.0.113.2/30"]),
                net.add_iface(server, "eth0", ["203.0.113.1/30"]))
    inside.default_gateway = IPv4Address("192.168.1.1")
    gw.enable_nat(inside=["eth0"], outside="eth1")
    # Note: the server has NO route back to 192.168.1.0/24 — only NAT makes
    # the reply possible.
    report = net.ping("pc", "203.0.113.1", count=3)
    assert report.received == 3
    rows = gw.nat_rows()
    assert rows and rows[0]["outside"].startswith("203.0.113.2:")
    # The outside capture must never show the private source address.
    wan_frames = net.capture.records(link_id="wan", limit=500)
    assert wan_frames
    assert not any("192.168.1.10" in r.layers.get("ipv4", {}).get("src", "") for r in wan_frames)


def test_acl_deny_blocks_and_permits_selectively():
    net = routed_chain()
    r1 = net.devices["r1"]
    assert isinstance(r1, Router)
    r1.acl_in["eth0"] = [
        AclRule(action="deny", proto="icmp", src=IPv4Network("192.168.1.10/32")),
        AclRule(action="permit"),
    ]
    report = net.ping("h1", "192.168.2.10", count=2)
    assert report.received == 0
    assert report.errors and "unreachable(code=13)" in report.errors[0]

    # Remove the deny -> traffic flows again.
    r1.acl_in["eth0"] = [AclRule(action="permit")]
    report2 = net.ping("h1", "192.168.2.10", count=2)
    assert report2.received == 2


# ---------------------------------------------------------------------------
# Fault injection & determinism at the run level
# ---------------------------------------------------------------------------

def test_link_down_stops_traffic_and_up_restores_it():
    net = routed_chain()
    rep1 = net.ping("h1", "192.168.2.10", count=2)
    assert rep1.received == 2
    net.set_link_state("core", up=False)
    rep2 = net.ping("h1", "192.168.2.10", count=2)
    assert rep2.received == 0
    net.set_link_state("core", up=True)
    rep3 = net.ping("h1", "192.168.2.10", count=2)
    assert rep3.received == 2


def test_device_power_off_stops_forwarding():
    net = routed_chain()
    net.set_device_power("r2", on=False)
    rep = net.ping("h1", "192.168.2.10", count=2)
    assert rep.received == 0
    net.set_device_power("r2", on=True)
    rep2 = net.ping("h1", "192.168.2.10", count=2)
    assert rep2.received == 2


def test_stats_snapshot_shape():
    net = routed_chain()
    net.ping("h1", "192.168.2.10", count=1)
    stats = net.stats()
    assert stats["devices"] == 4
    assert stats["links"] == 3
    assert stats["events_dispatched"] > 0
    assert stats["captured_frames"] > 0
