"""TCP — bare-minimum RFC 9293 state machine (A2-1: handshake + RST).

Lives alongside ``device.py``/``routing.py`` rather than under
``protocols/`` on purpose: ``protocols/__init__.py`` eagerly imports
bgp/isis/mpls/ospf, which import ``routing.Router``, which imports
``device.L3Device`` — importing anything under ``protocols/`` from
``device.py`` at module scope deadlocks on that partially-initialized
module. TCP is shared L3 transport machinery (like ARP/ICMP-echo in
``device.py``), not a router-only dynamic-routing "process" like BGP/OSPF,
so it does not belong under ``protocols/`` architecturally either.

Endpoints live directly on :class:`~engine.netstack.device.L3Device` (the
``tcp_conns`` table, keyed by the local 4-tuple) via the :class:`TcpEndpoint`
mixin — both hosts and routers can open or accept a TCP connection. BGP/
L3VPN/EVPN keep framing their own segments directly over port 179 with
seq/ack left at 0 (a pre-existing shortcut noted in bgp.py:40-41, untouched
by this slice) — this module only claims traffic that isn't port 179 (see
routing.py/device.py dispatch).

Only the states needed for a 3-way handshake + RST are implemented this
slice: CLOSED, LISTEN, SYN_SENT, SYN_RECEIVED, ESTABLISHED. The full RFC
9293 §3.3.2 name list is enumerated below so A2-2 (FIN/TIME_WAIT/CLOSING)
slots in without renaming anything already shipped.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from ipaddress import IPv4Address
from typing import TYPE_CHECKING

from engine.netstack.frames import PROTO_TCP, Ipv4Packet, TcpSegment

if TYPE_CHECKING:  # pragma: no cover
    from engine.netstack.iface import Interface
    from engine.netstack.network import Network

# RFC 9293 §3.3.2 full state list — only the first five are reachable this
# slice.
CLOSED = "CLOSED"
LISTEN = "LISTEN"
SYN_SENT = "SYN_SENT"
SYN_RECEIVED = "SYN_RECEIVED"
ESTABLISHED = "ESTABLISHED"
FIN_WAIT1 = "FIN_WAIT1"
FIN_WAIT2 = "FIN_WAIT2"
CLOSE_WAIT = "CLOSE_WAIT"
CLOSING = "CLOSING"
LAST_ACK = "LAST_ACK"
TIME_WAIT = "TIME_WAIT"

_MOD32 = 1 << 32


def isn(local_ip: str, local_port: int, remote_ip: str, remote_port: int, attempt: int) -> int:
    """Deterministic ISN (RFC 9293 §3.4.1): sha256 of the 4-tuple plus a
    per-4-tuple attempt counter the device keeps and increments each time
    that 4-tuple opens a new connection. No wall clock, no ``random`` — the
    same event order always produces the same ISN, so replay stays
    byte-identical."""
    key = f"{local_ip}:{local_port}-{remote_ip}:{remote_port}#{attempt}".encode()
    return int.from_bytes(hashlib.sha256(key).digest()[:4], "big")


@dataclass(slots=True)
class TcpConn:
    local_ip: IPv4Address
    local_port: int
    remote_ip: IPv4Address
    remote_port: int
    state: str = SYN_SENT
    iss: int = 0            # our initial sequence number
    irs: int = 0            # peer's initial sequence number
    snd_nxt: int = 0        # next seq we will send
    rcv_nxt: int = 0        # next seq we expect from the peer
    transitions: list = field(default_factory=list)

    @property
    def label(self) -> str:
        return f"{self.local_ip}:{self.local_port}-{self.remote_ip}:{self.remote_port}"


def _flags(seg: TcpSegment) -> set[str]:
    return set(seg.flags.split("-"))


def _key(local_ip, local_port, remote_ip, remote_port) -> tuple:
    return (local_ip, local_port, remote_ip, remote_port)


class TcpEndpoint:
    """Mixin: TCP connection table + FSM, shared by Host and Router."""

    def _tcp_init(self) -> None:
        self.tcp_conns: dict[tuple, TcpConn] = {}
        self.tcp_listen_ports: set[int] = set()
        # per-4-tuple attempt counter, feeds the deterministic ISN.
        self._tcp_attempt: dict[tuple, int] = {}
        self._tcp_ephemeral = 20000

    # ----- API --------------------------------------------------------------
    def tcp_listen(self, port: int) -> None:
        """Passive open: bind a port (like ``dns_zone``/``dhcp_pools``, this
        is state a test or CLI sets directly — no socket object)."""
        self.tcp_listen_ports.add(port)

    def tcp_connect(self, net: Network, remote_ip: IPv4Address, remote_port: int) -> TcpConn:
        """Active open (RFC 9293 §3.10.1): send SYN, park in SYN_SENT."""
        route = self.egress_for(remote_ip)
        if route is None:
            raise ValueError(f"{self.name}: no route to {remote_ip}")
        local_ip = route[0].ip.ip if route[0].ip else None
        if local_ip is None:
            raise ValueError(f"{self.name}: no address to connect from")
        self._tcp_ephemeral += 1
        local_port = self._tcp_ephemeral
        key = _key(local_ip, local_port, remote_ip, remote_port)
        attempt = self._tcp_attempt.get(key, 0)
        self._tcp_attempt[key] = attempt + 1
        iss = isn(str(local_ip), local_port, str(remote_ip), remote_port, attempt)
        conn = TcpConn(
            local_ip=local_ip, local_port=local_port,
            remote_ip=remote_ip, remote_port=remote_port,
            state=SYN_SENT, iss=iss, snd_nxt=(iss + 1) % _MOD32,
        )
        self.tcp_conns[key] = conn
        self._tcp_log(net, conn)
        self._tcp_send(net, local_ip, local_port, remote_ip, remote_port,
                        seq=iss, ack=0, flags="SYN")
        return conn

    # ----- ingress ------------------------------------------------------------
    def _handle_tcp(self, net: Network, iface: Interface, pkt: Ipv4Packet) -> None:
        seg = pkt.payload
        local_ip, local_port = pkt.dst, seg.dst_port
        remote_ip, remote_port = pkt.src, seg.src_port
        key = _key(local_ip, local_port, remote_ip, remote_port)
        flags = _flags(seg)
        conn = self.tcp_conns.get(key)

        if conn is None:
            self._tcp_no_conn(net, seg, flags, local_ip, local_port, remote_ip, remote_port, key)
            return

        if conn.state == SYN_SENT:
            self._tcp_in_syn_sent(net, conn, key, seg, flags, local_ip, local_port,
                                   remote_ip, remote_port)
        elif conn.state == SYN_RECEIVED:
            self._tcp_in_syn_received(net, conn, key, seg, flags)
        elif conn.state == ESTABLISHED and "RST" in flags:
            self._tcp_close(net, key, conn)
            # ponytail: FIN/data path deferred to A2-2 (CLOSE_WAIT/FIN_WAIT/…).

    def _tcp_no_conn(self, net, seg, flags, local_ip, local_port, remote_ip, remote_port, key):
        if "SYN" in flags and "ACK" not in flags and local_port in self.tcp_listen_ports:
            self._tcp_accept(net, seg, local_ip, local_port, remote_ip, remote_port, key)
            return
        if "RST" in flags:
            return  # RFC 9293 §3.10.7.1: RST to a nonexistent TCB is discarded.
        # RFC 9293 §3.10.7.1 CLOSED-state response.
        if "ACK" in flags:
            rseq, rack, rflags = seg.ack, 0, "RST"
        else:
            seg_len = 1 if flags & {"SYN", "FIN"} else 0
            rseq, rack, rflags = 0, (seg.seq + seg_len) % _MOD32, "RST-ACK"
        self._tcp_send(net, local_ip, local_port, remote_ip, remote_port,
                        seq=rseq, ack=rack, flags=rflags)

    def _tcp_accept(self, net, seg, local_ip, local_port, remote_ip, remote_port, key):
        """Passive open (RFC 9293 §3.10.7.2): SYN to a listening port."""
        attempt = self._tcp_attempt.get(key, 0)
        self._tcp_attempt[key] = attempt + 1
        iss = isn(str(local_ip), local_port, str(remote_ip), remote_port, attempt)
        irs = seg.seq
        conn = TcpConn(
            local_ip=local_ip, local_port=local_port,
            remote_ip=remote_ip, remote_port=remote_port,
            state=SYN_RECEIVED, iss=iss, irs=irs,
            snd_nxt=(iss + 1) % _MOD32, rcv_nxt=(irs + 1) % _MOD32,
        )
        self.tcp_conns[key] = conn
        self._tcp_log(net, conn)
        self._tcp_send(net, local_ip, local_port, remote_ip, remote_port,
                        seq=iss, ack=conn.rcv_nxt, flags="SYN-ACK")

    def _tcp_in_syn_sent(self, net, conn, key, seg, flags, local_ip, local_port,
                          remote_ip, remote_port):
        if "RST" in flags:
            if seg.ack == conn.snd_nxt:  # acceptable ACK (RFC 9293 §3.5.3)
                self._tcp_close(net, key, conn)
            return
        # ponytail: simultaneous open (bare SYN, no ACK) not handled here —
        # add alongside A2-2 if that scenario is needed.
        if flags == {"SYN", "ACK"} and seg.ack == conn.snd_nxt:
            conn.irs = seg.seq
            conn.rcv_nxt = (seg.seq + 1) % _MOD32
            conn.state = ESTABLISHED
            self._tcp_log(net, conn)
            self._tcp_send(net, local_ip, local_port, remote_ip, remote_port,
                            seq=conn.snd_nxt, ack=conn.rcv_nxt, flags="ACK")

    def _tcp_in_syn_received(self, net, conn, key, seg, flags):
        if "RST" in flags:
            self._tcp_close(net, key, conn)
            return
        if flags == {"ACK"} and seg.ack == conn.snd_nxt:
            conn.state = ESTABLISHED
            self._tcp_log(net, conn)

    def _tcp_close(self, net: Network, key: tuple, conn: TcpConn) -> None:
        conn.state = CLOSED
        self._tcp_log(net, conn)
        self.tcp_conns.pop(key, None)

    def _tcp_log(self, net: Network, conn: TcpConn) -> None:
        conn.transitions.append((net.now, conn.state))
        net.log_event("tcp.state", device=self.name, conn=conn.label, state=conn.state)

    def _tcp_send(self, net, local_ip, local_port, remote_ip, remote_port, *,
                  seq: int, ack: int, flags: str) -> None:
        self.send_ip(net, Ipv4Packet(
            src=local_ip, dst=remote_ip, proto=PROTO_TCP, ttl=64,
            payload=TcpSegment(
                src_port=local_port, dst_port=remote_port,
                flags=flags, seq=seq, ack=ack,
            ),
        ))
