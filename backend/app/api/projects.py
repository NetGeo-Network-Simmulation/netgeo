"""Projects + topology endpoints."""

from __future__ import annotations

import gzip
import json
from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import repo, translate_not_found
from app.core.config import storagehub_enabled
from app.models import Project, ProjectCreate, Topology
from app.services import archive as archive_svc
from app.services import storagehub_client
from app.store import MemoryRepository
from app.store import NotFound as StoreNotFound

router = APIRouter(tags=["projects"])


@router.get("/projects", response_model=list[Project])
async def list_projects(r: Annotated[MemoryRepository, Depends(repo)]):
    return await r.list_projects()


@router.post("/projects", response_model=Project, status_code=201)
async def create_project(
    body: ProjectCreate, r: Annotated[MemoryRepository, Depends(repo)]
):
    return await r.create_project(body.name, body.description)


# --- archive (NG-WS-03): export / import round-trip -------------------------
# Declared before the ``/projects/{project_id}`` catch-all so ``/projects/import``
# is not swallowed by it (POST anyway, but keep intent obvious).
@router.post("/projects/import", response_model=Project, status_code=201)
async def import_archive(body: dict, r: Annotated[MemoryRepository, Depends(repo)]):
    """Create a brand-new project from an archive envelope, minting fresh ids
    for every entity so it cannot collide with an existing copy. Rejects a
    malformed / unknown-format envelope with a 422 before touching the store."""
    parsed = archive_svc.parse_archive(body)  # raises ValidationError -> 422
    src = parsed["project"]
    project = await r.create_project(src.name, src.description)
    remapped = archive_svc.remap(parsed, project.id)
    for site in remapped["sites"]:
        await r.add_site(site)
    for rack in remapped["racks"]:
        await r.add_rack(rack)
    for node in remapped["nodes"]:
        await r.add_node(node)
    for link in remapped["links"]:
        await r.add_link(link)
    for cable in remapped["cables"]:  # after links: add_cable needs the link
        await r.add_cable(cable)
    for scenario in remapped["scenarios"]:
        await r.add_scenario(scenario)
    for config in remapped["configs"]:
        await r.add_config(config)
    return project


@router.get("/projects/{project_id}", response_model=Project)
async def get_project(project_id: str, r: Annotated[MemoryRepository, Depends(repo)]):
    try:
        return await r.get_project(project_id)
    except StoreNotFound as exc:
        raise translate_not_found(exc) from exc


@router.get("/projects/{project_id}/topology", response_model=Topology)
async def get_topology(project_id: str, r: Annotated[MemoryRepository, Depends(repo)]):
    try:
        return await r.topology(project_id)
    except StoreNotFound as exc:
        raise translate_not_found(exc) from exc


@router.get("/projects/{project_id}/archive")
async def export_archive(
    project_id: str, r: Annotated[MemoryRepository, Depends(repo)]
) -> dict:
    """Return the whole project as a versioned JSON archive envelope (NG-WS-03)."""
    try:
        bundle = await r.export_project(project_id)
    except StoreNotFound as exc:
        raise translate_not_found(exc) from exc
    return archive_svc.build_archive(bundle)


@router.post("/projects/{project_id}/backup-to-storagehub")
async def backup_to_storagehub(
    project_id: str, r: Annotated[MemoryRepository, Depends(repo)]
) -> dict:
    """Export the project archive (reusing the same builder as the plain
    ``/archive`` endpoint) and push it, gzip-compressed, into StorageHub
    (C-2). Disabled unless NETGEO_STORAGEHUB_URL + _API_KEY are both set."""
    if not storagehub_enabled():
        raise storagehub_client.StorageHubError(
            "StorageHub backup not configured", status_code=503
        )
    try:
        bundle = await r.export_project(project_id)
    except StoreNotFound as exc:
        raise translate_not_found(exc) from exc
    envelope = archive_svc.build_archive(bundle)
    content = gzip.compress(json.dumps(envelope).encode("utf-8"))
    return await storagehub_client.upload(
        content=content,
        filename=f"{project_id}.netgeo-archive.json.gz",
        source=f"netgeo-{project_id}",
    )
