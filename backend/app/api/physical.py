"""Physical plant endpoints (NG-PH-01/02).

Sites and racks are placement containers; cables are the physical runs that
realize logical links and drive propagation delay / over-length errors
(NG-PH-03). Devices are placed into racks via the existing node PATCH
(``rack_id`` / ``ru_start`` / ``ru_span``) — no separate endpoint needed.

The physical *effect* on a link (added delay, ``errored`` state) is computed at
read time in the store, so ``GET /projects/{id}/plant`` returns the same verdict
the simulation and the canvas see.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import repo, translate_not_found
from app.models import (
    Cable,
    CableCreate,
    CableUpdate,
    Rack,
    RackCreate,
    RackUpdate,
    Site,
    SiteCreate,
    SiteUpdate,
)
from app.services import notify
from app.services.physical import plant_report
from app.store import MemoryRepository
from app.store import NotFound as StoreNotFound
from app.utils.ids import new_id

router = APIRouter(tags=["physical"])


# --- sites & racks (NG-PH-01) ----------------------------------------------
@router.post("/sites", response_model=Site, status_code=201)
async def create_site(body: SiteCreate, r: Annotated[MemoryRepository, Depends(repo)]):
    return await r.add_site(Site(id=new_id(), **body.model_dump()))


@router.get("/sites", response_model=list[Site])
async def list_sites(project_id: str, r: Annotated[MemoryRepository, Depends(repo)]):
    return await r.list_sites(project_id)


@router.get("/sites/{site_id}", response_model=Site)
async def get_site(site_id: str, r: Annotated[MemoryRepository, Depends(repo)]):
    try:
        return await r.get_site(site_id)
    except StoreNotFound as exc:
        raise translate_not_found(exc) from exc


@router.patch("/sites/{site_id}", response_model=Site)
async def update_site(
    site_id: str, body: SiteUpdate, r: Annotated[MemoryRepository, Depends(repo)]
):
    try:
        patch = body.model_dump(exclude_unset=True)
        if not patch:
            return await r.get_site(site_id)
        return await r.update_site(site_id, patch)
    except StoreNotFound as exc:
        raise translate_not_found(exc) from exc


@router.delete("/sites/{site_id}", status_code=200)
async def delete_site(
    site_id: str, r: Annotated[MemoryRepository, Depends(repo)]
) -> dict:
    # Racks/nodes keep working without a site — clear the back-reference
    # rather than cascading a delete the user did not ask for.
    try:
        await r.delete_site(site_id)
    except StoreNotFound as exc:
        raise translate_not_found(exc) from exc
    return {"deleted": site_id}


@router.post("/racks", response_model=Rack, status_code=201)
async def create_rack(body: RackCreate, r: Annotated[MemoryRepository, Depends(repo)]):
    return await r.add_rack(Rack(id=new_id(), **body.model_dump()))


@router.patch("/racks/{rack_id}", response_model=Rack)
async def update_rack(
    rack_id: str, body: RackUpdate, r: Annotated[MemoryRepository, Depends(repo)]
):
    try:
        patch = body.model_dump(exclude_unset=True)
        if not patch:
            return await r.get_rack(rack_id)
        return await r.update_rack(rack_id, patch)
    except StoreNotFound as exc:
        raise translate_not_found(exc) from exc


# --- cables (NG-PH-02) ------------------------------------------------------
@router.post("/cables", response_model=Cable, status_code=201)
async def create_cable(
    body: CableCreate, r: Annotated[MemoryRepository, Depends(repo)]
):
    try:
        cable = await r.add_cable(Cable(id=new_id(), **body.model_dump()))
    except StoreNotFound as exc:  # unknown link_id
        raise translate_not_found(exc) from exc
    await notify.cable_changed(r, cable.project_id)
    return cable


@router.get("/cables/{cable_id}", response_model=Cable)
async def get_cable(cable_id: str, r: Annotated[MemoryRepository, Depends(repo)]):
    try:
        return await r.get_cable(cable_id)
    except StoreNotFound as exc:
        raise translate_not_found(exc) from exc


@router.patch("/cables/{cable_id}", response_model=Cable)
async def update_cable(
    cable_id: str, patch: CableUpdate, r: Annotated[MemoryRepository, Depends(repo)]
):
    try:
        cable = await r.update_cable(cable_id, patch.model_dump(exclude_unset=True))
    except StoreNotFound as exc:
        raise translate_not_found(exc) from exc
    await notify.cable_changed(r, cable.project_id)
    return cable


@router.delete("/cables/{cable_id}", status_code=200)
async def delete_cable(
    cable_id: str, r: Annotated[MemoryRepository, Depends(repo)]
) -> dict:
    try:
        cable = await r.get_cable(cable_id)
        await r.delete_cable(cable_id)
    except StoreNotFound as exc:
        raise translate_not_found(exc) from exc
    await notify.cable_changed(r, cable.project_id)
    return {"deleted": cable_id}


# --- diagnostics (NG-PH-03) -------------------------------------------------
@router.get("/projects/{project_id}/plant")
async def project_plant(
    project_id: str, r: Annotated[MemoryRepository, Depends(repo)]
) -> dict:
    """Per-link physical verdicts: total length, added propagation delay, and
    whether a run is over its rated max length (the teachable failure)."""
    try:
        topo = await r.topology(project_id)
    except StoreNotFound as exc:
        raise translate_not_found(exc) from exc
    report = plant_report(topo)
    return {
        "project_id": project_id,
        "links": {
            lid: {
                "total_length_m": eff.total_length_m,
                "added_delay_ms": eff.added_delay_ms,
                "over_length": eff.over_length,
                # media is already a plain string (models use use_enum_values)
                "over_media": eff.over_media or None,
            }
            for lid, eff in report.items()
        },
    }
