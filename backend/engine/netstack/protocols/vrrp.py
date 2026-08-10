"""VRRPv3 — first-hop redundancy (NG-SIM-03, RFC 9568 subset).

One :class:`VrrpProcess` instance = one virtual router (VRID) on one router
interface. The master owns the virtual MAC ``00:00:5e:00:01:{vrid}`` and the
virtual IP: it answers ARP for the VIP with the virtual MAC, accepts frames
addressed to it, and multicasts advertisements to 224.0.0.18 (IP proto 112,
TTL 255) every ``adv_interval``. Backups run the master-down timer
(3×adv + skew, skew = (256−prio)/256×adv); on expiry — or on a priority-0
resign advert — the highest-priority backup takes over and gratuitously ARPs
so switches re-learn the virtual MAC. Preemption is on by default.

All timing goes through the DES scheduler (timer-sequence invalidation for
resets) — no wall clock, no RNG: failover is deterministic and replayable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from ipaddress import IPv4Address
from typing import TYPE_CHECKING

from engine.events import EventType, SimEvent
from engine.netstack.addr import BROADCAST_MAC, MacAddr, ipv4_multicast_mac
from engine.netstack.frames import ETH_IPV4, ArpPacket, EthernetFrame, Ipv4Packet

if TYPE_CHECKING:  # pragma: no cover
    from engine.netstack.iface import Interface
    from engine.netstack.network import Network
    from engine.netstack.routing import Router

PROTO_VRRP = 112
VRRP_GROUP = IPv4Address("224.0.0.18")


def virtual_mac(vrid: int) -> MacAddr:
    return MacAddr(f"00:00:5e:00:01:{vrid & 0xFF:02x}")


@dataclass(slots=True)
class VrrpAdvert:
    """VRRPv3 advertisement payload (rides Ipv4Packet proto 112)."""

    vrid: int
    priority: int
    ips: tuple[str, ...] = ()
    adv_interval: float = 1.0

    @property
    def wire_size(self) -> int:
        return 8 + 4 * len(self.ips)

    def summary(self) -> str:
        return f"VRRPv3 advertisement vrid={self.vrid} prio={self.priority}"


@dataclass
class VrrpProcess:
    """One virtual router on one interface."""

    router: "Router"
    iface_name: str
    vrid: int
    vip: IPv4Address
    priority: int = 100
    adv_interval: float = 1.0
    preempt: bool = True

    proto: str = field(default="vrrp", init=False)
    state: str = field(default="init", init=False)   # init|backup|master
    transitions: list[tuple[float, str]] = field(default_factory=list, init=False)
    _timer_seq: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        self.vip = IPv4Address(self.vip)
        # Address owner (VIP configured on the interface) always wins.
        iface = self.router.interfaces.get(self.iface_name)
        if iface is not None and iface.has_ip(self.vip):
            self.priority = 255
        self.router.processes.append(self)

    # ----- derived timers (RFC 9568 §6.1) ---------------------------------
    @property
    def skew_time(self) -> float:
        return ((256 - self.priority) / 256.0) * self.adv_interval

    @property
    def master_down_interval(self) -> float:
        return 3.0 * self.adv_interval + self.skew_time

    @property
    def vmac(self) -> MacAddr:
        return virtual_mac(self.vrid)

    def _iface(self) -> "Interface | None":
        return self.router.interfaces.get(self.iface_name)

    # ----- lifecycle --------------------------------------------------------
    def start(self, net: "Network") -> None:
        if self.priority == 255:
            self._become_master(net)
        else:
            self._enter_backup(net)

    def on_iface_change(self, net: "Network") -> None:  # duck-typed hook
        pass

    def on_power_off(self, net: "Network") -> None:  # duck-typed hook
        """F47: a power loss must not leave a stale master claim behind —
        relinquish so mastership is re-earned via the normal listen/promote
        path on resume instead of being assumed the instant power returns
        (that gap is the dual-master window)."""
        if self.state == "master":
            self._enter_backup(net)

    # ----- state transitions -------------------------------------------------
    def _log(self, net: "Network", state: str) -> None:
        self.state = state
        self.transitions.append((net.now, state))
        net.log_event(
            "vrrp.state", device=self.router.name, vrid=self.vrid, state=state
        )

    def _enter_backup(self, net: "Network") -> None:
        if self.state == "master":
            self.router.mac_aliases.discard(str(self.vmac))
            self.router.ip_aliases.discard(self.vip)
        self._log(net, "backup")
        self._arm_master_down(net, self.master_down_interval)

    def _become_master(self, net: "Network") -> None:
        self._timer_seq += 1  # cancel any pending master-down timer
        self.router.mac_aliases.add(str(self.vmac))
        self.router.ip_aliases.add(self.vip)
        self._log(net, "master")
        self._gratuitous_arp(net)
        self._send_advert(net)
        self._arm_advert(net)

    # ----- timers (sequence-guarded so resets are cheap + deterministic) ----
    def _arm_master_down(self, net: "Network", delay: float) -> None:
        self._timer_seq += 1
        seq = self._timer_seq
        net.scheduler.schedule_after(
            delay,
            SimEvent(
                time=0.0,
                type=EventType.TIMER,
                handler=lambda _c, _e: self._master_down_fired(net, seq),
                node_id=self.router.node_id,
            ),
        )

    def _master_down_fired(self, net: "Network", seq: int) -> None:
        if seq != self._timer_seq or self.state == "master":
            return
        if not self.router.powered_on:
            # Still off — don't claim mastership (and transmit) blind; keep
            # listening so a later power-on is caught by the next expiry.
            self._arm_master_down(net, self.master_down_interval)
            return
        self._become_master(net)

    def _arm_advert(self, net: "Network") -> None:
        net.scheduler.schedule_after(
            self.adv_interval,
            SimEvent(
                time=0.0,
                type=EventType.TIMER,
                handler=lambda _c, _e: self._advert_tick(net),
                node_id=self.router.node_id,
            ),
        )

    def _advert_tick(self, net: "Network") -> None:
        if self.state != "master":
            return
        if self.router.powered_on:
            self._send_advert(net)
        self._arm_advert(net)

    # ----- wire I/O -----------------------------------------------------------
    def _send_advert(self, net: "Network", priority: int | None = None) -> None:
        iface = self._iface()
        if iface is None or iface.ip is None or not iface.is_up:
            return
        iface.transmit(
            net,
            EthernetFrame(
                src_mac=self.vmac,
                dst_mac=ipv4_multicast_mac(VRRP_GROUP),
                ethertype=ETH_IPV4,
                payload=Ipv4Packet(
                    src=iface.ip.ip,
                    dst=VRRP_GROUP,
                    proto=PROTO_VRRP,
                    ttl=255,
                    payload=VrrpAdvert(
                        vrid=self.vrid,
                        priority=self.priority if priority is None else priority,
                        ips=(str(self.vip),),
                        adv_interval=self.adv_interval,
                    ),
                ),
            ),
        )

    def _gratuitous_arp(self, net: "Network") -> None:
        iface = self._iface()
        if iface is None or not iface.is_up:
            return
        iface.transmit(
            net,
            EthernetFrame(
                src_mac=self.vmac,
                dst_mac=BROADCAST_MAC,
                ethertype=0x0806,
                payload=ArpPacket(
                    op="reply",
                    sender_mac=str(self.vmac),
                    sender_ip=self.vip,
                    target_mac=BROADCAST_MAC,
                    target_ip=self.vip,
                ),
            ),
        )

    # ----- ingress -------------------------------------------------------------
    def on_packet(self, net: "Network", iface: "Interface", pkt: Ipv4Packet) -> None:
        adv = pkt.payload
        if not isinstance(adv, VrrpAdvert) or adv.vrid != self.vrid:
            return
        if iface.name != self.iface_name:
            return
        if self.state == "master":
            # Higher priority (or equal priority + higher source IP) wins.
            if adv.priority > self.priority or (
                adv.priority == self.priority
                and self._iface() is not None
                and self._iface().ip is not None
                and pkt.src > self._iface().ip.ip
            ):
                self._enter_backup(net)
            return
        # Backup path.
        if adv.priority == 0:  # master resigned — take over after skew time
            self._arm_master_down(net, self.skew_time)
        elif not self.preempt or adv.priority >= self.priority:
            self._arm_master_down(net, self.master_down_interval)
        # else: higher local priority + preempt → let the timer expire.

    def on_arp_request(self, net: "Network", iface: "Interface", arp: ArpPacket) -> None:
        """Master answers ARP for the VIP with the virtual MAC."""
        if self.state != "master" or iface.name != self.iface_name:
            return
        if arp.target_ip != self.vip:
            return
        iface.transmit(
            net,
            EthernetFrame(
                src_mac=self.vmac,
                dst_mac=arp.sender_mac,
                ethertype=0x0806,
                payload=ArpPacket(
                    op="reply",
                    sender_mac=str(self.vmac),
                    sender_ip=self.vip,
                    target_mac=arp.sender_mac,
                    target_ip=arp.sender_ip,
                ),
            ),
        )

    # ----- introspection ----------------------------------------------------------
    def status_row(self) -> dict:
        return {
            "vrid": self.vrid,
            "iface": self.iface_name,
            "vip": str(self.vip),
            "priority": self.priority,
            "state": self.state,
            "adv_interval": self.adv_interval,
            "preempt": self.preempt,
            "vmac": str(self.vmac),
        }
