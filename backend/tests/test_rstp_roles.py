"""RSTP alternate/backup port roles + BPDU role flags — RSTP-a (IEEE 802.1w
/ 802.1D-2004 §17).

Covers: a discarding port whose superior BPDU comes from a *different*
neighbour bridge than the root port's is alternate; one whose superior BPDU
comes from the *same* neighbour bridge the root port already uses is
backup; and the periodic BPDU a designated port sends carries its own
role/learning/forwarding flags.
"""
from __future__ import annotations

from engine.netstack import Network
from engine.netstack.device import Host
from engine.netstack.frames import BpduFrame
from engine.netstack.iface import Interface
from engine.netstack.switching import Switch


def test_alternate_port_on_redundant_path():
    """Triangle of 3 switches (a loop): the non-root-port link on the
    switch farthest from root hears its superior BPDU from a *different*
    neighbour bridge than its root port -> alternate. Traffic still gets
    through end to end, which it couldn't if the loop weren't broken."""
    net = Network(seed=3)
    h1 = net.add_device(Host("h1"))
    h2 = net.add_device(Host("h2"))
    sws = [net.add_device(Switch(f"sw{i}")) for i in (1, 2, 3)]
    for a, b in ((0, 1), (1, 2), (0, 2)):
        pa = net.add_iface(sws[a], f"gi0/{b + 10}")
        pb = net.add_iface(sws[b], f"gi0/{a + 10}")
        net.connect(f"isl-{a}{b}", pa, pb)
    net.connect("h1", net.add_iface(h1, "eth0", ["10.0.0.1/24"]),
                net.add_iface(sws[0], "gi0/1"))
    net.connect("h2", net.add_iface(h2, "eth0", ["10.0.0.2/24"]),
                net.add_iface(sws[1], "gi0/1"))

    net.start()
    net.run_for(12.0)  # let STP elect and settle (matches test_netstack.py's
    # equivalent triangle, same constants)

    roles = [
        (sw.name, name, i.stp_role, i.stp_state)
        for sw in sws
        for name, i in sw.interfaces.items()
    ]
    alt = [r for r in roles if r[2] == "alternate"]
    assert len(alt) == 1, f"expected exactly one alternate port, got {roles}"
    assert alt[0][3] == "blocking"
    # No other port is left in a discarding role that isn't root/designated.
    discarding = [r for r in roles if r[3] == "blocking"]
    assert discarding == [alt[0]]

    # No loop: traffic still crosses the triangle end to end.
    report = net.ping("h1", "10.0.0.2", count=3)
    assert report.received == 3


def test_backup_port_on_same_segment():
    """Two parallel links between the same two switches: the losing port's
    superior BPDU comes from the *same* neighbour bridge the root port
    already reaches -> backup, not alternate (which is reserved for a path
    via a genuinely different neighbour bridge)."""
    net = Network(seed=5)
    sw = net.add_device(Switch("sw", priority=8192))
    root = net.add_device(Switch("root", priority=4096))
    p1, p2 = net.add_iface(sw, "gi0/1"), net.add_iface(sw, "gi0/2")
    r1, r2 = net.add_iface(root, "gi0/1"), net.add_iface(root, "gi0/2")
    net.connect("l1", p1, r1)
    net.connect("l2", p2, r2)
    net.start()
    net.run_for(0.5)  # one BPDU round-trip settles this (no forward delay
    # involved in the losing direction — see test_stp_forward_delay.py)

    losers = [i for i in (p1, p2) if i.stp_state == "blocking"]
    assert len(losers) == 1, f"expected exactly one blocked port, got {losers}"
    assert losers[0].stp_role == "backup"
    winner = p1 if losers[0] is p2 else p2
    assert winner.stp_role == "root"


def test_bpdu_carries_role_and_state_flags(monkeypatch):
    net = Network(seed=9)
    sw = net.add_device(Switch("sw"))
    peer = net.add_device(Switch("peer"))
    p = net.add_iface(sw, "gi0/1")
    q = net.add_iface(peer, "gi0/1")
    net.connect("l1", p, q)

    sent: list[BpduFrame] = []
    orig_transmit = Interface.transmit

    def spy(self, net, frame):
        if isinstance(frame.payload, BpduFrame):
            sent.append(frame.payload)
        return orig_transmit(self, net, frame)

    monkeypatch.setattr(Interface, "transmit", spy)
    net.start()  # each switch's first _hello() fires synchronously

    mine = [b for b in sent if b.bridge_id == sw.bridge_id]
    assert mine, f"sw should have sent a BPDU, got {sent}"
    bpdu = mine[0]
    assert bpdu.port_role == p.stp_role == "designated"
    assert bpdu.learning == (p.stp_state in ("learning", "forwarding"))
    assert bpdu.forwarding == (p.stp_state == "forwarding")
    # Rapid-transition handshake fields ride along but aren't used yet.
    assert bpdu.proposal is False
    assert bpdu.agreement is False
