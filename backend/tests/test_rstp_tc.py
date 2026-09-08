"""RSTP Topology Change -- P-? (IEEE 802.1D-2004 / 802.1w §17.31).

RSTP has no separate TCN BPDU (that's legacy STP compatibility mode, not
modelled here -- see switching.py's module docstring). Instead, a non-edge
port entering Forwarding is the *only* TC trigger; it flags TC on the
ordinary BPDU for one tcWhile (2x Hello Time), flooding outward on every
other designated port, and flushes its own MAC table (everywhere but the
originating/receiving port and any edge port). A bridge that receives a
TC-flagged BPDU does the same locally, cascading the flush through the tree.

Edge-port-loses-edge-on-BPDU (the other half of this slice's brief) is
already covered by test_rstp_rapid.py's
test_edge_port_forwards_immediately_and_reverts_on_bpdu -- that behaviour
predates this file (RSTP-b) and needed no new code, so it isn't repeated
here.

Tests drive ``_enter_blocking``/``_set_forwarding`` directly (same
white-box rationale as test_stp_forward_delay.py/test_rstp_rapid.py: no
public "force this port's role" API), and mark peer-facing ports non-edge
by hand where the peer switch has STP disabled and so never sends a BPDU
of its own (mirrors test_stp_forward_delay.py's ``_pair`` helper).
"""
from __future__ import annotations

from engine.netstack import Network
from engine.netstack.device import Host
from engine.netstack.frames import BpduFrame
from engine.netstack.iface import Interface
from engine.netstack.switching import STP_HELLO, STP_TC_WHILE, Switch


def _setup(seed: int) -> tuple[Network, Switch, Interface, Interface, Interface]:
    """``sw`` with three ports: ``p`` and ``q`` to passive (STP-disabled)
    peer switches -- marked non-edge by hand, standing in for two real
    bridge-facing links -- and ``e`` to a host, left as a genuine edge
    port."""
    net = Network(seed=seed)
    sw = net.add_device(Switch("sw"))
    peer_p = net.add_device(Switch("peerP", stp_enabled=False))
    peer_q = net.add_device(Switch("peerQ", stp_enabled=False))
    host = net.add_device(Host("host"))

    p = net.add_iface(sw, "gi0/1")
    net.connect("lp", p, net.add_iface(peer_p, "gi0/1"))
    q = net.add_iface(sw, "gi0/2")
    net.connect("lq", q, net.add_iface(peer_q, "gi0/1"))
    e = net.add_iface(sw, "gi0/3")
    net.connect("le", e, net.add_iface(host, "eth0", ["10.0.0.2/24"]))

    net.start()
    sw._not_edge.add(p.name)
    sw._not_edge.add(q.name)
    return net, sw, p, q, e


def _spy_bpdus(monkeypatch) -> list[tuple[str, BpduFrame]]:
    sent: list[tuple[str, BpduFrame]] = []
    orig_transmit = Interface.transmit

    def spy(self, net, frame):
        if isinstance(frame.payload, BpduFrame):
            sent.append((self.name, frame.payload))
        return orig_transmit(self, net, frame)

    monkeypatch.setattr(Interface, "transmit", spy)
    return sent


def test_non_edge_forwarding_flags_tc_on_other_ports_not_origin(monkeypatch):
    net, sw, p, q, _e = _setup(seed=11)
    sent = _spy_bpdus(monkeypatch)

    # p was up and designated; simulate a real topology change (a flap):
    # blocking, then back to forwarding.
    sw._enter_blocking(p, "designated")
    sw._set_forwarding(net, p)
    assert p.stp_state == "forwarding"

    t0 = net.now
    net.run(until=t0 + STP_HELLO + 0.1)  # let the next periodic hello fire

    p_bpdus = [b for name, b in sent if name == p.name]
    q_bpdus = [b for name, b in sent if name == q.name]
    assert q_bpdus and any(b.tc for b in q_bpdus), "other designated port must carry TC"
    assert p_bpdus and not any(b.tc for b in p_bpdus), "origin port must not see its own TC reflected back"


def test_tc_flushes_mac_table_except_origin_and_edge():
    net, sw, p, q, e = _setup(seed=12)
    sw.mac_table[(1, "aa:aa:aa:aa:aa:01")] = p.name
    sw.mac_table[(1, "aa:aa:aa:aa:aa:02")] = q.name
    sw.mac_table[(1, "aa:aa:aa:aa:aa:03")] = e.name

    sw._enter_blocking(p, "designated")
    sw._set_forwarding(net, p)

    assert (1, "aa:aa:aa:aa:aa:01") in sw.mac_table       # origin port: untouched
    assert (1, "aa:aa:aa:aa:aa:02") not in sw.mac_table   # other non-edge port: flushed
    assert (1, "aa:aa:aa:aa:aa:03") in sw.mac_table       # edge port: protected


def test_tc_while_expires_after_two_hello_intervals(monkeypatch):
    net, sw, p, q, _e = _setup(seed=13)
    net.run_for(0.05)  # clear of the t=0 hello so tcWhile's expiry never
    # lands on the exact same tick as a periodic hello (undefined tie order)
    sw._enter_blocking(p, "designated")
    sw._set_forwarding(net, p)

    sent = _spy_bpdus(monkeypatch)
    t0 = net.now  # tcWhile expires at t0 + STP_TC_WHILE

    net.run(until=t0 + STP_HELLO + 0.1)  # hello @ t0+2.0: well inside tcWhile
    assert any(b.tc for name, b in sent if name == q.name)
    sent.clear()

    net.run(until=t0 + STP_TC_WHILE + 0.1)  # hello @ t0+4.0: still just inside tcWhile
    assert any(b.tc for name, b in sent if name == q.name)
    sent.clear()

    net.run(until=t0 + STP_TC_WHILE + STP_HELLO + 0.1)  # hello @ t0+6.0: tcWhile long expired
    assert sent and not any(b.tc for _, b in sent), "TC flag must stop once tcWhile elapses"


def _run_tc_scenario(seed: int) -> list[tuple[float, str, bool, bool]]:
    net, sw, p, _q, _e = _setup(seed=seed)
    events: list[tuple[float, str, bool, bool]] = []
    orig_transmit = Interface.transmit

    def spy(self, net, frame):
        if isinstance(frame.payload, BpduFrame):
            events.append((net.now, self.name, frame.payload.tc, frame.payload.proposal))
        return orig_transmit(self, net, frame)

    Interface.transmit = spy
    try:
        net.run_for(0.05)
        sw._enter_blocking(p, "designated")
        sw._set_forwarding(net, p)
        net.run(until=net.now + 3 * STP_HELLO + 0.1)
    finally:
        Interface.transmit = orig_transmit
    return events


def test_tc_scenario_is_deterministic():
    assert _run_tc_scenario(seed=42) == _run_tc_scenario(seed=42)
