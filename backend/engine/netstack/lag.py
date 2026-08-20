"""Link aggregation — LACP / static LAG (NG-SIM-04, 802.1AX subset).

A :class:`LagInterface` is a *logical* port-channel owning the IP/VLAN/STP
configuration; its physical members keep their link attachments and point
back via ``Interface.lag_parent``. Egress picks a member by deterministic
flow hashing (CRC32 over MACs/IPs/ports — per-flow ordering is preserved,
like real hash-based LAGs). Ingress on a member is handed to the device as
if it arrived on the logical port, so MAC learning, STP and routing all see
one interface — which is exactly why a dual-link LAG between two switches
does **not** get STP-blocked.

LACP mode exchanges LACPDUs per member (fast rate: 1 s, 3 s timeout);
members only carry traffic while the partner is alive, so a lost/miswired
member drains deterministically. Static mode trusts link state alone.
"""
from __future__ import annotations

import zlib
from typing import TYPE_CHECKING

from engine.events import EventType, SimEvent
from engine.netstack.frames import EthernetFrame, Ipv4Packet, Ipv6Packet, LacpFrame
from engine.netstack.iface import Interface

if TYPE_CHECKING:  # pragma: no cover
    from engine.netstack.device import Device
    from engine.netstack.network import Network

LACP_MCAST_MAC = "01:80:c2:00:00:02"
ETH_SLOW = 0x8809
LACP_INTERVAL = 1.0     # fast LACP
LACP_TIMEOUT = 3.0


def _flow_key(frame: EthernetFrame) -> bytes:
    parts = [frame.src_mac, frame.dst_mac]
    p = frame.payload
    if isinstance(p, (Ipv4Packet, Ipv6Packet)):
        parts += [str(p.src), str(p.dst)]
        l4 = p.payload
        sp = getattr(l4, "src_port", None)
        dp = getattr(l4, "dst_port", None)
        if sp is not None:
            parts += [str(sp), str(dp)]
    return "|".join(parts).encode()


class LagInterface(Interface):
    """The logical port-channel."""

    __slots__ = ("_partner_seen", "_started", "members", "mode")

    def __init__(
        self,
        name: str,
        device: Device,
        members: list[Interface],
        mode: str = "lacp",
    ) -> None:
        if not members:
            raise ValueError("a LAG needs at least one member")
        # Real LAGs borrow one member's MAC for the logical port.
        super().__init__(name=name, device=device, mac=members[0].mac)
        self.members = members
        self.mode = mode            # "lacp" | "static"
        self._partner_seen: dict[str, float] = {}   # member name -> last LACPDU
        self._started = False
        for m in members:
            m.lag_parent = self

    # ----- membership -------------------------------------------------------
    def _member_alive(self, member: Interface, now: float) -> bool:
        if not member.is_up:
            return False
        if self.mode == "static":
            return True
        seen = self._partner_seen.get(member.name)
        return seen is not None and now - seen <= LACP_TIMEOUT

    def active_members(self, now: float) -> list[Interface]:
        return [m for m in self.members if self._member_alive(m, now)]

    @property
    def is_up(self) -> bool:  # type: ignore[override]
        return self.enabled and any(m.is_up for m in self.members)

    # ----- egress: hash a member ---------------------------------------------
    def transmit(self, net: Network, frame: EthernetFrame) -> None:  # type: ignore[override]
        active = self.active_members(net.now)
        if not active:
            self.counters.drops_down += 1
            net.record_drop("lag_no_members")
            return
        member = active[zlib.crc32(_flow_key(frame)) % len(active)]
        member.transmit(net, frame)

    # ----- LACP ---------------------------------------------------------------
    def start(self, net: Network) -> None:
        if self._started or self.mode != "lacp":
            return
        self._started = True
        self._lacp_tick(net)

    def _lacp_tick(self, net: Network) -> None:
        if self.device.powered_on:
            for m in self.members:
                if not m.is_up:
                    continue
                m.transmit(
                    net,
                    EthernetFrame(
                        src_mac=m.mac,
                        dst_mac=LACP_MCAST_MAC,
                        ethertype=ETH_SLOW,
                        payload=LacpFrame(
                            system=self.device.node_id, key=self.name, port=m.name
                        ),
                    ),
                )
        net.scheduler.schedule_after(
            LACP_INTERVAL,
            SimEvent(
                time=0.0,
                type=EventType.TIMER,
                handler=lambda _c, _e: self._lacp_tick(net),
                node_id=self.device.node_id,
            ),
        )

    def on_lacp(self, net: Network, member: Interface, pdu: LacpFrame) -> None:
        self._partner_seen[member.name] = net.now

    # ----- introspection ---------------------------------------------------------
    def brief(self) -> dict:  # type: ignore[override]
        out = super().brief()
        out["lag"] = {
            "mode": self.mode,
            "members": [m.name for m in self.members],
            "active": [m.name for m in self.members if m.lag_parent is self and m.is_up],
        }
        return out

    def status_row(self, now: float) -> dict:
        return {
            "name": self.name,
            "mode": self.mode,
            "members": [
                {
                    "name": m.name,
                    "up": m.is_up,
                    "bundled": self._member_alive(m, now),
                }
                for m in self.members
            ],
        }
