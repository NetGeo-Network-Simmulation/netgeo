"""L3 routing: the Router (and Firewall) device.

Implements the real forwarding pipeline:

    ingress ACL -> local-delivery check -> TTL decrement (ICMP time-exceeded)
    -> NAT (in->out source translation / out->in destination restore)
    -> longest-prefix-match route lookup (ICMP unreachable when none)
    -> egress ACL -> ARP resolution -> transmit

plus the service plane: DHCP server pools, an authoritative DNS zone, and
attachment points for dynamic routing processes (OSPF/BGP) which install
routes with their administrative distance.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from ipaddress import (
    IPv4Address,
    IPv4Interface,
    IPv4Network,
    IPv6Address,
    IPv6Network,
)
from typing import TYPE_CHECKING, Optional

from engine.events import EventType, SimEvent
from engine.netstack.addr import ALL_NODES_V6, ALL_ROUTERS_V6, BROADCAST_MAC, MacAddr
from engine.netstack.device import L3Device
from engine.netstack.frames import (
    ETH_IPV4,
    ETH_IPV6,
    ETH_MPLS,
    PROTO_ICMP,
    PROTO_ICMPV6,
    PROTO_OSPF,
    PROTO_TCP,
    PROTO_UDP,
    ArpPacket,
    DhcpMessage,
    DnsMessage,
    ISIS_PDUS,
    MPLS_L2_PDUS,
    EthernetFrame,
    IcmpMessage,
    Icmpv6Message,
    Ipv4Packet,
    Ipv6Packet,
    MplsPacket,
    TcpSegment,
    UdpSegment,
    VXLAN_UDP_PORT,
    VxlanPacket,
)
from engine.netstack.iface import Interface
from engine.netstack.qos import apply_marking

if TYPE_CHECKING:  # pragma: no cover
    from engine.netstack.network import Network

# Administrative distances (Cisco-style).
AD = {"connected": 0, "static": 1, "ebgp": 20, "ospf": 110, "isis": 115,
      "rip": 120, "ibgp": 200, "vpnv4": 200}


@dataclass(slots=True)
class Route:
    prefix: IPv4Network
    next_hop: IPv4Address | None      # None = directly connected
    iface_name: str | None            # egress interface (resolved if None)
    source: str = "static"            # connected|static|ospf|ebgp|ibgp|rip|vpnv4
    metric: int = 0
    vpn_label: int | None = None      # MPLS VPN label for a VPNv4 route (else None)

    @property
    def ad(self) -> int:
        return AD.get(self.source, 255)

    def as_dict(self) -> dict:
        d = {
            "prefix": str(self.prefix),
            "next_hop": str(self.next_hop) if self.next_hop else None,
            "iface": self.iface_name,
            "source": self.source,
            "metric": self.metric,
            "ad": self.ad,
        }
        if self.vpn_label is not None:
            d["vpn_label"] = self.vpn_label
        return d


def _lpm(routes: list["Route"], dst: IPv4Address) -> "Route | None":
    """Longest-prefix match over a route list; ties broken by (ad, metric).
    Shared by the global RIB and every per-VRF RIB."""
    best: Route | None = None
    for r in routes:
        if dst not in r.prefix:
            continue
        if (
            best is None
            or r.prefix.prefixlen > best.prefix.prefixlen
            or (
                r.prefix.prefixlen == best.prefix.prefixlen
                and (r.ad, r.metric) < (best.ad, best.metric)
            )
        ):
            best = r
    return best


@dataclass(slots=True)
class LfibEntry:
    """One MPLS forwarding entry: swap the top label (or pop at the egress LSR)
    and send to the LDP-learned L2 next hop."""

    prefix: str
    action: str                       # "swap" | "pop"
    out_label: int | None
    nh_ip: IPv4Address | None
    nh_mac: str | None
    out_iface: str | None


@dataclass(slots=True)
class AdjSidEntry:
    """One SR adjacency-SID: pop the label and send out this specific L2
    adjacency (NG-SIM-09). Written by SrProcess from the LDP-learned neighbor
    table, read by :meth:`Router._mpls_forward`."""

    out_iface: str
    nh_mac: str
    peer_router_id: str


@dataclass(slots=True)
class Vrf:
    """A VPN routing/forwarding instance: its own RIB, isolated from the
    global table and from every other VRF (NG-SIM-08)."""

    name: str
    rd: str = ""
    rt_import: frozenset = frozenset()
    rt_export: frozenset = frozenset()
    vpn_label: int = 0
    routes: list[Route] = field(default_factory=list)

    def lookup(self, dst: IPv4Address) -> Route | None:
        return _lpm(self.routes, dst)

    def install(self, route: Route) -> None:
        self.routes = [
            r for r in self.routes
            if not (r.prefix == route.prefix and r.source == route.source)
        ]
        self.routes.append(route)

    def withdraw(self, source: str) -> None:
        self.routes = [r for r in self.routes if r.source != source]

    def route_rows(self) -> list[dict]:
        return [r.as_dict() for r in sorted(
            self.routes, key=lambda r: (r.prefix.network_address, r.prefix.prefixlen, r.ad)
        )]


@dataclass(slots=True)
class Route6:
    """An IPv6 RIB entry — same shape as :class:`Route`."""

    prefix: IPv6Network
    next_hop: IPv6Address | None      # None = directly connected
    iface_name: str | None
    source: str = "static"
    metric: int = 0

    @property
    def ad(self) -> int:
        return AD.get(self.source, 255)

    def as_dict(self) -> dict:
        return {
            "prefix": str(self.prefix),
            "next_hop": str(self.next_hop) if self.next_hop else None,
            "iface": self.iface_name,
            "source": self.source,
            "metric": self.metric,
            "ad": self.ad,
        }


@dataclass(slots=True)
class AclRule:
    """A stateless access-list entry. ``None`` fields match anything."""

    action: str = "permit"                     # permit | deny
    proto: str | None = None                   # icmp|tcp|udp|ospf
    src: IPv4Network | None = None
    dst: IPv4Network | None = None
    dst_port: int | None = None

    def matches(self, pkt: Ipv4Packet) -> bool:
        if self.proto is not None and pkt.proto_name != self.proto:
            return False
        if self.src is not None and pkt.src not in self.src:
            return False
        if self.dst is not None and pkt.dst not in self.dst:
            return False
        if self.dst_port is not None:
            l4 = pkt.payload
            port = getattr(l4, "dst_port", None)
            if port != self.dst_port:
                return False
        return True

    def as_dict(self) -> dict:
        return {
            "action": self.action,
            "proto": self.proto,
            "src": str(self.src) if self.src else "any",
            "dst": str(self.dst) if self.dst else "any",
            "dst_port": self.dst_port,
        }


@dataclass(slots=True)
class NatBinding:
    inside_ip: IPv4Address
    inside_key: int              # src port (tcp/udp) or icmp ident
    outside_ip: IPv4Address
    outside_key: int
    proto: str


@dataclass(slots=True)
class DhcpPool:
    network: IPv4Network
    gateway: IPv4Address
    dns: IPv4Address | None = None
    range_start: int = 10        # host number of the first lease
    range_end: int = 250
    lease_s: int = 86400
    leases: dict[str, IPv4Address] = field(default_factory=dict)  # mac -> ip

    def allocate(self, mac: str) -> IPv4Address | None:
        if mac in self.leases:
            return self.leases[mac]
        taken = set(self.leases.values())
        base = int(self.network.network_address)
        for host_no in range(self.range_start, self.range_end + 1):
            candidate = IPv4Address(base + host_no)
            if candidate not in taken and candidate != self.gateway:
                self.leases[mac] = candidate
                return candidate
        return None


class Router(L3Device):
    """A router: LPM forwarding, ICMP errors, NAT44, ACLs, DHCP/DNS server."""

    kind = "router"

    def __init__(self, name: str, node_id: str | None = None, nos: str = "forgeos") -> None:
        super().__init__(name, node_id, nos)
        self.routes: list[Route] = []
        self.routes6: list[Route6] = []
        self.ra_enabled = False           # advertise prefixes for SLAAC
        self.ra_interval = 30.0
        self.acl_in: dict[str, list[AclRule]] = {}    # iface name -> rules
        self.acl_out: dict[str, list[AclRule]] = {}
        # NAT
        self.nat_inside: set[str] = set()             # iface names
        self.nat_outside: str | None = None
        self._nat_bindings: list[NatBinding] = []
        self._nat_next_key = 20000
        # Services
        self.dhcp_pools: list[DhcpPool] = []
        self.dns_zone: dict[str, IPv4Address] = {}
        # Dynamic routing processes (duck-typed: .on_packet(net, iface, pkt),
        # .start(net), .on_iface_change(net))
        self.processes: list = []
        self.forwarded = 0
        # MPLS / L3VPN (NG-SIM-08). Written by the LDP/L3VPN processes, read by
        # the forwarding pipeline. Empty on a plain IP router = zero overhead.
        self.mpls_enabled = False
        self.lfib: dict[int, LfibEntry] = {}          # local label -> action
        self.mpls_fec: dict[IPv4Network, LfibEntry] = {}  # FEC prefix -> push
        self.vrfs: dict[str, Vrf] = {}                # vrf name -> instance
        self.iface_vrf: dict[str, str] = {}           # iface name -> vrf name
        self.vpn_labels: dict[int, str] = {}          # my VPN label -> vrf name
        self.sr_adj: dict[int, AdjSidEntry] = {}      # SR adjacency-SID -> egress
        # VXLAN access ports (NG-SIM-10): iface names whose ingress frames are
        # bridged into the overlay by the VxlanProcess. Empty = zero overhead.
        self.vxlan_ports: set[str] = set()
        # QoS marking rules (NG-SIM-11 §2.3): applied on the egress forward path.
        # Each rule: {"match": {"proto": "udp", "dst_port": 5060}, "set_dscp": 46}
        self.qos_marking: list[dict] = []

    # ----- route table management -------------------------------------------------
    def sync_connected_routes(self) -> None:
        """(Re)derive connected routes from interface addressing (v4 + v6)."""
        self.routes = [r for r in self.routes if r.source != "connected"]
        self.routes6 = [r for r in self.routes6 if r.source != "connected"]
        for iface in self.interfaces.values():
            for ip in iface.ips:
                self.routes.append(
                    Route(
                        prefix=ip.network,
                        next_hop=None,
                        iface_name=iface.name,
                        source="connected",
                    )
                )
            for ip6 in iface.ips6:
                self.routes6.append(
                    Route6(
                        prefix=ip6.network,
                        next_hop=None,
                        iface_name=iface.name,
                        source="connected",
                    )
                )

    def add_static_route(
        self, prefix: str | IPv4Network, next_hop: str | IPv4Address
    ) -> Route:
        route = Route(
            prefix=IPv4Network(prefix),
            next_hop=IPv4Address(next_hop),
            iface_name=None,
            source="static",
            metric=0,
        )
        self.routes.append(route)
        return route

    def install_route(self, route: Route) -> None:
        """Install/replace a dynamic route (same prefix + source)."""
        self.routes = [
            r
            for r in self.routes
            if not (r.prefix == route.prefix and r.source == route.source)
        ]
        self.routes.append(route)

    def withdraw_routes(self, source: str, prefixes: set[IPv4Network] | None = None) -> None:
        self.routes = [
            r
            for r in self.routes
            if not (r.source == source and (prefixes is None or r.prefix in prefixes))
        ]

    def lookup(self, dst: IPv4Address) -> Route | None:
        """Longest-prefix match; ties broken by admin distance then metric."""
        return _lpm(self.routes, dst)

    def egress_for(self, dst: IPv4Address) -> tuple[Interface, IPv4Address] | None:
        route = self.lookup(dst)
        if route is None:
            return None
        next_hop = route.next_hop if route.next_hop is not None else dst
        iface = self.interfaces.get(route.iface_name) if route.iface_name else None
        if iface is None:
            # Recursive resolution: find the connected iface for the next hop.
            for cand in self.interfaces.values():
                for ip in cand.ips:
                    if next_hop in ip.network:
                        return cand, next_hop
            return None
        return iface, next_hop

    # ----- IPv6 route table --------------------------------------------------------
    def add_static_route6(
        self,
        prefix: str | IPv6Network,
        next_hop: str | IPv6Address,
        iface_name: str | None = None,
    ) -> Route6:
        """Static v6 route. ``iface_name`` is required when the next hop is a
        link-local address (fe80:: is ambiguous without a port)."""
        route = Route6(
            prefix=IPv6Network(prefix),
            next_hop=IPv6Address(next_hop),
            iface_name=iface_name,
            source="static",
            metric=0,
        )
        self.routes6.append(route)
        return route

    def lookup6(self, dst: IPv6Address) -> Route6 | None:
        best: Route6 | None = None
        for r in self.routes6:
            if dst not in r.prefix:
                continue
            if (
                best is None
                or r.prefix.prefixlen > best.prefix.prefixlen
                or (
                    r.prefix.prefixlen == best.prefix.prefixlen
                    and (r.ad, r.metric) < (best.ad, best.metric)
                )
            ):
                best = r
        return best

    def egress_for6(self, dst: IPv6Address) -> tuple[Interface, IPv6Address] | None:
        if dst.is_link_local or dst.is_multicast:
            return super().egress_for6(dst)
        route = self.lookup6(dst)
        if route is None:
            return None
        next_hop = route.next_hop if route.next_hop is not None else dst
        iface = self.interfaces.get(route.iface_name) if route.iface_name else None
        if iface is None:
            for cand in self.interfaces.values():
                for ip6 in cand.ips6:
                    if next_hop in ip6.network:
                        return cand, next_hop
            return None
        return iface, next_hop

    # ----- ingress -----------------------------------------------------------------
    def on_frame(self, net: "Network", iface: Interface, frame: EthernetFrame) -> None:
        if not self.powered_on:
            return
        # VXLAN access port (NG-SIM-10): the tenant frame is bridged into the
        # overlay, so it is *not* addressed to this router's MAC — intercept it
        # before the normal "addressed to me" NIC filter drops it.
        if iface.name in self.vxlan_ports:
            for proc in self.processes:
                if getattr(proc, "proto", "") == "vxlan":
                    proc.on_access_frame(net, iface, frame)
            return
        if (
            frame.dst_mac not in (iface.mac, BROADCAST_MAC)
            and frame.dst_mac not in self.mac_aliases
            and not MacAddr(frame.dst_mac).is_multicast
        ):
            return

        payload = frame.payload
        if isinstance(payload, ArpPacket):
            self._handle_arp(net, iface, payload)
            return
        if isinstance(payload, MplsPacket):
            self._mpls_forward(net, iface, payload)
            return
        if isinstance(payload, MPLS_L2_PDUS):
            # LDP + SR ride raw L2 (no IP), dispatched here like IS-IS. Pass the
            # whole frame so the process can learn the L2 next hop (src_mac).
            # Each process ignores PDUs that aren't its own.
            for proc in self.processes:
                if getattr(proc, "proto", "") in ("ldp", "sr"):
                    proc.on_frame(net, iface, frame)
            return
        if isinstance(payload, ISIS_PDUS):
            # IS-IS rides on raw L2 (no IP), so it is dispatched here at the
            # frame edge rather than in _local_deliver like OSPF/BGP.
            for proc in self.processes:
                if getattr(proc, "proto", "") == "isis":
                    proc.on_frame(net, iface, payload)
            return
        if isinstance(payload, Ipv6Packet):
            self._on_ipv6(net, iface, payload)
            return
        if not isinstance(payload, Ipv4Packet):
            return
        pkt = payload

        # Ingress ACL.
        if not self._acl_permits(self.acl_in.get(iface.name), pkt):
            net.record_drop("acl_deny_in")
            self._send_icmp_error(net, pkt, icmp_type=3, code=13)  # admin prohibited
            return

        # NAT precedes local delivery: traffic arriving on the outside iface
        # addressed to the NAT address may belong to an inside binding.
        if (
            self.nat_outside
            and iface.name == self.nat_outside
            and iface.has_ip(pkt.dst)
            and self._nat_inbound(net, pkt)
        ):
            self._forward(net, iface, pkt)
            return

        broadcastish = str(pkt.dst) == "255.255.255.255" or (
            iface.ip and pkt.dst == iface.ip.network.broadcast_address
        )
        if self.owns_ip(pkt.dst) or broadcastish or pkt.dst.is_multicast:
            self._local_deliver(net, iface, pkt)
            return

        self._forward(net, iface, pkt)

    def _handle_arp(self, net: "Network", iface: Interface, arp: ArpPacket) -> None:
        super()._handle_arp(net, iface, arp)
        if arp.op == "request":
            # A VRRP master answers for the virtual IP with the virtual MAC.
            for proc in self.processes:
                if getattr(proc, "proto", "") == "vrrp":
                    proc.on_arp_request(net, iface, arp)

    # ----- forwarding pipeline --------------------------------------------------------
    def _forward(self, net: "Network", in_iface: Interface, pkt: Ipv4Packet) -> None:
        # Traffic entering a VRF interface is confined to that VRF's RIB — the
        # root of L3VPN isolation (NG-SIM-08).
        vrf_name = self.iface_vrf.get(in_iface.name)
        if vrf_name is not None:
            self._forward_vrf(net, pkt, vrf_name)
            return
        if pkt.ttl <= 1:
            net.record_drop("ttl_expired")
            self._send_icmp_error(net, pkt, icmp_type=11, code=0)
            return
        pkt.ttl -= 1

        route = self.lookup(pkt.dst)
        if route is None:
            net.record_drop("no_route")
            self._send_icmp_error(net, pkt, icmp_type=3, code=0)
            return
        next_hop = route.next_hop if route.next_hop is not None else pkt.dst
        out = self.interfaces.get(route.iface_name) if route.iface_name else None
        if out is None:
            resolved = self.egress_for(pkt.dst)
            if resolved is None:
                net.record_drop("no_route")
                self._send_icmp_error(net, pkt, icmp_type=3, code=0)
                return
            out, next_hop = resolved

        # NAT: source-translate inside -> outside, restore outside -> inside.
        if self.nat_outside:
            if in_iface.name in self.nat_inside and out.name == self.nat_outside:
                if not self._nat_outbound(net, out, pkt):
                    return
            elif in_iface.name == self.nat_outside:
                self._nat_inbound(net, pkt)
                # Destination may have changed; re-route.
                resolved = self.egress_for(pkt.dst)
                if resolved is None:
                    net.record_drop("no_route")
                    return
                out, next_hop = resolved

        if not self._acl_permits(self.acl_out.get(out.name), pkt):
            net.record_drop("acl_deny_out")
            self._send_icmp_error(net, pkt, icmp_type=3, code=13)
            return

        # QoS marking: mutate DSCP before egress enqueue (§2.3).
        if self.qos_marking:
            apply_marking(self.qos_marking, pkt)

        self.forwarded += 1
        self._resolve_and_send(net, out, next_hop, pkt)

    # ----- MPLS / L3VPN forwarding (NG-SIM-08) --------------------------------------
    def _forward_vrf(self, net: "Network", pkt: Ipv4Packet, vrf_name: str) -> None:
        """Forward an IP packet that entered a VRF interface, using only that
        VRF's RIB. A remote (VPNv4) route triggers MPLS encapsulation; a local
        route stays inside the VRF."""
        if pkt.ttl <= 1:
            net.record_drop("ttl_expired")
            self._send_icmp_error(net, pkt, icmp_type=11, code=0)
            return
        pkt.ttl -= 1
        self._vrf_deliver(net, pkt, vrf_name)

    def _vrf_deliver(self, net: "Network", pkt: Ipv4Packet, vrf_name: str) -> None:
        vrf = self.vrfs.get(vrf_name)
        route = vrf.lookup(pkt.dst) if vrf else None
        if route is None:
            net.record_drop("no_route")
            self._send_icmp_error(net, pkt, icmp_type=3, code=0)
            return
        if route.vpn_label is not None and route.next_hop is not None:
            self._mpls_encap(net, pkt, route)
            return
        # Local VRF route (a directly-connected CE on this PE): send it out.
        next_hop = route.next_hop if route.next_hop is not None else pkt.dst
        out = self.interfaces.get(route.iface_name) if route.iface_name else None
        if out is None:
            net.record_drop("no_route")
            return
        self.forwarded += 1
        self._resolve_and_send(net, out, next_hop, pkt)

    def _mpls_encap(self, net: "Network", pkt: Ipv4Packet, route: Route) -> None:
        """Push [transport_label, vpn_label] and send the labelled packet toward
        the egress PE (``route.next_hop`` = its loopback)."""
        fec = self.mpls_fec.get(IPv4Network(f"{route.next_hop}/32"))
        if fec is None or fec.out_label is None or fec.out_iface is None:
            net.record_drop("mpls_no_lsp")
            return
        out = self.interfaces.get(fec.out_iface)
        if out is None:
            net.record_drop("mpls_no_iface")
            return
        out.transmit(
            net,
            EthernetFrame(
                src_mac=out.mac,
                dst_mac=fec.nh_mac,
                ethertype=ETH_MPLS,
                payload=MplsPacket(labels=[fec.out_label, route.vpn_label], inner=pkt),
            ),
        )

    def _mpls_forward(self, net: "Network", iface: Interface, mpls: MplsPacket) -> None:
        """Label-switch: pop VPN labels into the local VRF, pop our own transport
        label at the LSP egress, swap transit labels toward the next LSR."""
        labels = list(mpls.labels)
        while labels:
            top = labels[0]
            vrf_name = self.vpn_labels.get(top)
            if vrf_name is not None:            # egress PE: this is a VPN label
                if mpls.inner is None:
                    return
                self._vrf_deliver(net, mpls.inner, vrf_name)
                return
            adj = self.sr_adj.get(top)
            if adj is not None:                 # SR adjacency-SID: pop, egress
                labels.pop(0)                   # out this specific L2 adjacency
                out = self.interfaces.get(adj.out_iface)
                if out is None:
                    net.record_drop("mpls_no_iface")
                    return
                out.transmit(
                    net,
                    EthernetFrame(
                        src_mac=out.mac,
                        dst_mac=adj.nh_mac,
                        ethertype=ETH_MPLS,
                        payload=MplsPacket(labels=labels, inner=mpls.inner),
                    ),
                )
                return
            entry = self.lfib.get(top)
            if entry is None:
                net.record_drop("mpls_no_label")
                return
            if entry.action == "pop":           # LSP egress for this transport FEC
                labels.pop(0)
                continue                        # examine the next (VPN) label
            out = self.interfaces.get(entry.out_iface) if entry.out_iface else None
            if out is None or entry.out_label is None:
                net.record_drop("mpls_no_iface")
                return
            labels[0] = entry.out_label         # swap
            out.transmit(
                net,
                EthernetFrame(
                    src_mac=out.mac,
                    dst_mac=entry.nh_mac,
                    ethertype=ETH_MPLS,
                    payload=MplsPacket(labels=labels, inner=mpls.inner),
                ),
            )
            return
        # Stack fully popped and no VPN label matched: forward the inner packet.
        if mpls.inner is not None:
            self._forward(net, iface, mpls.inner)

    def _acl_permits(self, rules: list[AclRule] | None, pkt: Ipv4Packet) -> bool:
        if not rules:
            return True
        for rule in rules:
            if rule.matches(pkt):
                return rule.action == "permit"
        return False  # implicit deny at the end of a configured ACL

    # ----- local delivery ------------------------------------------------------------
    def _local_deliver(self, net: "Network", iface: Interface, pkt: Ipv4Packet) -> None:
        # Local import: engine.netstack.protocols/__init__ pulls in ospf.py et al,
        # which import Route/Router from this module — a module-level import here
        # would deadlock on the partially-initialized routing module.
        from engine.netstack.protocols.vrrp import PROTO_VRRP

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
        if pkt.proto == PROTO_OSPF:
            for proc in self.processes:
                if getattr(proc, "proto", "") == "ospf":
                    proc.on_packet(net, iface, pkt)
            return
        if pkt.proto == PROTO_VRRP:
            for proc in self.processes:
                if getattr(proc, "proto", "") == "vrrp":
                    proc.on_packet(net, iface, pkt)
            return
        if pkt.proto == PROTO_TCP and isinstance(pkt.payload, TcpSegment):
            seg = pkt.payload
            if seg.dst_port == 179 or seg.src_port == 179:
                # Port 179 carries BGP-4 plus the VPNv4 and EVPN side-channels;
                # hand to all — each process ignores messages that aren't its own.
                for proc in self.processes:
                    if getattr(proc, "proto", "") in ("bgp", "l3vpn", "vxlan"):
                        proc.on_packet(net, iface, pkt)
                return
        if pkt.proto == PROTO_UDP and isinstance(pkt.payload, UdpSegment):
            udp = pkt.payload
            app = udp.payload
            if isinstance(app, VxlanPacket) and udp.dst_port == VXLAN_UDP_PORT:
                # VTEP decapsulation (NG-SIM-10): hand the overlay packet to the
                # VxlanProcess, which bridges the inner frame to a local access
                # port in the matching VNI.
                for proc in self.processes:
                    if getattr(proc, "proto", "") == "vxlan":
                        proc.on_overlay(net, pkt, app)
                return
            if isinstance(app, DhcpMessage) and udp.dst_port == 67:
                self._dhcp_serve(net, iface, app)
                return
            if isinstance(app, DnsMessage) and udp.dst_port == 53 and app.op == "query":
                self._dns_serve(net, iface, pkt, udp, app)
                return

    # ----- IPv6 ingress + forwarding pipeline ----------------------------------------
    def _on_ipv6(self, net: "Network", iface: Interface, pkt: Ipv6Packet) -> None:
        if (
            self.owns_ip6(pkt.dst)
            or iface.joined_group(pkt.dst)
            or pkt.dst == ALL_ROUTERS_V6
        ):
            if pkt.proto == PROTO_ICMPV6 and isinstance(pkt.payload, Icmpv6Message):
                self._handle_icmpv6(net, iface, pkt, pkt.payload)
            return
        if pkt.dst.is_multicast or pkt.dst.is_link_local:
            return  # never forwarded off-link
        self._forward6(net, iface, pkt)

    def _forward6(self, net: "Network", in_iface: Interface, pkt: Ipv6Packet) -> None:
        if pkt.hop_limit <= 1:
            net.record_drop("hop_limit_expired")
            self._send_icmpv6_error(net, pkt, icmp_type=3, code=0)
            return
        pkt.hop_limit -= 1

        resolved = self.egress_for6(pkt.dst)
        if resolved is None:
            net.record_drop("no_route6")
            self._send_icmpv6_error(net, pkt, icmp_type=1, code=0)
            return
        out, next_hop = resolved
        self.forwarded += 1
        self._resolve_and_send6(net, out, next_hop, pkt)

    def on_ndp_router(
        self, net: "Network", iface: Interface, pkt: Ipv6Packet, icmp: Icmpv6Message
    ) -> None:
        # Routers answer solicitations immediately (solicited RA).
        if icmp.type == 133 and self.ra_enabled:
            if icmp.ll_addr and not pkt.src.is_unspecified:
                self._nd_learn(net, iface, pkt.src, icmp.ll_addr)
            self._send_ra(net, iface)

    def enable_ra(self, interval: float = 30.0) -> None:
        self.ra_enabled = True
        self.ra_interval = interval

    def on_start(self, net: "Network") -> None:
        if not self.ra_enabled:
            return
        for iface in self.interfaces.values():
            if iface.ips6:
                self._schedule_ra(net, iface, first=True)

    def _schedule_ra(self, net: "Network", iface: Interface, first: bool = False) -> None:
        net.scheduler.schedule_after(
            0.05 if first else self.ra_interval,
            SimEvent(
                time=0.0,
                type=EventType.TIMER,
                handler=lambda _c, _e, i=iface: self._ra_tick(net, i),
                node_id=self.node_id,
            ),
        )

    def _ra_tick(self, net: "Network", iface: Interface) -> None:
        if not self.ra_enabled:
            return
        if self.powered_on and iface.is_up:
            self._send_ra(net, iface)
        self._schedule_ra(net, iface)

    def _send_ra(self, net: "Network", iface: Interface) -> None:
        """Unsolicited/solicited RA to all-nodes: advertise this port's /64s."""
        prefixes = tuple(
            str(ip6.network) for ip6 in iface.ips6 if ip6.network.prefixlen == 64
        )
        iface.transmit(
            net,
            EthernetFrame(
                src_mac=iface.mac,
                dst_mac="33:33:00:00:00:01",
                ethertype=ETH_IPV6,
                payload=Ipv6Packet(
                    src=iface.link_local.ip,
                    dst=ALL_NODES_V6,
                    proto=PROTO_ICMPV6,
                    hop_limit=255,
                    payload=Icmpv6Message(
                        type=134,
                        ll_addr=str(iface.mac),
                        prefixes=prefixes,
                        router_lifetime=int(self.ra_interval * 3),
                    ),
                ),
            ),
        )

    def _send_icmpv6_error(
        self, net: "Network", offending: Ipv6Packet, icmp_type: int, code: int
    ) -> None:
        # Never generate errors about errors, nor toward multicast sources.
        inner = offending.payload
        if isinstance(inner, Icmpv6Message) and inner.type in (1, 2, 3):
            return
        if offending.src.is_multicast or offending.src.is_unspecified:
            return
        route = self.egress_for6(offending.src)
        if route is None:
            return
        out = route[0]
        src_ip = out.ips6[0].ip if out.ips6 else out.link_local.ip
        orig_ident = getattr(inner, "ident", 0) if isinstance(inner, Icmpv6Message) else 0
        orig_seq = getattr(inner, "seq", 0) if isinstance(inner, Icmpv6Message) else 0
        self.send_ip6(
            net,
            Ipv6Packet(
                src=src_ip,
                dst=offending.src,
                proto=PROTO_ICMPV6,
                hop_limit=64,
                payload=Icmpv6Message(
                    type=icmp_type,
                    code=code,
                    data_len=48,
                    original=(str(offending.src), str(offending.dst)),
                    orig_ident=orig_ident,
                    orig_seq=orig_seq,
                ),
            ),
        )

    # ----- ICMP error generation ----------------------------------------------------
    def _send_icmp_error(
        self, net: "Network", offending: Ipv4Packet, icmp_type: int, code: int
    ) -> None:
        # Never generate errors about errors (RFC 1122).
        inner = offending.payload
        if isinstance(inner, IcmpMessage) and inner.type in (3, 11):
            return
        route = self.egress_for(offending.src)
        if route is None:
            return
        src_ip = route[0].ip.ip if route[0].ip else None
        if src_ip is None:
            return
        orig_ident = getattr(inner, "ident", 0) if isinstance(inner, IcmpMessage) else 0
        orig_seq = getattr(inner, "seq", 0) if isinstance(inner, IcmpMessage) else 0
        self.send_ip(
            net,
            Ipv4Packet(
                src=src_ip,
                dst=offending.src,
                proto=PROTO_ICMP,
                ttl=64,
                payload=IcmpMessage(
                    type=icmp_type,
                    code=code,
                    data_len=28,
                    original=(str(offending.src), str(offending.dst)),
                    orig_ident=orig_ident,
                    orig_seq=orig_seq,
                ),
            ),
        )

    def on_mtu_drop(self, net: "Network", iface: Interface, frame: EthernetFrame) -> None:
        pkt = frame.payload
        if isinstance(pkt, Ipv4Packet):
            self._send_icmp_error(net, pkt, icmp_type=3, code=4)  # frag needed
        elif isinstance(pkt, Ipv6Packet):
            self._send_icmpv6_error(net, pkt, icmp_type=2, code=0)  # packet too big

    # ----- NAT44 (PAT) ------------------------------------------------------------------
    def enable_nat(self, inside: list[str], outside: str) -> None:
        self.nat_inside = set(inside)
        self.nat_outside = outside

    def _nat_key(self, pkt: Ipv4Packet) -> int | None:
        l4 = pkt.payload
        if isinstance(l4, IcmpMessage):
            return l4.ident
        if isinstance(l4, (UdpSegment, TcpSegment)):
            return l4.src_port
        return None

    def _nat_outbound(self, net: "Network", out: Interface, pkt: Ipv4Packet) -> bool:
        outside_ip = out.ip.ip if out.ip else None
        if outside_ip is None:
            net.record_drop("nat_no_outside_ip")
            return False
        key = self._nat_key(pkt)
        if key is None:
            net.record_drop("nat_unsupported_proto")
            return False
        binding = next(
            (
                b
                for b in self._nat_bindings
                if b.inside_ip == pkt.src and b.inside_key == key and b.proto == pkt.proto_name
            ),
            None,
        )
        if binding is None:
            self._nat_next_key += 1
            binding = NatBinding(
                inside_ip=pkt.src,
                inside_key=key,
                outside_ip=outside_ip,
                outside_key=self._nat_next_key,
                proto=pkt.proto_name,
            )
            self._nat_bindings.append(binding)
        pkt.src = binding.outside_ip
        l4 = pkt.payload
        if isinstance(l4, IcmpMessage):
            l4.ident = binding.outside_key
        elif isinstance(l4, (UdpSegment, TcpSegment)):
            l4.src_port = binding.outside_key
        return True

    def _nat_inbound(self, net: "Network", pkt: Ipv4Packet) -> bool:
        """Restore the inside destination for a reply. True if translated."""
        l4 = pkt.payload
        key: int | None = None
        if isinstance(l4, IcmpMessage):
            key = l4.ident
        elif isinstance(l4, (UdpSegment, TcpSegment)):
            key = l4.dst_port
        if key is None:
            return False
        binding = next(
            (
                b
                for b in self._nat_bindings
                if b.outside_key == key and b.proto == pkt.proto_name
            ),
            None,
        )
        if binding is None:
            return False
        pkt.dst = binding.inside_ip
        if isinstance(l4, IcmpMessage):
            l4.ident = binding.inside_key
        elif isinstance(l4, (UdpSegment, TcpSegment)):
            l4.dst_port = binding.inside_key
        return True

    # ----- DHCP server --------------------------------------------------------------------
    def add_dhcp_pool(self, pool: DhcpPool) -> None:
        self.dhcp_pools.append(pool)

    def _pool_for_iface(self, iface: Interface) -> DhcpPool | None:
        for ip in iface.ips:
            for pool in self.dhcp_pools:
                if pool.network == ip.network:
                    return pool
        return None

    def _dhcp_serve(self, net: "Network", iface: Interface, msg: DhcpMessage) -> None:
        pool = self._pool_for_iface(iface)
        if pool is None:
            return
        if msg.op == "discover":
            offer_ip = pool.allocate(msg.client_mac)
            if offer_ip is None:
                return
            self._dhcp_send(net, iface, msg.client_mac, DhcpMessage(
                op="offer",
                client_mac=msg.client_mac,
                your_ip=str(offer_ip),
                server_ip=str(iface.ip.ip) if iface.ip else None,
                subnet_mask=str(pool.network.prefixlen),
                gateway=str(pool.gateway),
                dns=str(pool.dns) if pool.dns else None,
                lease_s=pool.lease_s,
                xid=msg.xid,
            ))
        elif msg.op == "request" and msg.your_ip:
            leased = pool.leases.get(msg.client_mac)
            if leased is None or str(leased) != msg.your_ip:
                return
            self._dhcp_send(net, iface, msg.client_mac, DhcpMessage(
                op="ack",
                client_mac=msg.client_mac,
                your_ip=msg.your_ip,
                server_ip=str(iface.ip.ip) if iface.ip else None,
                subnet_mask=str(pool.network.prefixlen),
                gateway=str(pool.gateway),
                dns=str(pool.dns) if pool.dns else None,
                lease_s=pool.lease_s,
                xid=msg.xid,
            ))

    def _dhcp_send(
        self, net: "Network", iface: Interface, client_mac: str, msg: DhcpMessage
    ) -> None:
        iface.transmit(
            net,
            EthernetFrame(
                src_mac=iface.mac,
                dst_mac=client_mac,   # unicast L2 to the requesting NIC
                ethertype=ETH_IPV4,
                payload=Ipv4Packet(
                    src=iface.ip.ip if iface.ip else IPv4Address("0.0.0.0"),
                    dst=IPv4Address("255.255.255.255"),
                    proto=PROTO_UDP,
                    ttl=64,
                    payload=UdpSegment(src_port=67, dst_port=68, payload=msg),
                ),
            ),
        )

    # ----- DNS server -----------------------------------------------------------------------
    def _dns_serve(
        self,
        net: "Network",
        iface: Interface,
        pkt: Ipv4Packet,
        udp: UdpSegment,
        query: DnsMessage,
    ) -> None:
        answer = self.dns_zone.get(query.qname.lower())
        self.send_ip(
            net,
            Ipv4Packet(
                src=pkt.dst,
                dst=pkt.src,
                proto=PROTO_UDP,
                ttl=64,
                payload=UdpSegment(
                    src_port=53,
                    dst_port=udp.src_port,
                    payload=DnsMessage(
                        op="response",
                        qname=query.qname,
                        answer=str(answer) if answer else None,
                        xid=query.xid,
                    ),
                ),
            ),
        )

    # ----- introspection ---------------------------------------------------------------------
    def route_table_rows(self) -> list[dict]:
        return [r.as_dict() for r in sorted(
            self.routes, key=lambda r: (r.prefix.network_address, r.prefix.prefixlen, r.ad)
        )]

    def route_table_rows6(self) -> list[dict]:
        return [r.as_dict() for r in sorted(
            self.routes6, key=lambda r: (r.prefix.network_address, r.prefix.prefixlen, r.ad)
        )]

    def nat_rows(self) -> list[dict]:
        return [
            {
                "proto": b.proto,
                "inside": f"{b.inside_ip}:{b.inside_key}",
                "outside": f"{b.outside_ip}:{b.outside_key}",
            }
            for b in self._nat_bindings
        ]

    # ----- MPLS / VRF introspection -------------------------------------------------
    def mpls_forwarding_rows(self) -> list[dict]:
        rows = []
        for local, e in sorted(self.lfib.items()):
            rows.append({
                "local_label": local,
                "prefix": e.prefix,
                "action": e.action,
                "out_label": e.out_label if e.action == "swap" else None,
                "next_hop": str(e.nh_ip) if e.nh_ip else None,
                "out_iface": e.out_iface,
            })
        return rows

    def sr_adj_rows(self) -> list[dict]:
        return [
            {"label": lbl, "peer": e.peer_router_id, "out_iface": e.out_iface}
            for lbl, e in sorted(self.sr_adj.items())
        ]

    def vrf_names(self) -> list[str]:
        return sorted(self.vrfs)

    def vrf_route_rows(self, name: str) -> list[dict]:
        vrf = self.vrfs.get(name)
        return vrf.route_rows() if vrf else []


class Firewall(Router):
    """A router whose forwarding defaults to deny unless ACLs permit.

    Same pipeline as :class:`Router`; the distinction is the default posture:
    interfaces without an explicit ingress ACL use ``default_policy``.
    """

    kind = "firewall"

    def __init__(self, name: str, node_id: str | None = None, nos: str = "forgeos") -> None:
        super().__init__(name, node_id, nos)
        self.default_policy = "permit"   # operators flip to "deny" for strictness

    def _acl_permits(self, rules: list[AclRule] | None, pkt: Ipv4Packet) -> bool:
        if not rules:
            return self.default_policy == "permit"
        return super()._acl_permits(rules, pkt)
