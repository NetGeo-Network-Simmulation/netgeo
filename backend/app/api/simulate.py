"""Simulation control endpoints.

``POST /simulate`` runs the discrete-event kernel over a project's topology and
returns a result summary. The lifecycle endpoints (pause/resume/step/stop) are
the control surface the frontend SimulationBar drives; for v0.1 the in-process
runner executes synchronously, so they return the current run state. A
distributed run-manager (Redis-backed job queue, see infra/redis-design.md) is
the production target.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.concurrency import run_in_threadpool

from app.api.deps import repo, translate_not_found
from app.exceptions.base import SimulationError
from app.models import SimulateRequest
from app.services import sim as sim_service
from app.store import MemoryRepository
from app.store import NotFound as StoreNotFound

router = APIRouter(tags=["simulate"])


async def _topo_or_404(r: MemoryRepository, project_id: str):
    try:
        return await r.topology(project_id)
    except StoreNotFound as exc:
        raise translate_not_found(exc) from exc


@router.post("/simulate")
async def start_simulation(
    body: SimulateRequest, r: Annotated[MemoryRepository, Depends(repo)]
):
    topo = await _topo_or_404(r, body.project_id)

    if body.realtime:
        # Live run: a background task streams sim.tick telemetry over
        # /ws/topology and the lifecycle endpoints below steer it.
        try:
            return await sim_service.get_manager().start(
                topo, seed=body.seed, horizon=body.horizon
            )
        except Exception as exc:  # engine build errors -> 422 with a clear message
            raise SimulationError(str(exc)) from exc

    try:
        # Headless run: the kernel is synchronous and CPU-bound; running it inline
        # would block the event loop (stalling every other request and the
        # /ws/topology stream) for the whole run. Offload to a worker thread.
        result = await run_in_threadpool(
            sim_service.run_once, topo, seed=body.seed, horizon=body.horizon
        )
    except Exception as exc:  # engine errors -> 422 with a clear message
        raise SimulationError(str(exc)) from exc
    return {"project_id": body.project_id, "state": "completed", "result": result}


@router.post("/simulate/{project_id}/pause")
async def pause(project_id: str):
    return sim_service.get_manager().pause(project_id)


@router.post("/simulate/{project_id}/resume")
async def resume(project_id: str):
    return sim_service.get_manager().resume(project_id)


@router.post("/simulate/{project_id}/step")
async def step(project_id: str, r: Annotated[MemoryRepository, Depends(repo)]):
    topo = await _topo_or_404(r, project_id)
    try:
        return await sim_service.get_manager().step(topo)
    except Exception as exc:
        raise SimulationError(str(exc)) from exc


@router.post("/simulate/{project_id}/stop")
async def stop(project_id: str):
    return await sim_service.get_manager().stop(project_id)


@router.get("/simulate/{project_id}/status")
async def status(project_id: str):
    return sim_service.get_manager().status(project_id)
