"""RF planning endpoints (NG-RF-01) — propagation model registry.

A study picks a propagation model by id and supplies geometry + params; the
registry (``engine.propagation``) dispatches to a closed-form path-loss model.
``GET /api/rf/models`` lists the available models with their metadata;
``POST /api/rf/path-loss`` computes loss for a chosen model.

``GET /api/rf/radios`` (NG-RF follow-up) serves the static ISP radio catalog
(``app/data/device_catalog.json``, 23 devices with datasheet specs) so the
frontend can prefill Auto-Select/PtMP candidate fields instead of the user
typing tx power / antenna gain / rx sensitivity by hand.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.deps import repo, translate_not_found
from app.models import (
    CoverageRasterRequest,
    CoverageRasterResult,
    ElevationPoint,
    ElevationProfile,
    PathLossRequest,
    PathLossResult,
    ProductSelectRequest,
    ProductSelectResult,
    PropagationModelInfo,
    PtmpRequest,
    PtmpResult,
    PtpRequest,
    PtpResult,
    RfStudy,
    RfStudyCreate,
)
from app.services import elevation as esvc
from app.services import wireless as wsvc
from app.store import MemoryRepository
from app.store import NotFound as StoreNotFound
from app.utils.ids import new_id
from engine import wireless as rf

router = APIRouter(prefix="/rf", tags=["rf"])

# ponytail: one static JSON catalog, read once and cached — promote to the
# device-pack mechanism (network/devices/packs/) only if a second RF catalog
# source shows up; a single source doesn't earn that machinery.
_CATALOG_PATH = Path(__file__).resolve().parent.parent / "data" / "device_catalog.json"


class RadioCatalogEntry(BaseModel):
    id: str
    name: str
    vendor: str
    model: str
    type: str
    frequency_ghz: float
    tx_power_dbm: float
    antenna_gain_dbi: float
    rx_sensitivity_dbm: float
    max_range_km: float
    max_throughput_mbps: float
    standard: str
    notes: str = ""


@lru_cache(maxsize=1)
def _load_radio_catalog() -> list[dict]:
    data = json.loads(_CATALOG_PATH.read_text(encoding="utf-8"))
    return data["devices"]


@router.get("/radios", response_model=list[RadioCatalogEntry])
async def list_radios():
    """Static ISP radio catalog (real datasheet specs) for prefilling
    Auto-Select/PtMP candidate fields client-side. Read-only, no
    cost/bandwidth here — those stay user-supplied (see ``RadioCandidate``)."""
    return _load_radio_catalog()


@router.get("/models", response_model=list[PropagationModelInfo])
async def list_models():
    """List every registered propagation model with its metadata (valid
    frequency/distance ranges + tunable params)."""
    return wsvc.list_models()


@router.post("/path-loss", response_model=PathLossResult)
async def compute_path_loss(body: PathLossRequest):
    """Compute path loss (dB) for a chosen model. 422 on an unknown model id or
    a frequency outside the model's valid range."""
    try:
        loss = wsvc.path_loss(
            body.model_id,
            body.distance_m,
            body.freq_mhz,
            tx_height_m=body.tx_height_m,
            rx_height_m=body.rx_height_m,
            **body.params,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return PathLossResult(
        model_id=body.model_id,
        path_loss_db=round(loss, 2),
        distance_m=body.distance_m,
        freq_mhz=body.freq_mhz,
    )


@router.post("/ptp", response_model=PtpResult)
async def plan_ptp(body: PtpRequest):
    """Plan a point-to-point link (NG-RF-03): link budget via the propagation
    registry + terrain LoS/Fresnel verdict. Supply ``profile`` for an offline
    deterministic run, else terrain is fetched (flat-terrain fallback offline).
    Also returns a bidirectional budget (NG-RF-N2a): ``a_to_b``/``b_to_a`` with
    per-direction RSSI/SNR/MCS from each side's radio (``radio_a``/``radio_b``,
    falling back to the legacy shared fields when omitted). 422 on an unknown
    model id or out-of-range frequency."""
    distance_m = rf.haversine_m(body.a_lat, body.a_lon, body.b_lat, body.b_lon)
    try:
        budget = wsvc.ptp_budget(
            model_id=body.model_id,
            distance_m=distance_m,
            freq_mhz=body.freq_mhz,
            tx_height_m=body.tx_height_m,
            rx_height_m=body.rx_height_m,
            tx_power_dbm=body.tx_power_dbm,
            tx_gain_dbi=body.tx_gain_dbi,
            rx_gain_dbi=body.rx_gain_dbi,
            misc_loss_db=body.misc_loss_db,
            rx_sensitivity_dbm=body.rx_sensitivity_dbm,
            params=body.params,
            rain_rate_mm_hr=body.rain_rate_mm_hr,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if body.profile is not None:
        profile = [p.model_dump() for p in body.profile]
        out_profile = None
    else:
        # F67: fallback_to_flat=True keeps PtP planning usable on air-gapped /
        # offline installs when the DEM provider is unreachable, but the result
        # must say so — terrain_source below, surfaced by the frontend as a
        # visible "flat terrain" notice rather than presented as real DEM data.
        profile, terrain_source = await esvc.fetch_profile(
            body.a_lat,
            body.a_lon,
            body.b_lat,
            body.b_lon,
            body.samples,
            fallback_to_flat=True,
        )
        out_profile = ElevationProfile(
            samples=len(profile),
            total_distance_m=profile[-1]["distance_m"] if profile else 0.0,
            points=[ElevationPoint(**p) for p in profile],
            terrain_source=terrain_source,
        )

    los = esvc.analyse_los(
        distance_m=distance_m,
        frequency_ghz=body.freq_mhz / 1000.0,
        tx_height_m=body.tx_height_m,
        rx_height_m=body.rx_height_m,
        profile=profile,
    )
    link_ok = budget["rssi_dbm"] >= body.rx_sensitivity_dbm and los.fresnel_clear
    directions = wsvc.ptp_directions(body, budget["path_loss_db"])
    return PtpResult(
        model_id=body.model_id,
        distance_m=round(distance_m, 2),
        los_clear=los.los_clear,
        fresnel_clear=los.fresnel_clear,
        worst_obstruction_m=round(los.worst_obstruction_m, 2),
        min_clearance_ratio=round(los.min_clearance_ratio, 3),
        verdict="clear" if los.los_clear else "obstructed",
        link_ok=link_ok,
        profile=out_profile,
        **budget,
        **directions,
    )


@router.post("/coverage", response_model=CoverageRasterResult)
async def compute_coverage(body: CoverageRasterRequest):
    """Compute a best-server RSSI raster (NG-RF-02) over a bbox / centre+radius.
    Deterministic: identical request → identical raster + ``study_key``. 422 on
    the cell-cap overflow, bad geometry, unknown technology, or unknown model."""
    try:
        raster = wsvc.coverage_raster(body)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return CoverageRasterResult(**raster)


@router.post("/ptmp", response_model=PtmpResult)
async def plan_ptmp(body: PtmpRequest):
    """Plan an AP sector over its CPEs (NG-RF-04): per-CPE in-beam test, RSSI +
    MCS via the propagation registry, sector capacity roll-up. 422 on an unknown
    model, out-of-range freq, or a CPE lacking coords/distance+bearing."""
    try:
        result = wsvc.ptmp_plan(body)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return PtmpResult(**result)


@router.post("/product-select", response_model=ProductSelectResult)
async def select_product(body: ProductSelectRequest):
    """Rank candidate radio pairs for a required distance + target throughput
    (NG-RF-05), best margin-per-cost first. 422 on an unknown model or
    out-of-range frequency."""
    try:
        result = wsvc.product_select(body)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ProductSelectResult(**result)


# --- RF study persistence (R4 cross-cutting) ---------------------------------
# Save-on-demand only — no auto-cache/recompute. The client computes a study
# via the endpoints above, then POSTs the request+result here to snapshot it.
# Re-opening returns the stored result verbatim (PtP's elevation fetch can
# drift, so this is the only way a saved study "re-opens identically").


@router.post("/studies", response_model=RfStudy, status_code=201)
async def create_rf_study(
    body: RfStudyCreate, r: Annotated[MemoryRepository, Depends(repo)]
):
    return await r.add_rf_study(RfStudy(id=new_id(), **body.model_dump()))


@router.get("/studies", response_model=list[RfStudy])
async def list_rf_studies(
    project_id: str, r: Annotated[MemoryRepository, Depends(repo)]
):
    return await r.list_rf_studies(project_id)


@router.get("/studies/{study_id}", response_model=RfStudy)
async def get_rf_study(study_id: str, r: Annotated[MemoryRepository, Depends(repo)]):
    try:
        return await r.get_rf_study(study_id)
    except StoreNotFound as exc:
        raise translate_not_found(exc) from exc


@router.delete("/studies/{study_id}", status_code=200)
async def delete_rf_study(
    study_id: str, r: Annotated[MemoryRepository, Depends(repo)]
) -> dict:
    try:
        await r.delete_rf_study(study_id)
    except StoreNotFound as exc:
        raise translate_not_found(exc) from exc
    return {"deleted": study_id}
