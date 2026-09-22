"""TCP FSM (A2-1): 3-way handshake + RST — RFC 9293 §3.5 / §3.10.7.1.

Behavioural, from outside the engine: builds a small lab, drives it through
:meth:`Network.tcp_connect`, and asserts on the resulting connection state,
sequence/ack arithmetic and packet capture — not on internal dict shapes.
"""
from __future__ import annotations

from ipaddress import IPv4Address

from engine.netstack.device import Host
from engine.netstack.network import Network
from engine.netstack.routing import Router
from engine.netstack.tcp import CLOSED, ESTABLISHED, SYN_SENT


def _client_server() -> tuple[Network, Host, Host]:
    net = Network(seed=1)
    client = net.add_device(Host("client"))
    server = net.add_device(Host("server"))
    net.connect(
        "lan",
        net.add_iface(client, "eth0", ["10.0.0.1/24"]),
        net.add_iface(server, "eth0", ["10.0.0.2/24"]),
    )
    return net, client, server


def test_handshake_reaches_established_on_both_ends_with_exact_seq_ack():
    net, _client, server = _client_server()
    server.tcp_listen(80)

    conn = net.tcp_connect("client", IPv4Address("10.0.0.2"), 80)

    assert conn.state == ESTABLISHED
    server_conn = next(iter(server.tcp_conns.values()))
    assert server_conn.state == ESTABLISHED

    # Client ISN = conn.iss; server ISN = conn.irs (learned from SYN-ACK).
    # RFC 9293 §3.5: each side's next-expected == peer's ISN + 1.
    assert conn.rcv_nxt == (server_conn.iss + 1) % (1 << 32)
    assert server_conn.rcv_nxt == (conn.iss + 1) % (1 << 32)
    assert server_conn.iss == conn.irs


def test_syn_to_closed_port_gets_rst_with_correct_seq_ack_and_client_closes():
    net, _client, _server = _client_server()
    # Nothing listens on 81.
    conn = net.tcp_connect("client", IPv4Address("10.0.0.2"), 81)

    assert conn.state == CLOSED

    rst = next(
        r for r in net.capture.records(link_id="lan", limit=50)
        if r.layers.get("tcp", {}).get("flags") == "RST-ACK"
    )
    tcp = rst.layers["tcp"]
    # RFC 9293 §3.10.7.1 CLOSED-state response to a bare SYN (ACK bit off):
    # <SEQ=0><ACK=SEG.SEQ+SEG.LEN=ISN+1><CTL=RST,ACK>
    assert tcp["seq"] == 0
    assert tcp["ack"] == (conn.iss + 1) % (1 << 32)


def test_syn_to_unreachable_host_stays_syn_sent_same_iss_across_retransmits():
    net = Network(seed=1)
    client = net.add_device(Host("client"))
    gw = net.add_device(Router("gw"))
    net.connect(
        "lan",
        net.add_iface(client, "eth0", ["10.0.0.1/24"]),
        net.add_iface(gw, "eth0", ["10.0.0.254/24"]),
    )
    client.default_gateway = IPv4Address("10.0.0.254")
    gw.sync_connected_routes()

    conn = net.tcp_connect("client", IPv4Address("192.168.99.99"), 80)

    assert conn.state == SYN_SENT
    syns = [
        r for r in net.capture.records(link_id="lan", limit=50)
        if r.layers.get("tcp", {}).get("flags") == "SYN"
    ]
    # tcp_connect's default 5s settle now covers 3 RTO retransmits (A2-3:
    # RTO 1/2/4s) — every one of them resends the same ISN, never a new one.
    assert len({r.layers["tcp"]["seq"] for r in syns}) == 1
    assert len(syns) > 2, "expected retransmits within the 5s settle window"


def test_determinism_same_scenario_rebuilt_twice_is_byte_identical():
    def run() -> tuple[int, int, str]:
        net, _client, server = _client_server()
        server.tcp_listen(80)
        conn = net.tcp_connect("client", IPv4Address("10.0.0.2"), 80)
        return conn.iss, conn.irs, conn.state

    a = run()
    b = run()
    assert a == b


def test_capture_shows_tcp_flags_and_seq_ack():
    net, _client, server = _client_server()
    server.tcp_listen(80)
    net.tcp_connect("client", IPv4Address("10.0.0.2"), 80)

    tcp_records = [
        r.layers["tcp"] for r in net.capture.records(link_id="lan", limit=50)
        if "tcp" in r.layers
    ]
    flags_seen = {r["flags"] for r in tcp_records}
    assert {"SYN", "SYN-ACK", "ACK"} <= flags_seen
    for r in tcp_records:
        assert "seq" in r and "ack" in r
