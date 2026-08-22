"""BGP-4 — path-vector routing between autonomous systems (RFC 4271 subset).

What is modelled (v3, NG-SIM-08 follow-on — full FSM):
- the RFC 4271 §8 six-state FSM per neighbor: Idle, Connect, Active,
  OpenSent, OpenConfirm, Established — including NOTIFICATION handling
  (§6, §8) and Hold/Keepalive timer negotiation (§4.2, §4.4);
- explicit neighbor configuration (eBGP and iBGP by ASN comparison);
- session establishment over the simulated TCP port 179 (OPEN/KEEPALIVE
  exchange — real packets across the topology, so a broken data path means
  no session); the peer's router-id is learned from its OPEN;
- UPDATE messages carrying the full Adj-RIB-Out snapshot (implicit withdraw:
  a prefix missing from the latest update is removed); every route carries
  its attributes: AS-path, next-hop, local-pref, communities, originator;
- AS-path loop prevention + originator-id loop prevention for reflection;
- **route reflection**: a speaker with ``rr_client`` neighbors reflects
  iBGP-learned routes — client routes to everyone, non-client routes to
  clients (RFC 4456, single cluster);
- **communities**: propagated end-to-end; well-known ``no-export`` honoured
  (never advertised to an eBGP peer);
- **prefix filtering**: per-neighbor in/out prefix lists with ge/le, first
  match wins, implicit deny when a list is configured;
- **best-path selection (P-5)**: full RFC 4271 §9.1.2.2 (a)-(j) tie-break
  ladder — local-pref, AS-path length, origin type, MED (same neighboring
  AS only, unless ``always_compare_med``), eBGP-over-iBGP, IGP metric to
  the next-hop, route age, router-id, cluster-list length, peer address
  (last resort, not first — see ``BgpProcess._better`` for which of these
  are no-ops in this engine and why);
- iBGP split-horizon for non-reflectors; next-hop-self on advertisement.

Not modelled: confederations, MP-BGP, dynamic capability negotiation (so
NOTIFICATION 2/4 "Unsupported Optional Parameter" has no live trigger — the
wire format supports it, nothing in this engine ever sends it).

Transport note: this engine has no standalone TCP process — BGP frames its
own segments directly over IP (``TcpSegment`` with SYN/PSH flags for wire
realism). There is therefore no independent "transport connected" signal
distinct from BGP message delivery. Connect/Active are approximated using a
real, already-modelled signal instead of an invented one: whether the local
routing table currently resolves an egress toward the peer's IP
(``Router.egress_for``). No route yet -> Connect, then Active on retry
(exactly the classic "stuck in Active" troubleshooting symptom when the IGP
hasn't converged); route resolves -> OPEN is sent and the FSM moves to
OpenSent to wait for the peer's OPEN, same as a real implementation waiting
out its transport handshake.
"""
from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from ipaddress import IPv4Address, IPv4Network
from typing import TYPE_CHECKING

from engine.events import EventType, SimEvent
from engine.netstack.frames import PROTO_TCP, Ipv4Packet, TcpSegment
from engine.netstack.iface import Interface
from engine.netstack.routing import Route, Router

if TYPE_CHECKING:  # pragma: no cover
    from engine.netstack.network import Network

logger = logging.getLogger(__name__)

NO_EXPORT = "no-export"

# RFC 4271 §8: the six FSM states, in the order a session normally climbs.
STATES = ("idle", "connect", "active", "open-sent", "open-confirm", "established")

# RFC 4271 §4.5 NOTIFICATION error codes (only what this engine can trigger).
NOTIFICATION_CODES = {
    1: "Message Header Error",
    2: "OPEN Message Error",
    3: "UPDATE Message Error",
    4: "Hold Timer Expired",
    5: "Finite State Machine Error",
    6: "Cease",
}
NOTIFICATION_SUBCODES = {
    (2, 2): "Bad Peer AS",
    (2, 4): "Unsupported Optional Parameter",
}


# --- route attributes ----------------------------------------------------------

#: RFC 4271 §9.1.2.2 (c): IGP < EGP < Incomplete, lowest wins.
ORIGIN_RANK = {"igp": 0, "egp": 1, "incomplete": 2}


@dataclass(slots=True)
class BgpAttrs:
    """Path attributes carried with every prefix in an UPDATE."""

    as_path: tuple[int, ...] = ()
    next_hop: str = ""
    local_pref: int = 100
    communities: tuple[str, ...] = ()
    originator: str = ""          # router-id of the speaker that injected
                                  # the route into this AS (RFC 4456-ish)
    origin: str = "igp"           # "igp" | "egp" | "incomplete" (§4.3);
                                  # every route this engine originates is a
                                  # network statement, so "igp" is the only
                                  # value ever produced -- "incomplete" is
                                  # reachable only by a test constructing
                                  # BgpAttrs directly, no redistribution path
                                  # sets it today.
    med: int = 0                  # MULTI_EXIT_DISC (§9.1.2.2 (d)); nothing
                                  # in this engine sets a non-zero MED yet
                                  # (no redistribute/route-map knob) -- the
                                  # field and its same-neighbor-AS compare
                                  # rule exist so a future one can.

    @property
    def wire_size(self) -> int:
        return 14 + 2 * len(self.as_path) + 4 * len(self.communities)


# --- prefix lists ----------------------------------------------------------------

@dataclass(slots=True)
class PrefixRule:
    """One prefix-list entry. Without ge/le the match is exact (Cisco style)."""

    action: str                   # "permit" | "deny"
    prefix: IPv4Network
    ge: int | None = None
    le: int | None = None

    def matches(self, p: IPv4Network) -> bool:
        if not p.subnet_of(self.prefix):
            return False
        lo = self.ge if self.ge is not None else self.prefix.prefixlen
        hi = self.le if self.le is not None else (
            32 if self.ge is not None else self.prefix.prefixlen
        )
        return lo <= p.prefixlen <= hi


def _parse_plist(rules: Iterable | None) -> tuple[PrefixRule, ...]:
    out: list[PrefixRule] = []
    for r in rules or []:
        if isinstance(r, PrefixRule):
            out.append(r)
        else:  # dict from intent JSON
            out.append(
                PrefixRule(
                    action=str(r.get("action", "permit")),
                    prefix=IPv4Network(r["prefix"]),
                    ge=int(r["ge"]) if r.get("ge") is not None else None,
                    le=int(r["le"]) if r.get("le") is not None else None,
                )
            )
    return tuple(out)


def _plist_permits(plist: tuple[PrefixRule, ...], p: IPv4Network) -> bool:
    if not plist:
        return True
    for rule in plist:
        if rule.matches(p):
            return rule.action == "permit"
    return False  # implicit deny


# --- BGP messages (payload of TcpSegment port 179) ----------------------------

@dataclass(slots=True)
class BgpOpen:
    asn: int
    router_id: str
    hold_time: float = 90.0

    @property
    def wire_size(self) -> int:
        return 29

    def summary(self) -> str:
        return f"BGP OPEN as={self.asn} rid={self.router_id}"


@dataclass(slots=True)
class BgpKeepalive:
    @property
    def wire_size(self) -> int:
        return 19

    def summary(self) -> str:
        return "BGP KEEPALIVE"


@dataclass(slots=True)
class BgpUpdate:
    """Full Adj-RIB-Out snapshot: prefix -> attributes."""

    routes: dict[str, BgpAttrs] = field(default_factory=dict)

    @property
    def wire_size(self) -> int:
        return 23 + sum(a.wire_size for a in self.routes.values())

    def summary(self) -> str:
        return f"BGP UPDATE {len(self.routes)} route(s)"


@dataclass(slots=True)
class BgpNotification:
    """RFC 4271 §4.5 — tears down the session on send *and* on receipt."""

    code: int
    subcode: int = 0

    @property
    def wire_size(self) -> int:
        return 21

    def summary(self) -> str:
        name = NOTIFICATION_CODES.get(self.code, f"code {self.code}")
        return f"BGP NOTIFICATION {name} (subcode {self.subcode})"


@dataclass(slots=True)
class _Peer:
    ip: IPv4Address
    remote_asn: int
    state: str = "idle"                  # see STATES
    hold_time: float = 90.0              # negotiated (min of both sides) once known
    router_id: str = ""                  # learned from the peer's OPEN
    rr_client: bool = False
    admin_down: bool = False             # neighbor administratively shut down
    hold_seq: int = 0                    # sequence-guard for the Hold Timer
    connect_retry_seq: int = 0           # sequence-guard for the ConnectRetryTimer
    transitions: list[tuple[float, str]] = field(default_factory=list)
    plist_in: tuple[PrefixRule, ...] = ()
    plist_out: tuple[PrefixRule, ...] = ()
    rib_in: dict[IPv4Network, BgpAttrs] = field(default_factory=dict)
    # Last Adj-RIB-Out snapshot actually sent — updates go out only on
    # change, otherwise two speakers ping-pong identical UPDATEs forever
    # and the storm tail-drops real traffic in the egress queues.
    adj_out: dict[str, BgpAttrs] | None = None


class BgpProcess:
    """One BGP speaker attached to a Router."""

    proto = "bgp"

    def __init__(
        self,
        router: Router,
        asn: int,
        router_id: str | None = None,
        keepalive_interval: float = 30.0,
        hold_time: float = 90.0,
        always_compare_med: bool = False,
    ) -> None:
        self.router = router
        self.asn = asn
        self.router_id = router_id or (
            str(max((i.ip for i in router.all_ips()), default="0.0.0.0"))
        )
        # Pre-session retry cadence (ConnectRetryTimer, RFC 4271 §8). The
        # post-Established KEEPALIVE cadence is *not* this value — per
        # §4.4 it is always negotiated Hold Time / 3.
        self.keepalive_interval = keepalive_interval
        self.hold_time = hold_time
        # RFC 4271 §9.1.2.2 (d): by default MED is only meaningful between
        # routes from the same neighboring AS. Some deployments turn this
        # off ("bgp always-compare-med") to compare MED across ASes too.
        self.always_compare_med = always_compare_med
        self.peers: dict[IPv4Address, _Peer] = {}
        # (prefix, communities) advertised by this speaker
        self.networks: list[tuple[IPv4Network, tuple[str, ...]]] = []
        self._started = False
        router.processes.append(self)

    # ----- configuration -----------------------------------------------------
    def add_neighbor(
        self,
        peer_ip: str | IPv4Address,
        remote_asn: int,
        rr_client: bool = False,
        prefix_list_in: Iterable | None = None,
        prefix_list_out: Iterable | None = None,
    ) -> None:
        ip = IPv4Address(peer_ip)
        self.peers[ip] = _Peer(
            ip=ip,
            remote_asn=remote_asn,
            hold_time=self.hold_time,
            rr_client=rr_client,
            plist_in=_parse_plist(prefix_list_in),
            plist_out=_parse_plist(prefix_list_out),
        )

    def advertise_network(
        self, prefix: str | IPv4Network, communities: Iterable[str] = ()
    ) -> None:
        net_ = IPv4Network(prefix)
        comms = tuple(communities)
        if not any(p == net_ for p, _c in self.networks):
            self.networks.append((net_, comms))

    @property
    def is_reflector(self) -> bool:
        return any(p.rr_client for p in self.peers.values())

    # ----- administrative control ---------------------------------------------
    def shutdown_neighbor(self, net: Network, peer_ip: str | IPv4Address) -> None:
        """``neighbor x.x.x.x shutdown`` — administratively closes the
        session with a Cease NOTIFICATION and stops retrying until
        re-enabled."""
        peer = self.peers.get(IPv4Address(peer_ip))
        if peer is None or peer.admin_down:
            return
        peer.admin_down = True
        if peer.state == "idle":
            return
        self._notify(net, peer, 6, 0)  # Cease

    def no_shutdown_neighbor(self, net: Network, peer_ip: str | IPv4Address) -> None:
        peer = self.peers.get(IPv4Address(peer_ip))
        if peer is None or not peer.admin_down:
            return
        peer.admin_down = False
        self._begin_connect(net, peer)

    # ----- lifecycle ------------------------------------------------------------
    def start(self, net: Network) -> None:
        if self._started:
            return
        self._started = True
        for peer in self.peers.values():
            self._begin_connect(net, peer)

    def on_power_off(self, net: Network) -> None:
        """F47-style hook: a power loss drops every session locally (no
        graceful NOTIFICATION — a dead router can't send one) instead of
        leaving a stale Established claim behind. Goes through
        _close_session, so the ConnectRetryTimer seq-guard bump below
        already covers this path too — no separate bump needed here."""
        for peer in list(self.peers.values()):
            self._close_session(net, peer)

    # ----- state machine plumbing (RFC 4271 §8) --------------------------------
    def _set_state(self, net: Network, peer: _Peer, new_state: str) -> None:
        peer.state = new_state
        peer.transitions.append((net.now, new_state))
        net.log_event(
            "bgp.state", device=self.router.name, peer=str(peer.ip), state=new_state
        )

    def _begin_connect(self, net: Network, peer: _Peer) -> None:
        """Idle -> Connect (Start event)."""
        if peer.admin_down:
            return
        # A fresh cycle starts here: any ConnectRetryTimer armed by an
        # earlier generation (e.g. one that raced a NOTIFICATION-driven
        # close) is no longer this generation's — kill it even though this
        # call may or may not re-arm one of its own below.
        peer.connect_retry_seq += 1
        self._set_state(net, peer, "connect")
        self._attempt_open(net, peer)
        self._maybe_arm_connect_retry(net, peer)

    def _attempt_open(self, net: Network, peer: _Peer) -> None:
        """Try to complete the (approximated) transport connection by
        sending our OPEN. Only succeeds once local routing can actually
        reach the peer — see the module docstring."""
        if not self._send_open(net, peer):
            return
        self._set_state(net, peer, "open-sent")
        self._arm_hold(net, peer, self.hold_time)  # pre-negotiation guard

    def _maybe_arm_connect_retry(self, net: Network, peer: _Peer) -> None:
        if peer.state in ("connect", "active") and not peer.admin_down:
            self._arm_connect_retry(net, peer)

    def _arm_connect_retry(self, net: Network, peer: _Peer) -> None:
        # Sequence-guard (same pattern as _arm_hold / vrrp.py's
        # _timer_seq): the FSM state string alone isn't a safe identity
        # check here because "connect"/"active"/"idle" recur across
        # generations — a stale timer from an earlier generation could
        # fire after the state has cycled back to a value its own guard
        # would accept, and wrongly drive the FSM. The seq makes each
        # arm-to-fire pairing generation-specific.
        peer.connect_retry_seq += 1
        seq = peer.connect_retry_seq
        net.scheduler.schedule_after(
            self.keepalive_interval,
            SimEvent(
                time=0.0,
                type=EventType.TIMER,
                handler=lambda _c, _e: self._connect_retry_fired(net, peer, seq),
                node_id=self.router.node_id,
            ),
        )

    def _connect_retry_fired(self, net: Network, peer: _Peer, seq: int) -> None:
        if seq != peer.connect_retry_seq:
            return  # superseded by a newer generation's chain
        # Stale timer: session already progressed past the pre-OPEN phase,
        # or was administratively shut down in the meantime.
        if peer.admin_down or peer.state in ("open-sent", "open-confirm", "established"):
            return
        if not self.router.powered_on:
            self._arm_connect_retry(net, peer)  # keep listening for power-on
            return
        if peer.state == "idle":
            self._begin_connect(net, peer)  # RFC's optional idle-damping restart
            return
        if peer.state == "connect":
            # No response within one retry window: the (approximated)
            # transport attempt didn't pan out — this is the classic
            # "stuck in Active" state real operators troubleshoot.
            self._set_state(net, peer, "active")
        self._attempt_open(net, peer)
        self._maybe_arm_connect_retry(net, peer)

    def _close_session(self, net: Network, peer: _Peer) -> None:
        """Local teardown to Idle — used for both NOTIFICATION-driven
        closes and hard resets (power loss)."""
        if peer.state == "idle":
            return
        # Same reasoning as _begin_connect: kill any in-flight
        # ConnectRetryTimer from before this close, whether or not we're
        # about to arm a fresh one below.
        peer.connect_retry_seq += 1
        peer.rib_in.clear()
        peer.adj_out = None          # a new session must get a fresh UPDATE
        self._set_state(net, peer, "idle")
        self._decide_and_install(net)
        self._advertise_all(net)
        if not peer.admin_down:
            self._arm_connect_retry(net, peer)

    # ----- timers (Hold Timer + Keepalive Timer, RFC 4271 §4.2/§4.4) -----------
    def _arm_hold(self, net: Network, peer: _Peer, hold_time: float) -> None:
        if hold_time <= 0:
            return  # §4.4: Hold Time 0 means no keepalives, no hold timer
        peer.hold_seq += 1
        seq = peer.hold_seq
        net.scheduler.schedule_after(
            hold_time,
            SimEvent(
                time=0.0,
                type=EventType.TIMER,
                handler=lambda _c, _e: self._hold_fired(net, peer, seq),
                node_id=self.router.node_id,
            ),
        )

    def _hold_fired(self, net: Network, peer: _Peer, seq: int) -> None:
        if seq != peer.hold_seq:
            return  # superseded by activity that reset the timer
        if peer.state not in ("open-sent", "open-confirm", "established"):
            return
        if not self.router.powered_on:
            return  # on_power_off already tore this session down
        self._notify(net, peer, 4, 0)  # Hold Timer Expired

    def _arm_keepalive(self, net: Network, peer: _Peer) -> None:
        if peer.hold_time <= 0:
            return
        net.scheduler.schedule_after(
            peer.hold_time / 3.0,  # §4.4: send at one third of Hold Time
            SimEvent(
                time=0.0,
                type=EventType.TIMER,
                handler=lambda _c, _e: self._keepalive_fired(net, peer),
                node_id=self.router.node_id,
            ),
        )

    def _keepalive_fired(self, net: Network, peer: _Peer) -> None:
        if peer.state != "established":
            return  # session moved on (or died) — this chain simply ends
        if self.router.powered_on:
            self._send(net, peer, BgpKeepalive())
        self._arm_keepalive(net, peer)

    # ----- transport ---------------------------------------------------------------
    def _send(self, net: Network, peer: _Peer, msg, flags: str = "PSH") -> bool:
        route = self.router.egress_for(peer.ip)
        src_ip = route[0].ip.ip if route and route[0].ip else None
        if src_ip is None:
            return False
        self.router.send_ip(
            net,
            Ipv4Packet(
                src=src_ip,
                dst=peer.ip,
                proto=PROTO_TCP,
                ttl=64 if peer.remote_asn == self.asn else 2,  # eBGP is 1-hop-ish
                dscp=48,
                payload=TcpSegment(
                    src_port=179, dst_port=179, flags=flags, payload=msg
                ),
            ),
        )
        return True

    def _send_open(self, net: Network, peer: _Peer) -> bool:
        return self._send(
            net,
            peer,
            BgpOpen(asn=self.asn, router_id=self.router_id, hold_time=self.hold_time),
            flags="SYN",
        )

    def _notify(self, net: Network, peer: _Peer, code: int, subcode: int = 0) -> None:
        net.log_event(
            "bgp.notification", device=self.router.name, peer=str(peer.ip),
            code=code, subcode=subcode, direction="tx",
        )
        self._send(net, peer, BgpNotification(code=code, subcode=subcode))
        self._close_session(net, peer)

    # ----- message handling ------------------------------------------------------------
    def on_packet(self, net: Network, iface: Interface, pkt: Ipv4Packet) -> None:
        seg = pkt.payload
        if not isinstance(seg, TcpSegment):
            return
        peer = self.peers.get(pkt.src)
        if peer is None or peer.admin_down:
            return  # administratively shut down: don't reform the session
        msg = seg.payload
        if isinstance(msg, BgpNotification):
            self._on_notification(net, peer, msg)
        elif isinstance(msg, BgpOpen):
            self._on_open(net, peer, msg)
        elif isinstance(msg, BgpKeepalive):
            self._on_keepalive(net, peer)
        elif isinstance(msg, BgpUpdate):
            if peer.state != "established":
                self._notify(net, peer, 5, 0)  # FSM Error: UPDATE out of sequence
                return
            self._arm_hold(net, peer, peer.hold_time)  # any message resets Hold Timer
            self._on_update(net, peer, msg)

    def _on_open(self, net: Network, peer: _Peer, msg: BgpOpen) -> None:
        if peer.state == "established":
            self._notify(net, peer, 5, 0)  # FSM Error: OPEN after session is up
            return
        if msg.asn != peer.remote_asn:
            logger.debug("%s: OPEN from %s wrong ASN %s", self.router_id, peer.ip, msg.asn)
            self._notify(net, peer, 2, 2)  # OPEN Message Error / Bad Peer AS
            return
        peer.hold_time = min(self.hold_time, msg.hold_time)  # §4.2: negotiate the minimum
        peer.router_id = msg.router_id
        if peer.state in ("idle", "connect", "active"):
            # Peer reached us before our own retry fired (simultaneous/passive
            # open) — catch up by sending our OPEN now.
            self._send_open(net, peer)
        self._set_state(net, peer, "open-confirm")
        self._send(net, peer, BgpKeepalive())
        self._arm_hold(net, peer, peer.hold_time)

    def _on_keepalive(self, net: Network, peer: _Peer) -> None:
        if peer.state == "open-confirm":
            self._set_state(net, peer, "established")
            logger.debug("%s: BGP ESTABLISHED with %s", self.router_id, peer.ip)
            self._arm_hold(net, peer, peer.hold_time)
            self._arm_keepalive(net, peer)
            self._advertise(net, peer)
        elif peer.state == "established":
            self._arm_hold(net, peer, peer.hold_time)  # any message resets Hold Timer
        else:
            self._notify(net, peer, 5, 0)  # FSM Error: KEEPALIVE before OPEN exchange

    def _on_notification(self, net: Network, peer: _Peer, msg: BgpNotification) -> None:
        logger.debug(
            "%s: NOTIFICATION from %s code=%s subcode=%s",
            self.router_id, peer.ip, msg.code, msg.subcode,
        )
        net.log_event(
            "bgp.notification", device=self.router.name, peer=str(peer.ip),
            code=msg.code, subcode=msg.subcode, direction="rx",
        )
        self._close_session(net, peer)

    def _on_update(self, net: Network, peer: _Peer, update: BgpUpdate) -> None:
        rib: dict[IPv4Network, BgpAttrs] = {}
        for prefix_s, attrs in update.routes.items():
            if self.asn in attrs.as_path:
                continue  # AS-path loop prevention
            if attrs.originator and attrs.originator == self.router_id:
                continue  # reflection loop prevention (RFC 4456)
            prefix = IPv4Network(prefix_s)
            if not _plist_permits(peer.plist_in, prefix):
                continue
            rib[prefix] = attrs
        if rib == peer.rib_in:
            return  # nothing changed — no re-decision, no re-advertisement
        peer.rib_in = rib
        self._decide_and_install(net)
        self._advertise_all(net)

    # ----- decision process ----------------------------------------------------------------
    def best_paths(self) -> dict[IPv4Network, tuple[BgpAttrs, IPv4Address]]:
        """prefix -> (attrs, learned_from_peer_ip)."""
        best: dict[IPv4Network, tuple[BgpAttrs, IPv4Address]] = {}
        for peer in self.peers.values():
            if peer.state != "established":
                continue
            for prefix, attrs in peer.rib_in.items():
                cur = best.get(prefix)
                if cur is None or self._better(attrs, peer.ip, cur):
                    best[prefix] = (attrs, peer.ip)
        return best

    def _igp_metric_to(self, next_hop: str) -> int:
        """IGP cost from this router to a BGP next-hop, for tie-break (f).
        Falls back to 0 (neutral -- ties rather than wins/loses) when the
        next-hop doesn't parse or isn't in the routing table yet; that
        only happens mid-convergence, never in a settled comparison."""
        try:
            route = self.router.lookup(IPv4Address(next_hop))
        except ValueError:
            return 0
        return route.metric if route else 0

    def _better(
        self,
        attrs: BgpAttrs,
        peer_ip: IPv4Address,
        cur: tuple[BgpAttrs, IPv4Address],
    ) -> bool:
        """RFC 4271 §9.1.2.2 (a)-(j), in order -- each step decides as soon
        as the pair differs, falling through to the next only on an exact
        tie. Two steps are permanent, documented no-ops in this engine
        rather than invented infrastructure:
        (g) route age: rib_in stores only the latest attrs snapshot per
            peer, no per-route receive timestamp -- add one if a slice
            ever needs real flap-driven stability preference.
        (i) cluster-list length: this engine reflects within a single
            cluster only (see module docstring), so every route's
            cluster-list is the same length (0) -- add a real list if
            multi-cluster reflection ever lands.
        Both fall through immediately and never influence the outcome.
        """
        cur_attrs, cur_peer_ip = cur
        new_peer = self.peers[peer_ip]
        cur_peer = self.peers[cur_peer_ip]

        # (a) highest local-pref
        if attrs.local_pref != cur_attrs.local_pref:
            return attrs.local_pref > cur_attrs.local_pref
        # (b) shortest AS-path
        if len(attrs.as_path) != len(cur_attrs.as_path):
            return len(attrs.as_path) < len(cur_attrs.as_path)
        # (c) lowest origin type: IGP < EGP < Incomplete
        if attrs.origin != cur_attrs.origin:
            return ORIGIN_RANK[attrs.origin] < ORIGIN_RANK[cur_attrs.origin]
        # (d) lowest MED -- only between routes from the same neighboring
        # AS (leftmost AS_PATH hop; 0 sentinel for a locally-originated/
        # purely-intra-AS route with no hops yet), unless configured to
        # always compare.
        new_nas = attrs.as_path[0] if attrs.as_path else 0
        cur_nas = cur_attrs.as_path[0] if cur_attrs.as_path else 0
        if (self.always_compare_med or new_nas == cur_nas) and attrs.med != cur_attrs.med:
            return attrs.med < cur_attrs.med
        # (e) eBGP over iBGP
        new_ibgp = new_peer.remote_asn == self.asn
        cur_ibgp = cur_peer.remote_asn == self.asn
        if new_ibgp != cur_ibgp:
            return cur_ibgp  # new wins iff *cur* is the iBGP one
        # (f) lowest IGP metric to the BGP next-hop
        new_metric = self._igp_metric_to(attrs.next_hop)
        cur_metric = self._igp_metric_to(cur_attrs.next_hop)
        if new_metric != cur_metric:
            return new_metric < cur_metric
        # (g) route age -- no-op, see docstring above.
        # (h) lowest BGP router-id (originator-id for a reflected route,
        # else the sending peer's own router-id from its OPEN)
        new_rid = attrs.originator or new_peer.router_id
        cur_rid = cur_attrs.originator or cur_peer.router_id
        if new_rid != cur_rid:
            return new_rid < cur_rid
        # (i) cluster-list length -- no-op, see docstring above.
        # (j) lowest peer address -- last resort, not first.
        return peer_ip < cur_peer_ip

    def _decide_and_install(self, net: Network) -> None:
        local = {ip.network for ip in self.router.all_ips()}
        my_networks = {p for p, _c in self.networks}
        self.router.withdraw_routes("ebgp")
        self.router.withdraw_routes("ibgp")
        for prefix, (attrs, peer_ip) in self.best_paths().items():
            if prefix in local or prefix in my_networks:
                continue
            peer = self.peers[peer_ip]
            source = "ibgp" if peer.remote_asn == self.asn else "ebgp"
            self.router.install_route(
                Route(
                    prefix=prefix,
                    next_hop=IPv4Address(attrs.next_hop),
                    iface_name=None,
                    source=source,
                    metric=len(attrs.as_path),
                )
            )

    # ----- advertisement ----------------------------------------------------------------------
    def _advertise_all(self, net: Network) -> None:
        for peer in self.peers.values():
            if peer.state == "established":
                self._advertise(net, peer)

    def _advertise(self, net: Network, peer: _Peer) -> None:
        route = self.router.egress_for(peer.ip)
        my_nh = route[0].ip.ip if route and route[0].ip else None
        if my_nh is None:
            return
        is_ibgp_peer = peer.remote_asn == self.asn
        out: dict[str, BgpAttrs] = {}

        def offer(prefix: IPv4Network, attrs: BgpAttrs) -> None:
            if not _plist_permits(peer.plist_out, prefix):
                return
            out[str(prefix)] = attrs

        # Locally-originated networks.
        for prefix, comms in self.networks:
            offer(
                prefix,
                BgpAttrs(
                    as_path=() if is_ibgp_peer else (self.asn,),
                    next_hop=str(my_nh),
                    local_pref=100,
                    communities=comms,
                    # originator is an intra-AS attribute; never crosses eBGP.
                    # NB: our own no-export networks ARE offered to eBGP peers
                    # (RFC 1997 binds the *receiving* AS, not the originator).
                    originator=self.router_id if is_ibgp_peer else "",
                ),
            )

        # Best learned routes.
        for prefix, (attrs, learned_from) in self.best_paths().items():
            src_peer = self.peers[learned_from]
            learned_ibgp = src_peer.remote_asn == self.asn
            if learned_from == peer.ip:
                continue  # don't echo a peer's routes back to it
            if not is_ibgp_peer and NO_EXPORT in attrs.communities:
                continue  # RFC 1997: received no-export never leaves this AS
            if learned_ibgp and is_ibgp_peer:
                # Plain iBGP split-horizon — unless we are a route reflector
                # and the route involves at least one client (RFC 4456).
                if not (self.is_reflector and (src_peer.rr_client or peer.rr_client)):
                    continue
                if attrs.originator and attrs.originator == peer.router_id:
                    continue  # never reflect a route back to its originator
            offer(
                prefix,
                replace(
                    attrs,
                    as_path=attrs.as_path if is_ibgp_peer else (self.asn, *attrs.as_path),
                    next_hop=str(my_nh),
                    local_pref=attrs.local_pref if is_ibgp_peer else 100,
                    # First injection into the AS stamps the originator;
                    # stripped again when the route leaves the AS.
                    originator=(attrs.originator or self.router_id) if is_ibgp_peer else "",
                ),
            )

        if peer.adj_out is not None and out == peer.adj_out:
            return  # snapshot unchanged since last send
        peer.adj_out = dict(out)
        self._send(net, peer, BgpUpdate(routes=out))

    # ----- introspection -------------------------------------------------------------------------
    def summary_rows(self) -> list[dict]:
        return [
            {
                "neighbor": str(p.ip),
                "remote_as": p.remote_asn,
                "state": p.state,
                "prefixes_received": len(p.rib_in),
                "rr_client": p.rr_client,
            }
            for p in self.peers.values()
        ]
