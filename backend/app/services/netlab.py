"""NetLab service — live packet-level labs built from stored topologies.

The :class:`LabManager` keeps at most one built :class:`engine.netstack.Network`
per project, fingerprinted against the topology, so consoles, ping/traceroute
and capture inspection all operate on the *same* living lab. Any topology
change (node/link/intent edit) invalidates the lab; the next request rebuilds
and re-converges it.

Node ``intent`` fields understood by the builder (all optional):

    gateway: "192.168.1.1"                 # hosts: default gateway
    gateway6: "2001:db8::1"                # hosts: IPv6 default gateway
    slaac: true                            # hosts: autoconfigure v6 from RA
    dns: "192.168.1.1"                     # hosts: resolver address
    vlans: {"<iface-name>": {"mode": "access"|"trunk", "vlan": 10}}
    static_routes: [{"prefix": "0.0.0.0/0", "next_hop": "10.0.0.1"}]
    static_routes6: [{"prefix": "::/0", "next_hop": "2001:db8::1",
                      "iface": null}]
    ipv6_ra: {"enabled": true, "interval": 30}   # routers: advertise /64s
    vrrp: [{"iface": "eth0", "vrid": 10, "vip": "192.168.1.254",
            "priority": 120, "adv_interval": 1.0, "preempt": true}]
    lag: [{"name": "po1", "members": ["eth1", "eth2"], "mode": "lacp"}]
    ospf: {"enabled": true, "router_id": "1.1.1.1", "hello": 10,
           "areas": {"eth0": 0, "eth1": 1}, "default_originate": false}
    isis: {"enabled": true, "system_id": "1921.6800.1001", "level": 2,
           "hello": 10, "interfaces": ["eth0", "eth1"]}
           # "interfaces" may instead be {"eth0": 20, "eth1": 10} for per-iface
           # metrics; a bare list uses the default metric (10). L2 single-area.
    bgp: {"asn": 65001, "router_id": "1.1.1.1",
          "neighbors": [{"ip": "10.0.0.2", "asn": 65002}],
          "networks": ["198.51.100.0/24"]}
    mpls: true                             # routers: run LDP-lite label dist.
          # or {"enabled": true, "label_base": 100} for a distinct label space
    vrf: {"<iface-name>": {"name": "red", "rd": "65000:1",
                           "rt_import": ["100:1"], "rt_export": ["100:1"]}}
          # routers: L3VPN VRFs bound to interfaces; VPNv4 rides the iBGP peers
    vxlan: {"access": {"<iface-name>": 10}}       # routers: VTEP; access port
          # -> VNI bindings. EVPN Type-2/Type-3 ride the iBGP peers (TCP:179);
          # loopback = VTEP IP; underlay reachability via OSPF/IS-IS/BGP.
    dhcp_server: {"pools": [{"network": "192.168.88.0/24",
                             "gateway": "192.168.88.1", "dns": "..."}]}
    dns_zone: {"nas.lab": "192.168.1.40"}
    nat: {"inside": ["eth0"], "outside": "eth1"}
    acl: {"<iface-name>": {"in": [{"action": "deny", "proto": "icmp",
                                   "src": "10.0.0.0/8", "dst": null,
                                   "dst_port": null}], "out": [...]}}
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from functools import lru_cache
from ipaddress import IPv4Address, IPv4Network, IPv6Address, IPv6Network

from fastapi.concurrency import run_in_threadpool

from app.models import Topology
from app.services.events import get_bus
from engine.netstack import Network
from engine.netstack.cli import CliSession
from engine.netstack.device import Device, Host
from engine.netstack.protocols.bgp import BgpProcess
from engine.netstack.protocols.isis import IsisProcess
from engine.netstack.protocols.mpls import L3vpnProcess, LdpProcess
from engine.netstack.protocols.ospf import OspfProcess
from engine.netstack.protocols.vrrp import VrrpProcess
from engine.netstack.protocols.vxlan import VxlanProcess
from engine.netstack.qos import QosConfig
from engine.netstack.routing import AclRule, DhcpPool, Firewall, Router
from engine.netstack.switching import Switch

logger = logging.getLogger("netgeo.netlab")

_MBPS = 1_000_000

# Node kinds that behave as an L2 bridge in the lab.
_L2_KINDS = {"switch", "ap", "olt", "cloud"}
_HOST_KINDS = {"host", "server"}


def _fingerprint(topo: Topology) -> str:
    blob = topo.model_dump_json()
    return hashlib.sha256(blob.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------

def build_network(topo: Topology, seed: int = 0) -> Network:
    """Construct a runnable lab from a stored topology."""
    net = Network(seed=seed)
    iface_owner: dict[str, tuple[str, str]] = {}  # iface id -> (node id, iface name)

    for n in topo.nodes:
        kind = str(n.kind)
        if kind in _L2_KINDS:
            dev: Device = Switch(n.name, node_id=n.id, nos=str(n.nos))
        elif kind == "firewall":
            dev = Firewall(n.name, node_id=n.id, nos=str(n.nos))
        elif kind in _HOST_KINDS:
            dev = Host(n.name, node_id=n.id, nos=str(n.nos))
        else:  # router and anything router-like
            dev = Router(n.name, node_id=n.id, nos=str(n.nos))
        net.add_device(dev)

        intent = n.intent or {}
        vlans = intent.get("vlans") or {}
        for i in n.interfaces:
            iface = net.add_iface(dev, i.name, list(i.ip))
            if i.mac:
                try:
                    from engine.netstack.addr import MacAddr

                    iface.mac = MacAddr(i.mac)
                except ValueError:
                    pass
            vcfg = vlans.get(i.name)
            if isinstance(vcfg, dict):
                iface.vlan_mode = str(vcfg.get("mode", "access"))
                if "vlan" in vcfg:
                    iface.access_vlan = int(vcfg["vlan"])
                if isinstance(vcfg.get("allowed"), list):
                    iface.trunk_vlans = {int(v) for v in vcfg["allowed"]}
            iface_owner[i.id] = (n.id, i.name)

        _apply_intent(net, dev, intent)

    for l in topo.links:
        a = _resolve_iface(net, iface_owner, l.a_iface)
        b = _resolve_iface(net, iface_owner, l.b_iface)
        if a is None or b is None:
            logger.warning("link %s references unresolvable endpoint(s); skipped", l.id)
            continue
        net.connect(
            l.id,
            a,
            b,
            bandwidth_bps=float(l.bandwidth) * _MBPS,
            delay=l.delay / 1000.0,
            loss=l.loss,
            mtu=l.mtu,
            up=str(l.status) == "up",
            kind=str(l.type),
        )
    return net


def _resolve_iface(net: Network, owner: dict, ref: str):
    """A link endpoint may be an interface id or (frontend fallback) a node
    id/name — in that case auto-provision the next ethN port."""
    hit = owner.get(ref)
    if hit is not None:
        node_id, iface_name = hit
        dev = net.devices_by_id.get(node_id)
        return dev.interfaces.get(iface_name) if dev else None
    dev = net.find_device(ref)
    if dev is None:
        return None
    n = 0
    while f"eth{n}" in dev.interfaces:
        n += 1
    return net.add_iface(dev, f"eth{n}")


def _apply_intent(net: Network, dev: Device, intent: dict) -> None:
    # Link aggregation first — any device kind can bundle ports (NG-SIM-04).
    for lag in intent.get("lag") or []:
        try:
            dev.create_lag(
                str(lag["name"]),
                [str(m) for m in lag.get("members") or []],
                mode=str(lag.get("mode", "lacp")),
            )
        except (KeyError, ValueError) as exc:
            logger.warning("%s: bad lag config %r: %s", dev.name, lag, exc)

    if isinstance(dev, Host):
        gw = intent.get("gateway")
        if gw:
            dev.default_gateway = IPv4Address(gw)
        gw6 = intent.get("gateway6")
        if gw6:
            dev.default_gateway6 = IPv6Address(gw6)
        if intent.get("slaac"):
            for iface in dev.interfaces.values():
                iface.slaac = True
        dns = intent.get("dns")
        if dns:
            dev.dns_server = IPv4Address(dns)
        return

    if not isinstance(dev, Router):
        return

    for sr in intent.get("static_routes") or []:
        try:
            dev.add_static_route(sr["prefix"], sr["next_hop"])
        except (KeyError, ValueError) as exc:
            logger.warning("%s: bad static route %r: %s", dev.name, sr, exc)

    for sr in intent.get("static_routes6") or []:
        try:
            dev.add_static_route6(sr["prefix"], sr["next_hop"], sr.get("iface"))
        except (KeyError, ValueError) as exc:
            logger.warning("%s: bad static v6 route %r: %s", dev.name, sr, exc)

    ra_cfg = intent.get("ipv6_ra") or {}
    if ra_cfg.get("enabled"):
        dev.enable_ra(interval=float(ra_cfg.get("interval", 30.0)))

    for v in intent.get("vrrp") or []:
        try:
            VrrpProcess(
                dev,
                iface_name=str(v["iface"]),
                vrid=int(v["vrid"]),
                vip=IPv4Address(v["vip"]),
                priority=int(v.get("priority", 100)),
                adv_interval=float(v.get("adv_interval", 1.0)),
                preempt=bool(v.get("preempt", True)),
            )
        except (KeyError, ValueError) as exc:
            logger.warning("%s: bad vrrp config %r: %s", dev.name, v, exc)

    ospf_cfg = intent.get("ospf") or {}
    if ospf_cfg.get("enabled"):
        OspfProcess(
            dev,
            router_id=ospf_cfg.get("router_id"),
            hello_interval=float(ospf_cfg.get("hello", 10.0)),
            areas={
                str(k): int(v) for k, v in (ospf_cfg.get("areas") or {}).items()
            },
            default_originate=bool(ospf_cfg.get("default_originate", False)),
        )

    isis_cfg = intent.get("isis") or {}
    if isis_cfg.get("enabled"):
        ifaces_cfg = isis_cfg.get("interfaces")
        ifaces: list[str] | None = None
        metrics: dict[str, int] = {}
        if isinstance(ifaces_cfg, dict):
            ifaces = [str(k) for k in ifaces_cfg]
            metrics = {str(k): int(v) for k, v in ifaces_cfg.items()}
        elif isinstance(ifaces_cfg, list):
            ifaces = [str(i) for i in ifaces_cfg]
        IsisProcess(
            dev,
            system_id=isis_cfg.get("system_id"),
            level=int(isis_cfg.get("level", 2)),
            hello_interval=float(isis_cfg.get("hello", 10.0)),
            ifaces=ifaces,
            metrics=metrics,
        )

    bgp_cfg = intent.get("bgp") or {}
    if bgp_cfg.get("asn"):
        proc = BgpProcess(
            dev,
            asn=int(bgp_cfg["asn"]),
            router_id=bgp_cfg.get("router_id"),
            keepalive_interval=float(bgp_cfg.get("keepalive", 30.0)),
        )
        for nb in bgp_cfg.get("neighbors") or []:
            try:
                proc.add_neighbor(
                    nb["ip"],
                    int(nb["asn"]),
                    rr_client=bool(nb.get("rr_client", False)),
                    prefix_list_in=nb.get("prefix_list_in"),
                    prefix_list_out=nb.get("prefix_list_out"),
                )
            except (KeyError, ValueError) as exc:
                logger.warning("%s: bad bgp neighbor %r: %s", dev.name, nb, exc)
        # networks: "10.0.0.0/24" or {"prefix": ..., "communities": [...]}
        for entry in bgp_cfg.get("networks") or []:
            try:
                if isinstance(entry, dict):
                    proc.advertise_network(
                        entry["prefix"], entry.get("communities") or ()
                    )
                else:
                    proc.advertise_network(entry)
            except (KeyError, ValueError) as exc:
                logger.warning("%s: bad bgp network %r: %s", dev.name, entry, exc)

    # MPLS LDP-lite + L3VPN (NG-SIM-08).
    mpls_cfg = intent.get("mpls")
    if mpls_cfg:
        cfg = mpls_cfg if isinstance(mpls_cfg, dict) else {}
        if cfg.get("enabled", True):
            LdpProcess(dev, label_base=int(cfg.get("label_base", 16)))

    vrf_cfg = intent.get("vrf") or {}
    if vrf_cfg:
        l3vpn = L3vpnProcess(dev, vpn_label_base=int(intent.get("vpn_label_base", 1000)))
        seen: dict[str, None] = {}
        # Deterministic VPN-label order: VRFs added sorted by name.
        for iface_name, vc in sorted(vrf_cfg.items(), key=lambda kv: str(kv[1].get("name", kv[0]))):
            try:
                name = str(vc["name"])
                if name not in seen:
                    l3vpn.add_vrf(
                        name,
                        rd=str(vc.get("rd", "")),
                        rt_import=vc.get("rt_import") or [],
                        rt_export=vc.get("rt_export") or [],
                    )
                    seen[name] = None
                l3vpn.bind_iface(str(iface_name), name)
            except (KeyError, ValueError) as exc:
                logger.warning("%s: bad vrf config %r: %s", dev.name, vc, exc)

    # EVPN/VXLAN VTEP (NG-SIM-10): one VTEP per node; EVPN rides the iBGP peers.
    vxlan_cfg = intent.get("vxlan") or {}
    access = vxlan_cfg.get("access") or {}
    if access:
        vx = VxlanProcess(dev)
        for iface_name, vni in sorted(access.items()):
            try:
                vx.bind_access(str(iface_name), int(vni))
            except (KeyError, ValueError) as exc:
                logger.warning("%s: bad vxlan access %r: %s", dev.name, iface_name, exc)

    dhcp_cfg = intent.get("dhcp_server") or {}
    for pool in dhcp_cfg.get("pools") or []:
        try:
            dev.add_dhcp_pool(
                DhcpPool(
                    network=IPv4Network(pool["network"]),
                    gateway=IPv4Address(pool["gateway"]),
                    dns=IPv4Address(pool["dns"]) if pool.get("dns") else None,
                )
            )
        except (KeyError, ValueError) as exc:
            logger.warning("%s: bad dhcp pool %r: %s", dev.name, pool, exc)

    for qname, addr in (intent.get("dns_zone") or {}).items():
        try:
            dev.dns_zone[str(qname).lower()] = IPv4Address(addr)
        except ValueError:
            pass

    nat_cfg = intent.get("nat") or {}
    if nat_cfg.get("outside"):
        port_forwards = []
        for pf in nat_cfg.get("port_forwards") or []:
            try:
                port_forwards.append((
                    str(pf["proto"]),
                    int(pf["outside_port"]),
                    str(pf["inside_ip"]),
                    int(pf["inside_port"]),
                ))
            except (KeyError, ValueError) as exc:
                logger.warning("%s: bad nat port_forward %r: %s", dev.name, pf, exc)
        dev.enable_nat(
            list(nat_cfg.get("inside") or []), str(nat_cfg["outside"]), port_forwards
        )

    for iface_name, directions in (intent.get("acl") or {}).items():
        for direction in ("in", "out"):
            rules = []
            for r in (directions or {}).get(direction) or []:
                rules.append(
                    AclRule(
                        action=str(r.get("action", "permit")),
                        proto=r.get("proto"),
                        src=IPv4Network(r["src"]) if r.get("src") else None,
                        dst=IPv4Network(r["dst"]) if r.get("dst") else None,
                        dst_port=int(r["dst_port"]) if r.get("dst_port") else None,
                    )
                )
            if rules:
                target = dev.acl_in if direction == "in" else dev.acl_out
                target[iface_name] = rules

    # QoS marking rules (NG-SIM-11 §2.3): stored on the router for the forward path.
    qos_marking = intent.get("qos_marking")
    if qos_marking and isinstance(dev, Router):
        dev.qos_marking = list(qos_marking)


def _settle_time(net: Network) -> float:
    """Sim-seconds to run at build so protocols converge before first use."""
    has_bgp = has_ospf = has_vrrp = has_isis = has_mpls = has_evpn = False
    for dev in net.devices.values():
        for proc in getattr(dev, "processes", []):
            proto = getattr(proc, "proto", "")
            has_bgp |= proto == "bgp"
            has_ospf |= proto == "ospf"
            has_vrrp |= proto == "vrrp"
            has_isis |= proto == "isis"
            has_mpls |= proto in ("ldp", "l3vpn")
            has_evpn |= proto == "vxlan"
    if has_bgp or has_ospf or has_isis or has_mpls or has_evpn:
        return 65.0
    if any(isinstance(d, Switch) for d in net.devices.values()):
        return 12.0
    if has_vrrp:
        return 6.0  # master-down interval (~4s) + first adverts
    return 1.0


# ---------------------------------------------------------------------------
# Lab manager
# ---------------------------------------------------------------------------

@dataclass
class Lab:
    """A living lab plus its **stimulus journal** (NG-SIM-01).

    Every operation that can advance or perturb the simulation goes through a
    ``do_*`` method, which appends a journal entry *before* executing it. The
    journal + the deterministic engine make time travel cheap: seeking to
    event N = rebuild the network and re-apply the journal with the
    scheduler's dispatch cap set to N (topology edits invalidate the lab
    entirely, so the journal only ever describes one topology).

    ``mode`` is Packet Tracer parity: ``realtime`` auto-runs the scheduler
    after each stimulus; ``simulation`` leaves events queued for step/seek.
    """

    project_id: str
    fingerprint: str
    net: Network
    seed: int = 0
    mode: str = "realtime"          # realtime | simulation
    journal: list[dict] = field(default_factory=list)
    sessions: dict[str, CliSession] = field(default_factory=dict)
    _recording: bool = True

    def session_for(self, node_ref: str) -> CliSession | None:
        dev = self.net.find_device(node_ref)
        if dev is None:
            return None
        sess = self.sessions.get(dev.node_id)
        if sess is None:
            sess = CliSession(self.net, dev)
            self.sessions[dev.node_id] = sess
        return sess

    # ----- journal -----------------------------------------------------------
    def _record(self, kind: str, **args) -> None:
        if self._recording:
            self.journal.append(
                {"kind": kind, "seq_before": self.net.ledger.seq, "args": args}
            )

    # ----- journaled operations ----------------------------------------------
    def do_run(self, until: float | None = None, max_events: int | None = 500_000) -> int:
        self._record("run", until=until, max_events=max_events)
        return self.net.run(until=until, max_events=max_events)

    def do_mode(self, mode: str) -> None:
        self._record("mode", mode=mode)
        self.mode = mode
        self.net.auto_run = mode == "realtime"

    def do_ping(self, src: str, dst: str, count: int):
        self._record("ping", src=src, dst=dst, count=count)
        report = self.net.ping(src, dst, count=count, run_after=False)
        if self.mode == "realtime":
            self.do_run(until=self.net.now + count * 1.0 + 5.0)
        return report

    def do_traceroute(self, src: str, dst: str, max_hops: int):
        self._record("traceroute", src=src, dst=dst, max_hops=max_hops)
        report = self.net.traceroute(src, dst, max_hops=max_hops, run_after=False)
        if self.mode == "realtime":
            self.do_run(until=self.net.now + max_hops * 0.2 + 5.0)
            self._trim_traceroute(report)
        return report

    def _trim_traceroute(self, tr) -> None:
        reached_at = min(
            (ttl for ttl, (addr, _r) in tr.hops.items() if addr == tr.dst),
            default=None,
        )
        if reached_at is not None:
            tr.reached = True
            tr.hops = {t: h for t, h in tr.hops.items() if t <= reached_at}

    def do_cli(self, node_ref: str, command: str) -> str | None:
        session = self.session_for(node_ref)
        if session is None:
            return None
        self._record("cli", node=node_ref, command=command)
        return session.execute(command)

    def do_set_link_qos(self, link_id: str, qos: dict) -> bool:
        """Journaled QoS config change on a link attachment (NG-SIM-11 §2.2).

        qos dict: {"enabled": bool, "ef_min_dscp": int, "af_min_dscp": int,
                   "depth_per_class": int}
        Returns True if the attachment was found and updated.
        """
        self._record("set_link_qos", link_id=link_id, qos=qos)
        return self._set_link_qos(link_id, qos)

    def _set_link_qos(self, link_id: str, qos: dict) -> bool:
        att = self.net.attachments.get(link_id)
        if att is None:
            return False
        att.qos = QosConfig(
            enabled=bool(qos.get("enabled", False)),
            ef_min_dscp=int(qos.get("ef_min_dscp", 40)),
            af_min_dscp=int(qos.get("af_min_dscp", 8)),
            depth_per_class=int(qos.get("depth_per_class", 32)),
        )
        return True

    def _apply(self, entry: dict) -> None:
        """Re-execute one journal entry during replay (no re-recording)."""
        kind, args = entry["kind"], entry["args"]
        if kind == "run":
            self.net.run(until=args["until"], max_events=args["max_events"])
        elif kind == "mode":
            self.mode = args["mode"]
            self.net.auto_run = args["mode"] == "realtime"
        elif kind == "ping":
            self.net.ping(args["src"], args["dst"], count=args["count"], run_after=False)
        elif kind == "traceroute":
            self.net.traceroute(
                args["src"], args["dst"], max_hops=args["max_hops"], run_after=False
            )
        elif kind == "cli":
            session = self.session_for(args["node"])
            if session is not None:
                session.execute(args["command"])
        elif kind == "set_link_qos":
            self._set_link_qos(args["link_id"], args["qos"])


class LabManager:
    """At most one live lab per project, invalidated by topology fingerprint.

    ``Lab``/``Network`` mutation is synchronous, non-thread-safe Python state
    (event scheduler heap, ledger seq counter, journal). The API layer runs
    every lab endpoint via ``run_in_threadpool`` — real OS worker threads, not
    coroutines — so two concurrent requests against the *same* project can
    otherwise interleave inside the engine and corrupt it (F62). ``lock()``
    hands out one :class:`threading.Lock` per project id (not a single global
    lock, so unrelated projects never block each other) for callers to hold
    for the duration of any Lab/Network access.
    """

    def __init__(self) -> None:
        self._labs: dict[str, Lab] = {}
        self._locks: defaultdict[str, threading.Lock] = defaultdict(threading.Lock)

    def lock(self, project_id: str) -> threading.Lock:
        """Per-project lock guarding Lab/Network mutation (F62).

        ``defaultdict.__getitem__`` creates-or-fetches the lock in one atomic
        C call, so this needs no extra guard lock under the GIL.
        """
        return self._locks[project_id]

    def call_locked(self, project_id: str, fn):
        """Run synchronous ``fn()`` while holding ``project_id``'s lock.

        Callers run this via ``run_in_threadpool`` (it does blocking engine
        work); the lock is a plain ``threading.Lock`` for that reason — it
        must be visible to whichever OS worker thread gets the call, not just
        the event loop, so ``asyncio.Lock`` would not do here.
        """
        with self.lock(project_id):
            return fn()

    def get(self, topo: Topology, seed: int = 0) -> Lab:
        pid = topo.project.id
        fp = _fingerprint(topo)
        lab = self._labs.get(pid)
        if lab is not None and lab.fingerprint == fp:
            return lab
        net = build_network(topo, seed=seed)
        net.start()
        lab = Lab(project_id=pid, fingerprint=fp, net=net, seed=seed)
        lab.do_run(until=net.now + _settle_time(net))
        self._labs[pid] = lab
        logger.info(
            "lab built for %s: %s devices, %s links, settled to t=%.1fs",
            pid,
            len(net.devices),
            len(net.attachments),
            net.now,
        )
        return lab

    def seek(self, topo: Topology, target_seq: int) -> Lab:
        """Move the lab's cursor to event ``target_seq`` (step-back included):
        rebuild, cap the scheduler at the target, replay the journal. Journal
        entries recorded after the cursor are discarded — new stimuli rewrite
        the future, exactly like stepping back in Packet Tracer."""
        lab = self.get(topo)
        target_seq = max(0, min(target_seq, lab.net.ledger.seq))
        net = build_network(topo, seed=lab.seed)
        net.start()
        net.scheduler.dispatch_cap = target_seq
        replayed = Lab(
            project_id=lab.project_id,
            fingerprint=lab.fingerprint,
            net=net,
            seed=lab.seed,
            _recording=False,
        )
        kept: list[dict] = []
        for entry in lab.journal:
            if entry["seq_before"] >= target_seq:
                break
            replayed._apply(entry)
            kept.append(entry)
        net.scheduler.dispatch_cap = None
        replayed.journal = kept
        replayed.mode = "simulation"     # stepping implies simulation mode
        replayed.net.auto_run = False
        replayed._recording = True
        self._labs[lab.project_id] = replayed
        return replayed

    def invalidate(self, project_id: str) -> None:
        self._labs.pop(project_id, None)

    def peek(self, project_id: str) -> Lab | None:
        return self._labs.get(project_id)


@lru_cache(maxsize=1)
def get_lab_manager() -> LabManager:
    return LabManager()


# ---------------------------------------------------------------------------
# Continuous lab runner (S3-a) — the netstack (Jalur B) equivalent of
# ``app.services.sim.SimManager``. Jalur A's realtime run drives an
# in-process ``Simulation`` scheduler; this drives the same living ``Lab``
# used by ping/traceroute/CLI, so continuous "play" and one-shot diagnostics
# never diverge. Pattern (gate for pause, batched dispatch, best-effort
# sim.tick publish) is copied from SimManager on purpose — do not invent a
# second concurrency style.
# ---------------------------------------------------------------------------

_LAB_TICK_BATCH = 128  # same batch size as SimManager._TICK_BATCH


def _lab_metrics(net: Network) -> dict[str, float]:
    """Metrics shaped like ``app.services.sim._metrics`` but sourced from real
    netstack counters (no invented numbers):

    - ``delivered``/``injected``: summed across every :class:`PingReport` the
      Lab has run (``net.pings``), i.e. real ICMP echo replies/requests
      accounted by ``Network.ping_reply_received``/``Network.ping``. This is
      netstack's only global per-packet delivery counter today — traffic that
      never rides a tracked ping (raw protocol chatter, CLI-only frames) is
      not reflected here.
    - ``dropped``: sum of ``Network.drops`` (populated by
      ``Network.record_drop``, e.g. ``mtu_exceeded`` — real, not guessed).
    - ``avg_latency_s``: mean of every ping's ``rtts_ms`` in seconds; ``0.0``
      when no ping has completed yet (absence of data, not a fabricated
      value).
    - ``pending_events``/``events_dispatched``: direct netstack scheduler
      counters, same meaning as Jalur A's.
    """
    reports = list(net.pings.values())
    rtts = [r for rep in reports for r in rep.rtts_ms]
    return {
        "delivered": sum(rep.received for rep in reports),
        "dropped": sum(net.drops.values()),
        "injected": sum(rep.sent for rep in reports),
        "pending_events": len(net.scheduler.queue),
        "events_dispatched": net.scheduler.dispatched,
        "avg_latency_s": round(sum(rtts) / len(rtts) / 1000.0, 6) if rtts else 0.0,
    }


@dataclass
class _LabRun:
    project_id: str
    lab: Lab
    state: str = "running"          # running|paused|stopped|completed|error
    stopped: bool = False
    task: asyncio.Task | None = None
    # gate is *set* while running, *cleared* while paused; the driver awaits it.
    gate: asyncio.Event = field(default_factory=asyncio.Event)

    def status(self) -> dict:
        return {
            "project_id": self.project_id,
            "state": self.state,
            "sim_time": round(self.lab.net.now, 6),
            "metrics": _lab_metrics(self.lab.net),
        }


class LabRunManager:
    """Owns the at-most-one live background run per project's Lab."""

    def __init__(self) -> None:
        self._runs: dict[str, _LabRun] = {}

    def _publish(self, run: _LabRun) -> None:
        try:
            get_bus().publish(
                {
                    "type": "sim.tick",
                    "t": round(run.lab.net.now, 6),
                    "metrics": _lab_metrics(run.lab.net),
                    "state": run.state,
                },
                run.project_id,
            )
        except Exception:  # pragma: no cover - telemetry is best-effort
            logger.exception("sim.tick publish failed for %s", run.project_id)

    def _batch(self, run: _LabRun) -> int:
        """Dispatch one batch of events. Runs under the project's Lab lock
        (F62): the Lab is mutated by other ``/lab/*`` requests on worker
        threads too, so this must never touch it unlocked."""

        def work() -> int:
            return run.lab.do_run(until=None, max_events=_LAB_TICK_BATCH)

        return get_lab_manager().call_locked(run.project_id, work)

    async def start(self, topo: Topology) -> dict:
        pid = topo.project.id
        await self._cancel(pid)
        lab = await run_in_threadpool(get_lab_manager().get, topo)
        run = _LabRun(project_id=pid, lab=lab, state="running")
        run.gate.set()
        self._runs[pid] = run
        # Publish the "running" tick synchronously, before the background
        # driver starts: the frontend transport bar needs an immediate
        # confirmation on Play, not a wait for the first batch to dispatch
        # (mirrors Jalur A's SimManager, which ticks before its first await).
        self._publish(run)
        run.task = asyncio.create_task(self._drive(run))
        return run.status()

    async def _drive(self, run: _LabRun) -> None:
        try:
            while not run.stopped:
                await run.gate.wait()           # parks here while paused
                if run.stopped:
                    break
                n = await run_in_threadpool(self._batch, run)
                self._publish(run)
                if n == 0:                       # nothing left queued
                    break
                await asyncio.sleep(0)           # cooperative yield
            run.state = "stopped" if run.stopped else "completed"
            self._publish(run)
        except asyncio.CancelledError:
            raise
        except Exception:
            run.state = "error"
            self._publish(run)
            logger.exception("lab driver crashed for %s", run.project_id)

    def pause(self, project_id: str) -> dict:
        run = self._runs.get(project_id)
        if run and run.state == "running":
            run.gate.clear()
            run.state = "paused"
            self._publish(run)
        return self.status(project_id)

    def resume(self, project_id: str) -> dict:
        run = self._runs.get(project_id)
        if run and run.state == "paused":
            run.state = "running"
            run.gate.set()
            self._publish(run)
        return self.status(project_id)

    async def step(self, topo: Topology) -> dict:
        """Advance one batch of events, leaving the run paused. Bootstraps a
        fresh paused run if none exists — mirrors ``SimManager.step``."""
        pid = topo.project.id
        run = self._runs.get(pid)
        if run is None or run.state in ("completed", "stopped", "error"):
            lab = await run_in_threadpool(get_lab_manager().get, topo)
            run = _LabRun(project_id=pid, lab=lab, state="paused")
            run.gate.clear()
            self._runs[pid] = run
            run.task = asyncio.create_task(self._drive(run))
        else:
            run.state = "paused"
            run.gate.clear()

        n = await run_in_threadpool(self._batch, run)
        if n == 0:
            run.state = "completed"
        self._publish(run)
        return run.status()

    async def stop(self, project_id: str) -> dict:
        await self._cancel(project_id)
        return {"project_id": project_id, "state": "idle", "sim_time": 0.0, "metrics": {}}

    async def _cancel(self, project_id: str) -> None:
        run = self._runs.pop(project_id, None)
        if run is None:
            return
        run.stopped = True
        run.gate.set()  # unblock a paused driver so it can observe `stopped`
        if run.task is not None:
            run.task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await run.task

    def status(self, project_id: str) -> dict:
        run = self._runs.get(project_id)
        if run is None:
            return {"project_id": project_id, "state": "idle", "sim_time": 0.0, "metrics": {}}
        return run.status()


@lru_cache(maxsize=1)
def get_lab_run_manager() -> LabRunManager:
    """Process-wide singleton, mirrors ``app.services.sim.get_manager``."""
    return LabRunManager()


# ---------------------------------------------------------------------------
# Auto-addressing (UISP-style wizard backend)
# ---------------------------------------------------------------------------

def plan_auto_addressing(topo: Topology) -> dict:
    """Compute an IPv4 plan for every broadcast domain in the topology.

    - Point-to-point links between L3 devices get /30s from 10.255.0.0/16.
    - L2 domains (hosts/routers bridged by switches) get /24s from 10.<n>.0.0.
      Routers get the low addresses (.1, .2, ...), hosts get .10 upward and a
      default gateway pointing at the first router in the domain.

    Returns {node_id: {iface_id: "cidr"}, ...} plus per-host gateway hints —
    the API layer persists it back to the repository.
    """
    node_by_id = {n.id: n for n in topo.nodes}
    iface_node: dict[str, str] = {}
    for n in topo.nodes:
        for i in n.interfaces:
            iface_node[i.id] = n.id

    def node_of(ref: str) -> str | None:
        if ref in iface_node:
            return iface_node[ref]
        return ref if ref in node_by_id else None

    def is_l2(nid: str) -> bool:
        return str(node_by_id[nid].kind) in _L2_KINDS

    def is_l3(nid: str) -> bool:
        return not is_l2(nid)

    # Union endpoints into broadcast domains: links whose both ends are L3
    # devices are their own p2p domain; anything touching a switch merges into
    # that switch's domain.
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        parent[find(a)] = find(b)

    p2p: list[tuple[str, str, str, str]] = []  # (link id, a_iface, b_iface, kind)
    for l in topo.links:
        na, nb = node_of(l.a_iface), node_of(l.b_iface)
        if na is None or nb is None:
            continue
        if is_l3(na) and is_l3(nb):
            p2p.append((l.id, l.a_iface, l.b_iface, "p2p"))
        else:
            union(f"n:{na}", f"n:{nb}")

    assignments: dict[str, dict[str, str]] = {}
    gateways: dict[str, str] = {}
    assignments6: dict[str, dict[str, str]] = {}
    gateways6: dict[str, str] = {}

    def assign(node_id: str, iface_ref: str, cidr: str) -> None:
        assignments.setdefault(node_id, {})[iface_ref] = cidr

    def assign6(node_id: str, iface_ref: str, cidr: str) -> None:
        assignments6.setdefault(node_id, {})[iface_ref] = cidr

    # 1) Point-to-point links: /30 (v4) + ULA /64 (v6) per link.
    for p2p_pool, (link_id, a_ref, b_ref, _) in enumerate(p2p):
        na, nb = node_of(a_ref), node_of(b_ref)
        base = IPv4Address("10.255.0.0") + p2p_pool * 4
        assign(na, a_ref, f"{base + 1}/30")
        assign(nb, b_ref, f"{base + 2}/30")
        base6 = IPv6Address("fd00:255::") + p2p_pool * 2**64  # fd00:255:0:<n>::/64
        assign6(na, a_ref, f"{base6 + 1}/64")
        assign6(nb, b_ref, f"{base6 + 2}/64")

    # 2) LAN domains: /24 (v4) + ULA /64 (v6) per switch broadcast domain.
    domains: dict[str, list[tuple[str, str]]] = {}  # root -> [(node id, iface ref)]
    for l in topo.links:
        na, nb = node_of(l.a_iface), node_of(l.b_iface)
        if na is None or nb is None or (is_l3(na) and is_l3(nb)):
            continue
        for nid, ref in ((na, l.a_iface), (nb, l.b_iface)):
            if is_l3(nid):
                domains.setdefault(find(f"n:{nid}"), []).append((nid, ref))

    for lan_index, members in enumerate(domains.values(), start=1):
        base = IPv4Address("10.0.0.0") + lan_index * 65536  # 10.<n>.0.0/24
        base6 = IPv6Address("fd00::") + lan_index * 2**64   # fd00:0:0:<n>::/64
        routers = [m for m in members if str(node_by_id[m[0]].kind) in ("router", "firewall")]
        hosts = [m for m in members if m not in routers]
        addr = 0
        gw_ip: str | None = None
        gw6: str | None = None
        for nid, ref in routers:
            addr += 1
            assign(nid, ref, f"{base + addr}/24")
            assign6(nid, ref, f"{base6 + addr}/64")
            if gw_ip is None:
                gw_ip, gw6 = str(base + addr), str(base6 + addr)
        addr = max(addr, 9)
        for nid, ref in hosts:
            addr += 1
            assign(nid, ref, f"{base + addr}/24")
            assign6(nid, ref, f"{base6 + addr}/64")
            if gw_ip is not None:
                gateways[nid] = gw_ip
                gateways6[nid] = gw6

    return {
        "assignments": assignments,
        "gateways": gateways,
        "assignments6": assignments6,
        "gateways6": gateways6,
    }


def summarize_plan(plan: dict) -> dict:
    """Roll a raw auto-addressing plan into the wizard's preview tables.

    Groups the assigned CIDRs by network so the dry-run wizard can show one row
    per subnet (segment / subnet / gateway / usable hosts for v4; segment /
    prefix / gateway for v6). Everything here is derived from the numbers the
    planner already produced — no new addressing logic, no fabricated values.
    """
    # v4: group interface CIDRs by their /prefix network.
    v4_nets: dict[str, list[IPv4Address]] = {}
    for ifaces in plan.get("assignments", {}).values():
        for cidr in ifaces.values():
            net = IPv4Network(cidr, strict=False)
            v4_nets.setdefault(str(net), []).append(IPv4Address(cidr.split("/")[0]))

    ipv4 = []
    for net_str, addrs in sorted(v4_nets.items(), key=lambda kv: int(IPv4Network(kv[0]).network_address)):
        net = IPv4Network(net_str)
        gw = min(addrs)  # lowest assigned address = the domain's first router / gateway
        p2p = net.prefixlen >= 30
        ipv4.append(
            {
                "segment": f"{'P2P' if p2p else 'LAN'} {net_str}",
                "subnet": net_str,
                "gateway": str(gw),
                "hosts": net.num_addresses - 2 if net.prefixlen <= 30 else net.num_addresses,
            }
        )

    # v6: group by /64.
    v6_nets: dict[str, list[IPv6Address]] = {}
    for ifaces in plan.get("assignments6", {}).values():
        for cidr in ifaces.values():
            net = IPv6Network(cidr, strict=False)
            v6_nets.setdefault(str(net), []).append(IPv6Address(cidr.split("/")[0]))

    ipv6 = []
    for net_str, addrs in sorted(v6_nets.items(), key=lambda kv: int(IPv6Network(kv[0]).network_address)):
        gw = min(addrs)
        ipv6.append({"segment": f"ULA {net_str}", "prefix": net_str, "gateway": str(gw)})

    p2p_count = sum(1 for r in ipv4 if r["subnet"].endswith("/30"))
    return {
        "interfaces": sum(len(i) for i in plan.get("assignments", {}).values()),
        "lan_domains": len(ipv4) - p2p_count,
        "p2p_links": p2p_count,
        "ipv4": ipv4,
        "ipv6": ipv6,
    }
