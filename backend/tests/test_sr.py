r"""Segment Routing (SR-MPLS) — NG-SIM-09.

Core: the PE1–P–PE2 OSPF+LDP diamond from test_mpls_l3vpn, with an SrProcess on
every router. Node-SIDs come from an SRGB formula (label = srgb_base + node_sid),
adjacency-SIDs are auto-allocated one per LDP neighbor. A host sits behind PE2 so
an explicit-path packet can be observed being delivered end to end.

                       node-sids: pe1=101  p=100  pe2=102  (p2=104, diamond only)
      install_policy   loopbacks: pe1 10.255.0.1  p .2  pe2 .3  (p2 .4)
      [adj(pe1->p), node(pe2)]        srgb_base=16000  adj_sid_base=15000

    pe1 --10.0.12.0/30-- p --10.0.23.0/30-- pe2 --10.20.0.0/24-- h_pe2
     \__10.0.14.0/30__ p2 __10.0.45.0/30__/         (diamond adds p2 only)
"""
from __future__ import annotations

from ipaddress import IPv4Address

from engine.netstack import Network
from engine.netstack.device import Host
from engine.netstack.frames import IcmpMessage, Ipv4Packet
from engine.netstack.protocols.mpls import LdpProcess
from engine.netstack.protocols.ospf import OspfProcess
from engine.netstack.protocols.sr import SrProcess
from engine.netstack.routing import Router

SRGB = 16000
ADJ = 15000


def _lab(diamond: bool = False) -> Network:
    net = Network(seed=11)
    pe1 = net.add_device(Router("pe1"))
    p = net.add_device(Router("p"))
    pe2 = net.add_device(Router("pe2"))

    # Core: PE1 -- P -- PE2, plus loopbacks.
    net.connect("l1p", net.add_iface(pe1, "eth0", ["10.0.12.1/30"]),
                net.add_iface(p, "eth0", ["10.0.12.2/30"]))
    net.connect("lp2", net.add_iface(p, "eth1", ["10.0.23.1/30"]),
                net.add_iface(pe2, "eth0", ["10.0.23.2/30"]))
    net.add_iface(pe1, "lo0", ["10.255.0.1/32"])
    net.add_iface(p, "lo0", ["10.255.0.2/32"])
    net.add_iface(pe2, "lo0", ["10.255.0.3/32"])

    # A host behind PE2 (connected, not in the IGP) — the SR delivery target.
    net.add_iface(pe2, "eth2", ["10.20.0.1/24"])
    h = net.add_device(Host("h_pe2"))
    h.default_gateway = IPv4Address("10.20.0.1")
    net.connect("lh", net.add_iface(h, "eth0", ["10.20.0.10/24"]), pe2.interfaces["eth2"])

    pe1_core, pe2_core = ["eth0", "lo0"], ["eth0", "lo0"]
    if diamond:                       # add a second equal-cost path via P2
        p2 = net.add_device(Router("p2"))
        net.connect("l1q", net.add_iface(pe1, "eth1", ["10.0.14.1/30"]),
                    net.add_iface(p2, "eth0", ["10.0.14.2/30"]))
        net.connect("lq2", net.add_iface(p2, "eth1", ["10.0.45.1/30"]),
                    net.add_iface(pe2, "eth1", ["10.0.45.2/30"]))
        net.add_iface(p2, "lo0", ["10.255.0.4/32"])
        pe1_core, pe2_core = ["eth0", "eth1", "lo0"], ["eth0", "eth1", "lo0"]

    # IGP + LDP + SR — built once, with the final port lists.
    OspfProcess(pe1, router_id="10.255.0.1", hello_interval=1.0, ifaces=pe1_core)
    OspfProcess(p, router_id="10.255.0.2", hello_interval=1.0, ifaces=["eth0", "eth1", "lo0"])
    OspfProcess(pe2, router_id="10.255.0.3", hello_interval=1.0, ifaces=pe2_core)
    LdpProcess(pe1, label_base=16, interval=2.0)
    LdpProcess(p, label_base=100, interval=2.0)
    LdpProcess(pe2, label_base=200, interval=2.0)
    SrProcess(pe1, _ldp(pe1), node_sid=101)
    SrProcess(p, _ldp(p), node_sid=100)
    SrProcess(pe2, _ldp(pe2), node_sid=102)
    if diamond:
        p2 = net.devices["p2"]
        OspfProcess(p2, router_id="10.255.0.4", hello_interval=1.0,
                    ifaces=["eth0", "eth1", "lo0"])
        LdpProcess(p2, label_base=300, interval=2.0)
        SrProcess(p2, _ldp(p2), node_sid=104)

    net.start()
    net.run(until=60.0)
    return net


def _proc(net: Network, name: str, proto: str):
    return next(p for p in net.devices[name].processes if p.proto == proto)


def _ldp(dev: Router) -> LdpProcess:
    return next(p for p in dev.processes if p.proto == "ldp")


# ----- node-SID -----------------------------------------------------------------

def test_node_sid_installed_and_swapped():
    net = _lab()
    pe1, p, pe2 = net.devices["pe1"], net.devices["p"], net.devices["pe2"]

    # Every SR router pops its own node-SID label (label = srgb_base + node_sid).
    assert pe1.lfib[SRGB + 101].action == "pop"
    assert p.lfib[SRGB + 100].action == "pop"
    assert pe2.lfib[SRGB + 102].action == "pop"

    # pe1 has a node-SID swap toward pe2 via P, same label (uniform SRGB).
    e = pe1.lfib[SRGB + 102]
    assert e.action == "swap" and e.out_label == SRGB + 102
    assert e.nh_ip == IPv4Address("10.0.12.2")   # toward P's transit interface


def test_adjacency_sid_allocated_per_link():
    net = _lab()
    pe1, p, pe2 = net.devices["pe1"], net.devices["p"], net.devices["pe2"]

    # One adjacency-SID per LDP neighbor; labels in the adj_sid_base range.
    assert len(pe1.sr_adj) == 1   # pe1 <-> p
    assert len(pe2.sr_adj) == 1   # pe2 <-> p
    assert len(p.sr_adj) == 2     # p <-> pe1, p <-> pe2
    for dev in (pe1, p, pe2):
        assert all(ADJ <= lbl < ADJ + 100 for lbl in dev.sr_adj)

    # pe1's single adjacency-SID points out its core port toward P.
    (entry,) = pe1.sr_adj.values()
    assert entry.out_iface == "eth0" and entry.peer_router_id == "10.0.12.2"


def test_explicit_path_via_adjacency_sid_stack():
    net = _lab()
    pe1 = net.devices["pe1"]
    sr = _proc(net, "pe1", "sr")

    # adjacency-SID toward P + pe2's node-SID.
    adj_to_p = pe1.sr_adj_rows()[0]["label"]
    stack = [adj_to_p, SRGB + 102]
    inner = Ipv4Packet(src=IPv4Address("10.255.0.1"), dst=IPv4Address("10.20.0.10"),
                       payload=IcmpMessage(type=8, ident=7, seq=1))
    sr.install_policy(net, stack, inner)
    net.run_for(10.0)

    # On the wire into P the adjacency-SID has been popped: only the node-SID.
    l1p = [r for r in net.capture.records(link_id="l1p", limit=500) if "mpls" in r.layers]
    assert any(r.layers["mpls"]["labels"] == [SRGB + 102] for r in l1p), \
        [r.layers.get("mpls") for r in l1p]

    # And the inner packet is delivered to PE2's egress host.
    lh = net.capture.records(link_id="lh", limit=500)
    assert any(r.layers.get("ipv4", {}).get("dst") == "10.20.0.10"
               and "icmp" in r.layers for r in lh)


def test_sr_path_deterministic_across_replay():
    a = _lab(diamond=True)
    b = _lab(diamond=True)
    # Equal-cost pe1->pe2: the node-SID FEC must resolve to one stable next hop.
    ea = a.devices["pe1"].lfib[SRGB + 102]
    eb = b.devices["pe1"].lfib[SRGB + 102]
    assert ea.action == "swap" and ea.nh_ip == eb.nh_ip
    assert ea.out_iface == eb.out_iface
    assert ea.nh_ip in (IPv4Address("10.0.12.2"), IPv4Address("10.0.14.2"))


def test_show_segment_routing_commands():
    from engine.netstack.cli import CliSession

    net = _lab()
    sess_pe1 = CliSession(net, net.devices["pe1"])
    sids = sess_pe1.execute("show segment-routing sid-database")
    for label in (SRGB + 101, SRGB + 100, SRGB + 102):
        assert str(label) in sids
    assert "10.255.0.3/32" in sids

    adj = sess_pe1.execute("show segment-routing adjacency-sid")
    assert str(ADJ) in adj and "eth0" in adj

    # The MPLS forwarding table holds both LDP FEC and SR node-SID entries.
    sess_p = CliSession(net, net.devices["p"])
    fwd = sess_p.execute("show mpls forwarding-table")
    assert str(SRGB + 102) in fwd            # SR node-SID entry
    assert "10.255.0.3/32" in fwd            # LDP FEC entry (same table)


def test_stale_node_sid_swap_withdrawn_when_source_powered_off():
    """F44: pe1's node-SID swap toward pe2 must be retracted once OSPF has
    withdrawn the route to pe2's loopback (pe2 powered off) — otherwise a
    node-SID-tagged packet keeps getting swapped toward a next hop that no
    longer leads anywhere."""
    net = _lab()
    pe1 = net.devices["pe1"]
    assert (SRGB + 102) in pe1.lfib   # sanity: swap entry present while pe2 is up

    net.set_device_power("pe2", on=False)
    net.run_for(30.0)   # dead_interval (4s) + several OSPF/LDP/SR tick cycles

    # OSPF must have withdrawn the route to pe2's loopback by now.
    assert pe1.lookup(IPv4Address("10.255.0.3")) is None
    # The SR swap entry for pe2's node-SID must be retracted along with it.
    assert (SRGB + 102) not in pe1.lfib, pe1.lfib.get(SRGB + 102)


def test_replay_determinism():
    a, b = _lab(), _lab()
    assert a.ledger.seq == b.ledger.seq
    assert a.ledger.hash() == b.ledger.hash()
