"""Tests for the R4 exit features NG-RF-04 (PtMP sector) and NG-RF-05 (auto
product selection).

PtMP: a CPE in the sector beam gets a valid RSSI + MCS; one off-azimuth is
flagged out-of-beam; a farther CPE never gets a higher MCS (monotonic); sector
capacity rolls up. Product-select: ranking is by margin-per-cost and
deterministic; a link that can't meet the target throughput is flagged; unknown
model / empty candidate list → 422.
"""
from __future__ import annotations

import pytest

from app.models import (
    ProductSelectRequest,
    PtmpCpe,
    PtmpRequest,
    RadioCandidate,
)
from app.services import wireless as wsvc
from engine.wireless import sector_gain_dbi


# --- NG-RF-04 PtMP ----------------------------------------------------------
def _ptmp(**over) -> PtmpRequest:
    base = {
        "lat": 0.0, "lon": 0.0, "height_m": 30.0, "azimuth_deg": 0.0, "beamwidth_deg": 60.0,
        "freq_mhz": 5000.0, "bandwidth_mhz": 20.0, "tx_power_dbm": 23.0, "tx_gain_dbi": 16.0,
        "rx_sensitivity_dbm": -85.0, "model_id": "fspl",
        "cpes": [PtmpCpe(id="c1", distance_m=500.0, bearing_deg=0.0)],
    }
    base.update(over)
    return PtmpRequest(**base)


def test_ptmp_in_beam_cpe_has_rssi_and_mcs():
    r = wsvc.ptmp_plan(_ptmp())
    c = r["cpes"][0]
    assert c["in_beam"] is True
    assert c["served"] is True
    assert c["mcs"] is not None
    assert c["throughput_mbps"] > 0
    assert c["rssi_dbm"] >= -85.0


def test_ptmp_out_of_beam_flagged():
    # RF-2: in_beam is now "gain above the TR 38.901 sidelobe floor (A_m=30dB)",
    # not an angle cutoff — the main lobe rolls off past beamwidth/2 rather
    # than cutting off sharply, so due-east (bearing 90, ±30 half-beamwidth)
    # still has -11 dBi gain (above the -14 dBi floor) and reads in_beam=True.
    # Directly behind the sector (bearing 180) is where gain bottoms out at
    # the A_m floor, which is the case this test actually wants.
    r = wsvc.ptmp_plan(_ptmp(
        cpes=[PtmpCpe(id="rear", distance_m=500.0, bearing_deg=180.0)]
    ))
    c = r["cpes"][0]
    assert c["in_beam"] is False
    assert c["served"] is False
    assert r["served_count"] == 0


def test_ptmp_farther_cpe_not_higher_mcs():
    # Both in beam (bearing 0); farther CPE has weaker RSSI → MCS is monotone.
    r = wsvc.ptmp_plan(_ptmp(cpes=[
        PtmpCpe(id="near", distance_m=300.0, bearing_deg=0.0),
        PtmpCpe(id="far", distance_m=6000.0, bearing_deg=0.0),
    ]))
    near = next(c for c in r["cpes"] if c["cpe_id"] == "near")
    far = next(c for c in r["cpes"] if c["cpe_id"] == "far")
    assert near["rssi_dbm"] > far["rssi_dbm"]
    # Farther → same-or-lower MCS (None sorts as "no service").
    near_mcs = near["mcs"] if near["mcs"] is not None else -1
    far_mcs = far["mcs"] if far["mcs"] is not None else -1
    assert far_mcs <= near_mcs


def test_ptmp_capacity_rollup():
    r = wsvc.ptmp_plan(_ptmp(cpes=[
        PtmpCpe(id="a", distance_m=300.0, bearing_deg=0.0),
        PtmpCpe(id="b", distance_m=800.0, bearing_deg=10.0),
    ]))
    served = [c for c in r["cpes"] if c["served"]]
    assert r["served_count"] == len(served)
    assert r["sum_phy_mbps"] == round(sum(c["throughput_mbps"] for c in served), 2)
    # airtime-fair = mean served rate ≤ contention-free sum.
    assert r["airtime_fair_mbps"] <= r["sum_phy_mbps"]


def test_ptmp_cpe_from_coordinates():
    # A CPE given by lat/lon (north of the AP) resolves a ~0° bearing → in beam.
    r = wsvc.ptmp_plan(_ptmp(cpes=[PtmpCpe(id="geo", lat=0.004, lon=0.0)]))
    c = r["cpes"][0]
    assert c["in_beam"] is True
    assert c["distance_m"] > 0


async def test_ptmp_endpoint_ok(client):
    resp = await client.post(
        "/api/rf/ptmp",
        json={
            "lat": 0.0, "lon": 0.0, "azimuth_deg": 0.0, "beamwidth_deg": 60.0,
            "freq_mhz": 5000.0, "model_id": "fspl",
            "cpes": [{"id": "c1", "distance_m": 500.0, "bearing_deg": 0.0}],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["cpes"][0]["served"] is True
    assert body["served_count"] == 1


async def test_ptmp_endpoint_unknown_model_422(client):
    resp = await client.post(
        "/api/rf/ptmp",
        json={
            "lat": 0.0, "lon": 0.0, "azimuth_deg": 0.0, "beamwidth_deg": 60.0,
            "freq_mhz": 5000.0, "model_id": "itm",
            "cpes": [{"id": "c1", "distance_m": 500.0, "bearing_deg": 0.0}],
        },
    )
    assert resp.status_code == 422


# --- NG-RF-05 product selection --------------------------------------------
def _candidates() -> list[RadioCandidate]:
    return [
        # High margin, expensive → lower margin-per-cost.
        RadioCandidate(name="premium", tx_power_dbm=25.0, antenna_gain_dbi=20.0,
                       rx_sensitivity_dbm=-85.0, cost=200.0, bandwidth_mhz=40.0),
        # Lower margin, cheap → higher margin-per-cost (should rank first).
        RadioCandidate(name="budget", tx_power_dbm=23.0, antenna_gain_dbi=16.0,
                       rx_sensitivity_dbm=-90.0, cost=100.0, bandwidth_mhz=20.0),
    ]


def _psel(**over) -> ProductSelectRequest:
    base = {
        "distance_m": 5000.0, "freq_mhz": 5000.0, "target_throughput_mbps": 0.0,
        "model_id": "fspl", "candidates": _candidates(),
    }
    base.update(over)
    return ProductSelectRequest(**base)


def test_product_select_ranks_by_margin_per_cost():
    r = wsvc.product_select(_psel())
    ranked = r["ranked"]
    # Deterministic: non-increasing margin-per-cost.
    mpc = [x["margin_per_cost"] for x in ranked]
    assert mpc == sorted(mpc, reverse=True)
    assert ranked[0]["name"] == "budget"  # cheaper, best margin/cost


def test_product_select_deterministic():
    a = wsvc.product_select(_psel())
    b = wsvc.product_select(_psel())
    assert a == b


def test_product_select_flags_target_miss():
    # 266 Mbps premium meets a 100-target; 80 Mbps budget does not.
    r = wsvc.product_select(_psel(target_throughput_mbps=100.0))
    by_name = {x["name"]: x for x in r["ranked"]}
    assert by_name["premium"]["meets_target"] is True
    assert by_name["budget"]["meets_target"] is False


def test_product_select_link_that_cannot_close_is_fail():
    # A weak radio at 50 km: negative margin → does not close, fails target.
    r = wsvc.product_select(_psel(
        distance_m=50000.0, target_throughput_mbps=10.0,
        candidates=[RadioCandidate(
            name="weak", tx_power_dbm=20.0, antenna_gain_dbi=10.0,
            rx_sensitivity_dbm=-80.0, cost=50.0, bandwidth_mhz=20.0,
        )],
    ))
    item = r["ranked"][0]
    assert item["link_closes"] is False
    assert item["meets_target"] is False


async def test_product_select_endpoint_ok(client):
    resp = await client.post(
        "/api/rf/product-select",
        json={
            "distance_m": 5000.0, "freq_mhz": 5000.0,
            "target_throughput_mbps": 50.0, "model_id": "fspl",
            "candidates": [
                {"name": "budget", "tx_power_dbm": 23.0, "antenna_gain_dbi": 16.0,
                 "rx_sensitivity_dbm": -90.0, "cost": 100.0, "bandwidth_mhz": 20.0},
            ],
        },
    )
    assert resp.status_code == 200
    assert resp.json()["ranked"][0]["name"] == "budget"


async def test_product_select_endpoint_unknown_model_422(client):
    resp = await client.post(
        "/api/rf/product-select",
        json={
            "distance_m": 5000.0, "freq_mhz": 5000.0,
            "target_throughput_mbps": 50.0, "model_id": "itm",
            "candidates": [
                {"name": "x", "tx_power_dbm": 23.0, "antenna_gain_dbi": 16.0,
                 "rx_sensitivity_dbm": -90.0, "cost": 100.0},
            ],
        },
    )
    assert resp.status_code == 422


async def test_product_select_endpoint_empty_candidates_422(client):
    resp = await client.post(
        "/api/rf/product-select",
        json={
            "distance_m": 5000.0, "freq_mhz": 5000.0,
            "target_throughput_mbps": 50.0, "candidates": [],
        },
    )
    assert resp.status_code == 422


# --- RF-2: 3GPP TR 38.901 §7.3 sector antenna pattern -----------------------
def test_sector_gain_on_boresight_is_max_gain():
    assert sector_gain_dbi(0.0, 60.0, 20.0) == 20.0


def test_sector_gain_at_hpbw_edge_is_3db_down():
    assert sector_gain_dbi(30.0, 60.0, 20.0) == pytest.approx(17.0, abs=0.05)


def test_sector_gain_at_rear_hits_sidelobe_floor():
    assert sector_gain_dbi(180.0, 60.0, 20.0) == pytest.approx(20.0 - 30.0, abs=0.05)


def test_sector_gain_is_monotonic_non_increasing_0_to_180():
    prev = sector_gain_dbi(0.0, 60.0, 20.0)
    for deg in range(1, 181):
        cur = sector_gain_dbi(float(deg), 60.0, 20.0)
        assert cur <= prev + 1e-9
        prev = cur
