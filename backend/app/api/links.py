"""Link endpoints.

Link creation auto-provisions device interfaces: the frontend may reference a
*node id* as an endpoint (fresh nodes have no ports yet) — in that case the
backend mints the next free ``ethN`` interface on that node, persists it, and
wires the link to it. Both endpoint interfaces get ``peer_link_id`` set so the
UI and the lab engine always see real ports; deletion clears it again.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import repo, translate_not_found
from app.models import Interface, Link, LinkCreate, LinkUpdate
from app.services import notify
from app.store import MemoryRepository
from app.store import NotFound as StoreNotFound
from app.utils.ids import new_id

router = APIRouter(tags=["links"])


async def _find_iface_owner(r: MemoryRepository, project_id: str, iface_id: str):
    """(node, index) owning ``iface_id`` within the project, or None."""
    topo = await r.topology(project_id)
    for node in topo.nodes:
        for i, iface in enumerate(node.interfaces):
            if iface.id == iface_id:
                return node, i
    return None


async def _resolve_endpoint(
    r: MemoryRepository, project_id: str, ref: str, link_id: str
) -> str:
    """Return a real interface id for a link endpoint reference.

    ``ref`` may be an existing interface id (marked attached) or a node id
    (a new ``ethN`` interface is created on that node).
    """
    hit = await _find_iface_owner(r, project_id, ref)
    if hit is not None:
        node, i = hit
        if node.interfaces[i].peer_link_id != link_id:
            ifaces = list(node.interfaces)
            ifaces[i] = ifaces[i].model_copy(update={"peer_link_id": link_id})
            await r.update_node(node.id, {"interfaces": ifaces})
        return ref
    try:
        node = await r.get_node(ref)
    except StoreNotFound:
        return ref  # opaque endpoint (legacy payloads) — keep as-is
    existing = {i.name for i in node.interfaces}
    n = 0
    while f"eth{n}" in existing:
        n += 1
    iface = Interface(
        id=new_id(),
        node_id=node.id,
        name=f"eth{n}",
        peer_link_id=link_id,
    )
    await r.update_node(node.id, {"interfaces": [*node.interfaces, iface]})
    return iface.id


async def _detach_endpoint(
    r: MemoryRepository, project_id: str, iface_id: str, link_id: str
) -> None:
    hit = await _find_iface_owner(r, project_id, iface_id)
    if hit is None:
        return
    node, i = hit
    if node.interfaces[i].peer_link_id == link_id:
        ifaces = list(node.interfaces)
        ifaces[i] = ifaces[i].model_copy(update={"peer_link_id": None})
        await r.update_node(node.id, {"interfaces": ifaces})


@router.post("/links", response_model=Link, status_code=201)
async def create_link(body: LinkCreate, r: Annotated[MemoryRepository, Depends(repo)]):
    link_id = new_id()
    payload = body.model_dump()
    payload["a_iface"] = await _resolve_endpoint(
        r, body.project_id, body.a_iface, link_id
    )
    payload["b_iface"] = await _resolve_endpoint(
        r, body.project_id, body.b_iface, link_id
    )
    link = Link(id=link_id, **payload)
    created = await r.add_link(link)
    await notify.link_changed(r, created)
    return created


@router.get("/links/{link_id}", response_model=Link)
async def get_link(link_id: str, r: Annotated[MemoryRepository, Depends(repo)]):
    try:
        return await r.get_link(link_id)
    except StoreNotFound as exc:
        raise translate_not_found(exc) from exc


@router.patch("/links/{link_id}", response_model=Link)
async def update_link(
    link_id: str, patch: LinkUpdate, r: Annotated[MemoryRepository, Depends(repo)]
):
    try:
        updated = await r.update_link(link_id, patch.model_dump(exclude_unset=True))
    except StoreNotFound as exc:
        raise translate_not_found(exc) from exc
    await notify.link_changed(r, updated)
    return updated


@router.delete("/links/{link_id}", status_code=200)
async def delete_link(
    link_id: str, r: Annotated[MemoryRepository, Depends(repo)]
) -> dict:
    try:
        link = await r.get_link(link_id)
        await r.delete_link(link_id)
    except StoreNotFound as exc:
        raise translate_not_found(exc) from exc
    await _detach_endpoint(r, link.project_id, link.a_iface, link_id)
    await _detach_endpoint(r, link.project_id, link.b_iface, link_id)
    await notify.link_removed(link.project_id, link_id)
    return {"deleted": link_id}
