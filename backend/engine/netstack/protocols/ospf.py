"""OSPFv2 — multi-area link-state routing (simplified but event-faithful).

What is modelled (NG-SIM-05, DR/BDR election P-1a, network type + Network
LSA P-1c):
- periodic Hellos to 224.0.0.5 per enabled interface, tagged with the
  interface's **area**; adjacency only forms between same-area neighbors;
- per-area LSDBs: router LSAs flooded within their area, Dijkstra SPF per
  area (cost = ref_bandwidth / interface bandwidth);
- **network type** (RFC 2328 §9.5): an interface is inferred **broadcast**
  when its peer is a Switch, **point-to-point** when it's a direct
  router-router wire (no explicit config knob — see ``_is_lan_iface``,
  same detection ``isis.py`` uses). A point-to-point interface never runs
  DR/BDR election — adjacency goes straight to Full once 2-Way is reached;
- **DR/BDR election** (RFC 2328 §7.3, §9.4), broadcast interfaces only:
  every such interface carries a router priority (default 1, configurable
  per-interface; priority 0 never becomes DR/BDR) and its own election
  state (waiting -> dr | backup | drother); a Wait Timer (= dead_interval)
  gates the first election, with the RFC "BackupSeen" shortcut when a
  Hello already claims a DR/BDR; election is a deterministic total order
  (priority desc, router-id desc, no randomness) and **non-preemptive** —
  a higher-priority router joining after a DR is established does not
  unseat it; only the DR's own death triggers a new election (which
  promotes the BDR and elects a fresh BDR). Adjacency only climbs to Full
  for pairs involving the DR or BDR — a DROther/DROther pair stops at
  2-Way, so LSAs never flood across it;
- **Type-2 Network LSA / pseudonode** (RFC 2328 §12.4.2, §16.1): the DR of
  a broadcast segment (and only the DR) originates a Network LSA listing
  every router Full-adjacent on that segment plus itself, keyed by the
  DR's interface IP; a router's own Router LSA replaces its per-neighbor
  links on that segment with a single ``transit`` link to the pseudonode.
  SPF treats the pseudonode as a vertex with cost 0 to each listed router
  and the interface's cost the other way, gated by the same bidirectional
  check as ``ptp`` links (Network LSA must list the router back). A dead
  or superseded DR's old Network LSA is simply left unreferenced once
  survivors re-originate against the new DR — no route leaks through it;
- **ABR behaviour**: a router with links in area 0 plus others originates
  type-3 summary LSAs — non-backbone intra prefixes into area 0, and
  backbone intra + backbone-learned inter prefixes into its leaf areas
  (summaries are only *consumed* from the backbone, the RFC loop rule);
- inter-area routes installed as ``O IA``-style entries (intra-area wins);
- optional **default originate**: ABR injects 0.0.0.0/0 into leaf areas;
- dead-interval neighbor expiry, LSA re-origination and route withdrawal.

Not modelled (documented): NSSA/stub area types, LSA aging/refresh, virtual
links, authentication, OSPFv3, ExStart/Exchange/Loading (LSAs sync in one
shot on Full).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from ipaddress import IPv4Address, IPv4Network
from typing import TYPE_CHECKING

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


def _rid_key(router_id: str) -> int:
    """Total order for the DR/BDR tiebreak: numeric so "10.0.0.1" ranks
    above "9.0.0.1" (a plain string compare would get that backwards)."""
    try:
        return int(IPv4Address(router_id))
    except ValueError:
        return 0


# --- OSPF PDUs (payload of Ipv4Packet proto 89) ------------------------------

@dataclass(slots=True)
class OspfHello:
    router_id: str
    neighbors_seen: list[str] = field(default_factory=list)
    hello_interval: float = 10.0
    dead_interval: float = 40.0
    area: int = 0
    priority: int = 1
    dr: str = ""   # router-id the sender believes is DR ("" = none yet)
    bdr: str = ""  # router-id the sender believes is BDR ("" = none yet)

    @property
    def wire_size(self) -> int:
        # Real OSPF Hello already carries netmask/priority/DR/BDR inside its
        # fixed 44-byte base — only the neighbor list grows the size.
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
    # ("ptp", neighbor_router_id, cost) — direct router-router link (P2P);
    # ("transit", dr_interface_ip, cost) — broadcast segment, points at the
    #   pseudonode (Network LSA keyed "net|<dr_interface_ip>") instead of at
    #   each neighbor individually (P-1c);
    # ("stub", "prefix/len", cost) — connected prefix, no adjacency needed.
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


@dataclass(slots=True)
class NetworkLsa:
    """Type-2 pseudonode LSA (RFC 2328 §12.4.2) — originated only by the DR
    of a broadcast segment, listing every router Full-adjacent there (DR
    included). Keyed by the DR's own interface IP on that segment, so a new
    DR after a failover naturally gets a fresh key instead of colliding with
    the old one."""

    dr_ip: str               # DR's interface address on the segment (pseudonode id)
    seq: int
    mask: str                 # segment netmask, e.g. "255.255.255.0"
    attached_routers: list[str] = field(default_factory=list)  # sorted, DR included

    @property
    def key(self) -> str:
        return f"net|{self.dr_ip}"

    @property
    def wire_size(self) -> int:
        return 24 + 4 * len(self.attached_routers)

    def copy(self) -> NetworkLsa:
        return NetworkLsa(self.dr_ip, self.seq, self.mask, list(self.attached_routers))


Lsa = RouterLsa | SummaryLsa | NetworkLsa


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
    state: str = "init"          # init | 2-way | full
    last_seen: float = 0.0
    priority: int = 1
    hello_dr: str = ""    # DR this neighbor's last Hello claimed ("" = none)
    hello_bdr: str = ""   # BDR this neighbor's last Hello claimed


@dataclass(slots=True)
class _IfaceDr:
    """Per-interface DR/BDR election state (RFC 2328 §9.4)."""

    state: str = "waiting"   # waiting | dr | backup | drother | down
    dr: str = ""             # elected DR's router-id ("" = none yet)
    bdr: str = ""            # elected BDR's router-id ("" = none yet)
    wait_seq: int = 0        # sequence-guard for the Wait Timer


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
        priorities: dict[str, int] | None = None,
    ) -> None:
        self.router = router
        self.router_id = router_id or self._pick_router_id()
        self.hello_interval = hello_interval
        self.dead_interval = dead_interval if dead_interval is not None else hello_interval * 4
        self.iface_names = ifaces  # None = all L3 interfaces
        self.areas = {k: int(v) for k, v in (areas or {}).items()}  # iface -> area
        self.default_originate = default_originate
        self.priorities = {k: int(v) for k, v in (priorities or {}).items()}  # iface -> priority
        # (router_id, area) -> neighbor
        self.neighbors: dict[tuple[str, int], _Neighbor] = {}
        # iface name -> DR/BDR election state
        self._iface_dr: dict[str, _IfaceDr] = {}
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

    def iface_priority(self, iface_name: str) -> int:
        return self.priorities.get(iface_name, 1)

    def _is_lan_iface(self, iface: Interface) -> bool:
        """Broadcast (LAN) segment vs point-to-point wire (RFC 2328 §9.5):
        no explicit network-type knob, so this is inferred from topology —
        the peer is a Switch (several routers can share the segment) or a
        direct router-router link (peer.device.kind == "router"). Same
        detection ``isis.py``'s ``_is_lan_iface`` uses; keep both in sync."""
        peer = iface.peer()
        return peer is not None and peer.device.kind == "switch"

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
        for iface in self._enabled_ifaces():
            if self._is_lan_iface(iface):
                self._iface_dr[iface.name] = _IfaceDr()
                self._arm_wait_timer(net, iface.name, self.iface_area(iface.name))
        for area in self.my_areas():
            self._originate_lsa(net, area, flood=False)
        self._tick(net)

    def _tick(self, net: Network) -> None:
        # ponytail: keep the loop alive across power-cycles (F36/F47) — gate
        # the work, not the reschedule, so a power-on self-heals within one
        # interval instead of needing an explicit restart.
        if self.router.powered_on:
            self._resume_ifaces(net)
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

    def on_power_off(self, net: Network) -> None:
        """A power loss takes every interface's DR state down with it (no
        adjacency survives a dead router) instead of leaving a stale
        DR/BDR claim behind. ``_tick``'s self-heal brings it back via
        ``_resume_ifaces`` once the router powers back on."""
        for name in self._iface_dr:
            self._down_iface_dr(name)

    # ----- DR/BDR election (RFC 2328 §7.3, §9.4) -------------------------------
    def _down_iface_dr(self, iface_name: str) -> None:
        st = self._iface_dr.get(iface_name)
        if st is None:
            return
        st.wait_seq += 1  # abort any Wait Timer generation still in flight
        st.dr, st.bdr = "", ""
        st.state = "down"

    def _resume_ifaces(self, net: Network) -> None:
        for name, st in self._iface_dr.items():
            if st.state == "down":
                # Flips to "waiting" synchronously below, so the next tick
                # won't see "down" again and re-arm a second time.
                st.dr, st.bdr = "", ""
                st.state = "waiting"
                self._arm_wait_timer(net, name, self.iface_area(name))

    def _arm_wait_timer(self, net: Network, iface_name: str, area: int) -> None:
        st = self._iface_dr[iface_name]
        st.wait_seq += 1
        seq = st.wait_seq
        net.scheduler.schedule_after(
            self.dead_interval,
            SimEvent(
                time=0.0,
                type=EventType.TIMER,
                handler=lambda _c, _e: self._wait_timer_fired(net, iface_name, area, seq),
                node_id=self.router.node_id,
            ),
        )

    def _wait_timer_fired(self, net: Network, iface_name: str, area: int, seq: int) -> None:
        st = self._iface_dr.get(iface_name)
        if st is None or seq != st.wait_seq:
            return  # superseded by a newer generation (reset, or BackupSeen)
        if st.state != "waiting":
            return
        if not self.router.powered_on:
            return  # on_power_off already tore this interface's DR state down
        self._run_election(net, iface_name, area)

    def _candidates(self, iface_name: str, area: int) -> dict[str, tuple[int, bool, bool]]:
        """router_id -> (priority, self-claims-DR, self-claims-BDR), for
        every router visible on this segment (self plus every neighbor
        that has reached at least 2-Way). The self-claim flags come from
        each router's own last Hello (RFC 2328 §7.3/§9.4 elect off of who
        *declares itself* DR/BDR, not off priority ranking alone) — that's
        what lets a router recognise an already-established DR even on
        its very first election with it, e.g. a late joiner."""
        st = self._iface_dr.get(iface_name)
        out = {
            self.router_id: (
                self.iface_priority(iface_name),
                bool(st and st.dr == self.router_id),
                bool(st and st.bdr == self.router_id),
            )
        }
        for nbr in self.neighbors.values():
            if nbr.iface_name == iface_name and nbr.area == area and nbr.state in ("2-way", "full"):
                out[nbr.router_id] = (
                    nbr.priority,
                    nbr.hello_dr == nbr.router_id,
                    nbr.hello_bdr == nbr.router_id,
                )
        return out

    def _run_election(self, net: Network, iface_name: str, area: int) -> None:
        st = self._iface_dr.setdefault(iface_name, _IfaceDr())
        st.wait_seq += 1  # commits this generation — a pending Wait Timer is now stale
        candidates = self._candidates(iface_name, area)
        eligible = {rid: v for rid, v in candidates.items() if v[0] > 0}  # priority 0 never wins

        def best(pool: list[str]) -> str:
            return max(pool, key=lambda rid: (eligible[rid][0], _rid_key(rid)))

        def bdr_pool(exclude: str) -> list[str]:
            claiming = [
                rid for rid, (_p, cdr, cbdr) in eligible.items()
                if cbdr and not cdr and rid != exclude
            ]
            return claiming or [rid for rid in eligible if rid != exclude]

        if not eligible:
            dr, bdr = "", ""
        else:
            pool = bdr_pool("")
            bdr = best(pool) if pool else ""
            dr_claims = [rid for rid, (_p, cdr, _c) in eligible.items() if cdr]
            dr = best(dr_claims) if dr_claims else bdr
            if dr and dr == bdr:
                # RFC 2328 §9.4: redo the BDR pick once more with the DR now
                # fixed and excluded — covers both a genuine self-claim tie
                # and the "nobody claims DR yet" fallback picking the same
                # router for both roles.
                pool = bdr_pool(dr)
                bdr = best(pool) if pool else ""

        st.dr, st.bdr = dr, bdr
        if dr == self.router_id:
            st.state = "dr"
        elif bdr == self.router_id:
            st.state = "backup"
        else:
            st.state = "drother"
        net.log_event(
            "ospf.dr_election", device=self.router.name, iface=iface_name,
            dr=dr, bdr=bdr, role=st.state,
        )
        self._update_adjacencies(net, iface_name, area)

    def _set_full(self, net: Network, nbr: _Neighbor, area: int) -> None:
        nbr.state = "full"
        logger.debug(
            "%s: adjacency FULL with %s (area %s)",
            self.router_id, nbr.router_id, area,
        )
        self._originate_lsa(net, area)
        # Database sync: give the new neighbor this area's entire LSDB.
        self._send_lsu(net, nbr, list(self._area_db(area).values()))

    def _update_adjacencies(self, net: Network, iface_name: str, area: int) -> None:
        st = self._iface_dr.get(iface_name)
        if st is None:
            return
        for nbr in self.neighbors.values():
            if nbr.iface_name != iface_name or nbr.area != area or nbr.state == "init":
                continue
            should_be_full = self.router_id in (st.dr, st.bdr) or nbr.router_id in (st.dr, st.bdr)
            if should_be_full and nbr.state != "full":
                self._set_full(net, nbr, area)
            elif not should_be_full and nbr.state == "full":
                nbr.state = "2-way"
                self._originate_lsa(net, area)
        # Broadcast segment only: the DR's Network LSA membership may have
        # changed (a neighbor reached/left Full) even when its own role
        # didn't — recompute every time, it's a no-op when unchanged.
        self._originate_network_lsa(net, iface_name, area)

    # ----- hello protocol -------------------------------------------------------
    def _send_hellos(self, net: Network) -> None:
        for iface in self._enabled_ifaces():
            if not iface.is_up or not iface.ip:
                continue
            area = self.iface_area(iface.name)
            seen = [
                n.router_id for n in self.neighbors.values() if n.area == area
            ]
            st = self._iface_dr.get(iface.name)
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
                            priority=self.iface_priority(iface.name),
                            dr=st.dr if st else "",
                            bdr=st.bdr if st else "",
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
        nbr.priority = hello.priority
        nbr.hello_dr = hello.dr
        nbr.hello_bdr = hello.bdr

        if nbr.state == "init":
            if self.router_id in hello.neighbors_seen:
                nbr.state = "2-way"
            else:
                return  # not yet 2-Way: no election, no adjacency possible

        st = self._iface_dr.get(iface.name)
        if st is None:
            # Point-to-point (RFC 2328 §9.5): no DR/BDR election at all —
            # adjacency climbs straight to Full once 2-Way is reached.
            if nbr.state != "full":
                self._set_full(net, nbr, area)
            return
        if st.state == "waiting":
            if hello.dr or hello.bdr:
                # RFC 2328 §9.4 BackupSeen: a Hello already claiming a
                # DR/BDR lets us skip the rest of the Wait Timer.
                self._run_election(net, iface.name, area)
            return
        if not st.bdr:
            # NeighborChange: this router might fill a still-empty BDR
            # slot (e.g. rejoining after a drop). Non-preemptive for an
            # already-seated DR — see _run_election.
            self._run_election(net, iface.name, area)
        else:
            self._update_adjacencies(net, iface.name, area)

    def _expire_neighbors(self, net: Network) -> None:
        dead = [
            key
            for key, n in self.neighbors.items()
            if n.last_seen and net.now - n.last_seen > self.dead_interval
        ]
        if not dead:
            return
        touched: dict[str, int] = {}  # iface_name -> area, for interfaces with a dead neighbor
        for key in dead:
            rid, area = key
            nbr = self.neighbors.pop(key)
            touched[nbr.iface_name] = area
            st = self._iface_dr.get(nbr.iface_name)
            if st is not None and rid in (st.dr, st.bdr):
                # The DR or BDR itself died: re-elect now rather than
                # waiting — RFC 2328's NeighborChange event.
                self._run_election(net, nbr.iface_name, area)
        logger.debug("%s: neighbors dead: %s", self.router_id, dead)
        for _rid, area in dead:
            self._originate_lsa(net, area)
        for iface_name, area in touched.items():
            self._update_adjacencies(net, iface_name, area)
        self._schedule_spf(net)

    # ----- LSA origination / flooding -----------------------------------------------
    def _iface_cost(self, iface: Interface) -> int:
        att = iface.attachment
        bw = att.bandwidth_bps if att else 1e9
        return max(1, int(REF_BANDWIDTH / max(bw, 1.0)))

    def _dr_ip(self, iface: Interface, area: int) -> IPv4Address | None:
        """The elected DR's interface address on this segment — the target
        of a ``transit`` Router LSA link and the Network LSA's key."""
        st = self._iface_dr.get(iface.name)
        if st is None or not st.dr:
            return None
        if st.dr == self.router_id:
            return iface.ip.ip if iface.ip else None
        nbr = next(
            (
                n for n in self.neighbors.values()
                if n.iface_name == iface.name and n.area == area and n.router_id == st.dr
            ),
            None,
        )
        return nbr.ip if nbr else None

    def _originate_lsa(self, net: Network, area: int, flood: bool = True) -> None:
        self._seq += 1
        links: list[tuple[str, str, int]] = []
        for iface in self._enabled_ifaces():
            if self.iface_area(iface.name) != area:
                continue
            cost = self._iface_cost(iface)
            for ip in iface.ips:
                links.append(("stub", str(ip.network), cost))
            if self._is_lan_iface(iface):
                # Broadcast segment: one link to the pseudonode instead of
                # one per Full neighbor (P-1c) — only once we're actually
                # Full with someone there (otherwise there's no pseudonode
                # to point at yet).
                has_full = any(
                    n.iface_name == iface.name and n.area == area and n.state == "full"
                    for n in self.neighbors.values()
                )
                dr_ip = self._dr_ip(iface, area)
                if has_full and dr_ip is not None:
                    links.append(("transit", str(dr_ip), cost))
            else:
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

    def _originate_network_lsa(self, net: Network, iface_name: str, area: int) -> None:
        """Only the DR originates a Network LSA for its segment (RFC 2328
        §12.4.2), and only once it has >=1 Full neighbor there. Handles
        origination, membership updates, and withdrawal (DR role lost, or
        membership drops back to solo) in one pass — each is just "recompute
        the attached-router list and flood it if it changed"."""
        st = self._iface_dr.get(iface_name)
        iface = self.router.interfaces.get(iface_name)
        if st is None or iface is None or iface.ip is None:
            return
        db = self._area_db(area)
        key = f"net|{iface.ip.ip}"
        attached: list[str] = []
        if st.state == "dr":
            attached = sorted(
                {self.router_id} | {
                    n.router_id for n in self.neighbors.values()
                    if n.iface_name == iface_name and n.area == area and n.state == "full"
                }
            )
            if len(attached) < 2:
                attached = []  # nobody else attached yet: nothing to advertise
        current = db.get(key)
        if isinstance(current, NetworkLsa) and current.attached_routers == attached:
            return  # unchanged, including "still nothing to advertise"
        if not attached and current is None:
            return  # never originated one for this segment — nothing to withdraw
        self._seq += 1
        lsa = NetworkLsa(
            dr_ip=str(iface.ip.ip), seq=self._seq,
            mask=str(iface.ip.network.netmask), attached_routers=attached,
        )
        db[lsa.key] = lsa
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
        """Dijkstra over one area's LSAs: (dist, first_hop) by rid. A
        broadcast segment's pseudonode (RFC 2328 §16.1) is a vertex keyed
        "net|<dr_ip>" — cost 0 from pseudonode to each attached router,
        the interface's cost the other way — but never surfaces in the
        returned dicts as a hop: ``first_hop`` always names a real router,
        since that's the only kind of vertex with a ``_Neighbor`` (IP +
        egress iface) to route through."""
        db = self._area_db(area)
        adj: dict[str, list[tuple[str, int]]] = {}
        for lsa in db.values():
            if isinstance(lsa, RouterLsa):
                for kind, target, cost in lsa.links:
                    if kind == "ptp":
                        peer = db.get(f"rtr|{target}")
                        if not isinstance(peer, RouterLsa):
                            continue
                        if not any(
                            k == "ptp" and t == lsa.router_id for k, t, _ in peer.links
                        ):
                            continue  # not bidirectional -> not usable
                        adj.setdefault(lsa.router_id, []).append((target, cost))
                    elif kind == "transit":
                        net_lsa = db.get(f"net|{target}")
                        if not isinstance(net_lsa, NetworkLsa):
                            continue
                        if lsa.router_id not in net_lsa.attached_routers:
                            continue  # pseudonode doesn't list us back
                        adj.setdefault(lsa.router_id, []).append((net_lsa.key, cost))
            elif isinstance(lsa, NetworkLsa):
                for rid in lsa.attached_routers:
                    peer = db.get(f"rtr|{rid}")
                    if not isinstance(peer, RouterLsa):
                        continue
                    if not any(
                        k == "transit" and t == lsa.dr_ip for k, t, _ in peer.links
                    ):
                        continue  # router doesn't point back at this pseudonode
                    adj.setdefault(lsa.key, []).append((rid, 0))

        import heapq

        def is_pseudonode(v: str) -> bool:
            return v.startswith("net|")

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
                    # Only lock in a first hop once we land on a real router —
                    # a pseudonode is a transparent waypoint, not a hop itself.
                    new_fh = fh if fh is not None else (None if is_pseudonode(nxt) else nxt)
                    heapq.heappush(heap, (nd, nxt, new_fh))
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

            def nbr_for(rid: str, first_hop=first_hop, area=area):
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
                "priority": n.priority,
            }
            for n in self.neighbors.values()
        ]

    def dr_rows(self) -> list[dict]:
        return [
            {"iface": name, "state": st.state, "dr": st.dr, "bdr": st.bdr}
            for name, st in self._iface_dr.items()
        ]
