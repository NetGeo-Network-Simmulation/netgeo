"""Segment Routing (SR-MPLS) — NG-SIM-09.

:class:`SrProcess` attaches to a router alongside a sibling :class:`LdpProcess`.
It gives SR its one real differentiator over LDP: a router imposes a transport
label from ``srgb_base + node_sid`` by *formula*, with no per-FEC label
negotiation. It:

- installs the router's own node-SID (``srgb_base + node_sid``) as a ``pop``
  entry in ``router.lfib`` — the LSP egress for its loopback (UHP, like LDP);
- auto-allocates one adjacency-SID per LDP-discovered neighbor into
  ``router.sr_adj`` (deterministic, sorted by neighbor address);
- floods a small :class:`SrSidAdvert` (bespoke LSA-style, riding the peers
  ``ldp.adj`` already discovered) so every SR router installs a node-SID
  ``swap`` entry toward every other loopback, reusing the OSPF-installed route
  for the next hop and ``ldp.adj`` for the L2 rewrite.

Deliberate simplifications (ponytail — each names its ceiling + upgrade path):

- ``# ponytail:`` SR rides the sibling ``LdpProcess``'s adjacency table
  (``ldp.adj``) instead of running its own hello. Ceiling: SR can't run without
  LDP configured on the same router. Upgrade: native OSPF/IS-IS adjacency
  tracking on ``Router`` if LDP-free SR-MPLS is ever needed.
- ``# ponytail:`` SRGB is a uniform lab convention (``srgb_base`` equal on every
  router), not negotiated or collision-checked — colliding SRGBs silently
  corrupt forwarding. Upgrade: SRGB-conflict detection if a scenario hits it.
- ``# ponytail:`` adjacency-SIDs are keyed by the neighbor's interface address
  (``ldp.adj``'s key), not a correlated router-id — enough to identify the
  adjacency uniquely and stay deterministic. Upgrade: correlate to the peer's
  advertised router-id if a display needs it.
- ``# ponytail:`` :meth:`install_policy` injects a fixed label stack, no path
  computation, no TI-LFA/backup. Upgrade: path-finding when a real use case
  (not just a test) needs it.
"""
from __future__ import annotations

import logging
from ipaddress import IPv4Address, IPv4Network
from typing import TYPE_CHECKING

from engine.events import EventType, SimEvent
from engine.netstack.frames import (
    ALL_LDP_MAC,
    ETH_MPLS,
    ETH_SR,
    EthernetFrame,
    MplsPacket,
    SrSidAdvert,
)
from engine.netstack.iface import Interface
from engine.netstack.routing import AdjSidEntry, LfibEntry, Router
from engine.netstack.protocols.mpls import LdpProcess

if TYPE_CHECKING:  # pragma: no cover
    from engine.netstack.network import Network


def _schedule(net: "Network", after: float, node_id: str, fn) -> None:
    net.scheduler.schedule_after(
        after,
        SimEvent(time=0.0, type=EventType.TIMER, handler=lambda _c, _e: fn(), node_id=node_id),
    )


logger = logging.getLogger(__name__)


class SrProcess:
    """SR-MPLS control plane attached to a Router (needs a sibling LdpProcess)."""

    proto = "sr"

    def __init__(
        self,
        router: Router,
        ldp: LdpProcess,
        node_sid: int,
        srgb_base: int = 16000,
        adj_sid_base: int = 15000,
        interval: float = 2.0,
    ) -> None:
        self.router = router
        self.ldp = ldp
        self.node_sid = node_sid
        self.srgb_base = srgb_base
        self.adj_sid_base = adj_sid_base
        self.interval = interval
        self.router_id = ldp.router_id
        self.loopback = self._own_loopback()
        self.adj_sids: dict[str, int] = {}       # own: neighbor id -> adj label
        self.sid_db: dict[str, SrSidAdvert] = {}  # router_id -> latest advert
        self._sent: SrSidAdvert | None = None
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
    def start(self, net: "Network") -> None:
        if self._started:
            return
        self._started = True
        self._tick(net)

    def _tick(self, net: "Network") -> None:
        if not self.router.powered_on:
            return
        self._alloc_adj_sids()
        self._advertise(net)
        self._rebuild()
        _schedule(net, self.interval, self.router.node_id, lambda: self._tick(net))

    def _alloc_adj_sids(self) -> None:
        """One adjacency-SID per LDP-discovered neighbor, sorted by neighbor
        address so labels are stable across a rebuild/replay."""
        sr_adj: dict[int, AdjSidEntry] = {}
        adj_sids: dict[str, int] = {}
        for i, nh in enumerate(sorted(self.ldp.adj)):
            mac, ifn = self.ldp.adj[nh]
            label = self.adj_sid_base + i
            sr_adj[label] = AdjSidEntry(out_iface=ifn, nh_mac=mac, peer_router_id=str(nh))
            adj_sids[str(nh)] = label
        self.router.sr_adj = sr_adj
        self.adj_sids = adj_sids

    def _advert(self) -> SrSidAdvert:
        return SrSidAdvert(
            router_id=self.router_id,
            prefix=str(self.loopback),
            node_sid=self.node_sid,
            adj_sids=dict(self.adj_sids),
        )

    def _advertise(self, net: "Network") -> None:
        advert = self._advert()
        if self._sent == advert:
            return                       # send only on change (L3vpn pattern)
        self._sent = advert.copy()
        self.sid_db[self.router_id] = advert.copy()   # our own row in sid-database
        self._flood(net, advert, exclude_iface=None)

    def _flood(self, net: "Network", advert: SrSidAdvert, exclude_iface: str | None) -> None:
        for nh in sorted(self.ldp.adj):
            _mac, ifn = self.ldp.adj[nh]
            if ifn == exclude_iface:
                continue
            out = self.router.interfaces.get(ifn)
            if out is None:
                continue
            out.transmit(
                net,
                EthernetFrame(
                    src_mac=out.mac,
                    dst_mac=ALL_LDP_MAC,
                    ethertype=ETH_SR,
                    payload=advert.copy(),
                ),
            )

    # ----- ingress -----------------------------------------------------------
    def on_frame(self, net: "Network", iface: Interface, frame: EthernetFrame) -> None:
        advert = frame.payload
        if not isinstance(advert, SrSidAdvert):
            return
        if advert.router_id == self.router_id:
            return
        if self.sid_db.get(advert.router_id) == advert:
            return                       # unchanged: newer-wins by content diff
        self.sid_db[advert.router_id] = advert.copy()
        self._rebuild()
        self._flood(net, advert, exclude_iface=iface.name)   # reflood, not to sender

    def _rebuild(self) -> None:
        """Full replace of our node-SID label range, mirroring
        ``LdpProcess._rebuild``: every swap entry is re-derived from the
        *current* OSPF route + ldp.adj rather than written once and kept
        forever, so a source that's no longer reachable (powered off, no
        longer advertising) has its entry retracted instead of going stale
        (F44)."""
        mine = {self.srgb_base + a.node_sid for a in self.sid_db.values()}
        mine.add(self.srgb_base + self.node_sid)
        lfib: dict[int, LfibEntry] = {
            k: v for k, v in self.router.lfib.items() if k not in mine
        }
        # Our own node-SID is the LSP egress for our loopback: pop (UHP).
        lfib[self.srgb_base + self.node_sid] = LfibEntry(
            str(self.loopback), "pop", None, None, None, None
        )
        for rid, advert in sorted(self.sid_db.items()):
            if rid == self.router_id:
                continue
            entry = self._node_sid_entry(advert)
            if entry is not None:
                lfib[self.srgb_base + advert.node_sid] = entry
        self.router.lfib = lfib

    def _node_sid_entry(self, advert: SrSidAdvert) -> LfibEntry | None:
        """Node-SID swap toward a remote loopback: label unchanged (uniform
        SRGB), next hop from the OSPF route, L2 rewrite from ldp.adj. Returns
        None (no entry) once the route or adjacency is no longer live."""
        prefix = IPv4Network(advert.prefix)
        route = self.router.lookup(IPv4Address(prefix.network_address))
        if route is None or route.next_hop is None:
            return None
        adj = self.ldp.adj.get(route.next_hop)
        if adj is None:
            return None
        mac, ifn = adj
        label = self.srgb_base + advert.node_sid
        return LfibEntry(str(prefix), "swap", label, route.next_hop, mac, ifn)

    # ----- ops / test hook ---------------------------------------------------
    def install_policy(self, net: "Network", sid_list: list[int], inner) -> None:
        """Impose an explicit SID label stack on ``inner`` and inject it into our
        own forwarding plane. A leading adjacency-SID we own selects the egress
        link (popped here); the rest are switched hop-by-hop as usual."""
        self.router._mpls_forward(net, None, MplsPacket(labels=list(sid_list), inner=inner))

    # ----- introspection -----------------------------------------------------
    def sid_rows(self) -> list[dict]:
        return [
            {
                "router_id": rid,
                "prefix": adv.prefix,
                "sid": adv.node_sid,
                "label": self.srgb_base + adv.node_sid,
            }
            for rid, adv in sorted(self.sid_db.items())
        ]
