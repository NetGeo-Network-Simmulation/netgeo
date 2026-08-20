"""Digital twin endpoints (R6 base).

NG-TW-01 config import: parse a device config into a real node in the project,
so an existing network can be brought into the twin from its running configs.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from app.api.deps import repo, translate_not_found
from app.exceptions.base import AppException, SimulationError
from app.models import DriftReport, ImportSnapshot, Interface, Link, Node
from app.services import configimport, notify, reachability, twindiff
from app.store import MemoryRepository
from app.store import NotFound as StoreNotFound
from app.utils.ids import new_id

router = APIRouter(tags=["twin"])


class ImportConfigFailed(AppException):
    status_code = 422
    code = "IMPORT_CONFIG_FAILED"


class ImportConfigRequest(BaseModel):
    vendor: str = "ios"
    # Trust boundary: config text is untrusted and drives regex parsers. Cap it
    # (a real device config is well under this) so a giant paste can't tie up a
    # worker thread. 422 on overflow via pydantic.
    text: str = Field(max_length=512 * 1024)


@router.post(
    "/projects/{project_id}/import-config", response_model=Node, status_code=201
)
async def import_config(
    project_id: str,
    body: ImportConfigRequest,
    r: Annotated[MemoryRepository, Depends(repo)],
):
    """Parse an IOS-like config (NG-TW-01) into a node in ``project_id``."""
    try:
        await r.get_project(project_id)
    except StoreNotFound as exc:
        raise translate_not_found(exc) from exc
    try:
        parsed = configimport.parse(body.vendor, body.text)
    except ValueError as exc:
        raise ImportConfigFailed(str(exc)) from exc

    nid = new_id()
    ifaces = [
        Interface(id=new_id(), node_id=nid, name=i["name"], ip=i["ip"])
        for i in parsed["interfaces"]
    ]
    intent = {"static_routes": parsed["static_routes"], "imported": True}
    for proto in ("ospf", "bgp"):
        if parsed.get(proto):
            intent[proto] = parsed[proto]
    node = Node(
        id=nid,
        project_id=project_id,
        name=parsed["hostname"],
        kind="router",
        nos=body.vendor.lower()
        if body.vendor.lower() in ("routeros", "mikrotik")
        else "ios",
        interfaces=ifaces,
        intent=intent,
    )
    created = await r.add_node(node)
    # NG-TW-03: persist the raw import text so drift-diff can compare against it later.
    await r.save_import_snapshot(
        ImportSnapshot(
            id=new_id(),
            node_id=created.id,
            project_id=project_id,
            vendor=body.vendor,
            text=body.text,
        )
    )
    await notify.node_changed(r, created)
    return created


@router.post(
    "/projects/{project_id}/infer-links", response_model=list[Link], status_code=201
)
async def infer_links(project_id: str, r: Annotated[MemoryRepository, Depends(repo)]):
    """Wire interfaces that share an IP subnet (NG-TW-01) so the imported set
    becomes a connected twin. Idempotent: pairs already linked are skipped."""
    try:
        topo = await r.topology(project_id)
    except StoreNotFound as exc:
        raise translate_not_found(exc) from exc

    owner = {i.id: n.id for n in topo.nodes for i in n.interfaces}
    entries = [
        (i.id, n.id, cidr) for n in topo.nodes for i in n.interfaces for cidr in i.ip
    ]
    linked = {frozenset((l.a_iface, l.b_iface)) for l in topo.links}

    created: list[Link] = []
    for a_iface, b_iface in configimport.infer_links(entries):
        if frozenset((a_iface, b_iface)) in linked:
            continue
        link = Link(
            id=new_id(), project_id=project_id, a_iface=a_iface, b_iface=b_iface
        )
        await r.add_link(link)
        linked.add(frozenset((a_iface, b_iface)))
        # Best-effort UI wiring: claim peer_link_id only where the port is free.
        for iface_id in (a_iface, b_iface):
            node = await r.get_node(owner[iface_id])
            ifaces = list(node.interfaces)
            for idx, iface in enumerate(ifaces):
                if iface.id == iface_id and iface.peer_link_id is None:
                    ifaces[idx] = iface.model_copy(update={"peer_link_id": link.id})
                    await r.update_node(node.id, {"interfaces": ifaces})
                    break
        await notify.link_changed(r, link)
        created.append(link)
    return created


@router.get("/projects/{project_id}/nodes/{node_id}/drift", response_model=DriftReport)
async def node_drift(
    project_id: str, node_id: str, r: Annotated[MemoryRepository, Depends(repo)]
):
    """Diff the last-imported config against the node's current intent (NG-TW-03)."""
    try:
        await r.get_project(project_id)
        node = await r.get_node(node_id)
    except StoreNotFound as exc:
        raise translate_not_found(exc) from exc
    snap = await r.get_import_snapshot(node_id)
    return twindiff.drift_report(node, snap)


@router.get("/projects/{project_id}/drift", response_model=list[DriftReport])
async def project_drift(project_id: str, r: Annotated[MemoryRepository, Depends(repo)]):
    """Drift summary for all imported nodes in a project (NG-TW-03)."""
    try:
        topo = await r.topology(project_id)
    except StoreNotFound as exc:
        raise translate_not_found(exc) from exc
    imported = [n for n in topo.nodes if (n.intent or {}).get("imported")]
    reports = []
    for node in imported:
        snap = await r.get_import_snapshot(node.id)
        reports.append(twindiff.drift_report(node, snap))
    return reports


class ReachabilityRequest(BaseModel):
    src: str  # node id or name
    dst: str  # IPv4/IPv6 literal, or node id/name (first address used)
    count: int = 3


@router.post("/projects/{project_id}/reachability")
async def reachability_query(
    project_id: str,
    body: ReachabilityRequest,
    r: Annotated[MemoryRepository, Depends(repo)],
):
    """Answer "can src reach dst?" over the twin, with path + RIB evidence
    (NG-TW-02). Runs on an isolated lab, so the live lab is untouched."""
    try:
        topo = await r.topology(project_id)
    except StoreNotFound as exc:
        raise translate_not_found(exc) from exc

    def work():
        return reachability.answer(topo, body.src, body.dst, count=min(body.count, 20))

    try:
        return await run_in_threadpool(work)
    except ValueError as exc:
        raise SimulationError(str(exc)) from exc
