"""Signal calculation endpoint.

POST /api/signal/calculate — given AP and CPE coordinates plus RF parameters,
returns distance (Haversine), Free-Space Path Loss, estimated RSSI, and a
qualitative signal-quality rating.

Formulas
--------
Haversine (R = 6378.137 km — WGS84 semi-major axis (a), spherical; matches UISP's distance calc):
  a  = sin²(Δlat/2) + cos(lat1)·cos(lat2)·sin²(Δlon/2)
  d  = 2·R·asin(√a)

FSPL (dB) — standard Friis formula in SI units:
  FSPL = 20·log10(d_m) + 20·log10(f_hz) + 20·log10(4π / c)
  where c = 3 × 10⁸ m/s

Estimated RSSI:
  RSSI_dBm = tx_power_dBm − FSPL_dB

Signal quality thresholds (industry convention for wireless links):
  ≥ −60 dBm  → excellent
  ≥ −70 dBm  → good
  ≥ −80 dBm  → fair
  <  −80 dBm → poor
"""
from __future__ import annotations

import math
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

router = APIRouter(tags=["signal"])

# ---------------------------------------------------------------------------
# Pydantic schemas (local — not exported through app.models to keep the
# models package clean of domain-specific calculation types)
# ---------------------------------------------------------------------------
_EARTH_RADIUS_KM = 6378.137  # WGS84 semi-major axis (a), spherical model
_C = 3e8  # speed of light, m/s


class SignalCalcRequest(BaseModel):
    """Input parameters for the RF signal calculation."""

    ap_lat: float = Field(..., ge=-90, le=90, description="AP latitude (degrees)")
    ap_lon: float = Field(..., ge=-180, le=180, description="AP longitude (degrees)")
    cpe_lat: float = Field(..., ge=-90, le=90, description="CPE latitude (degrees)")
    cpe_lon: float = Field(..., ge=-180, le=180, description="CPE longitude (degrees)")
    tx_power: float = Field(..., ge=-30, le=60, description="Transmit power (dBm)")
    frequency: float = Field(
        ..., gt=0, le=100, description="Carrier frequency (GHz)"
    )


SignalQuality = Literal["excellent", "good", "fair", "poor"]


class SignalCalcResponse(BaseModel):
    """Calculated RF link budget results."""

    distance_km: float = Field(..., description="Haversine distance (km)")
    fspl_db: float = Field(..., description="Free-space path loss (dB)")
    estimated_rssi_dbm: float = Field(
        ..., description="Estimated received signal strength (dBm)"
    )
    signal_quality: SignalQuality = Field(
        ..., description="Qualitative rating based on estimated RSSI"
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return the great-circle distance between two WGS-84 points in km."""
    lat1_r, lon1_r = math.radians(lat1), math.radians(lon1)
    lat2_r, lon2_r = math.radians(lat2), math.radians(lon2)
    dlat = lat2_r - lat1_r
    dlon = lon2_r - lon1_r
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1_r) * math.cos(lat2_r) * math.sin(dlon / 2) ** 2
    return 2 * _EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def _fspl_db(distance_km: float, frequency_ghz: float) -> float:
    """Free-Space Path Loss using the Friis formula (SI units → dB)."""
    d_m = distance_km * 1000.0
    f_hz = frequency_ghz * 1e9
    # Guard against log(0) for co-located points
    if d_m <= 0:
        d_m = 0.001  # 1 mm floor
    return (
        20 * math.log10(d_m)
        + 20 * math.log10(f_hz)
        + 20 * math.log10(4 * math.pi / _C)
    )


def _quality(rssi_dbm: float) -> SignalQuality:
    if rssi_dbm >= -60:
        return "excellent"
    if rssi_dbm >= -70:
        return "good"
    if rssi_dbm >= -80:
        return "fair"
    return "poor"


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.post("/signal/calculate", response_model=SignalCalcResponse)
async def calculate_signal(body: SignalCalcRequest) -> SignalCalcResponse:
    """Calculate link budget between an AP and a CPE.

    Returns distance (km), FSPL (dB), estimated RSSI (dBm), and signal
    quality rating. No authentication required — pure computation.
    """
    dist = _haversine_km(body.ap_lat, body.ap_lon, body.cpe_lat, body.cpe_lon)
    fspl = _fspl_db(dist, body.frequency)
    rssi = body.tx_power - fspl
    return SignalCalcResponse(
        distance_km=round(dist, 6),
        fspl_db=round(fspl, 2),
        estimated_rssi_dbm=round(rssi, 2),
        signal_quality=_quality(rssi),
    )
