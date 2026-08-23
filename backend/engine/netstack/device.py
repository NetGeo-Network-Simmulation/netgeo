"""Devices — the base class and the end-host implementation.

A :class:`Device` owns interfaces and reacts to delivered frames. The shared
L3 machinery (ARP resolution with pending-packet queue, ICMP echo handling)
lives here so hosts, routers and firewalls behave identically at the edges.

:class:`Host` is a full little endpoint: ARP, ping (ICMP echo with RTT
accounting), DHCP client and DNS stub resolver.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from ipaddress import (
    IPv4Address,
    IPv4Interface,
    IPv6Address,
    IPv6Interface,
    IPv6Network,
)
from typing import TYPE_CHECKING

from engine.events import EventType, SimEvent
from engine.netstack.addr import (
    ALL_ROUTERS_V6,
    BROADCAST_MAC,
    MacAddr,
    ipv6_multicast_mac,
    slaac_address,
    solicited_node,
)
from engine.netstack.frames import (
    ETH_ARP,
    ETH_IPV4,
    ETH_IPV6,
    PROTO_ICMP,
    PROTO_ICMPV6,
    PROTO_UDP,
    ArpPacket,
    DhcpMessage,
    DnsMessage,
    EthernetFrame,
    IcmpMessage,
    Icmpv6Message,
    Ipv4Packet,
    Ipv6Packet,
    UdpSegment,
)
from engine.netstack.iface import Interface

if TYPE_CHECKING:  # pragma: no cover
    from engine.netstack.network import Network

logger = logging.getLogger(__name__)

ARP_TIMEOUT = 1.0        # seconds between ARP attempts
ARP_RETRIES = 3          # attempts before pending packets are dropped
ARP_MAX_PENDING = 32     # queued packets per unresolved next-hop


class Device:
    """Base simulated device."""

    kind = "device"

    def __init__(self, name: str, node_id: str | None = None, nos: str = "forgeos") -> None:
        self.name = name
        self.node_id = node_id or name
        self.nos = nos
        self.interfaces: dict[str, Interface] = {}
        self.powered_on = True
        # Extra L2/L3 identities this device answers to — e.g. a VRRP master
        # owns the virtual MAC 00:00:5e:00:01:xx and the virtual IP.
        self.mac_aliases: set[str] = set()
        self.ip_aliases: set[IPv4Address] = set()

    # ----- construction ----------------------------------------------------
    def add_interface(self, iface: Interface) -> Interface:
        self.interfaces[iface.name] = iface
        return iface

    def iface(self, name: str) -> Interface | None:
        return self.interfaces.get(name)

    def create_lag(self, name: str, member_names: list[str], mode: str = "lacp"):
        """Bundle existing ports into a logical port-channel (NG-SIM-04)."""
        from engine.netstack.lag import LagInterface

        members = [self.interfaces[m] for m in member_names if m in self.interfaces]
        lag = LagInterface(name=name, device=self, members=members, mode=mode)
        return self.add_interface(lag)

    # ----- frame entry point --------------------------------------------------
    def on_frame(self, net: Network, iface: Interface, frame: EthernetFrame) -> None:
        """Called when a frame is fully received on ``iface``. Override."""

    def on_mtu_drop(self, net: Network, iface: Interface, frame: EthernetFrame) -> None:
        """Hook: an egress frame exceeded the link MTU with DF set.

        Base no-op (P-4b): a plain host has no forwarding role to report
        back to, so it stays silent here — the frame's own application
        (e.g. ping) already learns of the loss by timeout. The drop is
        still observable via ``net.drops["mtu_exceeded"]``, incremented by
        the caller in :meth:`Interface.transmit` before this hook runs.
        :class:`~engine.netstack.routing.Router` overrides this to send
        ICMP "fragmentation needed"/"packet too big".
        """

    def on_start(self, net: Network) -> None:
        """Hook: the network started — kick off periodic/bootstrap behaviour."""

    # ----- helpers ---------------------------------------------------------------
    def owns_ip(self, addr: IPv4Address) -> bool:
        return addr in self.ip_aliases or any(
            i.has_ip(addr) for i in self.interfaces.values()
        )

    def owns_ip6(self, addr: IPv6Address) -> bool:
        return any(i.has_ip6(addr) for i in self.interfaces.values())

    def all_ips(self) -> list[IPv4Interface]:
        return [ip for i in self.interfaces.values() for ip in i.ips]

    def all_ips6(self) -> list[IPv6Interface]:
        """Global/ULA v6 addresses (link-locals excluded)."""
        return [ip for i in self.interfaces.values() for ip in i.ips6]

    def summary(self) -> dict:
        return {
            "name": self.name,
            "kind": self.kind,
            "nos": self.nos,
            "interfaces": [i.brief() for i in self.interfaces.values()],
        }


class L3Device(Device):
    """Shared ARP + ICMP-echo machinery for anything with an IP stack."""

    def __init__(self, name: str, node_id: str | None = None, nos: str = "forgeos") -> None:
        super().__init__(name, node_id, nos)
        # ip -> (mac, iface_name)
        self.arp_table: dict[IPv4Address, tuple[MacAddr, str]] = {}
        # next-hop ip -> queued IP packets awaiting resolution
        self._arp_pending: dict[IPv4Address, list[Ipv4Packet]] = {}
        # next-hop ip -> request attempts so far
        self._arp_attempts: dict[IPv4Address, int] = {}
        # IPv6 neighbor cache — same shape and pending machinery as ARP.
        self.nd_cache: dict[IPv6Address, tuple[MacAddr, str]] = {}
        self._nd_pending: dict[IPv6Address, list[Ipv6Packet]] = {}
        self._nd_attempts: dict[IPv6Address, int] = {}

    # ----- egress: IP -> frame -----------------------------------------------
    def egress_for(self, dst: IPv4Address) -> tuple[Interface, IPv4Address] | None:
        """(egress iface, next-hop ip) for ``dst``; None = no route.

        Base implementation: connected subnets only. Routers override with a
        real routing table; hosts add the default gateway.
        """
        for iface in self.interfaces.values():
            for ip in iface.ips:
                if dst in ip.network:
                    return iface, dst
        return None

    def send_ip(self, net: Network, packet: Ipv4Packet) -> None:
        route = self.egress_for(packet.dst)
        if route is None:
            net.record_drop("no_route")
            self.on_no_route(net, packet)
            return
        iface, next_hop = route
        self._resolve_and_send(net, iface, next_hop, packet)

    def _resolve_and_send(
        self, net: Network, iface: Interface, next_hop: IPv4Address, packet: Ipv4Packet
    ) -> None:
        if next_hop == packet.dst and self.owns_ip(packet.dst):
            return  # to-self; nothing to put on the wire
        cached = self.arp_table.get(next_hop)
        if cached is not None:
            mac, iface_name = cached
            out = self.interfaces.get(iface_name, iface)
            out.transmit(
                net,
                EthernetFrame(
                    src_mac=out.mac, dst_mac=mac, ethertype=ETH_IPV4, payload=packet
                ),
            )
            return

        pending = self._arp_pending.setdefault(next_hop, [])
        if len(pending) >= ARP_MAX_PENDING:
            net.record_drop("arp_queue_full")
            return
        first_request = not pending
        pending.append(packet)
        if first_request:
            self._arp_attempts[next_hop] = 1
            self._send_arp_request(net, iface, next_hop)
            self._schedule_arp_timeout(net, iface, next_hop)

    def _schedule_arp_timeout(
        self, net: Network, iface: Interface, next_hop: IPv4Address
    ) -> None:
        net.scheduler.schedule_after(
            ARP_TIMEOUT,
            SimEvent(
                time=0.0,
                type=EventType.TIMER,
                handler=lambda _c, _e, ip=next_hop, i=iface: self._arp_timeout(net, i, ip),
                node_id=self.node_id,
            ),
        )

    def _send_arp_request(self, net: Network, iface: Interface, target: IPv4Address) -> None:
        src_ip = iface.ip.ip if iface.ip else IPv4Address("0.0.0.0")
        iface.transmit(
            net,
            EthernetFrame(
                src_mac=iface.mac,
                dst_mac=BROADCAST_MAC,
                ethertype=ETH_ARP,
                payload=ArpPacket(
                    op="request",
                    sender_mac=iface.mac,
                    sender_ip=src_ip,
                    target_mac="00:00:00:00:00:00",
                    target_ip=target,
                ),
            ),
        )

    def _arp_timeout(self, net: Network, iface: Interface, ip: IPv4Address) -> None:
        if ip not in self._arp_pending or ip in self.arp_table:
            return
        attempts = self._arp_attempts.get(ip, 1)
        if attempts < ARP_RETRIES:
            self._arp_attempts[ip] = attempts + 1
            self._send_arp_request(net, iface, ip)
            self._schedule_arp_timeout(net, iface, ip)
            return
        stale = self._arp_pending.pop(ip, None)
        self._arp_attempts.pop(ip, None)
        if stale:
            for _pkt in stale:
                net.record_drop("arp_timeout")

    # ----- ARP ingress -------------------------------------------------------
    def _handle_arp(self, net: Network, iface: Interface, arp: ArpPacket) -> None:
        # Learn the sender either way (gratuitous learning, like real stacks).
        if str(arp.sender_ip) != "0.0.0.0":
            self.arp_table[arp.sender_ip] = (MacAddr(arp.sender_mac), iface.name)
            self._flush_pending(net, arp.sender_ip)

        if arp.op == "request" and iface.has_ip(arp.target_ip):
            iface.transmit(
                net,
                EthernetFrame(
                    src_mac=iface.mac,
                    dst_mac=arp.sender_mac,
                    ethertype=ETH_ARP,
                    payload=ArpPacket(
                        op="reply",
                        sender_mac=iface.mac,
                        sender_ip=arp.target_ip,
                        target_mac=arp.sender_mac,
                        target_ip=arp.sender_ip,
                    ),
                ),
            )

    def _flush_pending(self, net: Network, resolved: IPv4Address) -> None:
        self._arp_attempts.pop(resolved, None)
        queued = self._arp_pending.pop(resolved, None)
        if not queued:
            return
        mac, iface_name = self.arp_table[resolved]
        out = self.interfaces.get(iface_name)
        if out is None:
            return
        for pkt in queued:
            out.transmit(
                net,
                EthernetFrame(
                    src_mac=out.mac, dst_mac=mac, ethertype=ETH_IPV4, payload=pkt
                ),
            )

    # ----- ICMP -----------------------------------------------------------------
    def _handle_icmp_to_self(self, net: Network, packet: Ipv4Packet) -> None:
        icmp = packet.payload
        if not isinstance(icmp, IcmpMessage):
            return
        if icmp.type == 8:  # echo request -> reply
            self.send_ip(
                net,
                Ipv4Packet(
                    src=packet.dst,
                    dst=packet.src,
                    proto=PROTO_ICMP,
                    ttl=64,
                    dont_fragment=packet.dont_fragment,  # reply inherits request's DF (P-4b)
                    payload=IcmpMessage(
                        type=0, ident=icmp.ident, seq=icmp.seq, data_len=icmp.data_len
                    ),
                ),
            )
        else:
            net.on_icmp(self, packet, icmp)

    def on_no_route(self, net: Network, packet: Ipv4Packet) -> None:
        """Hook: no route for a locally-originated/forwarded packet."""

    # ==================== IPv6: egress + neighbor discovery ====================
    def egress_for6(self, dst: IPv6Address) -> tuple[Interface, IPv6Address] | None:
        """(egress iface, next-hop) for ``dst``; None = no route.

        Base implementation: connected prefixes, plus any-iface fallback for
        link-local/multicast destinations (every port owns a link-local).
        """
        for iface in self.interfaces.values():
            for ip in iface.ips6:
                if dst in ip.network:
                    return iface, dst
        if dst.is_link_local or dst.is_multicast:
            for iface in self.interfaces.values():
                if iface.ips6 or iface.slaac:
                    return iface, dst
            first = next(iter(self.interfaces.values()), None)
            if first is not None:
                return first, dst
        return None

    def send_ip6(self, net: Network, packet: Ipv6Packet) -> None:
        route = self.egress_for6(packet.dst)
        if route is None:
            net.record_drop("no_route6")
            self.on_no_route6(net, packet)
            return
        iface, next_hop = route
        self._resolve_and_send6(net, iface, next_hop, packet)

    def _resolve_and_send6(
        self, net: Network, iface: Interface, next_hop: IPv6Address, packet: Ipv6Packet
    ) -> None:
        if packet.dst.is_multicast:
            iface.transmit(
                net,
                EthernetFrame(
                    src_mac=iface.mac,
                    dst_mac=ipv6_multicast_mac(packet.dst),
                    ethertype=ETH_IPV6,
                    payload=packet,
                ),
            )
            return
        if next_hop == packet.dst and self.owns_ip6(packet.dst):
            return  # to-self; nothing to put on the wire
        cached = self.nd_cache.get(next_hop)
        if cached is not None:
            mac, iface_name = cached
            out = self.interfaces.get(iface_name, iface)
            out.transmit(
                net,
                EthernetFrame(
                    src_mac=out.mac, dst_mac=mac, ethertype=ETH_IPV6, payload=packet
                ),
            )
            return

        pending = self._nd_pending.setdefault(next_hop, [])
        if len(pending) >= ARP_MAX_PENDING:
            net.record_drop("nd_queue_full")
            return
        first_request = not pending
        pending.append(packet)
        if first_request:
            self._nd_attempts[next_hop] = 1
            self._send_ns(net, iface, next_hop)
            self._schedule_nd_timeout(net, iface, next_hop)

    def _send_ns(self, net: Network, iface: Interface, target: IPv6Address) -> None:
        group = solicited_node(target)
        iface.transmit(
            net,
            EthernetFrame(
                src_mac=iface.mac,
                dst_mac=ipv6_multicast_mac(group),
                ethertype=ETH_IPV6,
                payload=Ipv6Packet(
                    src=iface.link_local.ip,
                    dst=group,
                    proto=PROTO_ICMPV6,
                    hop_limit=255,
                    payload=Icmpv6Message(
                        type=135, target=target, ll_addr=str(iface.mac)
                    ),
                ),
            ),
        )

    def _schedule_nd_timeout(
        self, net: Network, iface: Interface, next_hop: IPv6Address
    ) -> None:
        net.scheduler.schedule_after(
            ARP_TIMEOUT,
            SimEvent(
                time=0.0,
                type=EventType.TIMER,
                handler=lambda _c, _e, ip=next_hop, i=iface: self._nd_timeout(net, i, ip),
                node_id=self.node_id,
            ),
        )

    def _nd_timeout(self, net: Network, iface: Interface, ip: IPv6Address) -> None:
        if ip not in self._nd_pending or ip in self.nd_cache:
            return
        attempts = self._nd_attempts.get(ip, 1)
        if attempts < ARP_RETRIES:
            self._nd_attempts[ip] = attempts + 1
            self._send_ns(net, iface, ip)
            self._schedule_nd_timeout(net, iface, ip)
            return
        stale = self._nd_pending.pop(ip, None)
        self._nd_attempts.pop(ip, None)
        if stale:
            for _pkt in stale:
                net.record_drop("nd_timeout")

    def _nd_learn(self, net: Network, iface: Interface, addr: IPv6Address, ll: str) -> None:
        self.nd_cache[addr] = (MacAddr(ll), iface.name)
        self._nd_attempts.pop(addr, None)
        queued = self._nd_pending.pop(addr, None)
        if not queued:
            return
        mac, iface_name = self.nd_cache[addr]
        out = self.interfaces.get(iface_name)
        if out is None:
            return
        for pkt in queued:
            out.transmit(
                net,
                EthernetFrame(
                    src_mac=out.mac, dst_mac=mac, ethertype=ETH_IPV6, payload=pkt
                ),
            )

    # ----- ICMPv6 ingress (NDP + echo + errors) --------------------------------
    def _handle_icmpv6(
        self, net: Network, iface: Interface, pkt: Ipv6Packet, icmp: Icmpv6Message
    ) -> None:
        if icmp.type == 135:  # neighbor solicitation
            if icmp.ll_addr and not pkt.src.is_unspecified:
                self._nd_learn(net, iface, pkt.src, icmp.ll_addr)
            if icmp.target is not None and iface.has_ip6(icmp.target):
                self._send_na(net, iface, pkt.src, icmp.target)
            return
        if icmp.type == 136:  # neighbor advertisement
            if icmp.target is not None and icmp.ll_addr:
                self._nd_learn(net, iface, icmp.target, icmp.ll_addr)
            return
        if icmp.type == 128:  # echo request -> reply
            src = pkt.dst if not pkt.dst.is_multicast else iface.link_local.ip
            self.send_ip6(
                net,
                Ipv6Packet(
                    src=src,
                    dst=pkt.src,
                    proto=PROTO_ICMPV6,
                    hop_limit=64,
                    payload=Icmpv6Message(
                        type=129, ident=icmp.ident, seq=icmp.seq, data_len=icmp.data_len
                    ),
                ),
            )
            return
        if icmp.type == 129:  # echo reply
            net.ping_reply_received(self, pkt, icmp)
            return
        if icmp.type in (1, 2, 3):  # unreachable / too-big / time exceeded
            net.on_icmp6(self, pkt, icmp)
            return
        self.on_ndp_router(net, iface, pkt, icmp)  # RS/RA — subclass hooks

    def _send_na(
        self, net: Network, iface: Interface, dst: IPv6Address, target: IPv6Address
    ) -> None:
        cached = self.nd_cache.get(dst)
        dst_mac = cached[0] if cached else ipv6_multicast_mac(IPv6Address("ff02::1"))
        iface.transmit(
            net,
            EthernetFrame(
                src_mac=iface.mac,
                dst_mac=dst_mac,
                ethertype=ETH_IPV6,
                payload=Ipv6Packet(
                    src=target,
                    dst=dst,
                    proto=PROTO_ICMPV6,
                    hop_limit=255,
                    payload=Icmpv6Message(
                        type=136, target=target, ll_addr=str(iface.mac)
                    ),
                ),
            ),
        )

    def on_ndp_router(
        self, net: Network, iface: Interface, pkt: Ipv6Packet, icmp: Icmpv6Message
    ) -> None:
        """Hook: RS/RA handling — hosts consume RA, routers answer RS."""

    def on_no_route6(self, net: Network, packet: Ipv6Packet) -> None:
        """Hook: no IPv6 route for a locally-originated/forwarded packet."""


class Host(L3Device):
    """An end host: ARP, ping, DHCP client, DNS stub resolver."""

    kind = "host"

    def __init__(self, name: str, node_id: str | None = None, nos: str = "forgeos") -> None:
        super().__init__(name, node_id, nos)
        self.default_gateway: IPv4Address | None = None
        self.default_gateway6: IPv6Address | None = None
        self._gateway6_iface: str | None = None   # iface the RA arrived on
        self.dns_server: IPv4Address | None = None
        self.dns_cache: dict[str, IPv4Address] = {}
        self._dhcp_xid = 0
        self._dns_xid = 0
        self._dns_waiting: dict[int, Callable[[IPv4Address | None], None]] = {}

    # ----- routing: connected or default gateway --------------------------------
    def egress_for(self, dst: IPv4Address) -> tuple[Interface, IPv4Address] | None:
        direct = super().egress_for(dst)
        if direct is not None:
            return direct
        if self.default_gateway is not None:
            for iface in self.interfaces.values():
                for ip in iface.ips:
                    if self.default_gateway in ip.network:
                        return iface, self.default_gateway
        return None

    def egress_for6(self, dst: IPv6Address) -> tuple[Interface, IPv6Address] | None:
        direct = super().egress_for6(dst)
        if direct is not None:
            return direct
        gw = self.default_gateway6
        if gw is not None:
            iface = self.interfaces.get(self._gateway6_iface or "")
            if iface is None:  # statically configured gateway: any v6 port
                iface = next(
                    (i for i in self.interfaces.values() if i.ips6 or i.slaac),
                    next(iter(self.interfaces.values()), None),
                )
            if iface is not None:
                return iface, gw
        return None

    # ----- SLAAC / router discovery ----------------------------------------------
    def on_start(self, net: Network) -> None:
        """Solicit routers on SLAAC-enabled ports so autoconfig converges fast."""
        for iface in self.interfaces.values():
            if iface.slaac:
                net.scheduler.schedule_after(
                    0.01,
                    SimEvent(
                        time=0.0,
                        type=EventType.TIMER,
                        handler=lambda _c, _e, i=iface: self._send_rs(net, i),
                        node_id=self.node_id,
                    ),
                )

    def _send_rs(self, net: Network, iface: Interface) -> None:
        iface.transmit(
            net,
            EthernetFrame(
                src_mac=iface.mac,
                dst_mac=ipv6_multicast_mac(ALL_ROUTERS_V6),
                ethertype=ETH_IPV6,
                payload=Ipv6Packet(
                    src=iface.link_local.ip,
                    dst=ALL_ROUTERS_V6,
                    proto=PROTO_ICMPV6,
                    hop_limit=255,
                    payload=Icmpv6Message(type=133, ll_addr=str(iface.mac)),
                ),
            ),
        )

    def on_ndp_router(
        self, net: Network, iface: Interface, pkt: Ipv6Packet, icmp: Icmpv6Message
    ) -> None:
        if icmp.type != 134:  # hosts only consume router advertisements
            return
        if icmp.ll_addr and not pkt.src.is_unspecified:
            self._nd_learn(net, iface, pkt.src, icmp.ll_addr)
        if icmp.router_lifetime > 0 and self.default_gateway6 is None:
            self.default_gateway6 = pkt.src
            self._gateway6_iface = iface.name
        if iface.slaac:
            for pfx in icmp.prefixes:
                try:
                    net6 = IPv6Network(pfx)
                except ValueError:
                    continue
                if net6.prefixlen != 64:
                    continue
                addr = slaac_address(net6, iface.mac)
                if addr not in iface.ips6:
                    iface.ips6.append(addr)
                    net.on_slaac_bound(self, iface, addr)

    # ----- frame handling --------------------------------------------------------
    def on_frame(self, net: Network, iface: Interface, frame: EthernetFrame) -> None:
        if not self.powered_on:
            return
        # NIC filter: mine, broadcast or multicast only.
        if frame.dst_mac not in (iface.mac, BROADCAST_MAC) and not MacAddr(
            frame.dst_mac
        ).is_multicast:
            return

        payload = frame.payload
        if isinstance(payload, ArpPacket):
            self._handle_arp(net, iface, payload)
            return
        if isinstance(payload, Ipv6Packet):
            self._on_ipv6(net, iface, payload)
            return
        if not isinstance(payload, Ipv4Packet):
            return

        pkt = payload
        broadcastish = str(pkt.dst) == "255.255.255.255" or (
            iface.ip and pkt.dst == iface.ip.network.broadcast_address
        )
        if not (self.owns_ip(pkt.dst) or broadcastish):
            return  # hosts do not forward

        if pkt.proto == PROTO_ICMP:
            icmp = pkt.payload
            if isinstance(icmp, IcmpMessage) and icmp.type == 0:
                net.ping_reply_received(self, pkt, icmp)
                return
            if isinstance(icmp, IcmpMessage) and icmp.type in (3, 11):
                net.on_icmp(self, pkt, icmp)
                return
            self._handle_icmp_to_self(net, pkt)
            return

        if pkt.proto == PROTO_UDP and isinstance(pkt.payload, UdpSegment):
            self._handle_udp(net, iface, pkt, pkt.payload)

    def _on_ipv6(self, net: Network, iface: Interface, pkt: Ipv6Packet) -> None:
        if not (self.owns_ip6(pkt.dst) or iface.joined_group(pkt.dst)):
            return  # hosts do not forward
        if pkt.proto == PROTO_ICMPV6 and isinstance(pkt.payload, Icmpv6Message):
            self._handle_icmpv6(net, iface, pkt, pkt.payload)

    # ----- applications ---------------------------------------------------------
    def ping(
        self,
        net: Network,
        dst: IPv4Address | IPv6Address,
        count: int = 4,
        interval: float = 1.0,
        size: int = 56,
        ttl: int = 64,
    ) -> int:
        """Send ``count`` ICMP(v6) echo requests — dual stack, dispatched on
        the destination's address family. Returns the session ident used to
        correlate replies (results are collected by the Network)."""
        ident = net.new_ping_session(self, dst, count)
        for seq in range(1, count + 1):
            net.scheduler.schedule_after(
                (seq - 1) * interval,
                SimEvent(
                    time=0.0,
                    type=EventType.TIMER,
                    handler=lambda _c, _e, s=seq: self._send_echo(net, dst, ident, s, size, ttl),
                    node_id=self.node_id,
                ),
            )
        return ident

    def _send_echo(
        self,
        net: Network,
        dst: IPv4Address | IPv6Address,
        ident: int,
        seq: int,
        size: int,
        ttl: int,
    ) -> None:
        if isinstance(dst, IPv6Address):
            self._send_echo6(net, dst, ident, seq, size, ttl)
            return
        src_iface = self.egress_for(dst)
        src_ip = (
            src_iface[0].ip.ip
            if src_iface and src_iface[0].ip
            else (self.all_ips()[0].ip if self.all_ips() else IPv4Address("0.0.0.0"))
        )
        net.ping_sent(ident, seq)
        self.send_ip(
            net,
            Ipv4Packet(
                src=src_ip,
                dst=dst,
                proto=PROTO_ICMP,
                ttl=ttl,
                payload=IcmpMessage(type=8, ident=ident, seq=seq, data_len=size),
            ),
        )

    def _send_echo6(
        self, net: Network, dst: IPv6Address, ident: int, seq: int, size: int, hlim: int
    ) -> None:
        route = self.egress_for6(dst)
        iface = route[0] if route else next(iter(self.interfaces.values()), None)
        if iface is None:
            net.record_drop("no_route6")
            return
        if dst.is_link_local or dst.is_multicast or not iface.ips6:
            src_ip = iface.link_local.ip
        else:
            src_ip = iface.ips6[0].ip
        net.ping_sent(ident, seq)
        self.send_ip6(
            net,
            Ipv6Packet(
                src=src_ip,
                dst=dst,
                proto=PROTO_ICMPV6,
                hop_limit=hlim,
                payload=Icmpv6Message(type=128, ident=ident, seq=seq, data_len=size),
            ),
        )

    # ----- DHCP client ------------------------------------------------------------
    def dhcp_discover(self, net: Network, iface_name: str | None = None) -> None:
        iface = (
            self.interfaces.get(iface_name)
            if iface_name
            else next(iter(self.interfaces.values()), None)
        )
        if iface is None:
            return
        self._dhcp_xid = net.next_xid()
        self._broadcast_dhcp(
            net, iface, DhcpMessage(op="discover", client_mac=iface.mac, xid=self._dhcp_xid)
        )

    def _broadcast_dhcp(self, net: Network, iface: Interface, msg: DhcpMessage) -> None:
        iface.transmit(
            net,
            EthernetFrame(
                src_mac=iface.mac,
                dst_mac=BROADCAST_MAC,
                ethertype=ETH_IPV4,
                payload=Ipv4Packet(
                    src=IPv4Address("0.0.0.0"),
                    dst=IPv4Address("255.255.255.255"),
                    proto=PROTO_UDP,
                    ttl=64,
                    payload=UdpSegment(src_port=68, dst_port=67, payload=msg),
                ),
            ),
        )

    def _handle_udp(
        self, net: Network, iface: Interface, pkt: Ipv4Packet, udp: UdpSegment
    ) -> None:
        app = udp.payload
        if isinstance(app, DhcpMessage) and udp.dst_port == 68:
            self._handle_dhcp(net, iface, app)
        elif isinstance(app, DnsMessage) and app.op == "response":
            cb = self._dns_waiting.pop(app.xid, None)
            answer = IPv4Address(app.answer) if app.answer else None
            if answer is not None:
                self.dns_cache[app.qname] = answer
            if cb is not None:
                cb(answer)
            net.on_dns_response(self, app)

    def _handle_dhcp(self, net: Network, iface: Interface, msg: DhcpMessage) -> None:
        if msg.xid != self._dhcp_xid:
            return
        if msg.op == "offer":
            self._broadcast_dhcp(
                net,
                iface,
                DhcpMessage(
                    op="request",
                    client_mac=iface.mac,
                    your_ip=msg.your_ip,
                    server_ip=msg.server_ip,
                    xid=msg.xid,
                ),
            )
        elif msg.op == "ack" and msg.your_ip and msg.subnet_mask:
            prefix = IPv4Interface(f"{msg.your_ip}/{msg.subnet_mask}")
            iface.ips = [prefix]
            if msg.gateway:
                self.default_gateway = IPv4Address(msg.gateway)
            if msg.dns:
                self.dns_server = IPv4Address(msg.dns)
            net.on_dhcp_bound(self, iface, prefix)

    # ----- DNS stub resolver ---------------------------------------------------------
    def resolve(
        self,
        net: Network,
        qname: str,
        callback: Callable[[IPv4Address | None], None] | None = None,
    ) -> None:
        cached = self.dns_cache.get(qname)
        if cached is not None:
            if callback:
                callback(cached)
            return
        if self.dns_server is None:
            if callback:
                callback(None)
            return
        self._dns_xid = net.next_xid()
        if callback:
            self._dns_waiting[self._dns_xid] = callback
        route = self.egress_for(self.dns_server)
        src_ip = route[0].ip.ip if route and route[0].ip else IPv4Address("0.0.0.0")
        self.send_ip(
            net,
            Ipv4Packet(
                src=src_ip,
                dst=self.dns_server,
                proto=PROTO_UDP,
                ttl=64,
                payload=UdpSegment(
                    src_port=30000 + (self._dns_xid % 20000),
                    dst_port=53,
                    payload=DnsMessage(op="query", qname=qname, xid=self._dns_xid),
                ),
            ),
        )
