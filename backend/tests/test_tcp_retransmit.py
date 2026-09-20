"""TCP FSM (A2-3): loss-driven retransmission — RFC 6298 (RTO backoff) plus
the duplicate-segment handling that makes those retransmits harmless.

Behavioural, from outside the engine, same style as A2-1/A2-2: builds a
small lab, uses the engine's existing fault-injection primitive
(``Network.set_link_state`` — the same one BGP/OSPF/LAG link-failure tests
use) to drop specific segments, and asserts on connection state, retransmit
counts and packet capture.
"""
from __future__ import annotations

from ipaddress import IPv4Address

from engine.events import EventType
from engine.netstack.frames import PROTO_TCP, Ipv4Packet, TcpSegment
from engine.netstack.network import Network
from engine.netstack.tcp import (
    CLOSED,
    ESTABLISHED,
    LAST_ACK,
    MAX_RETRIES,
    RTO_INITIAL,
    SYN_RECEIVED,
    SYN_SENT,
    TIME_WAIT,
)
from tests.test_tcp_handshake import _client_server

_MOD32 = 1 << 32


def _step_until(net: Network, predicate, limit: int = 100) -> None:
    """Advance one scheduler event at a time until ``predicate()`` is true —
    lets a test cut the link at an exact point in the packet exchange (e.g.
    "a segment was just scheduled but not yet transmitted"), which a
    wall-clock ``run_for`` window can't reliably hit."""
    steps = 0
    while not predicate() and steps < limit:
        net.scheduler.run(max_events=1)
        steps += 1
    assert predicate(), f"predicate not reached within {limit} events"


def _tcp_flags(net: Network, link_id: str = "lan") -> list[dict]:
    """Delivered (rx) TCP segments only — every frame is also captured on
    the tx side, which would double-count each segment."""
    return [r.layers["tcp"] for r in net.capture.records(link_id=link_id, limit=500)
            if "tcp" in r.layers and r.direction == "rx"]


# ---------------------------------------------------------------------------
# SYN retransmit (SYN_SENT)
# ---------------------------------------------------------------------------

def test_dropped_syn_retransmits_at_rto_then_handshake_completes():
    net, _client, server = _client_server()
    server.tcp_listen(80)
    net.set_link_state("lan", up=False)
    conn = net.tcp_connect("client", IPv4Address("10.0.0.2"), 80, run_after=False)
    net.run_for(0.01)                          # the first SYN's tx hits the down link
    assert conn.state == SYN_SENT and conn.retx_total == 0

    net.set_link_state("lan", up=True)
    net.run_for(RTO_INITIAL + 0.5)              # RTO=1.0s fires the retransmit
    assert conn.state == ESTABLISHED
    assert conn.retx_total == 1

    syns = [f for f in _tcp_flags(net) if f["flags"] == "SYN"]
    seqs = {f["seq"] for f in syns}
    assert len(seqs) == 1, "retransmit must reuse the original ISN, not a new one"
    # SYN_SENT logged at t=0; the dropped first attempt leaves no capture
    # trace (link-down drops happen before the capture hook), so the next
    # observable state change is ESTABLISHED, exactly one RTO later.
    assert round(conn.transitions[-1][0] - conn.transitions[0][0], 1) == RTO_INITIAL


def test_persistent_syn_loss_backs_off_1_2_4_8_then_gives_up():
    net, client, _server = _client_server()
    net.ping("client", "10.0.0.2", count=1)     # prime ARP so its timers don't
    net.set_link_state("lan", up=False)         # interleave with the TCP retx timer
    t0 = net.now
    conn = net.tcp_connect("client", IPv4Address("10.0.0.2"), 80, run_after=False)

    fire_times = []
    net.scheduler.add_observer(
        lambda now, ev: fire_times.append(round(now, 6))
        if ev.type == EventType.TIMER and ev.node_id == client.node_id else None
    )
    net.run_for(200.0)

    assert conn.state == CLOSED
    assert conn.close_reason == "timeout"
    assert conn.retx_total == MAX_RETRIES == 6
    # cumulative fire times 1,3,7,15,31,63,123 -> intervals 1,2,4,8,16,32,60
    # (last one capped at RTO_MAX=60, one giveup check past the last retx).
    deltas = [round(t - t0, 3) for t in fire_times]
    assert deltas == [1.0, 3.0, 7.0, 15.0, 31.0, 63.0, 123.0]


# ---------------------------------------------------------------------------
# SYN-ACK retransmit (SYN_RECEIVED)
# ---------------------------------------------------------------------------

def test_persistent_syn_ack_loss_backs_off_then_server_gives_up():
    net, _client, server = _client_server()
    server.tcp_listen(80)
    net.ping("client", "10.0.0.2", count=1)     # prime ARP both directions first
    net.tcp_connect("client", IPv4Address("10.0.0.2"), 80, run_after=False)
    _step_until(net, lambda: bool(server.tcp_conns)
                and next(iter(server.tcp_conns.values())).state == SYN_RECEIVED)
    server_conn = next(iter(server.tcp_conns.values()))
    t0 = net.now
    net.set_link_state("lan", up=False)         # every SYN-ACK from here is lost

    fire_times = []
    net.scheduler.add_observer(
        lambda now, ev: fire_times.append(round(now, 6))
        if ev.type == EventType.TIMER and ev.node_id == server.node_id else None
    )
    net.run_for(200.0)

    assert server_conn.state == CLOSED
    assert server_conn.close_reason == "timeout"
    assert server_conn.retx_total == MAX_RETRIES
    deltas = [round(t - t0, 3) for t in fire_times]
    assert deltas == [1.0, 3.0, 7.0, 15.0, 31.0, 63.0, 123.0]


def test_lost_syn_ack_recovers_to_established_both_sides():
    """Outside-observable version: whichever side's own RTO fires first
    (client re-sends its SYN, server re-sends its SYN-ACK — both are
    legitimate per RFC 9293/6298, and either resend makes the peer's
    duplicate-segment handling recover the handshake), the connection must
    still reach ESTABLISHED on both ends."""
    net, _client, server = _client_server()
    server.tcp_listen(80)
    conn = net.tcp_connect("client", IPv4Address("10.0.0.2"), 80, run_after=False)
    _step_until(net, lambda: bool(server.tcp_conns)
                and next(iter(server.tcp_conns.values())).state == SYN_RECEIVED)
    net.set_link_state("lan", up=False)
    net.run_for(0.01)                            # the SYN-ACK's tx hits the down link
    net.set_link_state("lan", up=True)
    net.run_for(RTO_INITIAL + 0.5)

    assert conn.state == ESTABLISHED
    server_conn = next(iter(server.tcp_conns.values()))
    assert server_conn.state == ESTABLISHED
    # The dropped SYN-ACK itself leaves no capture trace (link-down drops
    # happen before the capture hook, same as the plain SYN case above), so
    # "a resend happened" is checked via retx_total, not delivered SYN-ACK
    # count. Deterministically (seed=1) it's the client's own SYN retx that
    # fires first here, crossing with the server's still-SYN_RECEIVED state
    # and recovering it via the duplicate-SYN path (server_conn.retx_total
    # stays 0) — exactly the "either side's RTO" case the docstring names.
    assert conn.retx_total >= 1 or server_conn.retx_total >= 1, \
        "expected at least one side's RTO to have fired to recover the lost SYN-ACK"
    synacks = [f for f in _tcp_flags(net) if f["flags"] == "SYN-ACK"]
    assert len(synacks) >= 1
    assert len({f["seq"] for f in synacks}) == 1, "resend must reuse the same ISN"


# ---------------------------------------------------------------------------
# Duplicate-segment handling (harmless retransmits)
# ---------------------------------------------------------------------------

def test_duplicate_syn_in_syn_received_resends_syn_ack_without_touching_retx_count():
    net, client, server = _client_server()
    server.tcp_listen(80)
    # A real client-side conn (not a bare hand-crafted packet): a SYN-ACK
    # answering an untracked port would bounce the peer's own CLOSED-state
    # RST handler (RFC 9293 §3.10.7.1) and tear the server's conn back down
    # before the test gets to look at it.
    conn = net.tcp_connect("client", IPv4Address("10.0.0.2"), 80, run_after=False)
    _step_until(net, lambda: bool(server.tcp_conns)
                and next(iter(server.tcp_conns.values())).state == SYN_RECEIVED)
    server_conn = next(iter(server.tcp_conns.values()))

    # Peer's own SYN retransmit crosses ours — same 4-tuple, same SYN, sent
    # before the real client conn (still SYN_SENT) has seen any SYN-ACK.
    client.send_ip(net, Ipv4Packet(
        src=IPv4Address("10.0.0.1"), dst=IPv4Address("10.0.0.2"), proto=PROTO_TCP,
        payload=TcpSegment(src_port=conn.local_port, dst_port=80, flags="SYN", seq=conn.iss),
    ))
    net.run_for(0.01)

    # The injected duplicate SYN's resulting SYN-ACK is what completes the
    # handshake on both ends here (the original SYN-ACK is still in flight
    # behind it) — the point under test is retx_total staying 0 throughout.
    assert conn.state == ESTABLISHED
    assert server_conn.state == ESTABLISHED
    assert server_conn.retx_total == 0         # a courtesy reply, not our own RTO firing
    synacks = [f for f in _tcp_flags(net) if f["flags"] == "SYN-ACK"]
    assert len(synacks) == 2
    assert synacks[0]["seq"] == synacks[1]["seq"] == server_conn.iss


def test_duplicate_syn_ack_in_established_reacks_without_state_change():
    net, _client, server = _client_server()
    server.tcp_listen(80)
    conn = net.tcp_connect("client", IPv4Address("10.0.0.2"), 80)
    server_conn = next(iter(server.tcp_conns.values()))
    assert conn.state == ESTABLISHED

    # Server's SYN-ACK crossed the client's ACK and got retransmitted.
    server.send_ip(net, Ipv4Packet(
        src=IPv4Address("10.0.0.2"), dst=IPv4Address("10.0.0.1"), proto=PROTO_TCP,
        payload=TcpSegment(src_port=80, dst_port=conn.local_port, flags="SYN-ACK",
                            seq=server_conn.iss, ack=server_conn.rcv_nxt),
    ))
    net.run_for(0.01)

    assert conn.state == ESTABLISHED           # unchanged
    acks = [f for f in _tcp_flags(net) if f["flags"] == "ACK"]
    assert len(acks) == 2                       # original handshake ACK + the re-ACK
    assert acks[0]["seq"] == acks[1]["seq"]


# ---------------------------------------------------------------------------
# FIN retransmit + duplicate FIN in TIME_WAIT
# ---------------------------------------------------------------------------

def test_lost_final_fin_ack_retransmits_fin_and_restarts_time_wait():
    net, _client, server = _client_server()
    server.tcp_listen(80)
    conn = net.tcp_connect("client", IPv4Address("10.0.0.2"), 80)
    server_conn = next(iter(server.tcp_conns.values()))
    net.tcp_close("client", IPv4Address("10.0.0.2"), 80)       # -> client FIN_WAIT2
    net.tcp_close("server", IPv4Address("10.0.0.1"), conn.local_port, run_after=False)
    assert server_conn.state == LAST_ACK

    # Step to the exact instant the client's FSM flips to TIME_WAIT — its
    # reply ACK is scheduled but not yet transmitted, so this is the window
    # to drop *only* that final ACK.
    _step_until(net, lambda: conn.state == TIME_WAIT)
    t0 = net.now
    net.set_link_state("lan", up=False)
    net.run_for(0.01)                           # the ACK's tx hits the down link
    net.set_link_state("lan", up=True)

    assert server_conn.state == LAST_ACK        # our ACK never arrived
    # Land just past the *original* 2xMSL deadline — still TIME_WAIT proves
    # the duplicate FIN (server's own RTO retransmit) restarted the timer.
    net.run(until=t0 + 2.0 + 0.05)
    assert conn.state == TIME_WAIT
    assert server_conn.retx_total == 1
    assert server_conn.state == CLOSED          # the restarted ACK got through

    net.run_for(3.0)
    assert conn.state == CLOSED


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------

def test_determinism_retransmit_scenario_rebuilt_twice_is_byte_identical():
    def run():
        net, _client, server = _client_server()
        server.tcp_listen(80)
        net.set_link_state("lan", up=False)
        conn = net.tcp_connect("client", IPv4Address("10.0.0.2"), 80, run_after=False)
        net.run_for(0.01)
        net.set_link_state("lan", up=True)
        net.run_for(RTO_INITIAL + 0.5)
        return conn.transitions, conn.retx_total, _tcp_flags(net)

    a = run()
    b = run()
    assert a == b
