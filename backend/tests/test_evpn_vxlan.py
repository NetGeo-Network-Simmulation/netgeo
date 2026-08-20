"""EVPN/VXLAN overlay — NG-SIM-10.

Spine-leaf underlay; two VTEPs (leaf1/leaf2) + one spine. Hosts in VNI 10 span
the two leaves (bridged over VXLAN); a third host in VNI 20 proves isolation.

              h1 10.0.0.1/24 (VNI10)          h2 10.0.0.2/24 (VNI10)
                     |  eth2                          eth2 |
                   leaf1 --- eth0/eth0 --- spine --- eth1/eth0 --- leaf2
                     lo 10.255.0.1   10.0.12/30  10.0.23/30   lo 10.255.0.3
                                       spine lo 10.255.0.2      eth3 | (VNI20)
                                                              h3 10.0.0.3/24
"""
from __future__ import annotations

from ipaddress import IPv4Address

from engine.netstack import Network
from engine.netstack.device import Host
from engine.netstack.protocols.bgp import BgpProcess
from engine.netstack.protocols.ospf import OspfProcess
from engine.netstack.protocols.vxlan import VxlanProcess
from engine.netstack.routing import Router


def _lab() -> Network:
    net = Network(seed=13)
    leaf1 = net.add_device(Router("leaf1"))
    spine = net.add_device(Router("spine"))
    leaf2 = net.add_device(Router("leaf2"))

    # Underlay links + loopbacks (VTEP IPs).
    net.connect("u1", net.add_iface(leaf1, "eth0", ["10.0.12.1/30"]),
                net.add_iface(spine, "eth0", ["10.0.12.2/30"]))
    net.connect("u2", net.add_iface(spine, "eth1", ["10.0.23.1/30"]),
                net.add_iface(leaf2, "eth0", ["10.0.23.2/30"]))
    net.add_iface(leaf1, "lo0", ["10.255.0.1/32"])
    net.add_iface(spine, "lo0", ["10.255.0.2/32"])
    net.add_iface(leaf2, "lo0", ["10.255.0.3/32"])

    # Access ports (L2, no IP) — bridged into the overlay.
    net.add_iface(leaf1, "eth2")   # VNI 10 -> h1
    net.add_iface(leaf2, "eth2")   # VNI 10 -> h2
    net.add_iface(leaf2, "eth3")   # VNI 20 -> h3

    hosts = {
        "h1": ("10.0.0.1/24", leaf1, "eth2"),
        "h2": ("10.0.0.2/24", leaf2, "eth2"),
        "h3": ("10.0.0.3/24", leaf2, "eth3"),
    }
    for name, (cidr, leaf, port) in hosts.items():
        h = net.add_device(Host(name))
        net.connect(f"c_{name}", net.add_iface(h, "eth0", [cidr]), leaf.interfaces[port])

    # Underlay IGP: OSPF over core links + loopbacks (access ports excluded).
    OspfProcess(leaf1, router_id="10.255.0.1", hello_interval=1.0, ifaces=["eth0", "lo0"])
    OspfProcess(spine, router_id="10.255.0.2", hello_interval=1.0, ifaces=["eth0", "eth1", "lo0"])
    OspfProcess(leaf2, router_id="10.255.0.3", hello_interval=1.0, ifaces=["eth0", "lo0"])

    # iBGP leaf-to-leaf over loopbacks — carries the EVPN side-channel.
    for r, rid, peer in ((leaf1, "10.255.0.1", "10.255.0.3"),
                         (leaf2, "10.255.0.3", "10.255.0.1")):
        b = BgpProcess(r, asn=65000, router_id=rid, keepalive_interval=5.0, hold_time=20.0)
        b.add_neighbor(peer, 65000)

    # VTEPs: leaf1 has VNI 10; leaf2 has VNI 10 (h2) and VNI 20 (h3).
    v1 = VxlanProcess(leaf1, interval=2.0)
    v1.bind_access("eth2", 10)
    v2 = VxlanProcess(leaf2, interval=2.0)
    v2.bind_access("eth2", 10)
    v2.bind_access("eth3", 20)

    net.start()
    net.run(until=60.0)
    return net


def _vxlan(net: Network, name: str) -> VxlanProcess:
    return next(p for p in net.devices[name].processes if p.proto == "vxlan")


# ----- control plane: Type-3 IMET (ingress replication list) ---------------------

def test_type3_imet_populates_flood_list():
    net = _lab()
    v1, v2 = _vxlan(net, "leaf1"), _vxlan(net, "leaf2")
    # leaf1 learned leaf2 as the BUM target for VNI 10 (Type-3), and only VNI 10.
    assert IPv4Address("10.255.0.3") in v1.remote_vteps.get(10, set())
    # leaf2 learned leaf1 for VNI 10 but NOT for VNI 20 (leaf1 has no VNI 20).
    assert IPv4Address("10.255.0.1") in v2.remote_vteps.get(10, set())
    assert IPv4Address("10.255.0.1") not in v2.remote_vteps.get(20, set())


# ----- data plane: encap/decap, same-VNI reachability ---------------------------

def test_ping_same_vni_across_leaves():
    net = _lab()
    rep = net.ping("h1", "10.0.0.2", count=3)
    assert rep.received == 3, rep.as_dict()


def test_encap_decap_counted_on_underlay():
    net = _lab()
    net.ping("h1", "10.0.0.2", count=2)
    # The overlay actually rode the underlay: leaf1's core port carried frames
    # to the spine (VXLAN encap), not a direct leaf-to-leaf L2 path.
    assert net.devices["leaf1"].interfaces["eth0"].counters.tx_frames > 0
    assert net.devices["spine"].forwarded > 0


# ----- control plane: Type-2 MAC learning ---------------------------------------

def test_type2_mac_learning():
    net = _lab()
    net.ping("h1", "10.0.0.2", count=2)
    net.run_for(6.0)   # let EVPN Type-2 converge after the hosts spoke

    h1_mac = str(net.devices["h1"].interfaces["eth0"].mac)
    v1, v2 = _vxlan(net, "leaf1"), _vxlan(net, "leaf2")
    # leaf1 learned h1 locally (value = access port name).
    assert v1.mac_vni.get((10, h1_mac)) == "eth2"
    # leaf2 learned h1 via EVPN Type-2 (value = leaf1's VTEP IP), not by flooding.
    assert v2.mac_vni.get((10, h1_mac)) == IPv4Address("10.255.0.1")


# ----- isolation: different VNI must not reach --------------------------------

def test_vni_isolation():
    net = _lab()
    # h1 (VNI 10) and h3 (VNI 20) share a subnet but not a broadcast domain.
    assert net.ping("h1", "10.0.0.3", count=2).received == 0
    assert net.ping("h3", "10.0.0.1", count=2).received == 0
    # And a VNI-10 host cannot reach the VNI-20 host either.
    assert net.ping("h2", "10.0.0.3", count=2).received == 0


# ----- show + tables ------------------------------------------------------------

def test_show_and_tables():
    from engine.netstack.cli import CliSession

    net = _lab()
    net.ping("h1", "10.0.0.2", count=2)
    net.run_for(6.0)

    sess = CliSession(net, net.devices["leaf1"])
    evpn = sess.execute("show bgp evpn")
    assert "10" in evpn and "10.255.0" in evpn   # VNI 10 + a VTEP IP
    vtep = sess.execute("show vxlan vtep")
    assert "10.255.0.3" in vtep                  # remote VTEP for the flood list

    rows = _vxlan(net, "leaf1").mac_vni_rows()
    assert any(r["vni"] == 10 for r in rows)


# ----- determinism --------------------------------------------------------------

def test_replay_determinism():
    def build() -> Network:
        net = _lab()
        net.ping("h1", "10.0.0.2", count=2)
        return net

    a, b = build(), build()
    assert a.ledger.seq == b.ledger.seq
    assert a.ledger.hash() == b.ledger.hash()
