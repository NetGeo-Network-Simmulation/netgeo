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

A2-1 shipped the 3-way handshake + RST (CLOSED, LISTEN, SYN_SENT,
SYN_RECEIVED, ESTABLISHED). This slice (A2-2) adds graceful teardown
(active/passive/simultaneous close, RFC 9293 §3.6) and simultaneous open
(§3.5 Figure 8) — all 11 states from §3.3.2 are now reachable. Loss-driven
retransmission (RTO backoff) is still deferred to A2-3.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from ipaddress import IPv4Address
from typing import TYPE_CHECKING

from engine.events import EventType, SimEvent
from engine.netstack.frames import PROTO_TCP, Ipv4Packet, TcpSegment

if TYPE_CHECKING:  # pragma: no cover
    from engine.netstack.iface import Interface
    from engine.netstack.network import Network

# RFC 9293 §3.3.2 full state list — all 11 reachable as of A2-2.
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

# TIME_WAIT = 2×MSL (RFC 9293 §3.4.2: MSL is a chosen engineering value, "2
# minutes" there is illustrative, not normative — Linux hardcodes a fixed 60s
# TIME_WAIT regardless of measured RTT). A teaching sim has no wall clock to
# honor anyway; a few sim-seconds proves the "wait, then reclaim the 4-tuple"
# behavior without slowing every close-path test down.
MSL = 1.0
TIME_WAIT_DURATION = 2 * MSL


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
    timer_seq: int = 0      # sequence guard for the TIME_WAIT 2xMSL timer

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

    def tcp_connect(
        self, net: Network, remote_ip: IPv4Address, remote_port: int,
        local_port: int | None = None,
    ) -> TcpConn:
        """Active open (RFC 9293 §3.10.1): send SYN, park in SYN_SENT.

        ``local_port`` pins the source port instead of picking the next
        ephemeral one — needed to set up a simultaneous-open scenario (RFC
        9293 §3.5 Figure 8), where both sides must dial each other's known
        port rather than a random one."""
        route = self.egress_for(remote_ip)
        if route is None:
            raise ValueError(f"{self.name}: no route to {remote_ip}")
        local_ip = route[0].ip.ip if route[0].ip else None
        if local_ip is None:
            raise ValueError(f"{self.name}: no address to connect from")
        if local_port is None:
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
            return
        if conn.state == SYN_RECEIVED:
            self._tcp_in_syn_received(net, conn, key, seg, flags)
            return

        # Synchronized states (RFC 9293 §3.10.7.4): RST aborts unconditionally,
        # modulo a minimal in-window check. This engine has no receive-window
        # model (no scaling/backlog — see TcpSegment.window, always advertised
        # but never consulted), so "in window" collapses to "matches the next
        # byte we expect", stricter than a real stack (which accepts any seq
        # inside rcv_wnd). ponytail: a real window would widen this; not
        # needed for a teaching sim with no window-limited flow control.
        if "RST" in flags:
            if seg.seq == conn.rcv_nxt:
                self._tcp_close(net, key, conn)
            return

        if conn.state == ESTABLISHED:
            self._tcp_in_established(net, conn, key, seg, flags)
        elif conn.state == FIN_WAIT1:
            self._tcp_in_fin_wait1(net, conn, key, seg, flags)
        elif conn.state == FIN_WAIT2:
            self._tcp_in_fin_wait2(net, conn, key, seg, flags)
        elif conn.state == CLOSING:
            self._tcp_in_closing(net, conn, key, seg, flags)
        elif conn.state == LAST_ACK:
            self._tcp_in_last_ack(net, conn, key, seg, flags)
        # CLOSE_WAIT: nothing to react to here — waiting on the local app to
        # call tcp_close(). TIME_WAIT: RFC 9293 says re-ACK a retransmitted
        # FIN; not reachable without retransmission, deferred to A2-3.

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
        if flags == {"SYN"}:
            # Simultaneous open (RFC 9293 §3.5 Figure 8): the peer's SYN
            # crossed ours on the wire before either side saw an ACK. Learn
            # the peer's ISN like a passive accept would, but keep our own
            # iss/snd_nxt — we already sent that SYN.
            conn.irs = seg.seq
            conn.rcv_nxt = (seg.seq + 1) % _MOD32
            conn.state = SYN_RECEIVED
            self._tcp_log(net, conn)
            self._tcp_send(net, local_ip, local_port, remote_ip, remote_port,
                            seq=conn.iss, ack=conn.rcv_nxt, flags="SYN-ACK")
            return
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
        # Plain ACK completes a normal/passive handshake; a SYN-ACK here is
        # the peer's half of a simultaneous open (it already carries the ACK
        # of our SYN — irs/rcv_nxt were set when we first saw its bare SYN,
        # so there's nothing left to learn, just to confirm).
        if "ACK" in flags and seg.ack == conn.snd_nxt:
            conn.state = ESTABLISHED
            self._tcp_log(net, conn)

    # ----- teardown (RFC 9293 §3.6) ------------------------------------------
    def _tcp_in_established(self, net, conn, key, seg, flags) -> None:
        if "FIN" not in flags:
            return  # no data path modeled — nothing else to react to here
        # Passive close (§3.5.4): peer starts teardown; FIN consumes one seq.
        conn.rcv_nxt = (seg.seq + 1) % _MOD32
        conn.state = CLOSE_WAIT
        self._tcp_log(net, conn)
        self._tcp_send(net, conn.local_ip, conn.local_port, conn.remote_ip, conn.remote_port,
                        seq=conn.snd_nxt, ack=conn.rcv_nxt, flags="ACK")
        # ponytail: no auto-close-on-FIN — advancing CLOSE_WAIT→LAST_ACK is
        # an application decision, made via tcp_close().

    def _tcp_in_fin_wait1(self, net, conn, key, seg, flags) -> None:
        if "FIN" in flags:
            # Peer's FIN crossed ours before ours was ACKed — simultaneous
            # close (§3.5.4). This engine never piggybacks an ACK onto a FIN
            # segment (one semantic flag set per segment, like A2-1's
            # SYN/SYN-ACK/ACK/FIN/RST vocabulary), so FIN_WAIT1 always lands
            # here or in the plain-ACK branch below — never a direct
            # FIN_WAIT1→TIME_WAIT shortcut.
            conn.rcv_nxt = (seg.seq + 1) % _MOD32
            conn.state = CLOSING
            self._tcp_log(net, conn)
            self._tcp_send(net, conn.local_ip, conn.local_port, conn.remote_ip, conn.remote_port,
                            seq=conn.snd_nxt, ack=conn.rcv_nxt, flags="ACK")
            return
        if "ACK" in flags and seg.ack == conn.snd_nxt:
            conn.state = FIN_WAIT2
            self._tcp_log(net, conn)

    def _tcp_in_fin_wait2(self, net, conn, key, seg, flags) -> None:
        if "FIN" not in flags:
            return
        conn.rcv_nxt = (seg.seq + 1) % _MOD32
        conn.state = TIME_WAIT
        self._tcp_log(net, conn)
        self._tcp_send(net, conn.local_ip, conn.local_port, conn.remote_ip, conn.remote_port,
                        seq=conn.snd_nxt, ack=conn.rcv_nxt, flags="ACK")
        self._tcp_arm_time_wait(net, key, conn)

    def _tcp_in_closing(self, net, conn, key, seg, flags) -> None:
        if "ACK" in flags and seg.ack == conn.snd_nxt:
            conn.state = TIME_WAIT
            self._tcp_log(net, conn)
            self._tcp_arm_time_wait(net, key, conn)

    def _tcp_in_last_ack(self, net, conn, key, seg, flags) -> None:
        if "ACK" in flags and seg.ack == conn.snd_nxt:
            self._tcp_close(net, key, conn)  # no TIME_WAIT for the passive closer

    def tcp_close(
        self, net: Network, remote_ip: IPv4Address, remote_port: int | None = None,
    ) -> int:
        """User close (RFC 9293 §3.10.4): send FIN for every connection this
        device holds to ``remote_ip`` (or just ``remote_ip:remote_port`` if
        given). ESTABLISHED→FIN_WAIT1 is the active-close path; CLOSE_WAIT→
        LAST_ACK finishes a close the peer already started. Any other state
        is left alone (mirrors real close() being a no-op on a socket that
        isn't in a closable state). Returns how many connections it closed."""
        closed = 0
        for key, conn in list(self.tcp_conns.items()):
            if conn.remote_ip != remote_ip:
                continue
            if remote_port is not None and conn.remote_port != remote_port:
                continue
            if conn.state not in (ESTABLISHED, CLOSE_WAIT):
                continue
            conn.state = FIN_WAIT1 if conn.state == ESTABLISHED else LAST_ACK
            self._tcp_log(net, conn)
            self._tcp_send(net, conn.local_ip, conn.local_port, conn.remote_ip, conn.remote_port,
                            seq=conn.snd_nxt, ack=conn.rcv_nxt, flags="FIN")
            conn.snd_nxt = (conn.snd_nxt + 1) % _MOD32  # FIN consumes one seq
            closed += 1
        return closed

    # ----- TIME_WAIT timer (sequence-guarded, same idiom as VrrpProcess) ----
    def _tcp_arm_time_wait(self, net: Network, key: tuple, conn: TcpConn) -> None:
        conn.timer_seq += 1
        seq = conn.timer_seq
        net.scheduler.schedule_after(
            TIME_WAIT_DURATION,
            SimEvent(
                time=0.0, type=EventType.TIMER,
                handler=lambda _c, _e: self._tcp_time_wait_fired(net, key, conn, seq),
                node_id=self.node_id,
            ),
        )

    def _tcp_time_wait_fired(self, net: Network, key: tuple, conn: TcpConn, seq: int) -> None:
        if seq != conn.timer_seq or conn.state != TIME_WAIT:
            return
        self._tcp_close(net, key, conn)

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
