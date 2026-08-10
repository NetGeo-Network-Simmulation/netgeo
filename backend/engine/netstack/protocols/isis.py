"""IS-IS — Intermediate System to Intermediate System, level-2 single-area IGP.

Structurally identical to :mod:`engine.netstack.protocols.ospf`
(hello -> adjacency -> link-state flooding -> SPF -> RIB install), but the
protocol rides directly on L2 (OSI CLNS) instead of over IP: IIHs and LSPs are
sent to the AllL2ISs multicast MAC and dispatched at the frame edge in
``Router.on_frame``.

What is modelled (NG-SIM-07), level-2 single-area only:
- periodic point-to-point IIH hellos per enabled interface; a P2P 3-way
  handshake brings the adjacency ``up`` (we hear our own system-id echoed);
- one flat level-2 LSDB: each router originates an LSP listing its ``is``
  adjacencies and its ``ip`` connected prefixes; LSPs are flooded newest-wins;
- Dijkstra SPF over the LSDB (fixed per-interface metric, default 10) installs
  routes with ``source="isis"`` (admin distance 115) and the neighbour's
  advertised interface address as next hop;
- hold-time adjacency expiry, LSP re-origination and route withdrawal.

Not modelled (documented deferrals — ponytail, add when a lab needs it):
- **L1 / L1-L2 routing and route leaking** — level-1 areas, the ATT bit and
  L1<->L2 redistribution are out of scope; this is a single L2 domain.
- **CSNP / PSNP** — reliable flooding here relies on a full-LSDB dump to each
  newly-up neighbour (the DES is lossless in tests); periodic CSNP resync and
  PSNP retransmit would be the next step for lossy links.
- **LAN / DIS election** (pseudonode LSP) — P2P links only for now.
- **wide metrics (RFC 5305), metric-style/overload bit, auth** — a single
  narrow-style metric per interface, no 1023 path cap enforced.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from ipaddress import IPv4Address, IPv4Network
from typing import TYPE_CHECKING

from engine.events import EventType, SimEvent
from engine.netstack.frames import (
    ALL_L2_ISS_MAC,
    ETH_ISIS,
    EthernetFrame,
    IsisHello,
    IsisLsp,
)
from engine.netstack.iface import Interface
from engine.netstack.routing import Route, Router

if TYPE_CHECKING:  # pragma: no cover
    from engine.netstack.network import Network

logger = logging.getLogger(__name__)

DEFAULT_METRIC = 10  # IS-IS default interface metric (narrow-style)


@dataclass(slots=True)
class _Adjacency:
    system_id: str
    iface_name: str
    ip: IPv4Address | None = None   # neighbour's interface address (route NH)
    state: str = "init"             # init | up
    last_seen: float = 0.0


class IsisProcess:
    """One IS-IS instance attached to a Router (level-2, single area)."""

    proto = "isis"

    def __init__(
        self,
        router: Router,
        system_id: str | None = None,
        level: int = 2,
        hello_interval: float = 10.0,
        hold_multiplier: int = 3,
        ifaces: list[str] | None = None,
        metrics: dict[str, int] | None = None,
        area_id: str = "49.0001",
    ) -> None:
        self.router = router
        self.system_id = system_id or self._pick_system_id()
        self.level = level
        self.hello_interval = hello_interval
        self.hold_time = hello_interval * hold_multiplier
        self.iface_names = ifaces  # None = all L3 interfaces
        self.metrics = {str(k): int(v) for k, v in (metrics or {}).items()}
        self.area_id = area_id
        # (iface_name, system_id) -> adjacency
        self.neighbors: dict[tuple[str, str], _Adjacency] = {}
        # system_id -> LSP  (one flat level-2 database)
        self.lsdb: dict[str, IsisLsp] = {}
        self._seq = 0
        self._started = False
        self._spf_pending = False
        router.processes.append(self)

    def _pick_system_id(self) -> str:
        """Derive a 6-byte system-id from the highest interface IPv4, the
        classic ``192.168.1.1`` -> ``1921.6800.1001`` convention."""
        ips = self.router.all_ips()
        if ips:
            digits = "".join(f"{o:03d}" for o in max(i.ip for i in ips).packed)
            return f"{digits[0:4]}.{digits[4:8]}.{digits[8:12]}"
        return self.router.name

    def _iface_metric(self, iface: Interface) -> int:
        return self.metrics.get(iface.name, DEFAULT_METRIC)

    def _enabled_ifaces(self) -> list[Interface]:
        out = []
        for name, iface in self.router.interfaces.items():
            if self.iface_names is not None and name not in self.iface_names:
                continue
            if iface.ips:
                out.append(iface)
        return out

    # ----- lifecycle ---------------------------------------------------------
    def start(self, net: "Network") -> None:
        if self._started:
            return
        self._started = True
        self._originate_lsp(net, flood=False)
        self._tick(net)

    def _tick(self, net: "Network") -> None:
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

    # ----- hello protocol ----------------------------------------------------
    def _send_hellos(self, net: "Network") -> None:
        for iface in self._enabled_ifaces():
            if not iface.is_up:
                continue
            seen = [
                a.system_id
                for a in self.neighbors.values()
                if a.iface_name == iface.name
            ]
            iface.transmit(
                net,
                EthernetFrame(
                    src_mac=iface.mac,
                    dst_mac=ALL_L2_ISS_MAC,
                    ethertype=ETH_ISIS,
                    payload=IsisHello(
                        system_id=self.system_id,
                        neighbors_seen=seen,
                        ip_addresses=[str(ip.ip) for ip in iface.ips],
                        hold_time=self.hold_time,
                        level=self.level,
                        area_id=self.area_id,
                    ),
                ),
            )

    def on_frame(self, net: "Network", iface: Interface, pdu) -> None:
        if isinstance(pdu, IsisHello):
            self._on_hello(net, iface, pdu)
        elif isinstance(pdu, IsisLsp):
            self._on_lsp(net, iface, pdu)

    def _on_hello(self, net: "Network", iface: Interface, hello: IsisHello) -> None:
        if hello.system_id == self.system_id:
            return
        key = (iface.name, hello.system_id)
        adj = self.neighbors.get(key)
        if adj is None:
            adj = _Adjacency(system_id=hello.system_id, iface_name=iface.name)
            self.neighbors[key] = adj
        if hello.ip_addresses:
            adj.ip = IPv4Address(hello.ip_addresses[0])
        adj.last_seen = net.now

        if self.system_id in hello.neighbors_seen and adj.state != "up":
            adj.state = "up"
            logger.debug("%s: adjacency UP with %s on %s",
                         self.system_id, adj.system_id, iface.name)
            self._originate_lsp(net)
            # Database sync: dump the whole LSDB to the newly-up neighbour.
            # ponytail: CSNP/PSNP resync omitted — a full dump is enough on a
            # lossless DES; add SNPs when links can drop control PDUs.
            for lsp in list(self.lsdb.values()):
                self._send_lsp(net, iface.name, lsp)
            self._schedule_spf(net)

    def _expire_neighbors(self, net: "Network") -> None:
        dead = [
            key
            for key, a in self.neighbors.items()
            if a.last_seen and net.now - a.last_seen > self.hold_time
        ]
        for key in dead:
            del self.neighbors[key]
        if dead:
            logger.debug("%s: adjacencies dead: %s", self.system_id, dead)
            self._originate_lsp(net)
            self._schedule_spf(net)

    # ----- LSP origination / flooding ----------------------------------------
    def _originate_lsp(self, net: "Network", flood: bool = True) -> None:
        self._seq += 1
        links: list[tuple[str, str, int]] = []
        for iface in self._enabled_ifaces():
            metric = self._iface_metric(iface)
            for ip in iface.ips:
                links.append(("ip", str(ip.network), metric))
            for adj in self.neighbors.values():
                if adj.iface_name == iface.name and adj.state == "up":
                    links.append(("is", adj.system_id, metric))
        lsp = IsisLsp(system_id=self.system_id, seq=self._seq, links=links, level=self.level)
        self.lsdb[self.system_id] = lsp
        if flood:
            self._flood(net, lsp, exclude_iface=None)
        self._schedule_spf(net)

    def _flood(self, net: "Network", lsp: IsisLsp, exclude_iface: str | None) -> None:
        sent: set[str] = set()
        for adj in self.neighbors.values():
            if adj.state != "up" or adj.iface_name == exclude_iface:
                continue
            if adj.iface_name in sent:
                continue  # one multicast per interface reaches all neighbours
            sent.add(adj.iface_name)
            self._send_lsp(net, adj.iface_name, lsp)

    def _send_lsp(self, net: "Network", iface_name: str, lsp: IsisLsp) -> None:
        iface = self.router.interfaces.get(iface_name)
        if iface is None or not iface.is_up:
            return
        iface.transmit(
            net,
            EthernetFrame(
                src_mac=iface.mac,
                dst_mac=ALL_L2_ISS_MAC,
                ethertype=ETH_ISIS,
                payload=lsp.copy(),   # own instance per receiver, no aliasing
            ),
        )

    def _on_lsp(self, net: "Network", iface: Interface, lsp: IsisLsp) -> None:
        current = self.lsdb.get(lsp.system_id)
        if current is None or lsp.seq > current.seq:
            self.lsdb[lsp.system_id] = lsp.copy()
            self._flood(net, lsp, exclude_iface=iface.name)
            self._schedule_spf(net)

    # ----- SPF ---------------------------------------------------------------
    def _schedule_spf(self, net: "Network") -> None:
        """Debounce SPF: one run per burst of LSDB changes (mirrors OSPF)."""
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

    def _dijkstra(self) -> tuple[dict[str, int], dict[str, str]]:
        """Dijkstra over the LSDB's ``is`` adjacencies: (dist, first_hop)."""
        db = self.lsdb
        adj: dict[str, list[tuple[str, int]]] = {}
        for lsp in db.values():
            for kind, target, cost in lsp.links:
                if kind != "is":
                    continue
                peer = db.get(target)
                if peer is None:
                    continue
                if not any(k == "is" and t == lsp.system_id for k, t, _ in peer.links):
                    continue  # not bidirectional -> not usable
                adj.setdefault(lsp.system_id, []).append((target, cost))

        import heapq

        dist: dict[str, int] = {self.system_id: 0}
        first_hop: dict[str, str] = {}
        heap: list[tuple[int, str, str | None]] = [(0, self.system_id, None)]
        visited: set[str] = set()
        while heap:
            d, sid, fh = heapq.heappop(heap)
            if sid in visited:
                continue
            visited.add(sid)
            if fh is not None:
                first_hop[sid] = fh
            for nxt, cost in adj.get(sid, ()):
                nd = d + cost
                if nxt not in dist or nd < dist[nxt]:
                    dist[nxt] = nd
                    heapq.heappush(heap, (nd, nxt, fh if fh is not None else nxt))
        return dist, first_hop

    def _adj_for(self, first_hop_sid: str | None) -> _Adjacency | None:
        if first_hop_sid is None:
            return None
        for a in self.neighbors.values():
            if a.system_id == first_hop_sid and a.state == "up" and a.ip is not None:
                return a
        return None

    def _run_spf(self, net: "Network") -> None:
        self._spf_pending = False
        dist, first_hop = self._dijkstra()
        local_prefixes = {ip.network for ip in self.router.all_ips()}
        # prefix -> (next_hop, iface, metric)
        desired: dict[IPv4Network, tuple[IPv4Address, str, int]] = {}

        for lsp in self.lsdb.values():
            sid = lsp.system_id
            if sid == self.system_id or sid not in dist:
                continue
            nbr = self._adj_for(first_hop.get(sid))
            if nbr is None:
                continue
            for kind, target, cost in lsp.links:
                if kind != "ip":
                    continue
                prefix = IPv4Network(target)
                if prefix in local_prefixes:
                    continue
                total = dist[sid] + cost
                cur = desired.get(prefix)
                if cur is None or total < cur[2]:
                    desired[prefix] = (nbr.ip, nbr.iface_name, total)

        self.router.withdraw_routes("isis")
        for prefix, (nh, iface_name, metric) in desired.items():
            self.router.install_route(
                Route(
                    prefix=prefix,
                    next_hop=nh,
                    iface_name=iface_name,
                    source="isis",
                    metric=metric,
                )
            )

    # ----- introspection -----------------------------------------------------
    def neighbor_rows(self) -> list[dict]:
        return [
            {
                "system_id": a.system_id,
                "iface": a.iface_name,
                "ip": str(a.ip) if a.ip else "-",
                "state": a.state,
            }
            for a in self.neighbors.values()
        ]

    def lsdb_rows(self) -> list[dict]:
        return [
            {"system_id": lsp.system_id, "seq": lsp.seq, "links": len(lsp.links)}
            for lsp in sorted(self.lsdb.values(), key=lambda l: l.system_id)
        ]
