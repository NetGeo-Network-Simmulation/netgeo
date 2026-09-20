"""Segment Routing (SR-MPLS) — NG-SIM-09, A5: decoupled from LDP.

:class:`SrProcess` attaches to a router alongside a sibling :class:`OspfProcess`
(no LdpProcess required). SR's one real differentiator over LDP: a router
imposes a transport label from ``srgb_base + sid_index`` by *formula*, with
no per-FEC label negotiation. It:

- registers its own node-SID with OSPF as two opaque LSAs (RFC 8665) --
  an RI-LSA (opaque type 4, RFC 7770) carrying its SRGB, and an
  Extended-Prefix-LSA (opaque type 7, RFC 7684) carrying its loopback's
  Prefix-SID index -- and installs that node-SID (``srgb_base + node_sid``)
  as a ``pop`` entry in ``router.lfib`` (the LSP egress for its loopback);
- auto-allocates one adjacency-SID per **Full** OSPF neighbor into
  ``router.sr_adj`` (deterministic, sorted by neighbor interface address --
  same key convention the old LDP-sourced table used);
- watches OSPF's own LSDB (already flooded by :class:`OspfProcess`) for
  every other router's RI-LSA + Extended-Prefix-LSA and installs a node-SID
  entry toward each: ``swap`` for a transit hop, ``php`` (penultimate-hop-
  pop, RFC 8665 sec 4.1/4.2) for the hop directly adjacent to the label's
  owner *unless* that owner's Prefix-SID sub-TLV sets the NP (no-PHP) flag.
  The next hop and its L2 rewrite both come from OSPF: ``router.lookup()``
  for the SPF-installed route, ``router.arp_table`` for the MAC (populated
  by the ordinary ARP flow OSPF's own unicast LSU sends already trigger
  toward every Full neighbor -- no bespoke L2 discovery needed).

LDP coexistence (RFC 8402 permits both control planes on one router): SR no
longer reads anything from LdpProcess. Their LFIB label ranges don't collide
in practice (LDP's per-FEC labels start low, e.g. 16; SR's SRGB/adjacency
bases default to 16000+/15000+) and each process's ``_rebuild`` already only
touches the label keys it owns, so both coexist safely with **no precedence
flag** -- there is nothing left to prefer between, since SR simply doesn't
consult LDP any more. If a scenario ever needs SR labels to *route through* an
LDP LSP (label stacking), that's new work, not a config toggle.

Deliberate simplifications (ponytail — each names its ceiling + upgrade path):

- ``# ponytail:`` SRGB is a uniform lab convention (``srgb_base`` equal on
  every router), not negotiated or collision-checked -- colliding SRGBs
  silently corrupt forwarding. Upgrade: SRGB-conflict detection if a
  scenario hits it.
- ``# ponytail:`` adjacency-SIDs are keyed by the neighbor's interface
  address (OSPF's ``_Neighbor.ip``), not a correlated router-id -- enough to
  identify the adjacency uniquely and stay deterministic. Upgrade: correlate
  to the peer's advertised router-id if a display needs it.
- ``# ponytail:`` one area only -- ``originate_extended_prefix_lsa`` floods
  into every area this router's OSPF instance touches with the *same*
  content, no multi-area Prefix-SID re-summarization. Upgrade: per-area
  content if a multi-area SR scenario ever needs it.
- ``# ponytail:`` :meth:`install_policy` injects a fixed label stack, no path
  computation, no TI-LFA/backup. Upgrade: path-finding when a real use case
  (not just a test) needs it.
"""
from __future__ import annotations

import logging
from ipaddress import IPv4Address, IPv4Network
from typing import TYPE_CHECKING

from engine.events import EventType, SimEvent
from engine.netstack.frames import MplsPacket
from engine.netstack.protocols.ospf import OpaqueLsa, OspfProcess
from engine.netstack.routing import AdjSidEntry, LfibEntry, Router

if TYPE_CHECKING:  # pragma: no cover
    from engine.netstack.network import Network


def _schedule(net: Network, after: float, node_id: str, fn) -> None:
    net.scheduler.schedule_after(
        after,
        SimEvent(time=0.0, type=EventType.TIMER, handler=lambda _c, _e: fn(), node_id=node_id),
    )


logger = logging.getLogger(__name__)


class SrProcess:
    """SR-MPLS control plane attached to a Router (needs a sibling OspfProcess)."""

    proto = "sr"

    def __init__(
        self,
        router: Router,
        ospf: OspfProcess,
        node_sid: int,
        srgb_base: int = 16000,
        srgb_range: int = 8000,
        adj_sid_base: int = 15000,
        interval: float = 2.0,
        no_php: bool = False,
    ) -> None:
        self.router = router
        self.ospf = ospf
        self.node_sid = node_sid
        self.srgb_base = srgb_base
        self.srgb_range = srgb_range
        self.adj_sid_base = adj_sid_base
        self.interval = interval
        self.no_php = no_php   # this router's own Prefix-SID NP flag (RFC 8665 sec 4.2)
        self.router_id = ospf.router_id
        self.loopback = self._own_loopback()
        self.adj_sids: dict[str, int] = {}       # own: neighbor address -> adj label
        self._started = False
        router.mpls_enabled = True
        router.processes.append(self)

    def _own_loopback(self) -> IPv4Network:
        for iface in self.router.interfaces.values():
            for ip in iface.ips:
                if ip.network.prefixlen == 32:
                    return ip.network
        return IPv4Network(f"{self.router_id}/32")

    # ----- lifecycle ---------------------------------------------------------
    def start(self, net: Network) -> None:
        if self._started:
            return
        self._started = True
        self._tick(net)

    def _tick(self, net: Network) -> None:
        # ponytail: keep the loop alive across power-cycles (F36/F47) — gate
        # the work, not the reschedule, so a power-on self-heals within one
        # interval instead of needing an explicit restart.
        if self.router.powered_on:
            self._register(net)
            self._alloc_adj_sids()
            self._rebuild()
        _schedule(net, self.interval, self.router.node_id, lambda: self._tick(net))

    def _register(self, net: Network) -> None:
        """(Re-)register our own SID advertisement with OSPF's opaque LSAs --
        a no-op once the SRGB/prefix-SID stop changing (see
        ``OspfProcess.originate_*``'s own change detection)."""
        self.ospf.originate_routing_info_lsa(net, self.srgb_base, self.srgb_range)
        self.ospf.originate_extended_prefix_lsa(
            net, str(self.loopback), self.node_sid, node=True, no_php=self.no_php
        )

    def _alloc_adj_sids(self) -> None:
        """One adjacency-SID per **Full** OSPF neighbor, sorted by neighbor
        address so labels are stable across a rebuild/replay. A neighbor
        whose MAC OSPF's own unicast traffic hasn't resolved into
        ``router.arp_table`` yet is skipped for this tick and picked up on
        the next one -- still deterministic, just possibly a tick later."""
        sr_adj: dict[int, AdjSidEntry] = {}
        adj_sids: dict[str, int] = {}
        full = sorted(
            (n.ip, n.iface_name) for n in self.ospf.neighbors.values() if n.state == "full"
        )
        for i, (nh, ifn) in enumerate(full):
            cached = self.router.arp_table.get(nh)
            if cached is None:
                continue
            mac, _ifn2 = cached
            label = self.adj_sid_base + i
            sr_adj[label] = AdjSidEntry(out_iface=ifn, nh_mac=str(mac), peer_router_id=str(nh))
            adj_sids[str(nh)] = label
        self.router.sr_adj = sr_adj
        self.adj_sids = adj_sids

    # ----- LFIB rebuild --------------------------------------------------------
    def _sr_lsas(self) -> tuple[dict[str, OpaqueLsa], dict[str, OpaqueLsa]]:
        """Every router's RI-LSA (SRGB) and node-SID Extended-Prefix-LSA,
        read live from OSPF's own LSDB -- SR floods nothing of its own;
        OSPF's existing opaque-LSA flooding (RFC 5250) does the work."""
        ri: dict[str, OpaqueLsa] = {}
        ext: dict[str, OpaqueLsa] = {}
        for area in self.ospf.my_areas():
            for lsa in self.ospf.lsdb.get(area, {}).values():
                if not isinstance(lsa, OpaqueLsa):
                    continue
                if lsa.opaque_type == 4:
                    ri[lsa.router_id] = lsa
                elif lsa.opaque_type == 7 and lsa.tlvs.get("flags", {}).get("N"):
                    ext[lsa.router_id] = lsa
        return ri, ext

    def _rebuild(self) -> None:
        """Full replace of our node-SID label range, mirroring
        ``LdpProcess._rebuild``: every swap/php entry is re-derived from the
        *current* OSPF LSDB + SPF route rather than written once and kept
        forever, so a source that's no longer reachable (powered off, LSA
        withdrawn) has its entry retracted instead of going stale (F44)."""
        ri, ext = self._sr_lsas()
        mine = {
            ri[rid].tlvs["srgb_base"] + e.tlvs["sid_index"]
            for rid, e in ext.items() if rid in ri
        }
        mine.add(self.srgb_base + self.node_sid)
        lfib: dict[int, LfibEntry] = {
            k: v for k, v in self.router.lfib.items() if k not in mine
        }
        # Our own node-SID is the LSP egress for our loopback: pop (UHP).
        lfib[self.srgb_base + self.node_sid] = LfibEntry(
            str(self.loopback), "pop", None, None, None, None
        )
        for rid in sorted(ext):
            if rid == self.router_id:
                continue
            r = ri.get(rid)
            if r is None:
                continue
            entry = self._node_sid_entry(rid, r, ext[rid])
            if entry is not None:
                lfib[r.tlvs["srgb_base"] + ext[rid].tlvs["sid_index"]] = entry
        self.router.lfib = lfib

    def _node_sid_entry(self, rid: str, ri: OpaqueLsa, ext: OpaqueLsa) -> LfibEntry | None:
        """Node-SID entry toward a remote loopback: label unchanged (uniform
        SRGB), next hop + egress interface from the OSPF SPF-installed route,
        L2 rewrite from ``arp_table``. ``php`` (pop, no swap-to-self) at the
        hop directly adjacent to the owner unless its Prefix-SID sets NP
        (RFC 8665 sec 4.1/4.2); ``swap`` everywhere else. Returns None once
        the route or its L2 rewrite is no longer resolvable."""
        prefix = IPv4Network(ext.tlvs["prefix"])
        route = self.router.lookup(IPv4Address(prefix.network_address))
        if route is None or route.next_hop is None:
            return None
        cached = self.router.arp_table.get(route.next_hop)
        if cached is None:
            return None
        mac, ifn = cached
        label = ri.tlvs["srgb_base"] + ext.tlvs["sid_index"]
        no_php = bool(ext.tlvs.get("flags", {}).get("NP"))
        penultimate = not no_php and any(
            n.ip == route.next_hop and n.router_id == rid and n.state == "full"
            for n in self.ospf.neighbors.values()
        )
        if penultimate:
            return LfibEntry(str(prefix), "php", None, route.next_hop, str(mac), ifn)
        return LfibEntry(str(prefix), "swap", label, route.next_hop, str(mac), ifn)

    # ----- ops / test hook ---------------------------------------------------
    def install_policy(self, net: Network, sid_list: list[int], inner) -> None:
        """Impose an explicit SID label stack on ``inner`` and inject it into our
        own forwarding plane. A leading adjacency-SID we own selects the egress
        link (popped here); the rest are switched hop-by-hop as usual."""
        self.router._mpls_forward(net, None, MplsPacket(labels=list(sid_list), inner=inner))

    # ----- introspection -----------------------------------------------------
    def sid_rows(self) -> list[dict]:
        ri, ext = self._sr_lsas()
        return [
            {
                "router_id": rid,
                "prefix": e.tlvs["prefix"],
                "sid": e.tlvs["sid_index"],
                "label": ri[rid].tlvs["srgb_base"] + e.tlvs["sid_index"],
            }
            for rid, e in sorted(ext.items()) if rid in ri
        ]
