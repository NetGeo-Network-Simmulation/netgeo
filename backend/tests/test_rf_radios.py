"""Tests for the static RF radio catalog endpoint (``GET /api/rf/radios``).

Serves ``app/data/device_catalog.json`` verbatim (read-only) so the frontend
can prefill Auto-Select/PtMP candidate fields. See app/api/rf.py.
"""
from __future__ import annotations


async def test_radios_endpoint_returns_all_devices(client):
    resp = await client.get("/api/rf/radios")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 23


async def test_radios_endpoint_field_shape(client):
    resp = await client.get("/api/rf/radios")
    entry = resp.json()[0]
    for field in (
        "id", "name", "vendor", "model", "type", "frequency_ghz",
        "tx_power_dbm", "antenna_gain_dbi", "rx_sensitivity_dbm",
        "max_range_km", "max_throughput_mbps", "standard", "notes",
    ):
        assert field in entry
