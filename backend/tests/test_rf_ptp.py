"""Tests for the bidirectional PtP budget (NG-RF-N2a, S9).

``/api/rf/ptp`` gained a per-side ``radio_a``/``radio_b`` (tx power, gain,
bandwidth, sensitivity) and a per-direction ``a_to_b``/``b_to_a`` result
(RSSI, noise floor, SNR, MCS, fade margin) plus ``bearing_a_to_b``/
``bearing_b_to_a``. All new fields are additive: an old single-direction
request must still validate and resolve both directions symmetrically.
"""
from __future__ import annotations

import pytest

# Two points ~10 km apart along the equator (same fixture as test_rf_planning).
_A = (0.0, 0.0)
_B = (0.0, 0.0898315)

_FLAT_PROFILE = [
    {"lat": 0.0, "lon": 0.0, "elevation_m": 0.0, "distance_m": 0.0},
    {"lat": 0.0, "lon": 0.09, "elevation_m": 0.0, "distance_m": 10000.0},
]


def _base_payload(**over) -> dict:
    payload = {
        "a_lat": _A[0], "a_lon": _A[1], "b_lat": _B[0], "b_lon": _B[1],
        "freq_mhz": 5000.0, "tx_power_dbm": 50.0, "tx_gain_dbi": 0.0,
        "rx_gain_dbi": 0.0, "misc_loss_db": 0.0, "rx_sensitivity_dbm": -95.0,
        "tx_height_m": 10.0, "rx_height_m": 5.0, "model_id": "fspl",
        "profile": _FLAT_PROFILE,
    }
    payload.update(over)
    return payload


async def test_asymmetric_radios_give_different_rssi_per_direction(client):
    """A and B with different tx_power_dbm -> a_to_b and b_to_a RSSI differ."""
    resp = await client.post(
        "/api/rf/ptp",
        json=_base_payload(
            radio_a={"tx_power_dbm": 30.0, "bandwidth_mhz": 20.0},
            radio_b={"tx_power_dbm": 10.0, "bandwidth_mhz": 20.0},
        ),
    )
    assert resp.status_code == 200
    body = resp.json()
    a_to_b, b_to_a = body["a_to_b"], body["b_to_a"]
    assert a_to_b["rssi_dbm"] != b_to_a["rssi_dbm"]
    assert a_to_b["rssi_dbm"] - b_to_a["rssi_dbm"] == pytest.approx(20.0, abs=0.05)


async def test_mcs_present_in_both_directions(client):
    resp = await client.post("/api/rf/ptp", json=_base_payload())
    assert resp.status_code == 200
    body = resp.json()
    assert body["a_to_b"]["mcs"] is not None
    assert body["b_to_a"]["mcs"] is not None


async def test_noise_floor_changes_with_bandwidth(client):
    """noise_floor_dbm follows the receiving side's bandwidth, not a constant."""
    narrow = await client.post(
        "/api/rf/ptp",
        json=_base_payload(radio_b={"bandwidth_mhz": 5.0}),
    )
    wide = await client.post(
        "/api/rf/ptp",
        json=_base_payload(radio_b={"bandwidth_mhz": 40.0}),
    )
    assert narrow.status_code == 200 and wide.status_code == 200
    # b is the receiver on the a_to_b direction.
    noise_narrow = narrow.json()["a_to_b"]["noise_floor_dbm"]
    noise_wide = wide.json()["a_to_b"]["noise_floor_dbm"]
    assert noise_narrow != noise_wide
    assert noise_wide > noise_narrow  # wider channel -> higher noise floor


async def test_bearings_are_roughly_opposite(client):
    resp = await client.post("/api/rf/ptp", json=_base_payload())
    assert resp.status_code == 200
    body = resp.json()
    diff = abs(body["bearing_a_to_b"] - body["bearing_b_to_a"]) % 360.0
    diff = diff if diff <= 180.0 else 360.0 - diff
    assert diff == pytest.approx(180.0, abs=1.0)


async def test_legacy_payload_without_radio_a_b_still_passes(client):
    """Backward compatibility: an old request (no radio_a/radio_b, no
    bandwidth) still validates and the legacy top-level fields still fill in,
    same shape rfStore.ts already consumes."""
    resp = await client.post("/api/rf/ptp", json=_base_payload())
    assert resp.status_code == 200
    body = resp.json()
    # legacy fields untouched
    assert body["rssi_dbm"] is not None
    assert body["eirp_dbm"] == pytest.approx(50.0)
    assert body["path_loss_db"] > 0
    assert body["fade_margin_db"] is not None
    assert body["verdict"] == "clear"
    # new fields resolved symmetrically from the legacy shared radio
    assert body["a_to_b"]["mcs"] is not None
    assert body["b_to_a"]["mcs"] is not None


# --- RF-1: ITU-R P.838 rain fade wired into ptp_budget() -------------------
async def test_rain_rate_lowers_fade_margin_by_exact_p838_attenuation(client):
    """15 GHz / 5 km, rain_rate_mm_hr=42 vs 0: fade_margin_db drops by exactly
    rain_specific_attenuation_db_km(15, 42) * 5 km (pure math, so tight abs)."""
    from engine.wireless import rain_specific_attenuation_db_km

    # ~5 km apart along the equator (half of the 10 km fixture above).
    b_lon = 0.04491575
    payload = _base_payload(a_lat=0.0, a_lon=0.0, b_lat=0.0, b_lon=b_lon, freq_mhz=15000.0)
    payload["profile"] = [
        {"lat": 0.0, "lon": 0.0, "elevation_m": 0.0, "distance_m": 0.0},
        {"lat": 0.0, "lon": b_lon, "elevation_m": 0.0, "distance_m": 5000.0},
    ]

    dry = await client.post("/api/rf/ptp", json={**payload, "rain_rate_mm_hr": 0.0})
    wet = await client.post("/api/rf/ptp", json={**payload, "rain_rate_mm_hr": 42.0})
    assert dry.status_code == 200 and wet.status_code == 200
    dry_body, wet_body = dry.json(), wet.json()

    expected_rain_db = rain_specific_attenuation_db_km(15.0, 42.0) * 5.0
    assert dry_body["fade_margin_db"] - wet_body["fade_margin_db"] == pytest.approx(
        expected_rain_db, abs=0.01
    )
    assert wet_body["rain_fade_db"] == pytest.approx(expected_rain_db, abs=0.01)
    assert dry_body["rain_fade_db"] == pytest.approx(0.0, abs=0.01)


def test_p838_15ghz_matches_literal_itu_table_point(client):
    """Sanity-check the embedded P.838-3 table itself against the literal
    k=0.00367, alpha=1.1540 published for 15 GHz (an exact breakpoint in the
    table, not interpolated) — not a self-comparison of the function."""
    from engine.wireless import rain_specific_attenuation_db_km

    literal = 0.00367 * (42.0 ** 1.1540)
    assert rain_specific_attenuation_db_km(15.0, 42.0) == pytest.approx(literal, abs=1e-6)


async def test_legacy_ptp_request_without_rain_rate_is_unchanged(client):
    """Regression: a request that omits rain_rate_mm_hr (old client) budgets
    identically to explicit rain_rate_mm_hr=0.0."""
    payload = _base_payload()
    implicit = await client.post("/api/rf/ptp", json=payload)
    explicit = await client.post("/api/rf/ptp", json={**payload, "rain_rate_mm_hr": 0.0})
    assert implicit.status_code == 200 and explicit.status_code == 200
    a, b = implicit.json(), explicit.json()
    assert a["rssi_dbm"] == b["rssi_dbm"]
    assert a["fade_margin_db"] == b["fade_margin_db"]
    assert a["rain_fade_db"] == pytest.approx(0.0)
