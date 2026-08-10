"""MPLS LDP-lite + basic L3VPN (NG-SIM-08).

Two processes attach to a router:

- :class:`LdpProcess` (``mpls: true``) — label distribution. Each MPLS router
  allocates a deterministic local label per FEC (every prefix in its global
  RIB) and floods its prefix->label bindings to directly-connected LDP peers.
  From those bindings plus the RIB it builds the LFIB (``router.lfib``) and a
  FEC push map (``router.mpls_fec``): swap the top label toward the next LSR,
  pop our own label at the LSP egress. The data plane lives in
  :meth:`Router._mpls_forward` / :meth:`Router._mpls_encap`.

- :class:`L3vpnProcess` (``vrf`` intent) — per-interface VRFs with an RD and
  import/export route-targets, and a per-VRF VPN label. It moves the VRF
  interfaces' connected routes into the isolated VRF RIB and exchanges VPNv4
  routes with the PE's iBGP peers, so a CE at one site reaches a CE at another
  site in the same VRF (and only the same VRF).

Deliberate simplifications (ponytail — each names its ceiling + upgrade path):

- ``# ponytail:`` LDP runs over raw-Ethernet flooded bindings, no UDP/TCP 646
  session, no keepalives. A lossless DES delivers them reliably; add a real
  session + label-withdraw when links can drop control PDUs.
- ``# ponytail:`` ultimate-hop-popping only (the egress LSR pops its own label);
  no penultimate-hop-popping / implicit-null. Upgrade = advertise label 3 for
  connected FECs and pop one hop earlier.
- ``# ponytail:`` VPNv4 NLRI ride a side-channel VpnUpdate on TCP:179, sourced
  from the loopback to each iBGP peer loopback (peer set reused from the
  sibling ``BgpProcess`` config). We don't ride BGP's live session because the
  stock speaker has no update-source-loopback. Upgrade = MP-BGP AFI/SAFI 1/128
  UPDATEs over the real session once loopback peering lands.
- ``# ponytail:`` a VpnUpdate is a full snapshot from a single remote PE per
  VRF (withdraw-then-reinstall). Upgrade = a per-peer Adj-RIB-In keyed by the
  sending PE, like BGP's ``rib_in``, for >2-PE meshes.
"""
from __future__ import annotations

import logging
from ipaddress import IPv4Address, IPv4Network
from typing import TYPE_CHECKING

from engine.events import EventType, SimEvent
from engine.netstack.frames import (
    ALL_LDP_MAC,
    ETH_LDP,
    PROTO_TCP,
    EthernetFrame,
    Ipv4Packet,
    LdpBinding,
    TcpSegment,
    VpnRoute,
    VpnUpdate,
)
from engine.netstack.iface import Interface
from engine.netstack.routing import LfibEntry, Route, Router, Vrf

if TYPE_CHECKING:  # pragma: no cover
    from engine.netstack.network import Network

logger = logging.getLogger(__name__)


def _schedule(net: "Network", after: float, node_id: str, fn) -> None:
    net.scheduler.schedule_after(
        after,
        SimEvent(time=0.0, type=EventType.TIMER, handler=lambda _c, _e: fn(), node_id=node_id),
    )


class LdpProcess:
    """LDP-lite label distribution attached to a Router."""

    proto = "ldp"

    def __init__(self, router: Router, label_base: int = 16, interval: float = 5.0) -> None:
        self.router = router
        self.interval = interval
        self.router_id = str(max((i.ip for i in router.all_ips()), default="0.0.0.0"))
        self._next = label_base
        self.local: dict[IPv4Network, int] = {}                  # FEC -> our label
        self.remote: dict[IPv4Address, dict[IPv4Network, int]] = {}  # nh -> its labels
        self.adj: dict[IPv4Address, tuple[str, str]] = {}        # nh -> (mac, iface)
        self._started = False
        router.mpls_enabled = True
        router.processes.append(self)

    def _local_label(self, prefix: IPv4Network) -> int:
        lbl = self.local.get(prefix)
        if lbl is None:
            lbl = self._next
            self._next += 1
            self.local[prefix] = lbl
        return lbl

    def _fecs(self) -> list[IPv4Network]:
        """Every prefix in the global RIB is a labelled FEC — deterministic
        order so labels are stable across a rebuild/replay."""
        return sorted(
            {r.prefix for r in self.router.routes},
            key=lambda n: (n.network_address, n.prefixlen),
        )

    # ----- lifecycle ---------------------------------------------------------
    def start(self, net: "Network") -> None:
        if self._started:
            return
        self._started = True
        self._tick(net)

    def _tick(self, net: "Network") -> None:
        # ponytail: keep the loop alive across power-cycles (F36/F47) — gate
        # the work, not the reschedule, so a power-on self-heals within one
        # interval instead of needing an explicit restart.
        if self.router.powered_on:
            self._advertise(net)
            self._rebuild()
        _schedule(net, self.interval, self.router.node_id, lambda: self._tick(net))

    def _advertise(self, net: "Network") -> None:
        binds = {str(p): self._local_label(p) for p in self._fecs()}
        for name, iface in self.router.interfaces.items():
            if not iface.is_up or not iface.ip or name in self.router.iface_vrf:
                continue
            iface.transmit(
                net,
                EthernetFrame(
                    src_mac=iface.mac,
                    dst_mac=ALL_LDP_MAC,
                    ethertype=ETH_LDP,
                    payload=LdpBinding(
                        router_id=self.router_id,
                        src_ip=str(iface.ip.ip),
                        bindings=dict(binds),
                    ),
                ),
            )

    def on_frame(self, net: "Network", iface: Interface, frame: EthernetFrame) -> None:
        pdu = frame.payload
        if not isinstance(pdu, LdpBinding):
            return
        nh = IPv4Address(pdu.src_ip)
        self.adj[nh] = (frame.src_mac, iface.name)
        self.remote[nh] = {IPv4Network(k): v for k, v in pdu.bindings.items()}
        self._rebuild()

    # ----- LFIB / FEC push table ---------------------------------------------
    def _rebuild(self) -> None:
        # Preserve labels owned by other processes (e.g. SR node-SIDs, NG-SIM-09)
        # — LDP only manages the labels it allocated (``self.local``), so a full
        # rebuild must not wipe a sibling's disjoint-range entries.
        mine = set(self.local.values())
        lfib: dict[int, LfibEntry] = {
            k: v for k, v in self.router.lfib.items() if k not in mine
        }
        fec: dict[IPv4Network, LfibEntry] = {}
        for r in self.router.routes:
            p = r.prefix
            if r.source == "connected":
                # We are the LSP egress for our own connected FEC: pop.
                lfib[self._local_label(p)] = LfibEntry(str(p), "pop", None, None, None, None)
                continue
            nh = r.next_hop
            if nh is None:
                continue
            out_label = self.remote.get(nh, {}).get(p)
            adj = self.adj.get(nh)
            if out_label is None or adj is None:
                continue
            mac, ifn = adj
            entry = LfibEntry(str(p), "swap", out_label, nh, mac, ifn)
            lfib[self._local_label(p)] = entry
            fec[p] = entry
        self.router.lfib = lfib
        self.router.mpls_fec = fec

    def forwarding_rows(self) -> list[dict]:
        return self.router.mpls_forwarding_rows()


class L3vpnProcess:
    """Per-interface VRFs + VPNv4 exchange over the PE's iBGP session."""

    proto = "l3vpn"

    def __init__(self, router: Router, vpn_label_base: int = 1000, interval: float = 5.0) -> None:
        self.router = router
        self.interval = interval
        self._next = vpn_label_base
        self._started = False
        self._sent: dict[IPv4Address, tuple] = {}   # peer -> last snapshot sent
        router.processes.append(self)

    # ----- configuration -----------------------------------------------------
    def add_vrf(self, name: str, rd: str, rt_import, rt_export) -> Vrf:
        vrf = Vrf(
            name=name,
            rd=rd,
            rt_import=frozenset(str(x) for x in rt_import),
            rt_export=frozenset(str(x) for x in rt_export),
            vpn_label=self._next,
        )
        self._next += 1
        self.router.vrfs[name] = vrf
        self.router.vpn_labels[vrf.vpn_label] = name
        return vrf

    def bind_iface(self, iface_name: str, vrf_name: str) -> None:
        self.router.iface_vrf[iface_name] = vrf_name

    # ----- lifecycle ---------------------------------------------------------
    def start(self, net: "Network") -> None:
        if self._started:
            return
        self._started = True
        # Move each VRF interface's connected route out of the global RIB and
        # into its VRF RIB — this is what keeps the VRFs (and the global table)
        # mutually isolated.
        for ifn, vname in self.router.iface_vrf.items():
            iface = self.router.interfaces.get(ifn)
            vrf = self.router.vrfs.get(vname)
            if iface is None or vrf is None:
                continue
            for ip in iface.ips:
                self.router.routes = [
                    r for r in self.router.routes
                    if not (r.prefix == ip.network and r.source == "connected")
                ]
                vrf.install(Route(prefix=ip.network, next_hop=None, iface_name=ifn, source="connected"))
        self._tick(net)

    def _tick(self, net: "Network") -> None:
        # ponytail: same self-healing shape as the LDP tick above.
        if self.router.powered_on:
            self._advertise(net)
        _schedule(net, self.interval, self.router.node_id, lambda: self._tick(net))

    def _bgp(self):
        for p in self.router.processes:
            if getattr(p, "proto", "") == "bgp":
                return p
        return None

    def _local_routes(self) -> list[VpnRoute]:
        bgp = self._bgp()
        my_nh = str(bgp.router_id) if bgp else str(
            max((i.ip for i in self.router.all_ips()), default="0.0.0.0")
        )
        out: list[VpnRoute] = []
        for vrf in self.router.vrfs.values():
            rt = tuple(sorted(vrf.rt_export))
            for r in vrf.routes:
                if r.source != "connected":
                    continue
                out.append(VpnRoute(rd=vrf.rd, prefix=str(r.prefix), rt=rt,
                                    next_hop=my_nh, label=vrf.vpn_label))
        return out

    def _advertise(self, net: "Network") -> None:
        bgp = self._bgp()
        if bgp is None:
            return
        routes = self._local_routes()
        snap = tuple((r.rd, r.prefix, r.rt, r.next_hop, r.label) for r in routes)
        for ip, peer in bgp.peers.items():
            if peer.remote_asn != bgp.asn:
                continue  # VPNv4 exchange follows the iBGP peer topology
            # ponytail: sourced from our loopback to the peer loopback — the
            # stock BgpProcess has no update-source-loopback, so we can't ride
            # its live session; we reuse its *peer config* and send when the
            # loopback is reachable. Upgrade = MP-BGP over the real session.
            if self.router.egress_for(ip) is None:
                continue  # loopback not yet reachable (IGP still converging)
            if self._sent.get(ip) == snap:
                continue
            self._sent[ip] = snap
            self.router.send_ip(
                net,
                Ipv4Packet(
                    src=IPv4Address(str(bgp.router_id)),
                    dst=ip,
                    proto=PROTO_TCP,
                    ttl=64,
                    dscp=48,
                    payload=TcpSegment(
                        src_port=179, dst_port=179, flags="PSH",
                        payload=VpnUpdate(routes=list(routes)),
                    ),
                ),
            )

    # ----- ingress -----------------------------------------------------------
    def on_packet(self, net: "Network", iface: Interface, pkt: Ipv4Packet) -> None:
        seg = pkt.payload
        if not isinstance(seg, TcpSegment) or not isinstance(seg.payload, VpnUpdate):
            return
        self._on_update(seg.payload)

    def _on_update(self, update: VpnUpdate) -> None:
        for vrf in self.router.vrfs.values():
            vrf.withdraw("vpnv4")   # full-snapshot replace (single remote PE/VRF)
            for vr in update.routes:
                if not (set(vr.rt) & vrf.rt_import):
                    continue        # route-target import gate
                vrf.install(Route(
                    prefix=IPv4Network(vr.prefix),
                    next_hop=IPv4Address(vr.next_hop),
                    iface_name=None,
                    source="vpnv4",
                    metric=1,
                    vpn_label=vr.label,
                ))
