"""Tests for the RF planning exit-gate features (NG-RF-02 / NG-RF-03).

NG-RF-03 PtP link planner: link budget via the propagation registry + terrain
LoS/Fresnel. NG-RF-02 coverage raster: best-server RSSI grid over a bbox.

The PtP acceptance case (5 GHz / 10 km / 30 dBm EIRP / clear LoS) is hand-checked
against the Friis FSPL budget below so a reviewer can verify the reference.
"""
from __future__ import annotations

import httpx
import pytest

from app.models import CoverageRasterRequest, CoverageSite
from app.services import elevation as esvc
from app.services import wireless as wsvc
from engine import wireless as rf

# --- NG-RF-03 acceptance reference (hand calculation) -----------------------
# FSPL(dB) = 20log10(d_m) + 20log10(f_Hz) - 147.55.  d = 10 km, f = 5 GHz:
#   20log10(10000)      = 80.000
#   20log10(5e9)        = 193.979
#   20log10(4pi/c)      = -147.551
#   FSPL                = 126.428 dB
# EIRP 30 dBm, Grx 0, Lmisc 0  =>  RSSI = 30 - 126.428 = -96.43 dBm.
_AC_RSSI_DBM = -96.43

# Two points ~10 km apart along the equator (dlon = 10000 / 111320 deg).
_A = (0.0, 0.0)
_B = (0.0, 0.0898315)

# Flat, clear-LoS terrain profile (elevation 0 at both ends).
_FLAT_PROFILE = [
    {"lat": 0.0, "lon": 0.0, "elevation_m": 0.0, "distance_m": 0.0},
    {"lat": 0.0, "lon": 0.09, "elevation_m": 0.0, "distance_m": 10000.0},
]
# Obstructed profile: a 500 m ridge mid-path towers over the ~7.5 m LoS line.
_HILL_PROFILE = [
    {"lat": 0.0, "lon": 0.0, "elevation_m": 0.0, "distance_m": 0.0},
    {"lat": 0.0, "lon": 0.045, "elevation_m": 500.0, "distance_m": 5000.0},
    {"lat": 0.0, "lon": 0.09, "elevation_m": 0.0, "distance_m": 10000.0},
]


# --- NG-RF-03 PtP -----------------------------------------------------------
def test_ptp_budget_matches_hand_fspl():
    """Pure service: 5 GHz / 10 km / 30 dBm EIRP RSSI within +/-2 dB of hand FSPL."""
    dist = rf.haversine_m(*_A, *_B)
    b = wsvc.ptp_budget(
        model_id="fspl", distance_m=dist, freq_mhz=5000.0,
        tx_height_m=10.0, rx_height_m=5.0,
        tx_power_dbm=30.0, tx_gain_dbi=0.0, rx_gain_dbi=0.0,
        misc_loss_db=0.0, rx_sensitivity_dbm=-90.0, params={},
    )
    assert b["eirp_dbm"] == pytest.approx(30.0)
    assert b["rssi_dbm"] == pytest.approx(_AC_RSSI_DBM, abs=2.0)


async def test_ptp_endpoint_ac_clear_los(client):
    """NG-RF-03 AC: 5 GHz / 10 km / 30 dBm EIRP / clear LoS → RSSI +/-2 dB, link_ok."""
    resp = await client.post(
        "/api/rf/ptp",
        json={
            "a_lat": _A[0], "a_lon": _A[1], "b_lat": _B[0], "b_lon": _B[1],
            "freq_mhz": 5000.0, "tx_power_dbm": 30.0, "tx_gain_dbi": 0.0,
            "rx_gain_dbi": 0.0, "misc_loss_db": 0.0, "rx_sensitivity_dbm": -100.0,
            "tx_height_m": 10.0, "rx_height_m": 5.0, "model_id": "fspl",
            "profile": _FLAT_PROFILE,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["rssi_dbm"] == pytest.approx(_AC_RSSI_DBM, abs=2.0)
    assert body["los_clear"] is True
    assert body["verdict"] == "clear"
    assert body["link_ok"] is True


async def test_ptp_endpoint_obstructed_is_nlos(client):
    """An obstructing ridge flags NLOS (verdict obstructed, link not ok)."""
    resp = await client.post(
        "/api/rf/ptp",
        json={
            "a_lat": _A[0], "a_lon": _A[1], "b_lat": _B[0], "b_lon": _B[1],
            "freq_mhz": 5000.0, "tx_power_dbm": 30.0, "tx_height_m": 10.0,
            "rx_height_m": 5.0, "model_id": "fspl", "profile": _HILL_PROFILE,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["los_clear"] is False
    assert body["fresnel_clear"] is False
    assert body["verdict"] == "obstructed"
    assert body["link_ok"] is False
    assert body["worst_obstruction_m"] > 0


class _DeadProviderClient:
    """Fakes the elevation provider being unreachable (F67 regression guard) —
    no real network call, no `httpx` mutation: only ``app.services.elevation``'s
    own module-local ``httpx`` name is swapped for the duration of the test."""

    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, *a, **kw):
        raise httpx.ConnectError("simulated provider outage")


class _FakeHttpxModule:
    AsyncClient = _DeadProviderClient
    HTTPError = httpx.HTTPError


async def test_ptp_endpoint_flags_flat_fallback_when_provider_down(client, monkeypatch):
    """F67: when no ``profile`` is supplied and the DEM provider is down, the
    endpoint still returns 200 (offline-tolerant) but must mark the terrain as
    ``flat_fallback`` — never presented as real DEM data."""
    monkeypatch.setattr(esvc, "httpx", _FakeHttpxModule)
    resp = await client.post(
        "/api/rf/ptp",
        json={
            "a_lat": _A[0], "a_lon": _A[1], "b_lat": _B[0], "b_lon": _B[1],
            "freq_mhz": 5000.0, "tx_power_dbm": 30.0, "tx_height_m": 10.0,
            "rx_height_m": 5.0, "model_id": "fspl",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["profile"]["terrain_source"] == "flat_fallback"
    assert all(p["elevation_m"] == 0.0 for p in body["profile"]["points"])


async def test_ptp_endpoint_unknown_model_422(client):
    resp = await client.post(
        "/api/rf/ptp",
        json={"a_lat": _A[0], "a_lon": _A[1], "b_lat": _B[0], "b_lon": _B[1],
              "freq_mhz": 5000.0, "model_id": "itm", "profile": _FLAT_PROFILE},
    )
    assert resp.status_code == 422


async def test_ptp_endpoint_out_of_range_freq_422(client):
    # Okumura-Hata tops out at 1500 MHz; 5000 MHz must be rejected.
    resp = await client.post(
        "/api/rf/ptp",
        json={"a_lat": _A[0], "a_lon": _A[1], "b_lat": _B[0], "b_lon": _B[1],
              "freq_mhz": 5000.0, "model_id": "okumura_hata", "profile": _FLAT_PROFILE},
    )
    assert resp.status_code == 422


# --- NG-RF-02 coverage raster ----------------------------------------------
def _cov_req(**over) -> CoverageRasterRequest:
    base = {
        "sites": [CoverageSite(lat=0.0, lon=0.0)],
        "technology": "wifi_5ghz", "model_id": "fspl", "rows": 11, "cols": 11,
        "min_lat": -0.05, "min_lon": -0.05, "max_lat": 0.05, "max_lon": 0.05,
    }
    base.update(over)
    return CoverageRasterRequest(**base)


def test_coverage_signal_decreases_with_distance():
    raster = wsvc.coverage_raster(_cov_req())
    vals = raster["values"]
    centre = vals[5][5]          # cell centred on the site at (0,0)
    corner = vals[0][0]          # farthest cell
    assert centre > corner


def test_coverage_cell_cap_guard():
    with pytest.raises(ValueError):
        wsvc.coverage_raster(_cov_req(rows=100, cols=100))  # 10000 > 4096


async def test_coverage_cell_cap_returns_422(client):
    resp = await client.post(
        "/api/rf/coverage",
        json={
            "sites": [{"lat": 0.0, "lon": 0.0}], "technology": "wifi_5ghz",
            "rows": 100, "cols": 100,
            "min_lat": -0.05, "min_lon": -0.05, "max_lat": 0.05, "max_lon": 0.05,
        },
    )
    assert resp.status_code == 422


def test_coverage_is_deterministic():
    a = wsvc.coverage_raster(_cov_req())
    b = wsvc.coverage_raster(_cov_req())
    assert a["values"] == b["values"]
    assert a["study_key"] == b["study_key"]


def test_coverage_best_server_picks_strongest():
    # Two co-located sites; A dominates everywhere, so [A,B] == [A] alone.
    strong = CoverageSite(lat=0.0, lon=0.0, tx_power_dbm=40.0)
    weak = CoverageSite(lat=0.0, lon=0.0, tx_power_dbm=-20.0)
    both = wsvc.coverage_raster(_cov_req(sites=[strong, weak]))
    only_strong = wsvc.coverage_raster(_cov_req(sites=[strong]))
    assert both["values"] == only_strong["values"]
    assert both["site_count"] == 2


def test_coverage_center_radius_bounds():
    # Centre+radius derives a bbox; raster still computes.
    req = CoverageRasterRequest(
        sites=[CoverageSite(lat=0.0, lon=0.0)], technology="lte", rows=8, cols=8,
        center_lat=0.0, center_lon=0.0, radius_m=5000.0, model_id="fspl",
    )
    raster = wsvc.coverage_raster(req)
    assert raster["rows"] == 8 and raster["cols"] == 8
    assert raster["bounds"]["max_lat"] > raster["bounds"]["min_lat"]


async def test_coverage_endpoint_ok(client):
    resp = await client.post(
        "/api/rf/coverage",
        json={
            "sites": [{"lat": 0.0, "lon": 0.0}], "technology": "wifi_5ghz",
            "model_id": "fspl", "rows": 8, "cols": 8,
            "min_lat": -0.05, "min_lon": -0.05, "max_lat": 0.05, "max_lon": 0.05,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["values"]) == 8 and len(body["values"][0]) == 8
    assert body["legend"] and body["study_key"]


async def test_coverage_endpoint_unknown_model_422(client):
    resp = await client.post(
        "/api/rf/coverage",
        json={"sites": [{"lat": 0.0, "lon": 0.0}], "model_id": "itm",
              "min_lat": -0.05, "min_lon": -0.05, "max_lat": 0.05, "max_lon": 0.05},
    )
    assert resp.status_code == 422


async def test_coverage_endpoint_bad_geometry_422(client):
    # Neither a bbox nor centre+radius supplied.
    resp = await client.post(
        "/api/rf/coverage",
        json={"sites": [{"lat": 0.0, "lon": 0.0}], "technology": "wifi_5ghz"},
    )
    assert resp.status_code == 422


def test_ac_reference_value():
    # Guard the documented AC number itself against drift.
    fspl = rf.fspl_db(10_000.0, 5e9)
    assert (30.0 - fspl) == pytest.approx(_AC_RSSI_DBM, abs=0.05)
    assert fspl == pytest.approx(126.43, abs=0.05)
