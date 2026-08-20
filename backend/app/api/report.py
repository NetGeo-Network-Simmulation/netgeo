"""Project report + BOM endpoints (NG-CFG-02 / NG-FI-04)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse

from app.api.deps import repo, translate_not_found
from app.models import BomItem
from app.services.report import build_bom, project_report
from app.store import MemoryRepository
from app.store import NotFound as StoreNotFound

router = APIRouter(tags=["report"])


@router.get("/projects/{project_id}/bom", response_model=list[BomItem])
async def project_bom(project_id: str, r: Annotated[MemoryRepository, Depends(repo)]):
    try:
        topo = await r.topology(project_id)
    except StoreNotFound as exc:
        raise translate_not_found(exc) from exc
    return build_bom(topo, await r.list_fiber_paths(project_id))


@router.get("/projects/{project_id}/report", response_class=HTMLResponse)
async def project_html_report(
    project_id: str, r: Annotated[MemoryRepository, Depends(repo)]
):
    """Print-ready project report (NG-CFG-02)."""
    try:
        project = await r.get_project(project_id)
        topo = await r.topology(project_id)
    except StoreNotFound as exc:
        raise translate_not_found(exc) from exc
    _, html = project_report(project, topo, await r.list_fiber_paths(project_id))
    return HTMLResponse(content=html)
