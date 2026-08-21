"""Wireless planning service — adapts stored geo nodes to the RF engine.

Pulls nodes that carry a geographic position (``lat``/``lon``) from a project,
maps each to an ``engine.wireless.GeoDevice``, and runs the deterministic
planner / link-budget primitives. Like ``services/sim.py`` this is the seam
between the persistence schema (``app.models``) and the engine's own shapes.

Unit conventions: the schema's :class:`Radio` and the engine's ``Radio`` use the
*same* units (dBm / GHz / dBi / MHz / metres), so the mapping is field-for-field
with no conversion.
"""
from __future__ import annotations

import hashlib
import json
import math

from app.models import (
    CoverageCircle,
    CoverageRasterRequest,
    Node,
    ProductSelectRequest,
    PtmpRequest,
    PtpRadio,
    PtpRequest,
    Radio,
    Topology,
    WirelessLink,
    WirelessPlanResult,
)
from engine import propagation as prop
from engine import wireless as rf

# How a stored NodeKind maps onto an engine planner "role".
_ROLE_BY_KIND = {
    "ap": "ap",
    "olt": "tower",
    "host": "cpe",
    "server": "cpe",
    "router": "router",
    "switch": "switch",
    "firewall": "router",
}


def _to_engine_radio(r: Radio | None) -> rf.Radio:
    if r is None:
        return rf.Radio()
    return rf.Radio(
        tx_power_dbm=r.tx_power_dbm,
        frequency_ghz=r.frequency_ghz,
        antenna_gain_dbi=r.antenna_gain_dbi,
        bandwidth_mhz=r.bandwidth_mhz,
        rx_sensitivity_dbm=r.rx_sensitivity_dbm,
        misc_loss_db=r.misc_loss_db,
        max_range_m=r.max_range_m,
    )


def _role_for(node: Node) -> str:
    kind = str(node.kind)
    # A node placed on a tower position is modelled by kind=ap with a tower
    # intent flag; default to the kind→role table otherwise.
    if node.intent and node.intent.get("map_role") == "tower":
        return "tower"
    return _ROLE_BY_KIND.get(kind, "cpe")


def _geo_devices(nodes: list[Node]) -> list[rf.GeoDevice]:
    out: list[rf.GeoDevice] = []
    for n in nodes:
        if n.lat is None or n.lon is None:
            continue
        out.append(
            rf.GeoDevice(
                id=n.id,
                role=_role_for(n),
                lat=n.lat,
                lon=n.lon,
                radio=_to_engine_radio(n.radio),
            )
        )
    return out


def plan_topology(topo: Topology) -> WirelessPlanResult:
    """Run the RF planner over a project's geo-placed nodes.

    Returns the planned wireless links (with full link budgets) plus a coverage
    circle per serving node, ready for the map to render."""
    geo = _geo_devices(topo.nodes)
    planned = rf.plan_links(geo)
    links = [
        WirelessLink(
            a_id=p.a_id,
            b_id=p.b_id,
            distance_m=round(p.budget.distance_m, 2),
            fspl_db=round(p.budget.fspl_db, 2),
            rssi_dbm=round(p.budget.rssi_dbm, 2),
            margin_db=round(p.budget.margin_db, 2),
            noise_floor_dbm=round(p.budget.noise_floor_dbm, 2),
            snr_db=round(p.budget.snr_db, 2),
            quality=p.budget.quality.value,
            feasible=p.budget.feasible,
        )
        for p in planned
    ]

    coverage: list[CoverageCircle] = []
    for g in geo:
        if g.role in ("ap", "tower"):
            radius = rf.max_range_m(g.radio, g.radio)
            coverage.append(
                CoverageCircle(node_id=g.id, lat=g.lat, lon=g.lon, radius_m=round(radius, 1))
            )

    return WirelessPlanResult(project_id=topo.project.id, links=links, coverage=coverage)


def coverage_radius(node: Node) -> float:
    """Theoretical coverage radius (m) of a single serving node."""
    radio = _to_engine_radio(node.radio)
    return round(rf.max_range_m(radio, radio), 1)


def link_budget_between(
    tx: Radio,
    rx: Radio | None,
    *,
    distance_m: float | None = None,
    a_lat: float | None = None,
    a_lon: float | None = None,
    b_lat: float | None = None,
    b_lon: float | None = None,
    rain_rate_mm_hr: float = 0.0,
) -> rf.LinkBudget:
    """Compute a single point-to-point budget. Distance is taken from
    ``distance_m`` if given, else derived from the two coordinate pairs."""
    etx = _to_engine_radio(tx)
    erx = _to_engine_radio(rx) if rx is not None else etx
    if distance_m is None:
        if None in (a_lat, a_lon, b_lat, b_lon):
            raise ValueError("provide distance_m or both endpoint coordinates")
        distance_m = rf.haversine_m(a_lat, a_lon, b_lat, b_lon)  # type: ignore[arg-type]
    return rf.link_budget(etx, erx, distance_m, rain_rate_mm_hr)


def list_models() -> list[dict]:
    """Registry metadata for every propagation model (NG-RF-01)."""
    return prop.list_models()


def path_loss(
    model_id: str,
    distance_m: float,
    freq_mhz: float,
    tx_height_m: float = 30.0,
    rx_height_m: float = 1.5,
    **params: object,
) -> float:
    """Path loss (dB) from the chosen propagation model. Raises ``ValueError``
    on an unknown model id or out-of-range frequency."""
    return prop.path_loss(
        model_id, distance_m, freq_mhz, tx_height_m, rx_height_m, **params
    )


# --- NG-RF-03 point-to-point link budget -----------------------------------
def ptp_budget(
    *,
    model_id: str,
    distance_m: float,
    freq_mhz: float,
    tx_height_m: float,
    rx_height_m: float,
    tx_power_dbm: float,
    tx_gain_dbi: float,
    rx_gain_dbi: float,
    misc_loss_db: float,
    rx_sensitivity_dbm: float,
    params: dict,
) -> dict:
    """PtP link budget with path loss taken THROUGH the propagation registry.

        EIRP = Ptx + Gtx
        RSSI = EIRP + Grx - path_loss(model) - Lmisc

    Raises ``ValueError`` (→ HTTP 422) on an unknown model or out-of-range freq.
    """
    loss = prop.path_loss(model_id, distance_m, freq_mhz, tx_height_m, rx_height_m, **params)
    eirp = tx_power_dbm + tx_gain_dbi
    rssi = eirp + rx_gain_dbi - loss - misc_loss_db
    return {
        "eirp_dbm": round(eirp, 2),
        "path_loss_db": round(loss, 2),
        "rssi_dbm": round(rssi, 2),
        "fade_margin_db": round(rssi - rx_sensitivity_dbm, 2),
    }


def _ptp_direction(
    tx_power_dbm: float,
    tx_gain_dbi: float,
    rx_gain_dbi: float,
    rx_sensitivity_dbm: float,
    rx_bandwidth_mhz: float,
    path_loss_db: float,
    misc_loss_db: float,
) -> dict:
    """One direction of a PtP link, given a path loss already computed
    (direction-agnostic, see :func:`ptp_budget`). Noise floor and MCS use the
    *receiving* side's bandwidth."""
    eirp = tx_power_dbm + tx_gain_dbi
    rssi = eirp + rx_gain_dbi - path_loss_db - misc_loss_db
    noise = rf.noise_floor_dbm(rx_bandwidth_mhz)
    return {
        "tx_power_dbm": round(tx_power_dbm, 2),
        "eirp_dbm": round(eirp, 2),
        "rssi_dbm": round(rssi, 2),
        "noise_floor_dbm": round(noise, 2),
        "snr_db": round(rssi - noise, 2),
        "fade_margin_db": round(rssi - rx_sensitivity_dbm, 2),
        **mcs_for_rssi(rssi, rx_bandwidth_mhz),
    }


def ptp_directions(req: PtpRequest, path_loss_db: float) -> dict:
    """Bidirectional PtP budget (NG-RF-N2a): A->B and B->A, reusing the
    already-computed (direction-agnostic) path loss. ``req.radio_a``/
    ``radio_b`` give each side its own tx power/gain/bandwidth/sensitivity;
    any left unset (including an old request with neither supplied) falls
    back to the legacy shared ``tx_*``/``rx_*`` fields, i.e. a symmetric
    radio on both ends."""
    a, b = req.radio_a or PtpRadio(), req.radio_b or PtpRadio()
    a_tx = a.tx_power_dbm if a.tx_power_dbm is not None else req.tx_power_dbm
    a_gain = a.gain_dbi if a.gain_dbi is not None else req.tx_gain_dbi
    a_sens = a.sensitivity_dbm if a.sensitivity_dbm is not None else req.rx_sensitivity_dbm
    b_tx = b.tx_power_dbm if b.tx_power_dbm is not None else req.tx_power_dbm
    b_gain = b.gain_dbi if b.gain_dbi is not None else req.rx_gain_dbi
    b_sens = b.sensitivity_dbm if b.sensitivity_dbm is not None else req.rx_sensitivity_dbm
    return {
        "a_to_b": _ptp_direction(
            a_tx, a_gain, b_gain, b_sens, b.bandwidth_mhz, path_loss_db, req.misc_loss_db
        ),
        "b_to_a": _ptp_direction(
            b_tx, b_gain, a_gain, a_sens, a.bandwidth_mhz, path_loss_db, req.misc_loss_db
        ),
        "bearing_a_to_b": round(_bearing_deg(req.a_lat, req.a_lon, req.b_lat, req.b_lon), 2),
        "bearing_b_to_a": round(_bearing_deg(req.b_lat, req.b_lon, req.a_lat, req.a_lon), 2),
    }


# --- NG-RF-02 coverage raster ----------------------------------------------
# Cap on raster cells: bounds the sites×cells compute so a synchronous study
# stays within the request budget. 64×64. ponytail: synchronous bounded compute
# (a pure function of inputs is deterministic → cacheable via study_key); an
# async job queue is a follow-up only if bigger rasters are needed.
MAX_COVERAGE_CELLS = 4096

# Per-technology radio defaults (carrier / tx power / antenna gain). Sites may
# override any of these; sensitivity is informational for the caller's legend.
_TECH_PRESETS: dict[str, dict[str, float]] = {
    "wifi_2ghz":  {"freq_mhz": 2400.0,  "tx_power_dbm": 20.0, "tx_gain_dbi": 3.0},
    "wifi_5ghz":  {"freq_mhz": 5000.0,  "tx_power_dbm": 23.0, "tx_gain_dbi": 5.0},
    "wifi_6ghz":  {"freq_mhz": 6000.0,  "tx_power_dbm": 23.0, "tx_gain_dbi": 5.0},
    "lte":        {"freq_mhz": 1800.0,  "tx_power_dbm": 43.0, "tx_gain_dbi": 15.0},
    "wifi_60ghz": {"freq_mhz": 60000.0, "tx_power_dbm": 10.0, "tx_gain_dbi": 25.0},
}

# Legend thresholds mirror engine.wireless.LinkQuality so map + engine agree.
_COVERAGE_LEGEND = [
    {"label": "excellent", "min_dbm": -55.0},
    {"label": "good", "min_dbm": -70.0},
    {"label": "fair", "min_dbm": -80.0},
    {"label": "weak", "min_dbm": -95.0},
]


def _resolve_bounds(req: CoverageRasterRequest) -> tuple[float, float, float, float]:
    """(min_lat, min_lon, max_lat, max_lon) from an explicit bbox or centre+radius."""
    if None not in (req.min_lat, req.min_lon, req.max_lat, req.max_lon):
        lo_lat, hi_lat = sorted((req.min_lat, req.max_lat))  # type: ignore[arg-type]
        lo_lon, hi_lon = sorted((req.min_lon, req.max_lon))  # type: ignore[arg-type]
        if lo_lat == hi_lat or lo_lon == hi_lon:
            raise ValueError("bounding box has zero area")
        return lo_lat, lo_lon, hi_lat, hi_lon
    if None not in (req.center_lat, req.center_lon, req.radius_m):
        dlat = req.radius_m / 111_320.0  # type: ignore[operator]
        dlon = req.radius_m / (111_320.0 * max(math.cos(math.radians(req.center_lat)), 1e-6))  # type: ignore[operator,arg-type]
        return (req.center_lat - dlat, req.center_lon - dlon,  # type: ignore[operator]
                req.center_lat + dlat, req.center_lon + dlon)  # type: ignore[operator]
    raise ValueError("provide a bbox (min/max lat/lon) or center+radius")


def _study_key(req: CoverageRasterRequest) -> str:
    """Deterministic cache key: sha256 of the canonical request JSON. Same
    request → same key AND same raster (feeds the R4 'study re-opens identically'
    exit criterion)."""
    canonical = json.dumps(req.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def coverage_raster(req: CoverageRasterRequest) -> dict:
    """Compute a best-server RSSI raster (dBm per cell) over the request's bbox.

    O(sites × cells), bounded by ``MAX_COVERAGE_CELLS``. Great-circle distance
    per cell; path loss via the registry so ``model_id`` is honoured. Raises
    ``ValueError`` (→ 422) on cap overflow, bad geometry, unknown technology, or
    an unknown/out-of-range propagation model."""
    rows, cols = req.rows, req.cols
    if rows * cols > MAX_COVERAGE_CELLS:
        raise ValueError(
            f"grid {rows}x{cols} = {rows * cols} cells exceeds cap {MAX_COVERAGE_CELLS}"
        )
    preset = _TECH_PRESETS.get(req.technology)
    if preset is None:
        raise ValueError(f"unknown technology: {req.technology!r}")
    min_lat, min_lon, max_lat, max_lon = _resolve_bounds(req)

    # Resolve each site's radio once (preset defaults + per-site overrides).
    sites = [
        (
            s.lat, s.lon, s.height_m,
            preset["tx_power_dbm"] if s.tx_power_dbm is None else s.tx_power_dbm,
            preset["freq_mhz"] if s.freq_mhz is None else s.freq_mhz,
            preset["tx_gain_dbi"] if s.tx_gain_dbi is None else s.tx_gain_dbi,
        )
        for s in req.sites
    ]

    values: list[list[float]] = []
    for r in range(rows):
        lat = min_lat + (r + 0.5) / rows * (max_lat - min_lat)  # cell-centre latitude
        row_vals: list[float] = []
        for c in range(cols):
            lon = min_lon + (c + 0.5) / cols * (max_lon - min_lon)
            best = -math.inf
            for slat, slon, sh, sp, sf, sg in sites:
                dist = rf.haversine_m(slat, slon, lat, lon)
                loss = prop.path_loss(req.model_id, dist, sf, sh, req.rx_height_m, **req.params)
                rssi = sp + sg + req.rx_gain_dbi - loss - req.misc_loss_db
                best = max(best, rssi)
            row_vals.append(round(best, 2))
        values.append(row_vals)

    return {
        "model_id": req.model_id,
        "technology": req.technology,
        "rows": rows,
        "cols": cols,
        "bounds": {"min_lat": min_lat, "min_lon": min_lon,
                   "max_lat": max_lat, "max_lon": max_lon},
        "values": values,
        "legend": _COVERAGE_LEGEND,
        "study_key": _study_key(req),
        "site_count": len(sites),
    }


# --- NG-RF-04 / NG-RF-05 modulation ladder ---------------------------------
# Monotonic RSSI(dBm) floor → (MCS index, modulation, spectral efficiency in
# bits/s/Hz). A documented, simplified 802.11-style ladder (BPSK…256QAM);
# throughput = spectral_eff × bandwidth_mhz is an *idealised PHY rate* (no
# guard-interval / MAC overhead). ponytail: closed-form ladder — swap for a
# per-standard table only if certification-grade rates are ever needed.
_MCS_LADDER: list[tuple[float, int, str, float]] = [
    (-57.0, 9, "256QAM 5/6", 6.67),
    (-59.0, 8, "256QAM 3/4", 6.00),
    (-64.0, 7, "64QAM 5/6",  5.00),
    (-65.0, 6, "64QAM 3/4",  4.50),
    (-66.0, 5, "64QAM 2/3",  4.00),
    (-70.0, 4, "16QAM 3/4",  3.00),
    (-74.0, 3, "16QAM 1/2",  2.00),
    (-77.0, 2, "QPSK 3/4",   1.50),
    (-79.0, 1, "QPSK 1/2",   1.00),
    (-82.0, 0, "BPSK 1/2",   0.50),
]


def mcs_for_rssi(rssi_dbm: float, bandwidth_mhz: float) -> dict:
    """Highest MCS whose RSSI floor ≤ ``rssi_dbm``. Below the lowest floor the
    link carries no data (mcs=None, 0 Mbps). Monotonic: lower RSSI → lower MCS."""
    for floor, mcs, mod, se in _MCS_LADDER:
        if rssi_dbm >= floor:
            return {"mcs": mcs, "modulation": mod,
                    "throughput_mbps": round(se * bandwidth_mhz, 2)}
    return {"mcs": None, "modulation": None, "throughput_mbps": 0.0}


def _bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial great-circle bearing from point 1 → point 2, degrees [0, 360)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return math.degrees(math.atan2(y, x)) % 360.0


def _angular_diff(a: float, b: float) -> float:
    """Smallest absolute angle between two bearings, degrees [0, 180]."""
    d = abs(a - b) % 360.0
    return d if d <= 180.0 else 360.0 - d


def ptmp_plan(req: PtmpRequest) -> dict:
    """Plan one AP sector over its CPEs (NG-RF-04).

    Per CPE: resolve distance+bearing (explicit or from coords), test in-beam
    (bearing within azimuth ± beamwidth/2), path loss THROUGH the registry, RSSI
    = Ptx + Gtx + Grx − loss − Lmisc, then an MCS from :func:`mcs_for_rssi`.

    Sector capacity rolls up served CPEs two ways: ``sum_phy_mbps`` (contention-
    free upper bound) and ``airtime_fair_mbps`` (mean served rate = equal-airtime
    share of one shared medium). Raises ``ValueError`` (→ 422) on an unknown
    model, out-of-range freq, or a CPE missing both coords and distance+bearing.
    """
    half = req.beamwidth_deg / 2.0
    cpes: list[dict] = []
    served_rates: list[float] = []
    for c in req.cpes:
        if c.distance_m is not None and c.bearing_deg is not None:
            dist, brg = c.distance_m, c.bearing_deg
        elif c.lat is not None and c.lon is not None:
            dist = rf.haversine_m(req.lat, req.lon, c.lat, c.lon)
            brg = _bearing_deg(req.lat, req.lon, c.lat, c.lon)
        else:
            raise ValueError(f"cpe {c.id!r}: provide lat/lon or distance_m+bearing_deg")
        in_beam = _angular_diff(brg, req.azimuth_deg) <= half
        loss = prop.path_loss(
            req.model_id, dist, req.freq_mhz, req.height_m, c.height_m, **req.params
        )
        rssi = req.tx_power_dbm + req.tx_gain_dbi + c.rx_gain_dbi - loss - c.misc_loss_db
        mcs = mcs_for_rssi(rssi, req.bandwidth_mhz)
        served = in_beam and mcs["mcs"] is not None and rssi >= req.rx_sensitivity_dbm
        if served:
            served_rates.append(mcs["throughput_mbps"])
        cpes.append({
            "cpe_id": c.id,
            "distance_m": round(dist, 2),
            "bearing_deg": round(brg, 2),
            "in_beam": in_beam,
            "path_loss_db": round(loss, 2),
            "rssi_dbm": round(rssi, 2),
            "served": served,
            **mcs,
        })
    return {
        "model_id": req.model_id,
        "azimuth_deg": req.azimuth_deg,
        "beamwidth_deg": req.beamwidth_deg,
        "freq_mhz": req.freq_mhz,
        "bandwidth_mhz": req.bandwidth_mhz,
        "cpes": cpes,
        "served_count": len(served_rates),
        "sum_phy_mbps": round(sum(served_rates), 2),
        "airtime_fair_mbps": round(sum(served_rates) / len(served_rates), 2)
        if served_rates else 0.0,
    }


def product_select(req: ProductSelectRequest) -> dict:
    """Rank candidate radio pairs for a link (NG-RF-05).

    Symmetric pair (same model both ends): RSSI = Ptx + 2·Gant − loss − Lmisc;
    margin = RSSI − sensitivity. Ranked best margin-per-cost first (name breaks
    ties → deterministic). ``meets_target`` = link closes AND predicted PHY
    throughput ≥ target. Loss is identical for every candidate (same geometry),
    so it's computed once — an unknown model / out-of-range freq raises
    ``ValueError`` (→ 422) here."""
    loss = prop.path_loss(
        req.model_id, req.distance_m, req.freq_mhz,
        req.tx_height_m, req.rx_height_m, **req.params
    )
    ranked: list[dict] = []
    for c in req.candidates:
        rssi = c.tx_power_dbm + 2.0 * c.antenna_gain_dbi - loss - req.misc_loss_db
        margin = rssi - c.rx_sensitivity_dbm
        mcs = mcs_for_rssi(rssi, c.bandwidth_mhz)
        link_closes = margin >= 0.0 and mcs["mcs"] is not None
        meets = link_closes and mcs["throughput_mbps"] >= req.target_throughput_mbps
        ranked.append({
            "name": c.name,
            "rssi_dbm": round(rssi, 2),
            "margin_db": round(margin, 2),
            "predicted_throughput_mbps": mcs["throughput_mbps"],
            "cost": c.cost,
            "margin_per_cost": round(margin / c.cost, 4),
            "link_closes": link_closes,
            "meets_target": meets,
        })
    ranked.sort(key=lambda r: (-r["margin_per_cost"], r["name"]))
    return {
        "distance_m": req.distance_m,
        "freq_mhz": req.freq_mhz,
        "target_throughput_mbps": req.target_throughput_mbps,
        "ranked": ranked,
    }


__all__ = [
    "MAX_COVERAGE_CELLS",
    "coverage_radius",
    "coverage_raster",
    "link_budget_between",
    "list_models",
    "mcs_for_rssi",
    "path_loss",
    "plan_topology",
    "product_select",
    "ptmp_plan",
    "ptp_budget",
]
