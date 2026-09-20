"""EVPN/VXLAN — L2-over-L3 overlay (NG-SIM-10).

:class:`VxlanProcess` turns a router into a VTEP: it bridges tenant frames
arriving on VXLAN *access ports* into the overlay and back. One VTEP per node.

Control plane — EVPN over the existing BGP engine (side-channel, exactly like
``mpls.L3vpnProcess``'s VPNv4 exchange):

- **Type-2 (MAC/IP)**: each VTEP advertises the MACs it has learned on local
  access ports, so remote VTEPs install ``mac -> remote-VTEP`` and reach them
  by *unicast* VXLAN, no flooding.
- **Type-3 (IMET / ingress-replication)**: each VTEP advertises, per VNI, "send
  me the BUM traffic for this VNI". A VTEP with an unknown-unicast/broadcast/
  multicast tenant frame ingress-replicates one VXLAN copy to every remote VTEP
  that advertised Type-3 for that VNI.

Data plane — VXLAN encap/decap over UDP:4789 (``Router.on_frame`` intercepts
access-port frames; ``Router._local_deliver`` hands decapsulated overlay
packets back here). The underlay (loopback reachability) is plain routed IP.

Deliberate simplifications (ponytail — each names its ceiling + upgrade path):

- ``# ponytail:`` EVPN NLRI ride a side-channel EvpnUpdate on TCP:179, sourced
  from the loopback to each iBGP peer loopback (peer set reused from the
  sibling ``BgpProcess`` config) — the stock speaker has no MP-BGP AFI/SAFI
  25/70. Upgrade = real MP-BGP EVPN UPDATEs once loopback peering lands. Same
  seam L3vpn already took.
- ``# ponytail:`` remote MACs are learned *only* from Type-2, never from the
  decapsulated data plane. Unknown-unicast falls back to Type-3 BUM flooding,
  so correctness holds before Type-2 converges. Upgrade = data-plane MAC
  learning on decap if a scenario needs conversational learning without EVPN.
- ``# ponytail:`` one VTEP per node, keyed by the loopback /32 (like SR). No
  Type-5 IP-VRF, no anycast gateway (still NG-SIM-10 non-goals).
- ``# ponytail:`` ESI multi-homing (A4, RFC 7432 sec 5/7.1/8.2) tracks per-ES
  and per-EVI Ethernet A-D routes for mass withdrawal, but skips DF election
  (sec 8.5) and aliasing/load-balancing consumption (sec 8.4) — a remote PE
  still picks exactly one VTEP per learned MAC. Upgrade = DF election +
  multi-path forwarding when an active-active lab needs it.
"""
from __future__ import annotations

from ipaddress import IPv4Address
from typing import TYPE_CHECKING

from engine.events import EventType, SimEvent
from engine.netstack.frames import (
    PROTO_TCP,
    PROTO_UDP,
    VXLAN_UDP_PORT,
    EthernetFrame,
    EvpnRoute,
    EvpnUpdate,
    Ipv4Packet,
    TcpSegment,
    UdpSegment,
    VxlanPacket,
)
from engine.netstack.iface import Interface

if TYPE_CHECKING:  # pragma: no cover
    from engine.netstack.network import Network

# RFC 7432 sec 5: ESI 0 (all-zero, 10 octets) means "not multi-homed".
ESI_SINGLE_HOMED = "00:00:00:00:00:00:00:00:00:00"


def normalize_esi(value: str) -> str:
    """Validate/normalize a 10-octet Type-0 ESI, colon-hex like a MAC x2."""
    v = value.strip().lower().replace("-", ":")
    parts = v.split(":")
    hexdigits = set("0123456789abcdef")
    if len(parts) != 10 or not all(len(p) == 2 and set(p) <= hexdigits for p in parts):
        raise ValueError(f"invalid ESI, want 10 hex octets (e.g. a MAC twice): {value!r}")
    return v


def _schedule(net: Network, after: float, node_id: str, fn) -> None:
    net.scheduler.schedule_after(
        after,
        SimEvent(time=0.0, type=EventType.TIMER, handler=lambda _c, _e: fn(), node_id=node_id),
    )


class VxlanProcess:
    """A VTEP attached to a Router: VXLAN data plane + EVPN control plane."""

    proto = "vxlan"

    def __init__(self, router, interval: float = 5.0) -> None:
        self.router = router
        self.interval = interval
        self.vtep_ip: IPv4Address | None = None       # our loopback = VTEP IP
        self.access: dict[str, int] = {}              # access iface name -> VNI
        self.esi: dict[str, str] = {}                  # access iface name -> ESI (A4)
        # (vni, mac) -> location: a str = local access port, an IPv4Address =
        # remote VTEP (learned via Type-2). Absence = unknown -> BUM.
        self.mac_vni: dict[tuple[int, str], str | IPv4Address] = {}
        self.remote_vteps: dict[int, set[IPv4Address]] = {}   # vni -> remote VTEPs (Type-3)
        # (esi, vni) -> remote VTEPs advertising Ethernet A-D for it (Type-1);
        # vni 0 = per-ES (all EVIs, RFC 7432 sec 7.1), used for mass withdrawal.
        self.remote_ad: dict[tuple[str, int], set[IPv4Address]] = {}
        self._sent: dict[IPv4Address, tuple] = {}    # peer -> last snapshot sent
        self._started = False
        router.processes.append(self)

    # ----- configuration -----------------------------------------------------
    def bind_access(self, iface_name: str, vni: int, esi: str = ESI_SINGLE_HOMED) -> None:
        """``esi``: 10-octet colon-hex (see :func:`normalize_esi`); default
        single-homed. Two access ports (on different routers) sharing the same
        non-default ESI model one multi-homed CE (RFC 7432 sec 5)."""
        self.access[iface_name] = vni
        self.esi[iface_name] = normalize_esi(esi)
        self.router.vxlan_ports.add(iface_name)
        self.remote_vteps.setdefault(vni, set())

    def _port_up(self, iface_name: str) -> bool:
        iface = self.router.interfaces.get(iface_name)
        return iface is not None and iface.is_up

    def _live_vnis(self) -> set[int]:
        return {vni for name, vni in self.access.items() if self._port_up(name)}

    def _own_loopback_ip(self) -> IPv4Address:
        for iface in self.router.interfaces.values():
            for ip in iface.ips:
                if ip.network.prefixlen == 32:
                    return ip.ip
        return max((i.ip for i in self.router.all_ips()), default=IPv4Address("0.0.0.0"))

    # ----- lifecycle ---------------------------------------------------------
    def start(self, net: Network) -> None:
        if self._started:
            return
        self._started = True
        self.vtep_ip = self._own_loopback_ip()
        self._tick(net)

    def _tick(self, net: Network) -> None:
        # ponytail: keep the loop alive across power-cycles (F36/F47) — gate
        # the work, not the reschedule, so a power-on self-heals within one
        # interval instead of needing an explicit restart.
        if self.router.powered_on:
            self._advertise(net)
        _schedule(net, self.interval, self.router.node_id, lambda: self._tick(net))

    def _bgp(self):
        for p in self.router.processes:
            if getattr(p, "proto", "") == "bgp":
                return p
        return None

    def _local_routes(self) -> list[EvpnRoute]:
        # A down access port withdraws everything tied to it (its Type-3 IMET,
        # its Ethernet A-D, and its locally-learned Type-2 MACs) from the next
        # snapshot — that's the whole mass-withdrawal mechanism (sec 8.2): the
        # remote side's full-snapshot replace (_on_update) does the rest.
        vtep = str(self.vtep_ip)
        live_vnis = self._live_vnis()
        routes: list[EvpnRoute] = [
            EvpnRoute(route_type=3, vni=vni, vtep=vtep) for vni in sorted(live_vnis)
        ]
        for name, esi in sorted(self.esi.items()):
            if esi == ESI_SINGLE_HOMED or not self._port_up(name):
                continue
            routes.append(EvpnRoute(route_type=1, vni=0, vtep=vtep, esi=esi))          # per-ES
            routes.append(EvpnRoute(route_type=1, vni=self.access[name], vtep=vtep, esi=esi))  # per-EVI
        for (vni, mac), loc in sorted(self.mac_vni.items()):
            if isinstance(loc, str) and self._port_up(loc):   # locally-learned MAC -> Type-2
                routes.append(EvpnRoute(route_type=2, vni=vni, vtep=vtep, mac=mac))
        return routes

    def _advertise(self, net: Network) -> None:
        bgp = self._bgp()
        if bgp is None or self.vtep_ip is None:
            return
        routes = self._local_routes()
        snap = tuple((r.route_type, r.vni, r.mac) for r in routes)
        for ip, peer in bgp.peers.items():
            if peer.remote_asn != bgp.asn:
                continue  # EVPN exchange follows the iBGP peer topology
            # ponytail: loopback-sourced side-channel (see module docstring).
            if self.router.egress_for(ip) is None:
                continue  # loopback not reachable yet (IGP still converging)
            if self._sent.get(ip) == snap:
                continue
            self._sent[ip] = snap
            self.router.send_ip(
                net,
                Ipv4Packet(
                    src=self.vtep_ip,
                    dst=ip,
                    proto=PROTO_TCP,
                    ttl=64,
                    dscp=48,
                    payload=TcpSegment(
                        src_port=179, dst_port=179, flags="PSH",
                        payload=EvpnUpdate(routes=list(routes)),
                    ),
                ),
            )

    # ----- EVPN ingress ------------------------------------------------------
    def on_packet(self, net: Network, iface: Interface, pkt: Ipv4Packet) -> None:
        seg = pkt.payload
        if not isinstance(seg, TcpSegment) or not isinstance(seg.payload, EvpnUpdate):
            return
        self._on_update(pkt.src, seg.payload)

    def _on_update(self, sender: IPv4Address, update: EvpnUpdate) -> None:
        # Full-snapshot replace keyed by the sending VTEP (the packet's IP
        # source — not reconstructed from the NLRI, so a *fully withdrawn*
        # sender with an empty route list still clears its old state; that's
        # what makes mass withdrawal (sec 8.2) a single atomic step here).
        if sender == self.vtep_ip:
            return
        for vteps in self.remote_vteps.values():
            vteps.discard(sender)
        for vteps in self.remote_ad.values():
            vteps.discard(sender)
        self.mac_vni = {
            k: v for k, v in self.mac_vni.items()
            if not (isinstance(v, IPv4Address) and v == sender)
        }
        for r in update.routes:
            if r.route_type == 3:
                self.remote_vteps.setdefault(r.vni, set()).add(sender)
            elif r.route_type == 2 and r.mac:
                self.mac_vni[(r.vni, r.mac)] = sender
            elif r.route_type == 1 and r.esi:
                self.remote_ad.setdefault((r.esi, r.vni), set()).add(sender)

    # ----- data plane: access -> overlay -------------------------------------
    def on_access_frame(self, net: Network, iface: Interface, frame: EthernetFrame) -> None:
        vni = self.access.get(iface.name)
        if vni is None:
            return
        from engine.netstack.addr import MacAddr

        src = str(frame.src_mac)
        if not MacAddr(src).is_multicast:
            self.mac_vni[(vni, src)] = iface.name   # learn local MAC

        dst = str(frame.dst_mac)
        if not frame.is_broadcast and not MacAddr(dst).is_multicast:
            loc = self.mac_vni.get((vni, dst))
            if isinstance(loc, str):                 # known local: bridge locally
                out = self.router.interfaces.get(loc)
                if out is not None and out.name != iface.name:
                    out.transmit(net, frame)
                return
            if isinstance(loc, IPv4Address):         # known remote: unicast VXLAN
                self._encap(net, vni, loc, frame)
                return
        # BUM (broadcast/multicast/unknown-unicast): ingress-replicate.
        self._flood(net, vni, iface.name, frame)

    def _flood(self, net: Network, vni: int, in_port: str | None, frame: EthernetFrame) -> None:
        for name, avni in sorted(self.access.items()):
            if avni == vni and name != in_port:
                out = self.router.interfaces.get(name)
                if out is not None:
                    out.transmit(net, frame.clone())
        for vtep in sorted(self.remote_vteps.get(vni, ())):
            self._encap(net, vni, vtep, frame.clone())

    def _encap(self, net: Network, vni: int, vtep: IPv4Address, inner: EthernetFrame) -> None:
        if self.vtep_ip is None:
            return
        self.router.send_ip(
            net,
            Ipv4Packet(
                src=self.vtep_ip,
                dst=vtep,
                proto=PROTO_UDP,
                ttl=64,
                payload=UdpSegment(
                    src_port=_entropy_port(inner),
                    dst_port=VXLAN_UDP_PORT,
                    payload=VxlanPacket(vni=vni, inner=inner),
                ),
            ),
        )

    # ----- data plane: overlay -> access -------------------------------------
    def on_overlay(self, net: Network, pkt: Ipv4Packet, vx: VxlanPacket) -> None:
        inner = vx.inner
        if inner is None:
            return
        from engine.netstack.addr import MacAddr

        dst = str(inner.dst_mac)
        if not inner.is_broadcast and not MacAddr(dst).is_multicast:
            loc = self.mac_vni.get((vx.vni, dst))
            if isinstance(loc, str):
                out = self.router.interfaces.get(loc)
                if out is not None:
                    out.transmit(net, inner)
                return
        # Unknown-unicast/BUM: flood every local access port in this VNI.
        for name, avni in sorted(self.access.items()):
            if avni == vx.vni:
                out = self.router.interfaces.get(name)
                if out is not None:
                    out.transmit(net, inner.clone())

    # ----- introspection -----------------------------------------------------
    def mac_vni_rows(self) -> list[dict]:
        rows = []
        for (vni, mac), loc in sorted(self.mac_vni.items()):
            where = loc if isinstance(loc, str) else f"vtep {loc}"
            rows.append({"vni": vni, "mac": mac, "location": where})
        return rows

    def vtep_rows(self) -> list[dict]:
        rows = []
        for vni in sorted(self.remote_vteps):
            for vtep in sorted(self.remote_vteps[vni]):
                rows.append({"vni": vni, "remote_vtep": str(vtep)})
        return rows

    def evpn_rows(self) -> list[dict]:
        """The EVPN table: local + learned Type-1 (Ethernet A-D), Type-2
        (MAC) and Type-3 (IMET). ``esi`` is "-" for non-Type-1 rows; a
        Type-1 ``vni`` of "*" is the per-ES (all-EVI) route."""
        rows: list[dict] = []
        for vni in sorted(self._live_vnis()):
            rows.append({"type": 3, "vni": vni, "mac": "*",
                         "vtep": str(self.vtep_ip), "origin": "local", "esi": "-"})
        for name, esi in sorted(self.esi.items()):
            if esi == ESI_SINGLE_HOMED or not self._port_up(name):
                continue
            rows.append({"type": 1, "vni": "*", "mac": "-",
                         "vtep": str(self.vtep_ip), "origin": "local", "esi": esi})
            rows.append({"type": 1, "vni": self.access[name], "mac": "-",
                         "vtep": str(self.vtep_ip), "origin": "local", "esi": esi})
        for (vni, mac), loc in sorted(self.mac_vni.items()):
            local = isinstance(loc, str)
            rows.append({
                "type": 2, "vni": vni, "mac": mac,
                "vtep": str(self.vtep_ip) if local else str(loc),
                "origin": "local" if local else "remote", "esi": "-",
            })
        for vni in sorted(self.remote_vteps):
            for vtep in sorted(self.remote_vteps[vni]):
                rows.append({"type": 3, "vni": vni, "mac": "*",
                             "vtep": str(vtep), "origin": "remote", "esi": "-"})
        for esi, vni in sorted(self.remote_ad):
            for vtep in sorted(self.remote_ad[(esi, vni)]):
                rows.append({"type": 1, "vni": vni or "*", "mac": "-",
                             "vtep": str(vtep), "origin": "remote", "esi": esi})
        return rows


def _entropy_port(frame: EthernetFrame) -> int:
    """Deterministic UDP source port from the inner flow (real VXLAN hashes it
    for ECMP entropy). Stable across replay — no builtin ``hash`` (salted)."""
    octets = f"{frame.src_mac}:{frame.dst_mac}".split(":")
    total = sum(int(o, 16) for o in octets if o)
    return 49152 + (total & 0x3FFF)
