"""BGP-4 — path-vector routing between autonomous systems (simplified).

What is modelled (v2, NG-SIM-06):
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
- best-path selection: highest local-pref, shortest AS-path, lowest peer IP;
- hold-timer expiry drops the session and withdraws its routes;
- iBGP split-horizon for non-reflectors; next-hop-self on advertisement.

Not modelled: MED, confederations, MP-BGP, dynamic capability negotiation.
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


# --- route attributes ----------------------------------------------------------

@dataclass(slots=True)
class BgpAttrs:
    """Path attributes carried with every prefix in an UPDATE."""

    as_path: tuple[int, ...] = ()
    next_hop: str = ""
    local_pref: int = 100
    communities: tuple[str, ...] = ()
    originator: str = ""          # router-id of the speaker that injected
                                  # the route into this AS (RFC 4456-ish)

    @property
    def wire_size(self) -> int:
        return 9 + 2 * len(self.as_path) + 4 * len(self.communities)


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
class _Peer:
    ip: IPv4Address
    remote_asn: int
    state: str = "idle"                  # idle | open-sent | established
    hold_time: float = 90.0
    last_keepalive: float = 0.0
    router_id: str = ""                  # learned from the peer's OPEN
    rr_client: bool = False
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
    ) -> None:
        self.router = router
        self.asn = asn
        self.router_id = router_id or (
            str(max((i.ip for i in router.all_ips()), default="0.0.0.0"))
        )
        self.keepalive_interval = keepalive_interval
        self.hold_time = hold_time
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

    # ----- lifecycle ------------------------------------------------------------
    def start(self, net: Network) -> None:
        if self._started:
            return
        self._started = True
        for peer in self.peers.values():
            self._try_open(net, peer)
        self._tick(net)

    def _tick(self, net: Network) -> None:
        # ponytail: keep the loop alive across power-cycles (F36/F47) — gate
        # the work, not the reschedule, so a power-on self-heals within one
        # interval instead of needing an explicit restart.
        if self.router.powered_on:
            for peer in self.peers.values():
                if peer.state == "established":
                    if net.now - peer.last_keepalive > peer.hold_time:
                        self._session_down(net, peer)
                    else:
                        self._send(net, peer, BgpKeepalive())
                elif peer.state == "idle":
                    self._try_open(net, peer)   # automatic retry
        net.scheduler.schedule_after(
            self.keepalive_interval,
            SimEvent(
                time=0.0,
                type=EventType.TIMER,
                handler=lambda _c, _e: self._tick(net),
                node_id=self.router.node_id,
            ),
        )

    def _try_open(self, net: Network, peer: _Peer) -> None:
        peer.state = "open-sent"
        self._send(
            net,
            peer,
            BgpOpen(asn=self.asn, router_id=self.router_id, hold_time=self.hold_time),
            flags="SYN",
        )

    def _session_down(self, net: Network, peer: _Peer) -> None:
        logger.debug("%s: BGP session to %s down", self.router_id, peer.ip)
        peer.state = "idle"
        peer.rib_in.clear()
        peer.adj_out = None          # a new session must get a fresh UPDATE
        self._decide_and_install(net)
        self._advertise_all(net)

    # ----- transport ---------------------------------------------------------------
    def _send(self, net: Network, peer: _Peer, msg, flags: str = "PSH") -> None:
        route = self.router.egress_for(peer.ip)
        src_ip = route[0].ip.ip if route and route[0].ip else None
        if src_ip is None:
            return
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

    # ----- message handling ------------------------------------------------------------
    def on_packet(self, net: Network, iface: Interface, pkt: Ipv4Packet) -> None:
        seg = pkt.payload
        if not isinstance(seg, TcpSegment):
            return
        peer = self.peers.get(pkt.src)
        if peer is None:
            return
        msg = seg.payload
        if isinstance(msg, BgpOpen):
            if msg.asn != peer.remote_asn:
                logger.debug("%s: OPEN from %s wrong ASN %s", self.router_id, pkt.src, msg.asn)
                return
            peer.hold_time = min(self.hold_time, msg.hold_time)
            peer.router_id = msg.router_id
            if peer.state != "established":
                if peer.state == "idle":
                    # Passive side: answer with our own OPEN.
                    self._try_open(net, peer)
                self._send(net, peer, BgpKeepalive())
        elif isinstance(msg, BgpKeepalive):
            peer.last_keepalive = net.now
            if peer.state != "established":
                peer.state = "established"
                logger.debug("%s: BGP ESTABLISHED with %s", self.router_id, peer.ip)
                self._send(net, peer, BgpKeepalive())
                self._advertise(net, peer)
        elif isinstance(msg, BgpUpdate):
            peer.last_keepalive = net.now
            self._on_update(net, peer, msg)

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

    @staticmethod
    def _better(
        attrs: BgpAttrs,
        peer_ip: IPv4Address,
        cur: tuple[BgpAttrs, IPv4Address],
    ) -> bool:
        cur_attrs, cur_peer = cur
        if attrs.local_pref != cur_attrs.local_pref:
            return attrs.local_pref > cur_attrs.local_pref
        if len(attrs.as_path) != len(cur_attrs.as_path):
            return len(attrs.as_path) < len(cur_attrs.as_path)
        return peer_ip < cur_peer

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
