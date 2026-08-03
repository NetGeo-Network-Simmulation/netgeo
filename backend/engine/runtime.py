"""Per-node behaviour for *sim* mode.

A ``NodeRuntime`` is the control+data plane of a node when it is simulated
(``mode="sim"``) rather than emulated. The default implementation does L3
shortest-path forwarding using the model graph, which is enough to make small
scenarios actually move packets end-to-end. Dynamic routing protocols (BGP,
OSPF, ...) instead attach as processes under ``engine/netstack/protocols/``.

Emulated nodes (``mode="emul"``) do **not** use a ``NodeRuntime`` — their
forwarding happens inside a real container via an :class:`EmulationAdaptor`.
"""
from __future__ import annotations

import logging

from engine.events import EventType, SimEvent
from engine.model import NetworkModel, NodeModel
from engine.packet import Packet

logger = logging.getLogger(__name__)


class NodeRuntime:
    """Default sim-mode node behaviour: drop-aware shortest-path forwarding."""

    def __init__(self, node: NodeModel, model: NetworkModel) -> None:
        self.node = node
        self.model = model
        # Simple RIB: dst node id -> next-hop node id. Empty => fall back to
        # the model's shortest_path(). Protocol runtimes populate this.
        self.rib: dict[str, str] = {}
        self.rx_count = 0
        self.tx_count = 0
        self.drop_count = 0

    # ----- packet handling ---------------------------------------------
    def on_receive(self, sim, event: SimEvent) -> None:
        """Handle a packet that has fully arrived at this node."""
        packet: Packet = event.payload
        self.rx_count += 1

        if packet.dst == self.node.id:
            sim.record_delivery(packet)
            return

        if not packet.hop(self.node.id):
            self.drop_count += 1
            sim.record_drop(packet, reason="ttl_expired")
            return

        next_hop = self._next_hop(packet.dst)
        if next_hop is None:
            self.drop_count += 1
            sim.record_drop(packet, reason="no_route")
            return

        self.forward(sim, packet, next_hop)

    def forward(self, sim, packet: Packet, next_hop: str) -> None:
        """Send ``packet`` toward ``next_hop`` across the connecting link."""
        link = self._link_to(next_hop)
        if link is None or not link.up:
            self.drop_count += 1
            sim.record_drop(packet, reason="link_down")
            return

        # Loss model: deterministic given the sim RNG (reproducibility).
        if link.loss > 0.0 and sim.rng.random() < link.loss:
            self.drop_count += 1
            sim.record_drop(packet, reason="link_loss")
            return

        if packet.size_bytes > link.mtu:
            self.drop_count += 1
            sim.record_drop(packet, reason="mtu_exceeded")
            return

        self.tx_count += 1
        delay = link.transit_delay(packet.size_bytes)
        sim.scheduler.schedule_after(
            delay,
            SimEvent(
                time=0.0,  # set by schedule_after
                type=EventType.PACKET_RX,
                handler=sim.dispatch_receive,
                payload=packet,
                node_id=next_hop,
            ),
        )

    # ----- routing helpers ---------------------------------------------
    def _next_hop(self, dst: str) -> str | None:
        if dst in self.rib:
            return self.rib[dst]
        path = self.model.shortest_path(self.node.id, dst)
        if path and len(path) >= 2:
            return path[1]
        return None

    def _link_to(self, neighbor_id: str):
        for iface in self.node.interfaces:
            peer = self.model.peer_interface(iface.id)
            if peer and peer.node_id == neighbor_id:
                return self.model.link_for_iface(iface.id)
        return None

    @property
    def counters(self) -> dict[str, int]:
        return {"rx": self.rx_count, "tx": self.tx_count, "drop": self.drop_count}
