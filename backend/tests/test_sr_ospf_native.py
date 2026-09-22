"""SR without LDP (A5.2) — OSPF is the sole control-plane input.

Same PE1-P-PE2(+P2 diamond) shape as test_sr.py, but with **no LdpProcess
anywhere** — proves SR converges, allocates adjacency-SIDs, and forwards
end-to-end from OSPF's Full-neighbor table + SPF-installed routes +
arp_table alone. Also covers PHP-vs-swap (RFC 8665 sec 4.1/4.2) and
link-down reconvergence, which test_sr.py's LDP-attached lab doesn't.

                       node-sids: pe1=101  p=100  pe2=102  (p2=104, diamond only)
                       loopbacks: pe1 10.255.0.1  p .2  pe2 .3  (p2 .4)
                       srgb_base=16000  adj_sid_base=15000

    pe1 --10.0.12.0/30-- p --10.0.23.0/30-- pe2 --10.20.0.0/24-- h_pe2
     \\__10.0.14.0/30__ p2 __10.0.45.0/30__/         (diamond adds p2 only)
"""
from __future__ import annotations

from ipaddress import IPv4Address

from engine.netstack import Network
from engine.netstack.device import Host
from engine.netstack.frames import IcmpMessage, Ipv4Packet
from engine.netstack.protocols.ospf import OspfProcess
from engine.netstack.protocols.sr import SrProcess
from engine.netstack.routing import Router

SRGB = 16000
ADJ = 15000


def _lab(diamond: bool = False) -> Network:
    net = Network(seed=31)
    pe1 = net.add_device(Router("pe1"))
    p = net.add_device(Router("p"))
    pe2 = net.add_device(Router("pe2"))

    net.connect("l1p", net.add_iface(pe1, "eth0", ["10.0.12.1/30"]),
                net.add_iface(p, "eth0", ["10.0.12.2/30"]))
    net.connect("lp2", net.add_iface(p, "eth1", ["10.0.23.1/30"]),
                net.add_iface(pe2, "eth0", ["10.0.23.2/30"]))
    net.add_iface(pe1, "lo0", ["10.255.0.1/32"])
    net.add_iface(p, "lo0", ["10.255.0.2/32"])
    net.add_iface(pe2, "lo0", ["10.255.0.3/32"])

    net.add_iface(pe2, "eth2", ["10.20.0.1/24"])
    h = net.add_device(Host("h_pe2"))
    h.default_gateway = IPv4Address("10.20.0.1")
    net.connect("lh", net.add_iface(h, "eth0", ["10.20.0.10/24"]), pe2.interfaces["eth2"])

    pe1_core, pe2_core = ["eth0", "lo0"], ["eth0", "lo0"]
    if diamond:
        p2 = net.add_device(Router("p2"))
        net.connect("l1q", net.add_iface(pe1, "eth1", ["10.0.14.1/30"]),
                    net.add_iface(p2, "eth0", ["10.0.14.2/30"]))
        net.connect("lq2", net.add_iface(p2, "eth1", ["10.0.45.1/30"]),
                    net.add_iface(pe2, "eth1", ["10.0.45.2/30"]))
        net.add_iface(p2, "lo0", ["10.255.0.4/32"])
        pe1_core, pe2_core = ["eth0", "eth1", "lo0"], ["eth0", "eth1", "lo0"]

    # OSPF + SR only -- no LdpProcess anywhere in this lab.
    ospf_pe1 = OspfProcess(pe1, router_id="10.255.0.1", hello_interval=1.0, ifaces=pe1_core)
    ospf_p = OspfProcess(p, router_id="10.255.0.2", hello_interval=1.0, ifaces=["eth0", "eth1", "lo0"])
    ospf_pe2 = OspfProcess(pe2, router_id="10.255.0.3", hello_interval=1.0, ifaces=pe2_core)
    SrProcess(pe1, ospf_pe1, node_sid=101)
    SrProcess(p, ospf_p, node_sid=100)
    SrProcess(pe2, ospf_pe2, node_sid=102)
    if diamond:
        p2 = net.devices["p2"]
        ospf_p2 = OspfProcess(p2, router_id="10.255.0.4", hello_interval=1.0,
                               ifaces=["eth0", "eth1", "lo0"])
        SrProcess(p2, ospf_p2, node_sid=104)

    net.start()
    net.run(until=60.0)
    return net


def _proc(net: Network, name: str, proto: str):
    return next(p for p in net.devices[name].processes if p.proto == proto)


# ----- SR converges with zero LdpProcess anywhere ---------------------------

def test_sr_without_ldp_converges():
    net = _lab()
    for dev in net.devices.values():
        assert not any(getattr(p, "proto", "") == "ldp" for p in getattr(dev, "processes", []))
    pe1 = net.devices["pe1"]
    # Own node-SID pops (LSP egress, UHP).
    assert pe1.lfib[SRGB + 101].action == "pop"


def test_node_sid_label_equals_next_hop_srgb_base_plus_index():
    net = _lab()
    pe1 = net.devices["pe1"]
    # pe1's entry toward pe2's node-SID: label = pe2's SRGB base (16000,
    # uniform in this lab) + pe2's Prefix-SID index (102).
    e = pe1.lfib[SRGB + 102]
    assert e.out_label == SRGB + 102 or e.action == "php"
    assert e.nh_ip == IPv4Address("10.0.12.2")


def test_adjacency_sid_from_ospf_full_neighbors():
    net = _lab()
    pe1, p, pe2 = net.devices["pe1"], net.devices["p"], net.devices["pe2"]
    assert len(pe1.sr_adj) == 1
    assert len(pe2.sr_adj) == 1
    assert len(p.sr_adj) == 2
    (entry,) = pe1.sr_adj.values()
    assert entry.out_iface == "eth0" and entry.peer_router_id == "10.0.12.2"
    assert entry.nh_mac  # resolved via arp_table, not a bespoke LDP table


# ----- PHP semantics (RFC 8665 sec 4.1/4.2) ---------------------------------

def test_penultimate_hop_pop_default():
    """P is directly adjacent to pe2 (the owner of node-SID 102) and pe2's
    Prefix-SID does not set NP -- default PHP applies: P's entry pops rather
    than swapping the (numerically unchanged) label through."""
    net = _lab()
    p = net.devices["p"]
    e = p.lfib[SRGB + 102]
    assert e.action == "php", e
    assert e.out_label is None
    assert e.nh_ip == IPv4Address("10.0.23.2")


def test_transit_hop_is_swap_not_php():
    """pe1 is two hops from pe2 (via P) -- not the penultimate hop -- so its
    entry stays a swap (numerically unchanged label, uniform SRGB)."""
    net = _lab()
    pe1 = net.devices["pe1"]
    e = pe1.lfib[SRGB + 102]
    assert e.action == "swap" and e.out_label == SRGB + 102


def test_no_php_flag_keeps_penultimate_hop_swapping():
    """pe2 advertises no_php=True (NP set): even its direct neighbor P must
    NOT pop early -- P keeps a swap entry so the label survives to pe2."""
    net = Network(seed=32)
    pe1 = net.add_device(Router("pe1"))
    p = net.add_device(Router("p"))
    pe2 = net.add_device(Router("pe2"))
    net.connect("l1p", net.add_iface(pe1, "eth0", ["10.0.12.1/30"]),
                net.add_iface(p, "eth0", ["10.0.12.2/30"]))
    net.connect("lp2", net.add_iface(p, "eth1", ["10.0.23.1/30"]),
                net.add_iface(pe2, "eth0", ["10.0.23.2/30"]))
    net.add_iface(pe1, "lo0", ["10.255.0.1/32"])
    net.add_iface(p, "lo0", ["10.255.0.2/32"])
    net.add_iface(pe2, "lo0", ["10.255.0.3/32"])
    ospf_pe1 = OspfProcess(pe1, router_id="10.255.0.1", hello_interval=1.0, ifaces=["eth0", "lo0"])
    ospf_p = OspfProcess(p, router_id="10.255.0.2", hello_interval=1.0, ifaces=["eth0", "eth1", "lo0"])
    ospf_pe2 = OspfProcess(pe2, router_id="10.255.0.3", hello_interval=1.0, ifaces=["eth0", "lo0"])
    SrProcess(pe1, ospf_pe1, node_sid=101)
    SrProcess(p, ospf_p, node_sid=100)
    SrProcess(pe2, ospf_pe2, node_sid=102, no_php=True)
    net.start()
    net.run(until=30.0)

    e = p.lfib[SRGB + 102]
    assert e.action == "swap" and e.out_label == SRGB + 102, e


# ----- end-to-end forwarding over the SR label path --------------------------

def test_end_to_end_forwarding_over_sr_label_path():
    """No explicit policy -- just push pe2's plain node-SID and let ordinary
    hop-by-hop LFIB lookups (swap at pe1, php at P, deliver at pe2) carry it,
    same as real SR-MPLS traffic with no adjacency-SID stacking."""
    net = _lab()
    sr = _proc(net, "pe1", "sr")
    inner = Ipv4Packet(src=IPv4Address("10.255.0.1"), dst=IPv4Address("10.20.0.10"),
                        payload=IcmpMessage(type=8, ident=9, seq=1))
    sr.install_policy(net, [SRGB + 102], inner)
    net.run_for(10.0)

    l1p = [r for r in net.capture.records(link_id="l1p", limit=500) if "mpls" in r.layers]
    assert any(r.layers["mpls"]["labels"] == [SRGB + 102] for r in l1p)

    lh = net.capture.records(link_id="lh", limit=500)
    assert any(r.layers.get("ipv4", {}).get("dst") == "10.20.0.10" and "icmp" in r.layers
               for r in lh)


# ----- link-down reconvergence relabels --------------------------------------

def test_link_down_reconvergence_relabels():
    """P powers off: pe1's swap toward pe2 via P must retract, and once OSPF
    reconverges over P2 a fresh swap toward pe2 via P2 must appear with the
    same label (uniform SRGB) but a different next hop/interface."""
    net = _lab(diamond=True)
    pe1 = net.devices["pe1"]
    before = pe1.lfib[SRGB + 102]

    net.set_device_power("p", on=False)
    net.run_for(30.0)   # dead_interval + several OSPF/SR tick cycles

    after = pe1.lfib.get(SRGB + 102)
    assert after is not None, "node-SID swap must be re-installed via P2"
    assert after.nh_ip == IPv4Address("10.0.14.2")
    assert after.out_iface == "eth1"
    assert (before.nh_ip, before.out_iface) != (after.nh_ip, after.out_iface)


# ----- determinism ------------------------------------------------------------

def test_replay_determinism_no_ldp():
    a, b = _lab(diamond=True), _lab(diamond=True)
    assert a.ledger.seq == b.ledger.seq
    assert a.ledger.hash() == b.ledger.hash()


def test_sr_path_deterministic_across_replay_no_ldp():
    a = _lab(diamond=True)
    b = _lab(diamond=True)
    ea = a.devices["pe1"].lfib[SRGB + 102]
    eb = b.devices["pe1"].lfib[SRGB + 102]
    assert ea.action == eb.action
    assert ea.nh_ip == eb.nh_ip
    assert ea.out_iface == eb.out_iface
    assert ea.nh_ip in (IPv4Address("10.0.12.2"), IPv4Address("10.0.14.2"))
