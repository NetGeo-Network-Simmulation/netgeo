"""TCP FSM (A2-2): connection teardown + simultaneous open — RFC 9293 §3.5
(simultaneous open), §3.5.4/§3.6 (active/passive/simultaneous close).

Engine level: exact seq/ack + state-sequence assertions, driven straight
through :meth:`Network.tcp_connect`/:meth:`Network.tcp_close` like A2-1's
handshake tests. HTTP level (exit gate, mirrors R1's
``test_seek_step_back_reproduces_events_byte_identically``): the same
teardown driven through the CLI console endpoint, then seek/step-back
reproducing events byte-identically.
"""
from __future__ import annotations

from ipaddress import IPv4Address

import pytest

from app.services.netlab import get_lab_manager
from engine.netstack.device import Host
from engine.netstack.frames import PROTO_TCP, Ipv4Packet, TcpSegment
from engine.netstack.network import Network
from engine.netstack.tcp import (
    CLOSE_WAIT,
    CLOSED,
    CLOSING,
    ESTABLISHED,
    FIN_WAIT1,
    FIN_WAIT2,
    LAST_ACK,
    SYN_RECEIVED,
    SYN_SENT,
    TIME_WAIT,
    TIME_WAIT_DURATION,
)
from tests.test_tcp_handshake import _client_server

_MOD32 = 1 << 32


@pytest.fixture(autouse=True)
def _fresh_labs():
    get_lab_manager()._labs.clear()
    yield
    get_lab_manager()._labs.clear()


# ---------------------------------------------------------------------------
# Engine level
# ---------------------------------------------------------------------------

def test_active_close_then_passive_finish_exact_seq_ack_both_sides():
    net, _client, server = _client_server()
    server.tcp_listen(80)
    conn = net.tcp_connect("client", IPv4Address("10.0.0.2"), 80)
    server_conn = next(iter(server.tcp_conns.values()))
    assert conn.state == ESTABLISHED and server_conn.state == ESTABLISHED

    client_fin_seq = conn.snd_nxt
    closed = net.tcp_close("client", IPv4Address("10.0.0.2"), 80, run_after=False)
    assert closed == 1
    assert conn.state == FIN_WAIT1
    assert conn.snd_nxt == (client_fin_seq + 1) % _MOD32   # FIN consumed one seq

    net.run_for(2.0)
    assert conn.state == FIN_WAIT2                          # our FIN got ACKed
    assert server_conn.state == CLOSE_WAIT                  # ACKed, waiting for app
    assert server_conn.rcv_nxt == (client_fin_seq + 1) % _MOD32

    net.run_for(3.0)
    assert server_conn.state == CLOSE_WAIT, "must not auto-advance without app close()"

    server_fin_seq = server_conn.snd_nxt
    net.tcp_close("server", IPv4Address("10.0.0.1"), conn.local_port, run_after=False)
    assert server_conn.state == LAST_ACK
    assert server_conn.snd_nxt == (server_fin_seq + 1) % _MOD32

    net.run_for(2.0)
    assert server_conn.state == CLOSED
    assert conn.state == TIME_WAIT
    assert conn.rcv_nxt == (server_fin_seq + 1) % _MOD32

    net.run_for(TIME_WAIT_DURATION + 1.0)
    assert conn.state == CLOSED


def test_simultaneous_close_via_closing_then_time_wait_expiry():
    net, _client, server = _client_server()
    server.tcp_listen(80)
    conn = net.tcp_connect("client", IPv4Address("10.0.0.2"), 80)
    server_conn = next(iter(server.tcp_conns.values()))

    client_fin_seq = conn.snd_nxt
    server_fin_seq = server_conn.snd_nxt
    net.tcp_close("client", IPv4Address("10.0.0.2"), 80, run_after=False)
    net.tcp_close("server", IPv4Address("10.0.0.1"), conn.local_port, run_after=False)
    assert conn.state == FIN_WAIT1 and server_conn.state == FIN_WAIT1

    net.run_for(TIME_WAIT_DURATION + 3.0)   # let the whole crossing teardown play out
    assert conn.state == CLOSED
    assert server_conn.state == CLOSED
    assert conn.rcv_nxt == (server_fin_seq + 1) % _MOD32
    assert server_conn.rcv_nxt == (client_fin_seq + 1) % _MOD32

    # Both crossed each other's FIN before either got an ACK of its own —
    # must route through CLOSING, never the non-simultaneous FIN_WAIT2 path.
    assert [s for _, s in conn.transitions][-4:] == [FIN_WAIT1, CLOSING, TIME_WAIT, CLOSED]
    assert [s for _, s in server_conn.transitions][-4:] == [FIN_WAIT1, CLOSING, TIME_WAIT, CLOSED]


def test_time_wait_expires_to_closed_only_after_2x_msl():
    net, _client, server = _client_server()
    server.tcp_listen(80)
    conn = net.tcp_connect("client", IPv4Address("10.0.0.2"), 80)

    net.tcp_close("client", IPv4Address("10.0.0.2"), 80, run_after=False)
    net.run_for(2.0)
    net.tcp_close("server", IPv4Address("10.0.0.1"), conn.local_port, run_after=False)
    net.run_for(0.5)   # just enough for server's FIN + our ACK to land -> TIME_WAIT
    assert conn.state == TIME_WAIT
    entered_at = next(t for t, s in conn.transitions if s == TIME_WAIT)

    # Land just short of the 2xMSL deadline, measured from entry (not from
    # whatever net.now happened to be already) — must still be waiting.
    net.run_for(entered_at + TIME_WAIT_DURATION - net.now - 0.1)
    assert conn.state == TIME_WAIT, "must not reclaim the 4-tuple before 2xMSL elapses"

    net.run_for(0.2)   # cross the deadline
    assert conn.state == CLOSED


def test_simultaneous_open_reaches_established_both_sides():
    net = Network(seed=1)
    a = net.add_device(Host("a"))
    b = net.add_device(Host("b"))
    net.connect(
        "lan",
        net.add_iface(a, "eth0", ["10.0.0.1/24"]),
        net.add_iface(b, "eth0", ["10.0.0.2/24"]),
    )

    # Both dial each other's known port directly (RFC 9293 §3.5 Figure 8) —
    # a real simultaneous open needs a pre-agreed port on both ends, unlike
    # the usual client-dials-ephemeral-port-to-server-listen-port shape.
    conn_a = net.tcp_connect("a", IPv4Address("10.0.0.2"), 5000, local_port=5000,
                              run_after=False)
    conn_b = net.tcp_connect("b", IPv4Address("10.0.0.1"), 5000, local_port=5000,
                              run_after=False)
    net.run_for(2.0)

    assert conn_a.state == ESTABLISHED
    assert conn_b.state == ESTABLISHED
    assert conn_a.irs == conn_b.iss and conn_b.irs == conn_a.iss
    assert conn_a.rcv_nxt == (conn_b.iss + 1) % _MOD32
    assert conn_b.rcv_nxt == (conn_a.iss + 1) % _MOD32
    # No 4th packet: SYN, SYN, SYN-ACK, SYN-ACK — never a plain ACK.
    assert [s for _, s in conn_a.transitions] == [SYN_SENT, SYN_RECEIVED, ESTABLISHED]
    assert [s for _, s in conn_b.transitions] == [SYN_SENT, SYN_RECEIVED, ESTABLISHED]


def test_rst_aborts_established_connection():
    net, client, server = _client_server()
    server.tcp_listen(80)
    conn = net.tcp_connect("client", IPv4Address("10.0.0.2"), 80)
    server_conn = next(iter(server.tcp_conns.values()))
    assert server_conn.state == ESTABLISHED

    client.send_ip(net, Ipv4Packet(
        src=IPv4Address("10.0.0.1"), dst=IPv4Address("10.0.0.2"),
        proto=PROTO_TCP,
        payload=TcpSegment(src_port=conn.local_port, dst_port=80,
                            flags="RST", seq=server_conn.rcv_nxt),
    ))
    net.run_for(1.0)
    assert server_conn.state == CLOSED
    assert not server.tcp_conns   # torn down and removed from the table


def test_rst_with_wrong_seq_is_ignored_established_survives():
    """§3.10.7.4 minimal in-window check — not just any RST slams the door."""
    net, client, server = _client_server()
    server.tcp_listen(80)
    conn = net.tcp_connect("client", IPv4Address("10.0.0.2"), 80)
    server_conn = next(iter(server.tcp_conns.values()))

    client.send_ip(net, Ipv4Packet(
        src=IPv4Address("10.0.0.1"), dst=IPv4Address("10.0.0.2"),
        proto=PROTO_TCP,
        payload=TcpSegment(src_port=conn.local_port, dst_port=80,
                            flags="RST", seq=server_conn.rcv_nxt + 999),
    ))
    net.run_for(1.0)
    assert server_conn.state == ESTABLISHED


def test_determinism_close_scenario_rebuilt_twice_is_byte_identical():
    def run():
        net, _client, server = _client_server()
        server.tcp_listen(80)
        conn = net.tcp_connect("client", IPv4Address("10.0.0.2"), 80)
        net.tcp_close("client", IPv4Address("10.0.0.2"), 80)
        net.run_for(TIME_WAIT_DURATION + 3.0)
        tcp_records = [
            r.layers["tcp"] for r in net.capture.records(link_id="lan", limit=500)
            if "tcp" in r.layers
        ]
        return conn.transitions, tcp_records

    a = run()
    b = run()
    assert a == b


# ---------------------------------------------------------------------------
# HTTP level (exit gate) — CLI console + seek/step-back byte identity
# ---------------------------------------------------------------------------

def _iface(name: str, ips: list[str]):
    return {"id": "", "node_id": "", "name": name, "ip": ips}


async def _tcp_lab(client) -> str:
    """2 hosts, one lan: a -- b."""
    resp = await client.post("/api/projects", json={"name": "TcpTeardown"})
    pid = resp.json()["id"]

    async def node(name, ifaces):
        r = await client.post("/api/nodes", json={
            "project_id": pid, "name": name, "kind": "host", "interfaces": ifaces,
        })
        assert r.status_code == 201, r.text
        return r.json()

    a = await node("a", [_iface("eth0", ["10.0.0.1/24"])])
    b = await node("b", [_iface("eth0", ["10.0.0.2/24"])])

    def iface_id(node_json, name):
        return next(i["id"] for i in node_json["interfaces"] if i["name"] == name)

    r = await client.post("/api/links", json={
        "project_id": pid, "a_iface": iface_id(a, "eth0"), "b_iface": iface_id(b, "eth0"),
    })
    assert r.status_code == 201, r.text
    return pid


async def _cli(client, pid, node, command):
    r = await client.post(f"/api/lab/{pid}/cli", json={"node": node, "command": command})
    assert r.status_code == 200, r.text
    return r.json()["output"]


async def test_seek_step_back_reproduces_tcp_teardown_byte_identically(client):
    """Each ``/cli`` call is its own journal entry (like ``ping``), fully
    drained before the next one starts — so a step-back that lands *after*
    a later entry already ran can't replay that later entry too (seeking
    only ever discards/keeps whole entries, never re-issues a dropped one;
    that's covered separately by R1's ``test_seek_discards_rewritten_future``
    for the ping case). To exercise the same byte-identical-replay contract
    ``test_seek_step_back_reproduces_events_byte_identically`` checks for
    ping, seek into the *middle* of the single ``tcp close`` journal entry
    itself and step forward within it."""
    pid = await _tcp_lab(client)

    await _cli(client, pid, "b", "enable")
    await _cli(client, pid, "b", "configure terminal")
    await _cli(client, pid, "b", "tcp listen 80")
    await _cli(client, pid, "b", "end")

    async def ledger():
        return (await client.get(f"/api/lab/{pid}/ledger?limit=1")).json()

    connect_out = await _cli(client, pid, "a", "tcp connect 10.0.0.2 80")
    assert "state=ESTABLISHED" in connect_out

    before_close = (await ledger())["total"]
    close_a = await _cli(client, pid, "a", "tcp close 10.0.0.2 80")
    assert "Closed 1 connection" in close_a
    after_close = await ledger()
    after_close_seq, after_close_hash = after_close["total"], after_close["hash"]
    assert after_close_seq - before_close >= 2, "close action too small to bisect"

    close_b = await _cli(client, pid, "b", "tcp close 10.0.0.1")
    assert "Closed 1 connection" in close_b

    # Both sides ran 5s (default tcp_connect/tcp_close settle) — long enough
    # to clear TIME_WAIT (2xMSL) and pop the connection off the table.
    show = await _cli(client, pid, "a", "show tcp brief")
    assert show.strip().splitlines() == ["Local Address:Port      Remote Address:Port     State"]

    n = (before_close + after_close_seq) // 2

    # The full ledger now also holds close_b/show, past after_close_seq —
    # bound the comparison window to just the tail of the close_a entry.
    orig = (await client.get(
        f"/api/lab/{pid}/ledger?from_seq={n}&limit={after_close_seq - n}")).json()["records"]

    seek = (await client.post(f"/api/lab/{pid}/seek", json={"seq": n})).json()
    assert seek["seq"] == n and seek["mode"] == "simulation"

    step = (await client.post(
        f"/api/lab/{pid}/step", json={"events": after_close_seq - n})).json()
    assert step["seq"] == after_close_seq

    replay = (await client.get(
        f"/api/lab/{pid}/ledger?from_seq={n}&limit={after_close_seq - n}")).json()
    assert replay["records"] == orig                 # byte-identical event stream
    assert replay["hash"] == after_close_hash         # incremental hash matches too
