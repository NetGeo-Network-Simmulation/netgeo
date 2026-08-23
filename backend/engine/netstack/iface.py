"""Interfaces and link attachments — the physical layer of the netstack.

Realism model per egress interface:

- **Serialization**: a frame occupies the transmitter for ``size*8/bandwidth``
  seconds; frames queue behind it (FIFO within a class).
- **Queueing/QoS**: two egress queues — priority (DSCP >= 40, e.g. EF/CS6
  control traffic) and best-effort. Priority is always drained first. Each
  queue has a bounded depth; overflow is a **tail drop**.
- **Propagation**: fixed one-way delay plus optional uniform jitter drawn from
  the simulation's seeded RNG (deterministic).
- **Loss**: independent per-frame drop probability, same RNG.
- **MTU**: frames whose L3 payload exceeds the link MTU are dropped
  (routers send ICMP frag-needed upstream — handled at the device layer).

All transmission state lives here so devices only decide *what* to send and
*where*; the physics is uniform for every device type.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field, replace
from ipaddress import IPv4Address, IPv4Interface, IPv6Address, IPv6Interface
from typing import TYPE_CHECKING


def _three_zeros() -> list:
    return [0, 0, 0]

from engine.events import EventType, SimEvent
from engine.netstack.addr import MacAddr, link_local_for, solicited_node
from engine.netstack.frames import EthernetFrame, Ipv4Packet, Ipv6Packet
from engine.netstack.qos import QosClass, QosConfig, class_name, classify

if TYPE_CHECKING:  # pragma: no cover
    from engine.netstack.device import Device
    from engine.netstack.network import Network

PRIORITY_DSCP_THRESHOLD = 40  # CS5 and above ride the priority queue (legacy constant kept for external callers)


@dataclass(slots=True)
class FrameContext:
    """Event payload for PACKET_TX/RX: the frame plus where it happened.
    Consumed by the ledger (record enrichment) and the packet animation."""

    frame: EthernetFrame
    link_id: str
    iface: str      # qualified "device:port" at the recording end
    qos_class: str | None = None  # "EF"/"AF"/"BE" when QoS enabled; absent otherwise

    def ledger_fields(self) -> dict:
        d = {
            "link": self.link_id,
            "iface": self.iface,
            "frame_id": self.frame.id,
            "size": self.frame.size_bytes,
            "info": self.frame.summary(),
        }
        if self.qos_class is not None:
            d["qos_class"] = self.qos_class
        return d


@dataclass(slots=True)
class IfaceCounters:
    rx_frames: int = 0
    tx_frames: int = 0
    rx_bytes: int = 0
    tx_bytes: int = 0
    drops_queue: int = 0
    drops_loss: int = 0
    drops_mtu: int = 0
    drops_down: int = 0
    # Per-class counters (len 3: EF=0, AF=1, BE=2) — additive; zero when QoS disabled.
    tx_by_class: list = field(default_factory=_three_zeros)
    drops_queue_by_class: list = field(default_factory=_three_zeros)

    def as_dict(self) -> dict:
        return {
            "rx_frames": self.rx_frames,
            "tx_frames": self.tx_frames,
            "rx_bytes": self.rx_bytes,
            "tx_bytes": self.tx_bytes,
            "drops": {
                "queue": self.drops_queue,
                "loss": self.drops_loss,
                "mtu": self.drops_mtu,
                "down": self.drops_down,
            },
            "tx_by_class": list(self.tx_by_class),
            "drops_queue_by_class": list(self.drops_queue_by_class),
        }


class Interface:
    """A device port. Owns addressing, VLAN config and the egress queue."""

    __slots__ = (
        "_queues",
        "_transmitting",
        "access_vlan",
        "attachment",
        "counters",
        "device",
        "enabled",
        "ips",
        "ips6",
        # Link aggregation: member ports point at their logical port-channel.
        "lag_parent",
        "mac",
        "name",
        "queue_depth",
        "slaac",
        "stp_role",
        # STP per-port state (used when the owning device is a Switch)
        "stp_state",
        "trunk_vlans",
        "vlan_mode",
    )

    def __init__(
        self,
        name: str,
        device: Device,
        mac: MacAddr,
        ips: list[IPv4Interface] | None = None,
        queue_depth: int = 64,
    ) -> None:
        self.name = name
        self.device = device
        self.mac = mac
        self.ips: list[IPv4Interface] = ips or []
        self.ips6: list[IPv6Interface] = []      # global/ULA; link-local derived
        self.slaac: bool = False                 # autoconfigure from RA prefixes
        self.vlan_mode: str = "access"          # access | trunk
        self.access_vlan: int = 1
        self.trunk_vlans: set[int] | None = None  # None = allow all
        self.enabled: bool = True
        self.attachment: LinkAttachment | None = None
        self.counters = IfaceCounters()
        # Three deques indexed by QosClass (EF=0, AF=1, BE=2).
        # When QoS is disabled only EF and BE slots are used — mirrors the
        # former _queue_prio / _queue_be split for disabled-path parity.
        self._queues: tuple[deque[EthernetFrame], ...] = (deque(), deque(), deque())
        self._transmitting = False
        self.queue_depth = queue_depth
        self.stp_state: str = "forwarding"       # forwarding | blocking | learning
        self.stp_role: str = "designated"        # root | designated | blocked
        self.lag_parent: Interface | None = None

    # ----- addressing ----------------------------------------------------
    @property
    def ip(self) -> IPv4Interface | None:
        return self.ips[0] if self.ips else None

    def has_ip(self, addr: IPv4Address) -> bool:
        return any(i.ip == addr for i in self.ips)

    # ----- IPv6 addressing -------------------------------------------------
    @property
    def link_local(self) -> IPv6Interface:
        """fe80:: address derived from the MAC (EUI-64) — always present on
        an IPv6-capable port, like a real stack."""
        return link_local_for(self.mac)

    def all_ips6(self) -> list[IPv6Interface]:
        return [self.link_local, *self.ips6]

    def has_ip6(self, addr: IPv6Address) -> bool:
        return any(i.ip == addr for i in self.all_ips6())

    def joined_group(self, addr: IPv6Address) -> bool:
        """Is ``addr`` a multicast group this port listens to? (all-nodes and
        the solicited-node group of every configured address)."""
        if not addr.is_multicast:
            return False
        if addr == IPv6Address("ff02::1"):
            return True
        return any(solicited_node(i.ip) == addr for i in self.all_ips6())

    @property
    def qualified_name(self) -> str:
        return f"{self.device.name}:{self.name}"

    # ----- link state -----------------------------------------------------
    @property
    def is_up(self) -> bool:
        return (
            self.enabled
            and self.attachment is not None
            and self.attachment.up
        )

    def peer(self) -> Interface | None:
        return self.attachment.peer(self) if self.attachment else None

    # ----- VLAN helpers ----------------------------------------------------
    def vlan_allows(self, vlan: int) -> bool:
        if self.vlan_mode == "access":
            return vlan == self.access_vlan
        return self.trunk_vlans is None or vlan in self.trunk_vlans

    # ----- egress path ------------------------------------------------------
    def transmit(self, net: Network, frame: EthernetFrame) -> None:
        """Enqueue a frame for transmission out of this interface."""
        if frame.id == 0:
            frame.id = net.next_frame_id()
        att = self.attachment
        if att is None or not self.is_up:
            self.counters.drops_down += 1
            net.record_drop("link_down")
            return

        # MTU check on the L3 payload (L2 headers don't count against MTU).
        if isinstance(frame.payload, (Ipv4Packet, Ipv6Packet)) and frame.payload.wire_size > att.mtu:
            if isinstance(frame.payload, Ipv4Packet) and not frame.payload.dont_fragment:
                self._fragment_and_transmit(net, frame, att.mtu)
                return
            self.counters.drops_mtu += 1
            net.record_drop("mtu_exceeded")
            self.device.on_mtu_drop(net, self, frame)
            return

        qos_cfg = att.qos
        cls = classify(_frame_priority(frame), qos_cfg)
        queue = self._queues[cls]

        # Depth check: disabled path uses shared depth (legacy); enabled path
        # enforces per-class depth so EF/AF cannot be crowded out by BE.
        if qos_cfg.enabled:
            if len(queue) >= qos_cfg.depth_per_class:
                self.counters.drops_queue += 1
                self.counters.drops_queue_by_class[int(cls)] += 1
                net.record_drop("queue_overflow")
                net.capture.record(net.now, att.id, self.qualified_name, "drop", frame)
                return
        else:
            # Legacy shared-depth check — EF+BE only (AF slot always empty here).
            if len(self._queues[QosClass.EF]) + len(self._queues[QosClass.BE]) >= self.queue_depth:
                self.counters.drops_queue += 1
                net.record_drop("queue_overflow")
                net.capture.record(net.now, att.id, self.qualified_name, "drop", frame)
                return

        queue.append(frame)
        if qos_cfg.enabled:
            net.scheduler.schedule_after(
                0.0,
                SimEvent(
                    time=0.0,
                    type=EventType.PACKET_ENQUEUE,
                    payload=FrameContext(frame, att.id, self.qualified_name,
                                        qos_class=class_name(cls)),
                    node_id=self.device.node_id,
                ),
            )
        if not self._transmitting:
            self._start_next(net)

    def _fragment_and_transmit(self, net: Network, frame: EthernetFrame, mtu: int) -> None:
        """DF=0 and oversized: split into RFC 791 sec 3.2 fragments and
        transmit each (recursing through :meth:`transmit`, so a fragment
        that is still too big for a later, smaller-MTU hop is split again)."""
        pkt = frame.payload
        assert isinstance(pkt, Ipv4Packet)
        inner = getattr(pkt.payload, "wire_size", None)
        total_len = inner if inner is not None else pkt.payload_len
        max_data = ((mtu - 20) // 8) * 8
        if max_data <= 0:
            # ponytail: no lab topology configures an MTU this small; treat
            # it like DF=1 rather than emit zero-length fragments forever.
            self.counters.drops_mtu += 1
            net.record_drop("mtu_exceeded")
            self.device.on_mtu_drop(net, self, frame)
            return
        ident = pkt.identification or net.next_frame_id()
        offset = 0
        while offset < total_len:
            chunk = min(max_data, total_len - offset)
            frag = replace(
                pkt,
                payload=pkt.payload if offset == 0 else None,
                payload_len=0,
                identification=ident,
                more_fragments=(offset + chunk) < total_len,
                fragment_offset=offset,
                frag_len=chunk,
            )
            self.transmit(
                net,
                EthernetFrame(
                    src_mac=frame.src_mac, dst_mac=frame.dst_mac,
                    ethertype=frame.ethertype, vlan=frame.vlan, payload=frag,
                ),
            )
            offset += chunk

    def _start_next(self, net: Network) -> None:
        # Strict-priority drain: EF first, then AF, then BE.
        # Disabled path: AF queue is always empty so EF→BE order is preserved
        # bit-for-bit (same as former _queue_prio → _queue_be drain).
        frame = None
        drained_cls: QosClass | None = None
        for i, q in enumerate(self._queues):
            if q:
                frame = q.popleft()
                drained_cls = QosClass(i)
                break
        if frame is None:
            self._transmitting = False
            return

        att = self.attachment
        assert att is not None
        self._transmitting = True
        ser = att.serialization_delay(frame.size_bytes)
        self.counters.tx_frames += 1
        self.counters.tx_bytes += frame.size_bytes
        if att.qos.enabled and drained_cls is not None:
            self.counters.tx_by_class[int(drained_cls)] += 1
        net.capture.record(net.now, att.id, self.qualified_name, "tx", frame)

        # TX completes after serialization — then the next queued frame starts
        # and this frame begins propagating toward the peer. The frame + link
        # ride along as the event payload so the ledger (NG-SIM-01) can show
        # per-PDU records and the canvas can animate them (NG-CAP-04).
        qos_cls_name = class_name(drained_cls) if att.qos.enabled and drained_cls is not None else None
        net.scheduler.schedule_after(
            ser,
            SimEvent(
                time=0.0,
                type=EventType.PACKET_TX,
                handler=lambda _ctx, _ev, f=frame: self._tx_done(net, f),
                payload=FrameContext(frame, att.id, self.qualified_name,
                                     qos_class=qos_cls_name),
                node_id=self.device.node_id,
            ),
        )

    def _tx_done(self, net: Network, frame: EthernetFrame) -> None:
        att = self.attachment
        # Keep the pipe busy with whatever queued up meanwhile.
        self._transmitting = False
        if att is not None and any(self._queues):
            self._start_next(net)

        if att is None or not att.up:
            self.counters.drops_down += 1
            net.record_drop("link_down")
            return

        # Random loss (deterministic via seeded RNG).
        if att.loss > 0.0 and net.rng.random() < att.loss:
            self.counters.drops_loss += 1
            net.record_drop("link_loss")
            net.capture.record(net.now, att.id, self.qualified_name, "drop", frame)
            return

        delay = att.delay
        if att.jitter > 0.0:
            delay += net.rng.uniform(0.0, att.jitter)

        peer = att.peer(self)
        net.scheduler.schedule_after(
            delay,
            SimEvent(
                time=0.0,
                type=EventType.PACKET_RX,
                handler=lambda _ctx, _ev, f=frame, p=peer: p._deliver(net, f),
                payload=FrameContext(frame, att.id, peer.qualified_name),
                node_id=peer.device.node_id,
            ),
        )

    # ----- ingress path -------------------------------------------------------
    def _deliver(self, net: Network, frame: EthernetFrame) -> None:
        if not self.enabled:
            self.counters.drops_down += 1
            net.record_drop("iface_down")
            return
        self.counters.rx_frames += 1
        self.counters.rx_bytes += frame.size_bytes
        if self.attachment is not None:
            net.capture.record(
                net.now, self.attachment.id, self.qualified_name, "rx", frame
            )
        # LAG members hand ingress to their logical port-channel — except
        # LACPDUs, which are negotiated per physical member.
        if self.lag_parent is not None:
            payload = frame.payload
            if type(payload).__name__ == "LacpFrame":
                self.lag_parent.on_lacp(net, self, payload)  # type: ignore[attr-defined]
            else:
                self.device.on_frame(net, self.lag_parent, frame)
            return
        self.device.on_frame(net, self, frame)

    # ----- introspection ---------------------------------------------------
    def brief(self) -> dict:
        return {
            "name": self.name,
            "mac": str(self.mac),
            "ips": [str(i) for i in self.ips],
            "ips6": [str(i) for i in self.ips6],
            "link_local": str(self.link_local),
            "vlan_mode": self.vlan_mode,
            "access_vlan": self.access_vlan,
            "up": self.is_up,
            "stp": {"state": self.stp_state, "role": self.stp_role},
            "counters": self.counters.as_dict(),
        }


def _frame_priority(frame: EthernetFrame) -> int:
    p = frame.payload
    if isinstance(p, (Ipv4Packet, Ipv6Packet)):
        return p.dscp
    return 48  # L2 control (ARP/BPDU) rides the priority queue


@dataclass(slots=True)
class LinkAttachment:
    """The medium between two interfaces (full duplex, symmetric)."""

    id: str
    a: Interface
    b: Interface
    bandwidth_bps: float = 1_000_000_000.0
    delay: float = 0.000_1        # one-way propagation, seconds
    jitter: float = 0.0           # max extra uniform delay, seconds
    loss: float = 0.0             # per-frame drop probability
    mtu: int = 1500
    up: bool = True
    kind: str = "copper"          # copper | fiber | wireless | virtual
    qos: QosConfig = field(default_factory=QosConfig)  # default: disabled, legacy behaviour

    def __post_init__(self) -> None:
        self.a.attachment = self
        self.b.attachment = self

    def peer(self, iface: Interface) -> Interface:
        return self.b if iface is self.a else self.a

    def serialization_delay(self, size_bytes: int) -> float:
        if self.bandwidth_bps <= 0:
            return 0.0
        return (size_bytes * 8) / self.bandwidth_bps
