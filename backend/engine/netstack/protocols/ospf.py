"""OSPFv2 — multi-area link-state routing (simplified but event-faithful).

What is modelled (NG-SIM-05):
- periodic Hellos to 224.0.0.5 per enabled interface, tagged with the
  interface's **area**; adjacency only forms between same-area neighbors;
- per-area LSDBs: router LSAs flooded within their area, Dijkstra SPF per
  area (cost = ref_bandwidth / interface bandwidth);
- **ABR behaviour**: a router with links in area 0 plus others originates
  type-3 summary LSAs — non-backbone intra prefixes into area 0, and
  backbone intra + backbone-learned inter prefixes into its leaf areas
  (summaries are only *consumed* from the backbone, the RFC loop rule);
- inter-area routes installed as ``O IA``-style entries (intra-area wins);
- optional **default originate**: ABR injects 0.0.0.0/0 into leaf areas;
- dead-interval neighbor expiry, LSA re-origination and route withdrawal.

Not modelled (documented): DR/BDR election, NSSA/stub area types, LSA
aging/refresh, virtual links, authentication, OSPFv3.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from ipaddress import IPv4Address, IPv4Network
from typing import TYPE_CHECKING, Union

from engine.events import EventType, SimEvent
from engine.netstack.frames import ETH_IPV4, PROTO_OSPF, EthernetFrame, Ipv4Packet
from engine.netstack.iface import Interface
from engine.netstack.routing import Route, Router

if TYPE_CHECKING:  # pragma: no cover
    from engine.netstack.network import Network

logger = logging.getLogger(__name__)

OSPF_MCAST_IP = IPv4Address("224.0.0.5")
OSPF_MCAST_MAC = "01:00:5e:00:00:05"
REF_BANDWIDTH = 100_000_000.0  # 100 Mbps reference, Cisco default
BACKBONE = 0
LS_INFINITY = 0xFFFFFF  # RFC 2328 §12.4.3: summary at LSInfinity = withdrawn


# --- OSPF PDUs (payload of Ipv4Packet proto 89) ------------------------------

@dataclass(slots=True)
class OspfHello:
    router_id: str
    neighbors_seen: list[str] = field(default_factory=list)
    hello_interval: float = 10.0
    dead_interval: float = 40.0
    area: int = 0

    @property
    def wire_size(self) -> int:
        return 44 + 4 * len(self.neighbors_seen)

    def summary(self) -> str:
        return (
            f"OSPF Hello rid={self.router_id} area={self.area} "
            f"seen={len(self.neighbors_seen)}"
        )


@dataclass(slots=True)
class RouterLsa:
    router_id: str
    seq: int
    # ("ptp", neighbor_router_id, cost) | ("stub", "prefix/len", cost)
    links: list[tuple[str, str, int]] = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"rtr|{self.router_id}"

    @property
    def wire_size(self) -> int:
        return 24 + 12 * len(self.links)

    def copy(self) -> RouterLsa:
        return RouterLsa(self.router_id, self.seq, list(self.links))


@dataclass(slots=True)
class SummaryLsa:
    """Type-3 inter-area prefix summary, originated by an ABR."""

    router_id: str          # the originating ABR
    seq: int
    prefix: str             # "a.b.c.d/nn"
    metric: int

    @property
    def key(self) -> str:
        return f"sum|{self.router_id}|{self.prefix}"

    @property
    def wire_size(self) -> int:
        return 28

    def copy(self) -> SummaryLsa:
        return SummaryLsa(self.router_id, self.seq, self.prefix, self.metric)


Lsa = Union[RouterLsa, SummaryLsa]


@dataclass(slots=True)
class OspfLsu:
    lsas: list[Lsa] = field(default_factory=list)

    @property
    def wire_size(self) -> int:
        return 28 + sum(l.wire_size for l in self.lsas)

    def summary(self) -> str:
        return f"OSPF LSU {len(self.lsas)} LSA(s)"


@dataclass(slots=True)
class _Neighbor:
    router_id: str
    ip: IPv4Address
    iface_name: str
    area: int = 0
    state: str = "init"          # init | full
    last_seen: float = 0.0


class OspfProcess:
    """One OSPF instance attached to a Router."""

    proto = "ospf"

    def __init__(
        self,
        router: Router,
        router_id: str | None = None,
        hello_interval: float = 10.0,
        dead_interval: float | None = None,
        ifaces: list[str] | None = None,
        areas: dict[str, int] | None = None,
        default_originate: bool = False,
    ) -> None:
        self.router = router
        self.router_id = router_id or self._pick_router_id()
        self.hello_interval = hello_interval
        self.dead_interval = dead_interval if dead_interval is not None else hello_interval * 4
        self.iface_names = ifaces  # None = all L3 interfaces
        self.areas = {k: int(v) for k, v in (areas or {}).items()}  # iface -> area
        self.default_originate = default_originate
        # (router_id, area) -> neighbor
        self.neighbors: dict[tuple[str, int], _Neighbor] = {}
        # area -> lsa key -> LSA
        self.lsdb: dict[int, dict[str, Lsa]] = {}
        # (area, prefix) -> our originated summary (change detection)
        self._my_summaries: dict[tuple[int, str], SummaryLsa] = {}
        self._seq = 0
        self._started = False
        self._spf_pending = False
        router.processes.append(self)

    def _pick_router_id(self) -> str:
        ips = self.router.all_ips()
        return str(max(i.ip for i in ips)) if ips else self.router.name

    def iface_area(self, iface_name: str) -> int:
        return self.areas.get(iface_name, BACKBONE)

    def my_areas(self) -> list[int]:
        return sorted({self.iface_area(i.name) for i in self._enabled_ifaces()})

    @property
    def is_abr(self) -> bool:
        areas = self.my_areas()
        return len(areas) > 1 and BACKBONE in areas

    def _enabled_ifaces(self) -> list[Interface]:
        out = []
        for name, iface in self.router.interfaces.items():
            if self.iface_names is not None and name not in self.iface_names:
                continue
            if iface.ips:
                out.append(iface)
        return out

    def _area_db(self, area: int) -> dict[str, Lsa]:
        return self.lsdb.setdefault(area, {})

    # ----- lifecycle ---------------------------------------------------------
    def start(self, net: Network) -> None:
        if self._started:
            return
        self._started = True
        for area in self.my_areas():
            self._originate_lsa(net, area, flood=False)
        self._tick(net)

    def _tick(self, net: Network) -> None:
        # ponytail: keep the loop alive across power-cycles (F36/F47) — gate
        # the work, not the reschedule, so a power-on self-heals within one
        # interval instead of needing an explicit restart.
        if self.router.powered_on:
            self._expire_neighbors(net)
            self._send_hellos(net)
        net.scheduler.schedule_after(
            self.hello_interval,
            SimEvent(
                time=0.0,
                type=EventType.TIMER,
                handler=lambda _c, _e: self._tick(net),
                node_id=self.router.node_id,
            ),
        )

    # ----- hello protocol -------------------------------------------------------
    def _send_hellos(self, net: Network) -> None:
        for iface in self._enabled_ifaces():
            if not iface.is_up or not iface.ip:
                continue
            area = self.iface_area(iface.name)
            seen = [
                n.router_id for n in self.neighbors.values() if n.area == area
            ]
            iface.transmit(
                net,
                EthernetFrame(
                    src_mac=iface.mac,
                    dst_mac=OSPF_MCAST_MAC,
                    ethertype=ETH_IPV4,
                    payload=Ipv4Packet(
                        src=iface.ip.ip,
                        dst=OSPF_MCAST_IP,
                        proto=PROTO_OSPF,
                        ttl=1,
                        dscp=48,
                        payload=OspfHello(
                            router_id=self.router_id,
                            neighbors_seen=seen,
                            hello_interval=self.hello_interval,
                            dead_interval=self.dead_interval,
                            area=area,
                        ),
                    ),
                ),
            )

    def on_packet(self, net: Network, iface: Interface, pkt: Ipv4Packet) -> None:
        payload = pkt.payload
        if isinstance(payload, OspfHello):
            self._on_hello(net, iface, pkt.src, payload)
        elif isinstance(payload, OspfLsu):
            self._on_lsu(net, iface, pkt.src, payload)

    def _on_hello(
        self, net: Network, iface: Interface, src: IPv4Address, hello: OspfHello
    ) -> None:
        if hello.router_id == self.router_id:
            return
        area = self.iface_area(iface.name)
        if hello.area != area:
            net.record_drop("ospf_area_mismatch")
            return
        key = (hello.router_id, area)
        nbr = self.neighbors.get(key)
        if nbr is None:
            nbr = _Neighbor(
                router_id=hello.router_id, ip=src, iface_name=iface.name,
                area=area, state="init",
            )
            self.neighbors[key] = nbr
        nbr.ip = src
        nbr.iface_name = iface.name
        nbr.last_seen = net.now

        if self.router_id in hello.neighbors_seen and nbr.state != "full":
            nbr.state = "full"
            logger.debug(
                "%s: adjacency FULL with %s (area %s)",
                self.router_id, nbr.router_id, area,
            )
            self._originate_lsa(net, area)
            # Database sync: give the new neighbor this area's entire LSDB.
            self._send_lsu(net, nbr, list(self._area_db(area).values()))

    def _expire_neighbors(self, net: Network) -> None:
        dead = [
            key
            for key, n in self.neighbors.items()
            if n.last_seen and net.now - n.last_seen > self.dead_interval
        ]
        for key in dead:
            del self.neighbors[key]
        if dead:
            logger.debug("%s: neighbors dead: %s", self.router_id, dead)
            for _rid, area in dead:
                self._originate_lsa(net, area)
            self._schedule_spf(net)

    # ----- LSA origination / flooding -----------------------------------------------
    def _iface_cost(self, iface: Interface) -> int:
        att = iface.attachment
        bw = att.bandwidth_bps if att else 1e9
        return max(1, int(REF_BANDWIDTH / max(bw, 1.0)))

    def _originate_lsa(self, net: Network, area: int, flood: bool = True) -> None:
        self._seq += 1
        links: list[tuple[str, str, int]] = []
        for iface in self._enabled_ifaces():
            if self.iface_area(iface.name) != area:
                continue
            cost = self._iface_cost(iface)
            for ip in iface.ips:
                links.append(("stub", str(ip.network), cost))
            for nbr in self.neighbors.values():
                if (
                    nbr.iface_name == iface.name
                    and nbr.area == area
                    and nbr.state == "full"
                ):
                    links.append(("ptp", nbr.router_id, cost))
        lsa = RouterLsa(router_id=self.router_id, seq=self._seq, links=links)
        self._area_db(area)[lsa.key] = lsa
        if flood:
            self._flood(net, lsa, area, exclude_rid=None)
        self._schedule_spf(net)

    def _flood(
        self, net: Network, lsa: Lsa, area: int, exclude_rid: str | None
    ) -> None:
        for nbr in self.neighbors.values():
            if nbr.area != area or nbr.state != "full" or nbr.router_id == exclude_rid:
                continue
            self._send_lsu(net, nbr, [lsa])

    def _send_lsu(self, net: Network, nbr: _Neighbor, lsas: list[Lsa]) -> None:
        if not lsas:
            return
        iface = self.router.interfaces.get(nbr.iface_name)
        if iface is None or not iface.ip:
            return
        self.router.send_ip(
            net,
            Ipv4Packet(
                src=iface.ip.ip,
                dst=nbr.ip,
                proto=PROTO_OSPF,
                ttl=1,
                dscp=48,
                payload=OspfLsu(lsas=[l.copy() for l in lsas]),
            ),
        )

    def _on_lsu(
        self, net: Network, iface: Interface, src: IPv4Address, lsu: OspfLsu
    ) -> None:
        area = self.iface_area(iface.name)
        db = self._area_db(area)
        sender_rid = next(
            (n.router_id for n in self.neighbors.values()
             if n.ip == src and n.area == area),
            None,
        )
        changed = False
        for lsa in lsu.lsas:
            current = db.get(lsa.key)
            if current is None or lsa.seq > current.seq:
                db[lsa.key] = lsa
                self._flood(net, lsa, area, exclude_rid=sender_rid)
                changed = True
        if changed:
            self._schedule_spf(net)

    # ----- SPF ------------------------------------------------------------------------
    def _schedule_spf(self, net: Network) -> None:
        """Debounce SPF: one run per burst of LSDB changes."""
        if self._spf_pending:
            return
        self._spf_pending = True
        net.scheduler.schedule_after(
            0.05,
            SimEvent(
                time=0.0,
                type=EventType.TIMER,
                handler=lambda _c, _e: self._run_spf(net),
                node_id=self.router.node_id,
            ),
        )

    def _spf_area(self, area: int) -> tuple[dict[str, int], dict[str, str]]:
        """Dijkstra over one area's router LSAs: (dist, first_hop) by rid."""
        db = self._area_db(area)
        adj: dict[str, list[tuple[str, int]]] = {}
        for lsa in db.values():
            if not isinstance(lsa, RouterLsa):
                continue
            for kind, target, cost in lsa.links:
                if kind != "ptp":
                    continue
                peer = db.get(f"rtr|{target}")
                if not isinstance(peer, RouterLsa):
                    continue
                if not any(
                    k == "ptp" and t == lsa.router_id for k, t, _ in peer.links
                ):
                    continue  # not bidirectional -> not usable
                adj.setdefault(lsa.router_id, []).append((target, cost))

        import heapq

        dist: dict[str, int] = {self.router_id: 0}
        first_hop: dict[str, str] = {}
        heap: list[tuple[int, str, str | None]] = [(0, self.router_id, None)]
        visited: set[str] = set()
        while heap:
            d, rid, fh = heapq.heappop(heap)
            if rid in visited:
                continue
            visited.add(rid)
            if fh is not None:
                first_hop[rid] = fh
            for nxt, cost in adj.get(rid, ()):
                nd = d + cost
                if nxt not in dist or nd < dist[nxt]:
                    dist[nxt] = nd
                    heapq.heappush(heap, (nd, nxt, fh if fh is not None else nxt))
        return dist, first_hop

    def _run_spf(self, net: Network) -> None:
        self._spf_pending = False
        local_prefixes = {ip.network for ip in self.router.all_ips()}
        # prefix -> (next_hop, iface, metric, is_intra)
        desired: dict[IPv4Network, tuple[IPv4Address, str, int, bool]] = {}
        # area -> {prefix: metric} of *intra-area* reachable prefixes (for ABR
        # summarization) — includes our own connected prefixes in that area.
        intra_by_area: dict[int, dict[str, int]] = {}

        for area in self.my_areas():
            dist, first_hop = self._spf_area(area)
            db = self._area_db(area)
            intra: dict[str, int] = {}
            for iface in self._enabled_ifaces():
                if self.iface_area(iface.name) == area:
                    for ip in iface.ips:
                        intra[str(ip.network)] = self._iface_cost(iface)

            def nbr_for(rid: str):
                fh = first_hop.get(rid)
                return self.neighbors.get((fh, area)) if fh else None

            def offer(prefix: IPv4Network, nh, iface_name, total: int, is_intra: bool):
                """Install preference: intra beats inter, then lower metric."""
                cur = desired.get(prefix)
                if (
                    cur is None
                    or (is_intra and not cur[3])
                    or (is_intra == cur[3] and total < cur[2])
                ):
                    desired[prefix] = (nh, iface_name, total, is_intra)

            for lsa in db.values():
                if isinstance(lsa, RouterLsa):
                    rid = lsa.router_id
                    if rid == self.router_id or rid not in dist:
                        continue
                    nbr = nbr_for(rid)
                    if nbr is None:
                        continue
                    for kind, target, cost in lsa.links:
                        if kind != "stub":
                            continue
                        prefix = IPv4Network(target)
                        total = dist[rid] + cost
                        intra_cur = intra.get(target)
                        if intra_cur is None or total < intra_cur:
                            intra[target] = total
                        if prefix in local_prefixes:
                            continue
                        offer(prefix, nbr.ip, nbr.iface_name, total, True)
                elif isinstance(lsa, SummaryLsa):
                    # Consume summaries only from the backbone unless we are
                    # an internal (single-area) router — the RFC loop rule.
                    if self.is_abr and area != BACKBONE:
                        continue
                    if lsa.router_id == self.router_id:
                        continue
                    if lsa.metric >= LS_INFINITY:
                        continue  # withdrawn summary
                    abr_dist = dist.get(lsa.router_id)
                    nbr = nbr_for(lsa.router_id)
                    if abr_dist is None or nbr is None:
                        continue
                    prefix = IPv4Network(lsa.prefix)
                    if prefix in local_prefixes:
                        continue
                    offer(prefix, nbr.ip, nbr.iface_name, abr_dist + lsa.metric, False)
            intra_by_area[area] = intra

        self.router.withdraw_routes("ospf")
        for prefix, (nh, iface_name, metric, _intra) in desired.items():
            self.router.install_route(
                Route(
                    prefix=prefix,
                    next_hop=nh,
                    iface_name=iface_name,
                    source="ospf",
                    metric=metric,
                )
            )

        if self.is_abr:
            self._originate_summaries(net, desired, intra_by_area)

    # ----- ABR summarization (type-3) ---------------------------------------------
    def _originate_summaries(
        self,
        net: Network,
        desired: dict[IPv4Network, tuple[IPv4Address, str, int, bool]],
        intra_by_area: dict[int, dict[str, int]],
    ) -> None:
        wanted: dict[tuple[int, str], int] = {}   # (into_area, prefix) -> metric

        backbone_prefixes: dict[str, int] = dict(intra_by_area.get(BACKBONE, {}))
        # Inter-area prefixes learned via backbone summaries are re-advertised
        # into leaf areas so multi-hop area chains (1—0—2) converge.
        for prefix, (_nh, _if, metric, is_intra) in desired.items():
            if not is_intra:
                backbone_prefixes.setdefault(str(prefix), metric)

        for area in self.my_areas():
            if area == BACKBONE:
                # Leaf intra prefixes go into the backbone.
                for leaf in self.my_areas():
                    if leaf == BACKBONE:
                        continue
                    for prefix, metric in intra_by_area.get(leaf, {}).items():
                        cur = wanted.get((BACKBONE, prefix))
                        if cur is None or metric < cur:
                            wanted[(BACKBONE, prefix)] = metric
            else:
                for prefix, metric in backbone_prefixes.items():
                    if prefix in intra_by_area.get(area, {}):
                        continue  # already intra there
                    wanted[(area, prefix)] = metric
                if self.default_originate:
                    wanted[(area, "0.0.0.0/0")] = 1

        for (area, prefix), metric in sorted(wanted.items()):
            current = self._my_summaries.get((area, prefix))
            if current is not None and current.metric == metric:
                continue
            self._seq += 1
            lsa = SummaryLsa(
                router_id=self.router_id, seq=self._seq, prefix=prefix, metric=metric
            )
            self._my_summaries[(area, prefix)] = lsa
            self._area_db(area)[lsa.key] = lsa
            self._flood(net, lsa, area, exclude_rid=None)

        # Withdraw summaries for prefixes that vanished: deleting locally is
        # not enough — other routers would keep the stale route forever. Flood
        # a newer instance at LSInfinity so receivers drop it (RFC 2328 trick);
        # the infinity instance stays in the DB for sync with late joiners.
        for (area, prefix), lsa in list(self._my_summaries.items()):
            if (area, prefix) in wanted or lsa.metric >= LS_INFINITY:
                continue
            self._seq += 1
            dead = SummaryLsa(
                router_id=self.router_id, seq=self._seq,
                prefix=prefix, metric=LS_INFINITY,
            )
            self._my_summaries[(area, prefix)] = dead
            self._area_db(area)[dead.key] = dead
            self._flood(net, dead, area, exclude_rid=None)

    # ----- introspection ------------------------------------------------------------------
    def neighbor_rows(self) -> list[dict]:
        return [
            {
                "router_id": n.router_id,
                "ip": str(n.ip),
                "iface": n.iface_name,
                "area": n.area,
                "state": n.state,
            }
            for n in self.neighbors.values()
        ]
