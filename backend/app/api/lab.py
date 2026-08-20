"""Live-lab endpoints: packet-level diagnostics over the netstack engine.

Every endpoint operates on the project's living lab (built and cached by
:mod:`app.services.netlab`), so a ping here traverses the same simulated
network the device consoles are attached to. Engine work is CPU-bound and
synchronous — it is offloaded to a worker thread to keep the event loop live.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict

from app.api.deps import repo, translate_not_found
from app.exceptions.base import NotFound, SimulationError
from app.services import netlab, notify
from app.store import MemoryRepository
from app.store import NotFound as StoreNotFound

router = APIRouter(prefix="/lab", tags=["lab"])


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PingRequest(_Body):
    src: str  # node id or name
    dst: str  # IPv4 address, or node id/name (first IP used)
    count: int = 4


class TracerouteRequest(_Body):
    src: str
    dst: str
    max_hops: int = 16


class CliRequest(_Body):
    node: str  # node id or name
    command: str


class ModeRequest(_Body):
    mode: str  # "realtime" | "simulation"


class StepRequest(_Body):
    events: int | None = None  # dispatch at most N events
    duration: float | None = None  # or advance the clock this many sim-seconds


class SeekRequest(_Body):
    seq: int  # ledger cursor (0 = pristine lab)


async def _topo(r: MemoryRepository, project_id: str):
    try:
        return await r.topology(project_id)
    except StoreNotFound as exc:
        raise translate_not_found(exc) from exc


async def _locked(project_id: str, fn):
    """Run ``fn`` in the threadpool while holding the project's lab lock.

    Every lab endpoint below mutates the same cached ``Lab``/``Network`` on a
    real OS worker thread (``run_in_threadpool``), which isn't thread-safe —
    two concurrent requests on one project can interleave inside the engine
    and corrupt shared state such as the ledger's seq counter (F62).
    """
    return await run_in_threadpool(netlab.get_lab_manager().call_locked, project_id, fn)


def _lab_for(topo):
    return netlab.get_lab_manager().get(topo)


def _resolve_dst(lab: netlab.Lab, ref: str) -> str:
    """Accept a literal IPv4/IPv6 or a node reference (first address wins,
    IPv4 preferred for backward compatibility)."""
    import ipaddress

    try:
        ipaddress.ip_address(ref)
        return ref
    except ValueError:
        pass
    dev = lab.net.find_device(ref)
    if dev is not None:
        ips = dev.all_ips()
        if ips:
            return str(ips[0].ip)
        ips6 = dev.all_ips6()
        if ips6:
            return str(ips6[0].ip)
    raise NotFound(f"cannot resolve destination '{ref}' to an IP address")


@router.post("/{project_id}/ping")
async def lab_ping(
    project_id: str, body: PingRequest, r: Annotated[MemoryRepository, Depends(repo)]
):
    topo = await _topo(r, project_id)

    def work():
        lab = _lab_for(topo)
        dst = _resolve_dst(lab, body.dst)
        report = lab.do_ping(body.src, dst, count=min(body.count, 50))
        return {
            **report.as_dict(),
            "ident": report.ident,
            "done": report.done,
            "mode": lab.mode,
        }

    try:
        return await _locked(project_id, work)
    except NotFound:
        raise
    except ValueError as exc:
        raise SimulationError(str(exc)) from exc


@router.post("/{project_id}/traceroute")
async def lab_traceroute(
    project_id: str,
    body: TracerouteRequest,
    r: Annotated[MemoryRepository, Depends(repo)],
):
    topo = await _topo(r, project_id)

    def work():
        lab = _lab_for(topo)
        dst = _resolve_dst(lab, body.dst)
        report = lab.do_traceroute(body.src, dst, max_hops=min(body.max_hops, 32))
        return {**report.as_dict(), "mode": lab.mode}

    try:
        return await _locked(project_id, work)
    except NotFound:
        raise
    except ValueError as exc:
        raise SimulationError(str(exc)) from exc


@router.post("/{project_id}/cli")
async def lab_cli(
    project_id: str, body: CliRequest, r: Annotated[MemoryRepository, Depends(repo)]
):
    topo = await _topo(r, project_id)

    def work():
        lab = _lab_for(topo)
        output = lab.do_cli(body.node, body.command)
        if output is None:
            raise NotFound(f"node '{body.node}' not found in lab")
        session = lab.session_for(body.node)
        return {"node": body.node, "output": output, "prompt": session.prompt}

    try:
        return await _locked(project_id, work)
    except NotFound:
        raise
    except ValueError as exc:
        raise SimulationError(str(exc)) from exc


@router.get("/{project_id}/ledger")
async def lab_ledger(
    project_id: str,
    r: Annotated[MemoryRepository, Depends(repo)],
    from_seq: int = 0,
    limit: int = 500,
    type_prefix: str | None = None,
    node: str | None = None,
):
    """Event ledger of the living lab (NG-SIM-01): replay hash + recent records."""
    topo = await _topo(r, project_id)

    def work():
        lab = _lab_for(topo)
        led = lab.net.ledger
        return {
            "hash": led.hash(),
            "total": led.seq,
            "mode": lab.mode,
            "sim_time": round(lab.net.now, 9),
            "pending_events": len(lab.net.scheduler.queue),
            "records": led.tail(from_seq, min(limit, 2000), type_prefix, node),
        }

    return await _locked(project_id, work)


@router.post("/{project_id}/mode")
async def lab_mode(
    project_id: str, body: ModeRequest, r: Annotated[MemoryRepository, Depends(repo)]
):
    """Switch realtime <-> simulation mode (NG-SIM-01, PT parity)."""
    if body.mode not in ("realtime", "simulation"):
        raise SimulationError("mode must be 'realtime' or 'simulation'")
    topo = await _topo(r, project_id)

    def work():
        lab = _lab_for(topo)
        lab.do_mode(body.mode)
        return {"project_id": project_id, "mode": lab.mode, "seq": lab.net.ledger.seq}

    return await _locked(project_id, work)


@router.post("/{project_id}/step")
async def lab_step(
    project_id: str, body: StepRequest, r: Annotated[MemoryRepository, Depends(repo)]
):
    """Advance the simulation: N events (default 1) or a sim-time slice."""
    topo = await _topo(r, project_id)

    def work():
        lab = _lab_for(topo)
        if body.duration is not None:
            dispatched = lab.do_run(until=lab.net.now + max(0.0, body.duration))
        else:
            dispatched = lab.do_run(
                until=None, max_events=max(1, min(body.events or 1, 100_000))
            )
        led = lab.net.ledger
        return {
            "project_id": project_id,
            "dispatched": dispatched,
            "seq": led.seq,
            "sim_time": round(lab.net.now, 9),
            "pending_events": len(lab.net.scheduler.queue),
            "records": led.tail(led.seq - dispatched, min(dispatched, 500)),
        }

    return await _locked(project_id, work)


@router.post("/{project_id}/seek")
async def lab_seek(
    project_id: str, body: SeekRequest, r: Annotated[MemoryRepository, Depends(repo)]
):
    """Move the ledger cursor to event ``seq`` — step-back included.

    Determinism makes this cheap: the lab is rebuilt and the stimulus journal
    replayed with the scheduler capped at the target event (NG-SIM-01)."""
    topo = await _topo(r, project_id)

    def work():
        lab = netlab.get_lab_manager().seek(topo, body.seq)
        return {
            "project_id": project_id,
            "mode": lab.mode,
            "seq": lab.net.ledger.seq,
            "hash": lab.net.ledger.hash(),
            "sim_time": round(lab.net.now, 9),
            "pending_events": len(lab.net.scheduler.queue),
        }

    return await _locked(project_id, work)


@router.get("/{project_id}/captures")
async def lab_captures(
    project_id: str,
    r: Annotated[MemoryRepository, Depends(repo)],
    link_id: str | None = None,
    limit: int = 100,
    filter: str | None = None,
):
    """Capture records, optionally narrowed by a Wireshark-style display
    filter (NG-CAP-02), e.g. ``icmp && ip.addr==10.0.0.1``."""
    topo = await _topo(r, project_id)

    def work():
        from engine.netstack.filterlang import compile_filter

        try:
            predicate = compile_filter(filter or "")
        except ValueError as exc:
            raise SimulationError(f"bad display filter: {exc}") from exc
        lab = _lab_for(topo)
        records = lab.net.capture.records(link_id=link_id, limit=1000)
        rows = [rec.as_dict() for rec in records]
        if filter:
            rows = [row for row in rows if predicate(row)]
        rows = rows[-min(limit, 1000) :]
        return {
            "project_id": project_id,
            "link_id": link_id,
            "filter": filter,
            "count": len(rows),
            "records": rows,
        }

    return await _locked(project_id, work)


@router.get("/{project_id}/pcapng")
async def lab_pcapng(
    project_id: str,
    r: Annotated[MemoryRepository, Depends(repo)],
    link_id: str | None = None,
):
    """Export captured frames as a real .pcapng (NG-CAP-01) — opens in
    Wireshark/tshark with native protocol decode."""
    topo = await _topo(r, project_id)

    def work():
        from engine.netstack.pcapng import write_pcapng

        lab = _lab_for(topo)
        records = lab.net.capture.records(link_id=link_id, limit=1000)
        return write_pcapng(records)

    data = await _locked(project_id, work)
    name = f"netgeo-{project_id[:8]}{('-' + link_id[:8]) if link_id else ''}.pcapng"
    return Response(
        content=data,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@router.get("/{project_id}/tables/{node_ref}")
async def lab_tables(
    project_id: str, node_ref: str, r: Annotated[MemoryRepository, Depends(repo)]
):
    """Live control-plane state of one device: RIB, ARP, MAC, protocols, NAT."""
    topo = await _topo(r, project_id)

    def work():
        lab = _lab_for(topo)
        dev = lab.net.find_device(node_ref)
        if dev is None:
            raise NotFound(f"node '{node_ref}' not found in lab")
        out: dict = {
            "node": dev.name,
            "kind": dev.kind,
            "interfaces": [i.brief() for i in dev.interfaces.values()],
        }
        arp = getattr(dev, "arp_table", None)
        if arp is not None:
            out["arp"] = [
                {"ip": str(ip), "mac": str(mac), "iface": ifname}
                for ip, (mac, ifname) in sorted(arp.items())
            ]
        nd = getattr(dev, "nd_cache", None)
        if nd is not None:
            out["neighbors6"] = [
                {"ip": str(ip), "mac": str(mac), "iface": ifname}
                for ip, (mac, ifname) in sorted(nd.items(), key=lambda kv: str(kv[0]))
            ]
        if hasattr(dev, "route_table_rows"):
            out["routes"] = dev.route_table_rows()
            out["routes6"] = dev.route_table_rows6()
            out["nat"] = dev.nat_rows()
        if hasattr(dev, "mac_table_rows"):
            out["mac_table"] = dev.mac_table_rows()
        for proc in getattr(dev, "processes", []):
            proto = getattr(proc, "proto", "")
            if proto == "ospf":
                out["ospf_neighbors"] = proc.neighbor_rows()
            elif proto == "bgp":
                out["bgp_peers"] = proc.summary_rows()
            elif proto == "vrrp":
                out.setdefault("vrrp", []).append(proc.status_row())
            elif proto == "ldp":
                out["mpls_forwarding"] = dev.mpls_forwarding_rows()
            elif proto == "l3vpn":
                out["vrfs"] = {
                    name: dev.vrf_route_rows(name) for name in dev.vrf_names()
                }
            elif proto == "vxlan":
                out["vxlan"] = {
                    "mac_vni": proc.mac_vni_rows(),
                    "vtep": proc.vtep_rows(),
                    "evpn": proc.evpn_rows(),
                }
        # QoS per-class counters (NG-SIM-11 §2.6) — present on all devices.
        _cls = ("EF", "AF", "BE")
        qos_rows = []
        for i in dev.interfaces.values():
            enabled = i.attachment is not None and i.attachment.qos.enabled
            qos_rows.append(
                {
                    "iface": i.name,
                    "qos_enabled": enabled,
                    "classes": [
                        {
                            "class": _cls[ci],
                            "tx_frames": i.counters.tx_by_class[ci],
                            "drops": i.counters.drops_queue_by_class[ci],
                        }
                        for ci in range(3)
                    ],
                }
            )
        out["qos"] = qos_rows
        return out

    return await _locked(project_id, work)


@router.get("/{project_id}/status")
async def lab_status(project_id: str, r: Annotated[MemoryRepository, Depends(repo)]):
    topo = await _topo(r, project_id)

    def work():
        lab = _lab_for(topo)
        return {
            "project_id": project_id,
            "stats": lab.net.stats(),
            "events": lab.net.events_log[-50:],
        }

    return await _locked(project_id, work)


@router.post("/{project_id}/rebuild")
async def lab_rebuild(project_id: str, r: Annotated[MemoryRepository, Depends(repo)]):
    """Force a fresh lab (drops live CLI state and captures)."""
    topo = await _topo(r, project_id)
    netlab.get_lab_manager().invalidate(project_id)

    def work():
        lab = _lab_for(topo)
        return {"project_id": project_id, "stats": lab.net.stats()}

    return await _locked(project_id, work)


@router.post("/{project_id}/auto-address")
async def lab_auto_address(
    project_id: str,
    r: Annotated[MemoryRepository, Depends(repo)],
    dry_run: bool = False,
):
    """Auto-assign dual-stack (IPv4 + IPv6 ULA) addressing to the whole
    topology and persist it (NG-TD-03).

    /30 + /64 for router-to-router links, /24 + /64 per switch broadcast
    domain, host default gateways (v4 and v6) pointed at the first router in
    their domain.

    With ``dry_run=true`` the plan is computed and summarized for the
    addressing wizard's Preview step but NOTHING is persisted — no node update,
    no gateway write, no lab invalidation — so the wizard can show the plan
    before the operator commits.
    """
    topo = await _topo(r, project_id)
    plan = netlab.plan_auto_addressing(topo)

    if dry_run:
        return {
            "project_id": project_id,
            "dry_run": True,
            "nodes_updated": 0,
            "plan": plan,
            "summary": netlab.summarize_plan(plan),
        }

    changed = 0
    for node in topo.nodes:
        node_assign = plan["assignments"].get(node.id) or {}
        node_assign6 = plan["assignments6"].get(node.id) or {}
        gateway = plan["gateways"].get(node.id)
        gateway6 = plan["gateways6"].get(node.id)
        if not node_assign and not node_assign6 and not gateway and not gateway6:
            continue
        interfaces = []
        for iface in node.interfaces:
            # v4 first so callers reading ip[0] keep seeing the IPv4 CIDR.
            ips = [
                ip
                for ip in (node_assign.get(iface.id), node_assign6.get(iface.id))
                if ip
            ]
            interfaces.append(iface.model_copy(update={"ip": ips}) if ips else iface)
        patch: dict = {"interfaces": interfaces}
        if gateway or gateway6:
            intent = dict(node.intent or {})
            if gateway:
                intent["gateway"] = gateway
            if gateway6:
                intent["gateway6"] = gateway6
            patch["intent"] = intent
        updated = await r.update_node(node.id, patch)
        await notify.node_changed(r, updated)
        changed += 1

    netlab.get_lab_manager().invalidate(project_id)
    return {
        "project_id": project_id,
        "dry_run": False,
        "nodes_updated": changed,
        "plan": plan,
        "summary": netlab.summarize_plan(plan),
    }
