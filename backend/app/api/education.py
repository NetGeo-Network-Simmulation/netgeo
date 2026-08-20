"""Education mode endpoints (NG-EDU-01/02).

An *activity* bundles teaching instructions with a starting network and a model
answer (both NG-WS-03 archive envelopes) plus a tree of weighted grading checks.
``/activities/{id}/grade`` runs those checks against a student's live project
and returns a per-item report with a live completion %.
"""

from __future__ import annotations

import csv
import io
from typing import Annotated

from fastapi import APIRouter, Depends, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict

from app.api.deps import repo, translate_not_found
from app.models import (
    Activity,
    ActivityCreate,
    GradeReport,
    GradeResult,
    GradeSubmit,
    Project,
)
from app.services import archive as archive_svc
from app.services import grading
from app.store import MemoryRepository
from app.store import NotFound as StoreNotFound
from app.utils.ids import new_id

router = APIRouter(tags=["education"])


class GradeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: str


@router.post("/activities", response_model=Activity, status_code=201)
async def create_activity(
    body: ActivityCreate, r: Annotated[MemoryRepository, Depends(repo)]
):
    return await r.add_activity(Activity(id=new_id(), **body.model_dump()))


@router.get("/activities", response_model=list[Activity])
async def list_activities(r: Annotated[MemoryRepository, Depends(repo)]):
    return await r.list_activities()


@router.get("/activities/{activity_id}", response_model=Activity)
async def get_activity(activity_id: str, r: Annotated[MemoryRepository, Depends(repo)]):
    try:
        return await r.get_activity(activity_id)
    except StoreNotFound as exc:
        raise translate_not_found(exc) from exc


@router.delete("/activities/{activity_id}", status_code=200)
async def delete_activity(
    activity_id: str, r: Annotated[MemoryRepository, Depends(repo)]
) -> dict:
    try:
        await r.delete_activity(activity_id)
    except StoreNotFound as exc:
        raise translate_not_found(exc) from exc
    return {"deleted": activity_id}


@router.post("/activities/{activity_id}/grade", response_model=GradeReport)
async def grade_activity(
    activity_id: str, body: GradeRequest, r: Annotated[MemoryRepository, Depends(repo)]
):
    """Grade a student's project against this activity's checks (NG-EDU-02)."""
    try:
        activity = await r.get_activity(activity_id)
        topo = await r.topology(body.project_id)
    except StoreNotFound as exc:
        raise translate_not_found(exc) from exc
    # Engine work (build + settle + ping) is CPU-bound — keep the loop live.
    return await run_in_threadpool(grading.grade, topo, activity.checks)


@router.post(
    "/activities/{activity_id}/submit", response_model=GradeResult, status_code=201
)
async def submit_activity(
    activity_id: str, body: GradeSubmit, r: Annotated[MemoryRepository, Depends(repo)]
):
    """Grade a student's project and **persist** the attempt (NG-EDU-03).

    Unlike ``/grade`` (a pure, side-effect-free report) this records a
    :class:`GradeResult` — who took it, elapsed time, and whether that was
    within the activity's ``time_limit_s`` — so instructors can export results.
    """
    try:
        activity = await r.get_activity(activity_id)
        topo = await r.topology(body.project_id)
    except StoreNotFound as exc:
        raise translate_not_found(exc) from exc
    report = await run_in_threadpool(grading.grade, topo, activity.checks)
    within = None
    if body.elapsed_s is not None and activity.time_limit_s is not None:
        within = body.elapsed_s <= activity.time_limit_s
    result = GradeResult(
        id=new_id(),
        activity_id=activity_id,
        student=body.student,
        score_pct=report.score_pct,
        earned_weight=report.earned_weight,
        total_weight=report.total_weight,
        elapsed_s=body.elapsed_s,
        within_time=within,
        items=report.items,
    )
    return await r.add_grade_result(result)


@router.get("/activities/{activity_id}/results.csv")
async def export_results_csv(
    activity_id: str, r: Annotated[MemoryRepository, Depends(repo)]
):
    """Download all graded attempts for this activity as CSV (NG-EDU-03)."""
    try:
        await r.get_activity(activity_id)  # 404 if the activity is unknown
    except StoreNotFound as exc:
        raise translate_not_found(exc) from exc
    results = sorted(
        await r.list_grade_results(activity_id),
        key=lambda g: (g.graded_at, g.student),
    )
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(
        [
            "student",
            "score_pct",
            "earned_weight",
            "total_weight",
            "elapsed_s",
            "within_time",
            "graded_at",
        ]
    )
    for g in results:
        w.writerow(
            [
                g.student,
                g.score_pct,
                g.earned_weight,
                g.total_weight,
                "" if g.elapsed_s is None else g.elapsed_s,
                "" if g.within_time is None else g.within_time,
                g.graded_at.isoformat(),
            ]
        )
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="activity-{activity_id}-results.csv"'
        },
    )


@router.get("/activities/{activity_id}/export")
async def export_activity(
    activity_id: str, r: Annotated[MemoryRepository, Depends(repo)]
) -> dict:
    """Export one activity as a sharable ``.netgeo-lab`` envelope (NG-EDU-03)."""
    try:
        activity = await r.get_activity(activity_id)
    except StoreNotFound as exc:
        raise translate_not_found(exc) from exc
    return archive_svc.build_activity_archive(activity)


@router.post("/activities/import", response_model=Activity, status_code=201)
async def import_activity(body: dict, r: Annotated[MemoryRepository, Depends(repo)]):
    """Create a brand-new activity from a ``.netgeo-lab`` envelope, minting a
    fresh id (NG-EDU-03). Raises 422 on a malformed/wrong-format envelope."""
    activity = archive_svc.parse_activity_archive(body)  # ValidationError -> 422
    return await r.add_activity(activity.model_copy(update={"id": new_id()}))


@router.post(
    "/activities/{activity_id}/instantiate", response_model=Project, status_code=201
)
async def instantiate_activity(
    activity_id: str, r: Annotated[MemoryRepository, Depends(repo)]
):
    """Import the activity's ``initial`` network as a fresh project so a student
    gets the starting topology (reuses the NG-WS-03 archive import path)."""
    try:
        activity = await r.get_activity(activity_id)
    except StoreNotFound as exc:
        raise translate_not_found(exc) from exc
    parsed = archive_svc.parse_archive(activity.initial)  # 422 on a bad envelope
    project = await r.create_project(
        f"{activity.name} (attempt)", parsed["project"].description
    )
    remapped = archive_svc.remap(parsed, project.id)
    for site in remapped["sites"]:
        await r.add_site(site)
    for rack in remapped["racks"]:
        await r.add_rack(rack)
    for node in remapped["nodes"]:
        await r.add_node(node)
    for link in remapped["links"]:
        await r.add_link(link)
    for cable in remapped["cables"]:
        await r.add_cable(cable)
    for scenario in remapped["scenarios"]:
        await r.add_scenario(scenario)
    for config in remapped["configs"]:
        await r.add_config(config)
    return project
