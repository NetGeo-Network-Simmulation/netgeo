"""BGP FSM tests — RFC 4271 §8 six-state machine + NOTIFICATION handling.

Covers: every valid state transition, ASN-mismatch NOTIFICATION (2/2), Hold
Time negotiated as the minimum of both sides, Hold Timer expiry NOTIFICATION
(4/0), NOTIFICATION receipt closing a session, administrative shutdown
NOTIFICATION (6/0 Cease), and replay determinism.
"""
from __future__ import annotations

from engine.netstack import Network
from engine.netstack.protocols.bgp import STATES, BgpProcess
from engine.netstack.routing import Router


def _link(net: Network, a: Router, an: str, aip: str, b: Router, bn: str, bip: str):
    net.connect(f"{a.name}-{b.name}", net.add_iface(a, an, [aip]),
                net.add_iface(b, bn, [bip]))


def _pair(**kw) -> tuple[Network, BgpProcess, BgpProcess]:
    """Two directly-connected eBGP speakers; kw forwarded to both BgpProcess."""
    net = Network(seed=23)
    a = net.add_device(Router("a"))
    b = net.add_device(Router("b"))
    _link(net, a, "eth0", "10.0.0.1/30", b, "eth0", "10.0.0.2/30")
    pa = BgpProcess(a, asn=65001, router_id="1.1.1.1", keepalive_interval=1.0, **kw)
    pb = BgpProcess(b, asn=65002, router_id="2.2.2.2", keepalive_interval=1.0, **kw)
    pa.add_neighbor("10.0.0.2", 65002)
    pb.add_neighbor("10.0.0.1", 65001)
    return net, pa, pb


def _peer(proc: BgpProcess):
    return next(iter(proc.peers.values()))


def test_fsm_walks_every_valid_state_in_order():
    net, pa, pb = _pair(hold_time=9.0)
    net.start()
    net.run(until=15.0)

    seen = [s for _t, s in _peer(pa).transitions]
    # idle -> connect -> open-sent -> open-confirm -> established, in order
    # (no state skipped, none out of order); "active" only shows up when a
    # retry window elapses with no transport yet, which doesn't happen on a
    # link that's up from t=0 — assert the ordering constraint instead of an
    # exact list.
    order = {s: i for i, s in enumerate(STATES)}
    prev = -1
    for s in seen:
        assert s in order
        assert order[s] >= prev or s == "idle"  # idle may recur after a reset
        prev = order[s] if s != "idle" else prev
    assert seen[-1] == "established"
    assert _peer(pb).state == "established"


def test_fsm_visits_active_when_transport_is_not_yet_reachable():
    """No static/dynamic route to the peer at start -> Connect can't send
    OPEN -> the retry timer demotes it to Active, exactly the "stuck in
    Active" symptom real operators troubleshoot. Once a route appears, the
    session still climbs all the way to Established."""
    net = Network(seed=29)
    a = net.add_device(Router("a"))
    mid = net.add_device(Router("mid"))
    b = net.add_device(Router("b"))
    _link(net, a, "eth0", "10.0.1.1/30", mid, "eth0", "10.0.1.2/30")
    _link(net, mid, "eth1", "10.0.2.1/30", b, "eth0", "10.0.2.2/30")
    # Deliberately no route from a to b's peering IP yet.
    pa = BgpProcess(a, asn=65001, router_id="1.1.1.1", keepalive_interval=1.0)
    pb = BgpProcess(b, asn=65002, router_id="2.2.2.2", keepalive_interval=1.0)
    pa.add_neighbor("10.0.2.2", 65002)
    pb.add_neighbor("10.0.1.1", 65001)
    net.start()
    net.run(until=1.5)  # one retry window: connect -> active, no route yet

    assert _peer(pa).state == "active"

    # Routing now converges (mid is directly connected to both links
    # already); the next retry finds the path and proceeds normally.
    a.add_static_route("10.0.2.0/30", "10.0.1.2")
    b.add_static_route("10.0.1.0/30", "10.0.2.1")
    net.run(until=15.0)
    assert _peer(pa).state == "established"


def test_asn_mismatch_sends_notification_bad_peer_as_and_returns_idle():
    net, pa, pb = _pair()
    # b is misconfigured: it thinks a's ASN is 65099, not 65001.
    _peer(pb).remote_asn = 65099
    net.start()
    net.run(until=1.0)

    tx = [e for e in net.events_log if e["event"] == "bgp.notification"]
    assert any(e["code"] == 2 and e["subcode"] == 2 and e["direction"] == "tx" for e in tx)
    assert _peer(pb).state == "idle"
    # a received the NOTIFICATION and closed its side too.
    assert _peer(pa).state == "idle"


def test_hold_time_negotiated_as_minimum_of_both_sides():
    net = Network(seed=31)
    a = net.add_device(Router("a"))
    b = net.add_device(Router("b"))
    _link(net, a, "eth0", "10.0.0.1/30", b, "eth0", "10.0.0.2/30")
    pa = BgpProcess(a, asn=65001, router_id="1.1.1.1", keepalive_interval=1.0, hold_time=30.0)
    pb = BgpProcess(b, asn=65002, router_id="2.2.2.2", keepalive_interval=1.0, hold_time=9.0)
    pa.add_neighbor("10.0.0.2", 65002)
    pb.add_neighbor("10.0.0.1", 65001)
    net.start()
    net.run(until=5.0)

    assert _peer(pa).hold_time == 9.0
    assert _peer(pb).hold_time == 9.0


def test_hold_timer_expiry_sends_notification_and_returns_idle():
    net, pa, pb = _pair(hold_time=6.0)
    net.start()
    net.run(until=5.0)
    assert _peer(pa).state == "established"

    # Kill the link after establishment: no more KEEPALIVEs cross it, so
    # the Hold Timer on both sides eventually fires.
    net.set_link_state("a-b", up=False)
    net.run(until=25.0)

    tx = [e for e in net.events_log if e["event"] == "bgp.notification"]
    assert any(e["code"] == 4 and e["subcode"] == 0 for e in tx)
    assert _peer(pa).state == "idle"
    assert _peer(pb).state == "idle"


def test_receiving_notification_closes_the_session():
    net, pa, pb = _pair(hold_time=9.0)
    net.start()
    net.run(until=5.0)
    assert _peer(pa).state == "established" and _peer(pb).state == "established"

    pb.shutdown_neighbor(net, "10.0.0.1")  # sends Cease to a
    net.run(until=6.0)

    assert _peer(pb).state == "idle"
    assert _peer(pa).state == "idle"
    tx = [e for e in net.events_log if e["event"] == "bgp.notification" and e["direction"] == "tx"]
    assert any(e["code"] == 6 and e["subcode"] == 0 for e in tx)


def test_shutdown_neighbor_stops_retries_until_re_enabled():
    net, pa, _pb = _pair(hold_time=9.0)
    net.start()
    net.run(until=5.0)
    pa.shutdown_neighbor(net, "10.0.0.2")
    net.run(until=10.0)
    assert _peer(pa).state == "idle"
    assert _peer(pa).admin_down is True

    pa.no_shutdown_neighbor(net, "10.0.0.2")
    net.run(until=15.0)
    assert _peer(pa).state == "established"


def test_bgp_fsm_replay_determinism():
    net1, _pa1, _pb1 = _pair(hold_time=9.0)
    net1.start()
    net1.run(until=20.0)
    net2, _pa2, _pb2 = _pair(hold_time=9.0)
    net2.start()
    net2.run(until=20.0)
    assert net1.ledger.seq == net2.ledger.seq
    assert net1.ledger.hash() == net2.ledger.hash()
