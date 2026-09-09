"""MPLS LDP-lite + basic L3VPN — NG-SIM-08.

Topology: two PEs joined by one P (label-switch router). Each PE has a CE in
VRF "red" and a CE in VRF "blue"; the core runs OSPF (loopbacks) + LDP, and the
PEs run iBGP over their loopbacks with a VPNv4 side-channel.

                 red 10.1.1.0/24                     red 10.1.2.0/24
      ce_r1 ---- eth1                                eth1 ---- ce_r2
                 pe1 --- eth0/eth0 --- P --- eth1/eth0 --- pe2
      ce_b1 ---- eth2   10.0.12.0/30    10.0.23.0/30  eth2 ---- ce_b2
                 blue 10.2.1.0/24                     blue 10.2.2.0/24

    loopbacks: pe1 10.255.0.1  P 10.255.0.2  pe2 10.255.0.3
"""
from __future__ import annotations

from ipaddress import IPv4Address

from engine.netstack import Network
from engine.netstack.device import Host
from engine.netstack.protocols.bgp import BgpProcess
from engine.netstack.protocols.mpls import L3vpnProcess, LdpProcess
from engine.netstack.protocols.ospf import OspfProcess
from engine.netstack.routing import Router


def _lab() -> Network:
    net = Network(seed=11)
    pe1 = net.add_device(Router("pe1"))
    p = net.add_device(Router("p"))
    pe2 = net.add_device(Router("pe2"))

    # Core links + loopbacks.
    net.connect("l12", net.add_iface(pe1, "eth0", ["10.0.12.1/30"]),
                net.add_iface(p, "eth0", ["10.0.12.2/30"]))
    net.connect("l23", net.add_iface(p, "eth1", ["10.0.23.1/30"]),
                net.add_iface(pe2, "eth0", ["10.0.23.2/30"]))
    net.add_iface(pe1, "lo0", ["10.255.0.1/32"])
    net.add_iface(p, "lo0", ["10.255.0.2/32"])
    net.add_iface(pe2, "lo0", ["10.255.0.3/32"])

    # CE-facing PE interfaces (in VRFs).
    net.add_iface(pe1, "eth1", ["10.1.1.1/24"])   # red
    net.add_iface(pe1, "eth2", ["10.2.1.1/24"])   # blue
    net.add_iface(pe2, "eth1", ["10.1.2.1/24"])   # red
    net.add_iface(pe2, "eth2", ["10.2.2.1/24"])   # blue

    # CEs (hosts).
    ces = {
        "ce_r1": ("10.1.1.10/24", "10.1.1.1", pe1, "eth1"),
        "ce_b1": ("10.2.1.10/24", "10.2.1.1", pe1, "eth2"),
        "ce_r2": ("10.1.2.10/24", "10.1.2.1", pe2, "eth1"),
        "ce_b2": ("10.2.2.10/24", "10.2.2.1", pe2, "eth2"),
    }
    for i, (name, (cidr, gw, pe, port)) in enumerate(ces.items()):
        ce = net.add_device(Host(name))
        ce.default_gateway = IPv4Address(gw)
        net.connect(f"c{i}", net.add_iface(ce, "eth0", [cidr]), pe.interfaces[port])

    # Core IGP: OSPF over core links + loopbacks only (VRF ports excluded).
    OspfProcess(pe1, router_id="10.255.0.1", hello_interval=1.0, ifaces=["eth0", "lo0"])
    OspfProcess(p, router_id="10.255.0.2", hello_interval=1.0, ifaces=["eth0", "eth1", "lo0"])
    OspfProcess(pe2, router_id="10.255.0.3", hello_interval=1.0, ifaces=["eth0", "lo0"])

    # LDP-lite on all three; distinct label spaces so swaps are observable.
    LdpProcess(pe1, label_base=16, interval=2.0)
    LdpProcess(p, label_base=100, interval=2.0)
    LdpProcess(pe2, label_base=200, interval=2.0)

    # iBGP between the PEs (loopback peering); P is a pure LSR.
    for pe, rid, peer in ((pe1, "10.255.0.1", "10.255.0.3"),
                          (pe2, "10.255.0.3", "10.255.0.1")):
        b = BgpProcess(pe, asn=65000, router_id=rid, keepalive_interval=5.0, hold_time=20.0)
        b.add_neighbor(peer, 65000)

    # L3VPN: red + blue on each PE.
    for pe, base in ((pe1, 1000), (pe2, 2000)):
        v = L3vpnProcess(pe, vpn_label_base=base, interval=2.0)
        v.add_vrf("blue", "65000:2", ["100:2"], ["100:2"])
        v.add_vrf("red", "65000:1", ["100:1"], ["100:1"])
        v.bind_iface("eth1", "red")
        v.bind_iface("eth2", "blue")

    net.start()
    net.run(until=60.0)
    return net


def _ldp(net: Network, name: str) -> LdpProcess:
    return next(p for p in net.devices[name].processes if p.proto == "ldp")


def _lab_chain() -> Network:
    """pe1 -- p1 -- p2 -- pe2: a genuine 2-hop LSR core, so p1 is a true
    transit hop (2 links from the egress) and p2 is the true penultimate hop
    -- unlike ``_lab()`` where the single P is always penultimate for every
    remote FEC."""
    net = Network(seed=13)
    pe1 = net.add_device(Router("pe1"))
    p1 = net.add_device(Router("p1"))
    p2 = net.add_device(Router("p2"))
    pe2 = net.add_device(Router("pe2"))

    net.connect("l1", net.add_iface(pe1, "eth0", ["10.0.10.1/30"]),
                net.add_iface(p1, "eth0", ["10.0.10.2/30"]))
    net.connect("l2", net.add_iface(p1, "eth1", ["10.0.11.1/30"]),
                net.add_iface(p2, "eth0", ["10.0.11.2/30"]))
    net.connect("l3", net.add_iface(p2, "eth1", ["10.0.12.1/30"]),
                net.add_iface(pe2, "eth0", ["10.0.12.2/30"]))
    net.add_iface(pe1, "lo0", ["10.255.1.1/32"])
    net.add_iface(p1, "lo0", ["10.255.1.2/32"])
    net.add_iface(p2, "lo0", ["10.255.1.3/32"])
    net.add_iface(pe2, "lo0", ["10.255.1.4/32"])

    net.add_iface(pe1, "eth2", ["10.1.1.1/24"])   # red (eth2 keeps eth1 free below)
    net.add_iface(pe2, "eth2", ["10.1.2.1/24"])   # red
    ce1 = net.add_device(Host("ce1"))
    ce1.default_gateway = IPv4Address("10.1.1.1")
    net.connect("c1", net.add_iface(ce1, "eth0", ["10.1.1.10/24"]), pe1.interfaces["eth2"])
    ce2 = net.add_device(Host("ce2"))
    ce2.default_gateway = IPv4Address("10.1.2.1")
    net.connect("c2", net.add_iface(ce2, "eth0", ["10.1.2.10/24"]), pe2.interfaces["eth2"])

    OspfProcess(pe1, router_id="10.255.1.1", hello_interval=1.0, ifaces=["eth0", "lo0"])
    OspfProcess(p1, router_id="10.255.1.2", hello_interval=1.0, ifaces=["eth0", "eth1", "lo0"])
    OspfProcess(p2, router_id="10.255.1.3", hello_interval=1.0, ifaces=["eth0", "eth1", "lo0"])
    OspfProcess(pe2, router_id="10.255.1.4", hello_interval=1.0, ifaces=["eth0", "lo0"])

    LdpProcess(pe1, label_base=16, interval=2.0)
    LdpProcess(p1, label_base=100, interval=2.0)
    LdpProcess(p2, label_base=150, interval=2.0)
    LdpProcess(pe2, label_base=200, interval=2.0)

    for pe, rid, peer in ((pe1, "10.255.1.1", "10.255.1.4"),
                          (pe2, "10.255.1.4", "10.255.1.1")):
        b = BgpProcess(pe, asn=65000, router_id=rid, keepalive_interval=5.0, hold_time=20.0)
        b.add_neighbor(peer, 65000)

    for pe, base in ((pe1, 1000), (pe2, 2000)):
        v = L3vpnProcess(pe, vpn_label_base=base, interval=2.0)
        v.add_vrf("red", "65000:1", ["100:1"], ["100:1"])
        v.bind_iface("eth2", "red")

    net.start()
    net.run(until=80.0)
    return net


# ----- label distribution -------------------------------------------------------

def test_ldp_distributes_labels_and_swaps():
    net = _lab()
    pe1, p, pe2 = net.devices["pe1"], net.devices["p"], net.devices["pe2"]

    # Every LSR allocated local labels (adjacency label exchange happened).
    for name in ("pe1", "p", "pe2"):
        assert _ldp(net, name).local, f"{name} allocated no labels"

    # pe1 has a transport LSP (FEC) to pe2's loopback via P: P is not pe2's
    # loopback FEC's origin, so it relayed a real (swap) label, not implicit-null.
    from ipaddress import IPv4Network
    fec = pe1.mpls_fec.get(IPv4Network("10.255.0.3/32"))
    assert fec is not None and fec.action == "swap"
    assert fec.nh_ip == IPv4Address("10.0.12.2")   # toward P

    # P sits directly next to both pe1 and pe2 in this 3-router lab, so it is
    # the penultimate hop for *every* remote FEC (PHP, not swap) -- see
    # test_transit_hop_still_swaps_only_penultimate_pops for a real transit hop.
    assert p.lfib
    assert all(e.action in ("pop", "php") for e in p.lfib.values())
    assert any(e.action == "php" and e.prefix == "10.255.0.3/32" for e in p.lfib.values())
    php_entry = next(e for e in p.lfib.values() if e.prefix == "10.255.0.3/32")
    assert php_entry.out_label is None

    # pe2 is the egress for its own loopback: a pop entry still exists locally
    # (defensive UHP fallback), even though nobody swaps a real label into it.
    assert any(e.action == "pop" and e.prefix == "10.255.0.3/32" for e in pe2.lfib.values())


def test_implicit_null_travels_only_one_hop():
    from ipaddress import IPv4Network

    net = _lab_chain()
    p1_ldp, p2_ldp = _ldp(net, "p1"), _ldp(net, "p2")
    egress_lo = IPv4Network("10.255.1.4/32")   # pe2's loopback
    p2_own_lo = IPv4Network("10.255.1.3/32")   # p2's own loopback

    # LDP ``remote`` is keyed by the neighbour's *link* IP (LDP has no
    # update-source-loopback -- see the module docstring), not its loopback.
    pe2_adj_ip = IPv4Address("10.0.12.2")   # pe2's address on the p2-pe2 link
    p2_adj_ip = IPv4Address("10.0.11.2")    # p2's address on the p1-p2 link

    # p2 (true penultimate hop for pe2) received Implicit Null from pe2...
    assert p2_ldp.remote[pe2_adj_ip][egress_lo] == 3
    # ...and, being egress for its OWN connected loopback, advertises Implicit
    # Null for that prefix upstream in turn.
    assert p1_ldp.remote[p2_adj_ip][p2_own_lo] == 3

    # p1 is a genuine transit hop (2 links from pe2): it gets p2's *real*
    # relayed label for the egress loopback, never the implicit-null value.
    relayed = p1_ldp.remote[p2_adj_ip][egress_lo]
    assert relayed != 3 and relayed >= 150


def test_transit_hop_still_swaps_only_penultimate_pops():
    from ipaddress import IPv4Network

    net = _lab_chain()
    p1, p2 = net.devices["p1"], net.devices["p2"]
    egress_lo = IPv4Network("10.255.1.4/32")

    fec_p1 = p1.mpls_fec.get(egress_lo)
    assert fec_p1 is not None and fec_p1.action == "swap"   # real transit, unaffected by PHP

    fec_p2 = p2.mpls_fec.get(egress_lo)
    assert fec_p2 is not None and fec_p2.action == "php"    # true penultimate hop
    assert fec_p2.out_label is None

    # End-to-end connectivity across the longer LSP is unchanged.
    assert net.ping("ce1", "10.1.2.10", count=3).received == 3


# ----- data plane: PHP wire proof ------------------------------------------------

def test_php_leaves_only_vpn_label_on_wire_to_egress():
    net = _lab()
    red = net.ping("ce_r1", "10.1.2.10", count=2)
    assert red.received == 2, red.as_dict()

    saw_single_label_on_penultimate_link = False
    for rec in net.capture.records(limit=100000):
        mpls = rec.layers.get("mpls")
        if mpls is None:
            continue
        # Implicit Null (3) is a control-plane advertisement value only -- it
        # must never appear inside a data-plane label stack.
        assert 3 not in mpls["labels"], f"implicit-null leaked onto the wire: {rec.as_dict()}"
        if rec.link_id == "l23" and rec.direction == "tx" and len(mpls["labels"]) == 1:
            saw_single_label_on_penultimate_link = True
    assert saw_single_label_on_penultimate_link, "P did not pop the transport label before pe2"


def test_php_pops_to_bare_ip_when_no_label_survives():
    """Without an underlying VPN label, PHP must strip the MPLS header
    entirely -- proven from the transmitted frame's own ethertype/layers,
    not just from the LFIB action string."""
    from ipaddress import IPv4Network

    from engine.netstack.frames import ETH_MPLS, EthernetFrame, Ipv4Packet, MplsPacket

    net = _lab()
    p = net.devices["p"]
    egress_lo = IPv4Network("10.255.0.3/32")
    p_ldp = _ldp(net, "p")
    local_label = p_ldp.local[egress_lo]
    assert p.lfib[local_label].action == "php"

    # TEST-NET-1 (RFC 5737) marker dst -- unreachable, so it can't collide with
    # any background OSPF/BGP/LDP traffic already queued on this link.
    marker_dst = "192.0.2.1"
    net.capture.clear()
    p.on_frame(
        net,
        p.interfaces["eth0"],
        EthernetFrame(
            src_mac="aa:aa:aa:aa:aa:01",
            dst_mac=p.interfaces["eth0"].mac,
            ethertype=ETH_MPLS,
            payload=MplsPacket(
                labels=[local_label],
                inner=Ipv4Packet(src=IPv4Address("10.255.0.1"), dst=IPv4Address(marker_dst)),
            ),
        ),
    )
    # A frame merely appended to an interface's tx queue is not captured until
    # the queue is drained -- run the sim a little further to flush it.
    net.run_for(1.0)

    frame_out = next(
        (
            r for r in net.capture.records(link_id="l23")
            if r.direction == "tx" and r.layers.get("ipv4", {}).get("dst") == marker_dst
        ),
        None,
    )
    assert frame_out is not None, "P did not forward the marker packet toward pe2"
    assert "mpls" not in frame_out.layers, "MPLS header still present after PHP"


def test_vpnv4_import_and_isolation_of_rib():
    net = _lab()
    pe1 = net.devices["pe1"]

    # pe1's red VRF learned pe2's red CE subnet via VPNv4, with a VPN label.
    red = pe1.vrfs["red"]
    r = next((x for x in red.routes if str(x.prefix) == "10.1.2.0/24"), None)
    assert r is not None and r.source == "vpnv4" and r.vpn_label is not None
    assert r.next_hop == IPv4Address("10.255.0.3")

    # Isolation in the tables: red never learns blue's remote subnet, and no CE
    # prefix leaks into the global RIB.
    assert not any(str(x.prefix) == "10.2.2.0/24" for x in red.routes)
    globals_ = {str(x.prefix) for x in pe1.routes}
    for ce_prefix in ("10.1.1.0/24", "10.1.2.0/24", "10.2.1.0/24", "10.2.2.0/24"):
        assert ce_prefix not in globals_, f"{ce_prefix} leaked to global RIB"


# ----- data plane ---------------------------------------------------------------

def test_ping_same_vrf_across_sites():
    net = _lab()
    red = net.ping("ce_r1", "10.1.2.10", count=3)
    assert red.received == 3, red.as_dict()
    blue = net.ping("ce_b1", "10.2.2.10", count=3)
    assert blue.received == 3, blue.as_dict()


def test_vrf_isolation_blocks_cross_vrf():
    net = _lab()
    # red CE must not reach a blue CE (different VRF, no imported route).
    assert net.ping("ce_r1", "10.2.2.10", count=2).received == 0
    assert net.ping("ce_b1", "10.1.2.10", count=2).received == 0


# ----- show commands ------------------------------------------------------------

def test_show_commands():
    from engine.netstack.cli import CliSession

    net = _lab()
    sess = CliSession(net, net.devices["pe1"])
    fwd = sess.execute("show mpls forwarding-table")
    assert "10.255.0.3/32" in fwd and ("Pop Label" in fwd or "Local" in fwd)

    vrf = sess.execute("show ip route vrf red")
    assert "VRF red" in vrf and "10.1.2.0/24" in vrf
    # The blue subnet must not appear in red's table.
    assert "10.2.2.0/24" not in vrf


# ----- determinism --------------------------------------------------------------

def test_replay_determinism():
    a, b = _lab(), _lab()
    assert a.ledger.seq == b.ledger.seq
    assert a.ledger.hash() == b.ledger.hash()
