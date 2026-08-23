"""RSTP Proposal/Agreement rapid transition + edge ports -- RSTP-b (IEEE
802.1w / 802.1D-2004 §17.3).

Covers: a designated port not yet forwarding proposes; a bridge whose port
becomes root port on a superior proposal syncs its other non-edge
designated ports to discarding *before* replying with agreement, then
jumps straight to forwarding; the proposer does the same on receiving that
agreement; a port that never receives a BPDU (edge) forwards immediately
with no delay, and loses that status the moment a BPDU actually arrives;
and, if no agreement ever comes back, the pre-armed Forward Delay chain
from P-3 still lands the port on forwarding.

Several tests seed ``sw._enter_blocking(iface)`` + ``sw._not_edge.add(name)``
before starting the network. A freshly created port already defaults to
designated/forwarding (see test_stp_forward_delay.py) and a virgin port
(never having sent or received anything) reads as edge -- neither leaves
anything for the rapid-transition machinery to do. Forcing blocking + a
prior BPDU sighting simulates a bridge reconverging after a topology
change: a real, non-edge role transition, which is when this handshake
actually matters (same white-box rationale as test_stp_forward_delay.py's
docstring -- there's no public "force this port's role" API).
"""
from __future__ import annotations

from engine.netstack import Network
from engine.netstack.device import Host
from engine.netstack.frames import BpduFrame
from engine.netstack.iface import Interface
from engine.netstack.switching import STP_FORWARD_DELAY, Switch


def test_proposal_agreement_skips_forward_delay(monkeypatch):
    net = Network(seed=1)
    sw = net.add_device(Switch("sw", priority=4096))
    peer = net.add_device(Switch("peer", priority=8192))
    p = net.add_iface(sw, "gi0/1")
    q = net.add_iface(peer, "gi0/1")
    net.connect("l1", p, q)
    sw._enter_blocking(p)
    sw._not_edge.add(p.name)
    peer._enter_blocking(q)
    peer._not_edge.add(q.name)

    sent: list[BpduFrame] = []
    orig_transmit = Interface.transmit

    def spy(self, net, frame):
        if isinstance(frame.payload, BpduFrame):
            sent.append(frame.payload)
        return orig_transmit(self, net, frame)

    monkeypatch.setattr(Interface, "transmit", spy)

    net.start()
    t0 = net.now
    # Well under even a single Forward Delay, let alone the 2x a fresh
    # designated/root port would normally need.
    net.run(until=t0 + STP_FORWARD_DELAY - 0.5)

    assert p.stp_state == "forwarding"
    assert q.stp_state == "forwarding"
    assert any(b.proposal for b in sent), "expected a proposal-carrying BPDU on the wire"
    assert any(b.agreement for b in sent), "expected an agreement-carrying BPDU on the wire"


def test_sync_blocks_other_designated_ports_first(monkeypatch):
    net = Network(seed=7)
    core = net.add_device(Switch("core", priority=8192))
    leaf_a = net.add_device(Switch("leafA", priority=32768))
    leaf_b = net.add_device(Switch("leafB", priority=32768))

    c_a = net.add_iface(core, "gi0/1")
    a_c = net.add_iface(leaf_a, "gi0/1")
    net.connect("lA", c_a, a_c)
    c_b = net.add_iface(core, "gi0/2")
    b_c = net.add_iface(leaf_b, "gi0/1")
    net.connect("lB", c_b, b_c)

    net.start()
    net.run_for(0.01)  # let core<->leaf settle to designated+forwarding, non-edge
    assert c_a.stp_state == "forwarding" and c_a.stp_role == "designated"
    assert c_b.stp_state == "forwarding" and c_b.stp_role == "designated"

    # A lower-priority root bridge shows up on a new port after the fact --
    # a real topology change, exercising sync rather than fresh bring-up.
    root = net.add_device(Switch("root", priority=4096))
    c_r = net.add_iface(core, "gi0/3")
    r_c = net.add_iface(root, "gi0/1")
    net.connect("lR", c_r, r_c)
    root._enter_blocking(r_c)
    root._not_edge.add(r_c.name)

    seen_before_agreement: list[tuple[str, str]] = []
    orig_transmit = Interface.transmit

    def spy(self, net, frame):
        if self.device is core and isinstance(frame.payload, BpduFrame) and frame.payload.agreement:
            seen_before_agreement.append((c_a.stp_state, c_b.stp_state))
        return orig_transmit(self, net, frame)

    monkeypatch.setattr(Interface, "transmit", spy)

    root.start(net)
    net.run_for(0.01)

    assert seen_before_agreement, "core should have replied to root's proposal with agreement"
    # Both other designated ports were already forced to discarding by the
    # time the agreement went out -- no window where a loop could form.
    assert seen_before_agreement[0] == ("blocking", "blocking")
    assert c_r.stp_state == "forwarding"
    assert c_r.stp_role == "root"


def test_edge_port_forwards_immediately_and_reverts_on_bpdu():
    net = Network(seed=2)
    sw = net.add_device(Switch("sw"))
    h = net.add_device(Host("h"))
    p = net.add_iface(sw, "gi0/1")
    e = net.add_iface(h, "eth0", ["10.0.0.2/24"])
    net.connect("l1", p, e)

    # Force a real (non-default) transition so the immediate-forward below
    # is provably edge detection, not just the "fresh port already defaults
    # to forwarding" shortcut covered in test_stp_forward_delay.py.
    sw._enter_blocking(p)
    net.start()
    assert p.stp_role == "designated"
    assert p.stp_state == "forwarding"  # edge: no Listening/Learning wait

    # A BPDU shows up on this port (a switch got plugged in where a host
    # used to be) -- edge status is revoked for good, normal STP applies.
    bpdu = BpduFrame(
        root_id="4096.aa:aa:aa:aa:aa:01",
        root_cost=0,
        bridge_id="4096.aa:aa:aa:aa:aa:01",
        port_id=0,
    )
    sw._handle_bpdu(net, p, bpdu)
    assert p.name in sw._not_edge
    assert p.stp_role == "root"        # 4096 beats sw's default priority (32768)
    assert p.stp_state == "listening"  # real transition now, no more shortcut


def test_no_agreement_falls_back_to_forward_delay(monkeypatch):
    net = Network(seed=3)
    sw = net.add_device(Switch("sw", priority=4096))
    peer = net.add_device(Switch("peer", priority=8192))
    p = net.add_iface(sw, "gi0/1")
    q = net.add_iface(peer, "gi0/1")
    net.connect("l1", p, q)
    sw._enter_blocking(p)
    sw._not_edge.add(p.name)
    peer._enter_blocking(q)
    peer._not_edge.add(q.name)

    orig_transmit = Interface.transmit

    def drop_agreements(self, net, frame):
        if isinstance(frame.payload, BpduFrame) and frame.payload.agreement:
            return  # simulate a non-conformant peer / a lost reply
        return orig_transmit(self, net, frame)

    monkeypatch.setattr(Interface, "transmit", drop_agreements)

    net.start()
    t0 = net.now
    net.run(until=t0 + 2 * STP_FORWARD_DELAY - 0.1)
    assert p.stp_state != "forwarding"  # no agreement ever arrived

    net.run(until=t0 + 2 * STP_FORWARD_DELAY + 0.1)
    assert p.stp_state == "forwarding"  # ...but P-3's pre-armed chain still lands it
