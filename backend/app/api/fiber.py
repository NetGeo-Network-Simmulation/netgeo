"""Fiber plant endpoints (NG-FI-01/02/03).

CRUD for GPON distribution paths + a derived loss-budget report. The budget /
GPON checks are computed at read time by ``services/fiber.py`` (pure), so the
report always reflects the path as currently stored.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import repo, translate_not_found
from app.models import FiberPath, FiberPathCreate, FiberPathUpdate, LossBudget
from app.services.fiber import loss_budget
from app.store import MemoryRepository
from app.store import NotFound as StoreNotFound
from app.utils.ids import new_id

router = APIRouter(tags=["fiber"])


@router.post("/fiber-paths", response_model=FiberPath, status_code=201)
async def create_fiber_path(
    body: FiberPathCreate, r: Annotated[MemoryRepository, Depends(repo)]
):
    return await r.add_fiber_path(FiberPath(id=new_id(), **body.model_dump()))


@router.get("/fiber-paths", response_model=list[FiberPath])
async def list_fiber_paths(
    project_id: str, r: Annotated[MemoryRepository, Depends(repo)]
):
    return await r.list_fiber_paths(project_id)


@router.get("/fiber-paths/{fiber_id}", response_model=FiberPath)
async def get_fiber_path(fiber_id: str, r: Annotated[MemoryRepository, Depends(repo)]):
    try:
        return await r.get_fiber_path(fiber_id)
    except StoreNotFound as exc:
        raise translate_not_found(exc) from exc


@router.patch("/fiber-paths/{fiber_id}", response_model=FiberPath)
async def update_fiber_path(
    fiber_id: str, patch: FiberPathUpdate, r: Annotated[MemoryRepository, Depends(repo)]
):
    try:
        return await r.update_fiber_path(fiber_id, patch.model_dump(exclude_unset=True))
    except StoreNotFound as exc:
        raise translate_not_found(exc) from exc


@router.delete("/fiber-paths/{fiber_id}", status_code=200)
async def delete_fiber_path(
    fiber_id: str, r: Annotated[MemoryRepository, Depends(repo)]
) -> dict:
    try:
        await r.delete_fiber_path(fiber_id)
    except StoreNotFound as exc:
        raise translate_not_found(exc) from exc
    return {"deleted": fiber_id}


@router.get("/fiber-paths/{fiber_id}/budget", response_model=LossBudget)
async def fiber_budget(fiber_id: str, r: Annotated[MemoryRepository, Depends(repo)]):
    """Auto loss budget + GPON checks for one path (NG-FI-02/03)."""
    try:
        path = await r.get_fiber_path(fiber_id)
    except StoreNotFound as exc:
        raise translate_not_found(exc) from exc
    return loss_budget(path)
