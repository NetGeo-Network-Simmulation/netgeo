"""IS-IS — Intermediate System to Intermediate System, level-2 single-area IGP.

Structurally identical to :mod:`engine.netstack.protocols.ospf`
(hello -> adjacency -> link-state flooding -> SPF -> RIB install), but the
protocol rides directly on L2 (OSI CLNS) instead of over IP: IIHs and LSPs are
sent to the AllL2ISs multicast MAC and dispatched at the frame edge in
``Router.on_frame``.

What is modelled (NG-SIM-07, DIS election P-1b, pseudonode LSP P-1d),
level-2 single-area only:
- periodic IIH hellos per enabled interface; on a point-to-point link a 3-way
  handshake brings the adjacency ``up`` (we hear our own system-id echoed);
- one flat level-2 LSDB: each router originates an LSP listing its ``is``
  adjacencies and its ``ip`` connected prefixes; LSPs are flooded newest-wins;
- Dijkstra SPF over the LSDB (fixed per-interface metric, default 10) installs
  routes with ``source="isis"`` (admin distance 115) and the neighbour's
  advertised interface address as next hop;
- hold-time adjacency expiry, LSP re-origination and route withdrawal;
- **LAN DIS election** (ISO 10589 §8.4.5): an interface whose peer is a
  Switch (broadcast segment, several IS's rather than one) carries a LAN
  priority (default 64, configurable per-interface) and recomputes its
  Designated IS on every Hello and every neighbour expiry — highest priority
  wins, tie broken by system-id descending (see deviation note below).
  Unlike OSPF's DR/BDR there is no backup role and, critically, election is
  **preemptive**: a higher-priority IS joining later *does* take over the
  DIS, per §8.4.5 — do not copy OSPF's non-preemptive rule here. Adjacency
  state (``up``) is unaffected by DIS role — every IS on a LAN still forms a
  direct adjacency with every other IS (no 2-Way-style gating as in OSPF);
- **pseudonode LSP** (ISO 10589 §7.2.5/§9.8, P-1d): the DIS of a broadcast
  circuit (and only the DIS) originates a pseudonode LSP for it, keyed
  ``<dis_system_id>.<circuit_id>`` (circuit_id non-zero; a normal router LSP
  uses ``.00``), listing every IS Up-adjacent on that circuit plus itself
  via ``("is", system_id, 0)`` entries — metric 0, per the standard. A
  router's own LSP replaces its per-neighbour ``is`` links on that circuit
  with a single link to the pseudonode; SPF treats the pseudonode as a
  vertex (cost 0 outbound, the circuit's metric inbound), gated by the same
  bidirectionality check as a direct ``is`` link. A LAN IIH advertises the
  sender's own candidate LSP-ID for the circuit (``lan_id``) regardless of
  whether it is currently DIS, so whoever wins election is immediately
  identifiable by everyone else. A dead or preempted DIS's old pseudonode
  LSP is simply left unreferenced once survivors point at the new DIS's —
  no route leaks through it. Point-to-point links are unaffected: no DIS,
  no pseudonode, direct ``is`` links as before.

Not modelled (documented deferrals — ponytail, add when a lab needs it):
- **L1 / L1-L2 routing and route leaking** — level-1 areas, the ATT bit and
  L1<->L2 redistribution are out of scope; this is a single L2 domain.
- **CSNP / PSNP** — reliable flooding here relies on a full-LSDB dump to each
  newly-up neighbour (the DES is lossless in tests); periodic CSNP resync and
  PSNP retransmit would be the next step for lossy links.
- **DIS tiebreak by SNPA (MAC)** — ISO 10589 breaks a priority tie by highest
  MAC address, not system-id. This repo's L2 dispatch (``Router.on_frame`` ->
  ``IsisProcess.on_frame``) only forwards the decoded IIH, not the sending
  interface's MAC, so the tiebreak falls back to system-id descending
  instead (deliberate, sanctioned deviation — add MAC forwarding if a lab
  ever needs a MAC-accurate tiebreak).
- **wide metrics (RFC 5305), metric-style/overload bit, auth** — a single
  narrow-style metric per interface, no 1023 path cap enforced.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
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
    priority: int = 64               # neighbour's advertised LAN priority
    lan_id: str = ""                 # neighbour's own candidate pseudonode LSP-ID


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
        priorities: dict[str, int] | None = None,
    ) -> None:
        self.router = router
        self.system_id = system_id or self._pick_system_id()
        self.level = level
        self.hello_interval = hello_interval
        self.hold_time = hello_interval * hold_multiplier
        self.iface_names = ifaces  # None = all L3 interfaces
        self.metrics = {str(k): int(v) for k, v in (metrics or {}).items()}
        self.area_id = area_id
        self.priorities = {str(k): int(v) for k, v in (priorities or {}).items()}  # iface -> LAN priority
        # (iface_name, system_id) -> adjacency
        self.neighbors: dict[tuple[str, str], _Adjacency] = {}
        # system_id -> LSP  (one flat level-2 database)
        self.lsdb: dict[str, IsisLsp] = {}
        # iface name -> elected DIS system_id; only present for LAN interfaces
        # (peer is a Switch) — a point-to-point iface never gets an entry.
        self._iface_dis: dict[str, str] = {}
        # iface name -> this router's own local circuit id for that LAN
        # interface (P-1d), used as the pseudonode LSP-ID suffix if/when this
        # router is elected DIS there. Assigned once at start(), 1-based in
        # sorted iface-name order — distinct per LAN interface on this router
        # so being DIS on two segments at once can't collide on one LSP-ID.
        self._iface_circuit_id: dict[str, int] = {}
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

    def iface_priority(self, iface_name: str) -> int:
        return self.priorities.get(iface_name, 64)

    def _is_lan_iface(self, iface: Interface) -> bool:
        """A LAN segment is one whose peer is a Switch (several IS's can
        share it); a direct router<->router link is point-to-point and has
        no DIS. ponytail: no explicit network-type config knob (real IS-IS
        has one) — inferred from topology instead, since this repo's link
        model already tells a broadcast segment apart from a P2P wire."""
        peer = iface.peer()
        return peer is not None and peer.device.kind == "switch"

    def _enabled_ifaces(self) -> list[Interface]:
        out = []
        for name, iface in self.router.interfaces.items():
            if self.iface_names is not None and name not in self.iface_names:
                continue
            if iface.ips:
                out.append(iface)
        return out

    # ----- lifecycle ---------------------------------------------------------
    def start(self, net: Network) -> None:
        if self._started:
            return
        self._started = True
        lan_ifaces = [i for i in self._enabled_ifaces() if self._is_lan_iface(i)]
        for idx, iface in enumerate(sorted(lan_ifaces, key=lambda i: i.name), start=1):
            self._iface_circuit_id[iface.name] = idx
        for iface in lan_ifaces:
            self._iface_dis[iface.name] = ""
            self._elect_dis(net, iface.name)  # solo on the LAN -> self
            self._originate_pseudonode_lsp(net, iface.name)  # solo -> no-op
        self._originate_lsp(net, flood=False)
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

    # ----- hello protocol ----------------------------------------------------
    def _send_hellos(self, net: Network) -> None:
        for iface in self._enabled_ifaces():
            if not iface.is_up:
                continue
            seen = [
                a.system_id
                for a in self.neighbors.values()
                if a.iface_name == iface.name
            ]
            cid = self._iface_circuit_id.get(iface.name)
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
                        priority=self.iface_priority(iface.name),
                        lan_id=f"{self.system_id}.{cid:02d}" if cid else "",
                    ),
                ),
            )

    def on_frame(self, net: Network, iface: Interface, pdu) -> None:
        if isinstance(pdu, IsisHello):
            self._on_hello(net, iface, pdu)
        elif isinstance(pdu, IsisLsp):
            self._on_lsp(net, iface, pdu)

    def _on_hello(self, net: Network, iface: Interface, hello: IsisHello) -> None:
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
        adj.priority = hello.priority
        adj.lan_id = hello.lan_id

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

        if iface.name in self._iface_dis:
            self._elect_dis(net, iface.name)
            self._originate_pseudonode_lsp(net, iface.name)

    def _expire_neighbors(self, net: Network) -> None:
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
            for iface_name, _sid in dead:
                if iface_name in self._iface_dis:
                    self._elect_dis(net, iface_name)  # DIS may have died
                    self._originate_pseudonode_lsp(net, iface_name)

    # ----- DIS election (ISO 10589 §8.4.5, LAN segments only) -----------------
    def _elect_dis(self, net: Network, iface_name: str) -> None:
        candidates: dict[str, int] = {self.system_id: self.iface_priority(iface_name)}
        for adj in self.neighbors.values():
            if adj.iface_name == iface_name:
                candidates[adj.system_id] = adj.priority
        # Highest priority wins; tie -> system-id descending (MAC-address
        # tiebreak deviation documented in the module docstring). No BDIS,
        # and deliberately re-run from scratch every time (no "current DIS
        # keeps it on a tie") so a later, truly-higher-priority IS preempts —
        # ISO 10589 makes DIS election preemptive, unlike OSPF's DR.
        winner = max(candidates, key=lambda sid: (candidates[sid], sid))
        if winner != self._iface_dis.get(iface_name):
            self._iface_dis[iface_name] = winner
            net.log_event(
                "isis.dis_election", device=self.router.name, iface=iface_name,
                dis=winner,
            )
            # Our own LSP's pseudonode link on this circuit points at whoever
            # is DIS — re-originate now rather than waiting for the next
            # adjacency transition, since election is preemptive and can
            # change with no Up/down transition of its own.
            self._originate_lsp(net)

    def _dis_pseudonode_key(self, iface_name: str) -> str | None:
        """The elected DIS's pseudonode LSP-ID for this circuit — the target
        of a router LSP's transit ``is`` link. ``None`` while no DIS is known
        yet, or its LSP-ID hasn't been heard from it yet."""
        dis = self._iface_dis.get(iface_name)
        if not dis:
            return None
        if dis == self.system_id:
            cid = self._iface_circuit_id.get(iface_name)
            return f"{self.system_id}.{cid:02d}" if cid else None
        adj = next(
            (
                a for a in self.neighbors.values()
                if a.iface_name == iface_name and a.system_id == dis
            ),
            None,
        )
        return adj.lan_id if adj and adj.lan_id else None

    # ----- LSP origination / flooding ----------------------------------------
    def _originate_lsp(self, net: Network, flood: bool = True) -> None:
        self._seq += 1
        links: list[tuple[str, str, int]] = []
        for iface in self._enabled_ifaces():
            metric = self._iface_metric(iface)
            for ip in iface.ips:
                links.append(("ip", str(ip.network), metric))
            if iface.name in self._iface_dis:
                # Broadcast circuit: one link to the pseudonode instead of
                # one per Up neighbour (P-1d) — only once we actually have a
                # neighbour there (otherwise there's no pseudonode yet).
                has_adj = any(
                    a.iface_name == iface.name and a.state == "up"
                    for a in self.neighbors.values()
                )
                pn_key = self._dis_pseudonode_key(iface.name)
                if has_adj and pn_key is not None:
                    links.append(("is", pn_key, metric))
            else:
                for adj in self.neighbors.values():
                    if adj.iface_name == iface.name and adj.state == "up":
                        links.append(("is", adj.system_id, metric))
        lsp = IsisLsp(system_id=self.system_id, seq=self._seq, links=links, level=self.level)
        self.lsdb[lsp.key] = lsp
        if flood:
            self._flood(net, lsp, exclude_iface=None)
        self._schedule_spf(net)

    def _originate_pseudonode_lsp(self, net: Network, iface_name: str) -> None:
        """Only the DIS originates a *non-empty* pseudonode LSP for its
        circuit (ISO 10589 §7.2.5), and only once it has >=1 Up neighbour
        there — but every router still owns the key ``<own_sysid>.<own
        circuit_id>`` and re-originates it (possibly empty) on every call.
        That's what makes losing the DIS role a proper withdrawal: if this
        router authored a non-empty pseudonode LSP earlier and is no longer
        DIS, ``attached`` is now ``[]`` and differs from what's in the LSDB,
        so an empty version goes out — same "recompute and flood if changed"
        shape origination and membership updates use."""
        cid = self._iface_circuit_id.get(iface_name)
        if cid is None:
            return
        key = f"{self.system_id}.{cid:02d}"
        attached: list[str] = []
        if self._iface_dis.get(iface_name) == self.system_id:
            attached = sorted(
                {self.system_id} | {
                    a.system_id for a in self.neighbors.values()
                    if a.iface_name == iface_name and a.state == "up"
                }
            )
            if len(attached) < 2:
                attached = []  # solo on the circuit: nothing to advertise yet
        current = self.lsdb.get(key)
        current_attached = [t for _, t, _ in current.links] if current is not None else None
        if current_attached == attached:
            return  # unchanged, including "still nothing to advertise"
        if not attached and current is None:
            return  # never originated one for this circuit -> nothing to withdraw
        self._seq += 1
        lsp = IsisLsp(
            system_id=self.system_id, seq=self._seq,
            links=[("is", sid, 0) for sid in attached],
            level=self.level, circuit_id=cid,
        )
        self.lsdb[lsp.key] = lsp
        self._flood(net, lsp, exclude_iface=None)
        self._schedule_spf(net)

    def _flood(self, net: Network, lsp: IsisLsp, exclude_iface: str | None) -> None:
        sent: set[str] = set()
        for adj in self.neighbors.values():
            if adj.state != "up" or adj.iface_name == exclude_iface:
                continue
            if adj.iface_name in sent:
                continue  # one multicast per interface reaches all neighbours
            sent.add(adj.iface_name)
            self._send_lsp(net, adj.iface_name, lsp)

    def _send_lsp(self, net: Network, iface_name: str, lsp: IsisLsp) -> None:
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

    def _on_lsp(self, net: Network, iface: Interface, lsp: IsisLsp) -> None:
        current = self.lsdb.get(lsp.key)
        if current is None or lsp.seq > current.seq:
            self.lsdb[lsp.key] = lsp.copy()
            self._flood(net, lsp, exclude_iface=iface.name)
            self._schedule_spf(net)

    # ----- SPF ---------------------------------------------------------------
    def _schedule_spf(self, net: Network) -> None:
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
        """Dijkstra over the LSDB's ``is`` adjacencies: (dist, first_hop) by
        system-id. A circuit's pseudonode LSP (ISO 10589 §7.2.5) is a vertex
        keyed by its own LSP-ID (``<dis_sysid>.<circuit_id>``) — reachable
        from a router at that router's interface metric, and reaching every
        attached router back at cost 0 — but never surfaces in ``first_hop``:
        only a real router (one with an ``_Adjacency`` to route through) is
        ever locked in as a hop."""
        db = self.lsdb
        pseudonodes = {lsp.key for lsp in db.values() if lsp.circuit_id}
        adj: dict[str, list[tuple[str, int]]] = {}
        for lsp in db.values():
            vtx = lsp.key if lsp.circuit_id else lsp.system_id
            for kind, target, cost in lsp.links:
                if kind != "is":
                    continue
                peer = db.get(target) or db.get(f"{target}.00")
                if not isinstance(peer, IsisLsp):
                    continue
                if not any(k == "is" and t == vtx for k, t, _ in peer.links):
                    continue  # not bidirectional -> not usable
                peer_vtx = peer.key if peer.circuit_id else peer.system_id
                adj.setdefault(vtx, []).append((peer_vtx, cost))

        import heapq

        dist: dict[str, int] = {self.system_id: 0}
        first_hop: dict[str, str] = {}
        heap: list[tuple[int, str, str | None]] = [(0, self.system_id, None)]
        visited: set[str] = set()
        while heap:
            d, vtx, fh = heapq.heappop(heap)
            if vtx in visited:
                continue
            visited.add(vtx)
            if fh is not None:
                first_hop[vtx] = fh
            for nxt, cost in adj.get(vtx, ()):
                nd = d + cost
                if nxt not in dist or nd < dist[nxt]:
                    dist[nxt] = nd
                    # Only lock in a first hop once we land on a real router —
                    # a pseudonode is a transparent waypoint, not a hop itself.
                    new_fh = fh if fh is not None else (None if nxt in pseudonodes else nxt)
                    heapq.heappush(heap, (nd, nxt, new_fh))
        return dist, first_hop

    def _adj_for(self, first_hop_sid: str | None) -> _Adjacency | None:
        if first_hop_sid is None:
            return None
        for a in self.neighbors.values():
            if a.system_id == first_hop_sid and a.state == "up" and a.ip is not None:
                return a
        return None

    def _run_spf(self, net: Network) -> None:
        self._spf_pending = False
        dist, first_hop = self._dijkstra()
        local_prefixes = {ip.network for ip in self.router.all_ips()}
        # prefix -> (next_hop, iface, metric)
        desired: dict[IPv4Network, tuple[IPv4Address, str, int]] = {}

        for lsp in self.lsdb.values():
            if lsp.circuit_id:
                continue  # pseudonode LSP: no "ip" links, nothing to collect
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

    def dis_rows(self) -> list[dict]:
        return [
            {"iface": name, "dis": dis}
            for name, dis in self._iface_dis.items()
        ]
