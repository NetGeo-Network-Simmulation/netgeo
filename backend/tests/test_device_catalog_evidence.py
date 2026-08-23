"""Guards the honesty fix in ``app/data/device_catalog.json`` (K-D): the file
used to claim "official datasheets" for all 23 SKUs, which an audit proved
false (see device-catalog-claim-audit.md). This locks in that the claim stays
gone and every SKU carries a per-field evidence block instead of silence.
"""
from __future__ import annotations

import json
from pathlib import Path

CATALOG_PATH = (
    Path(__file__).resolve().parent.parent / "app" / "data" / "device_catalog.json"
)
ALLOWED_STATUSES = {"V", "V(2nd)", "derived", "UNVERIFIED", "low-confidence"}


def _load():
    return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))


def test_description_drops_official_datasheet_claim():
    data = _load()
    assert "official datasheets" not in data["description"]


def test_every_device_has_an_evidence_block():
    data = _load()
    for device in data["devices"]:
        assert device.get("evidence"), device["id"]


def test_evidence_entries_only_use_allowed_statuses():
    data = _load()
    for device in data["devices"]:
        for field, entry in device["evidence"].items():
            status = entry.get("status")
            assert status in ALLOWED_STATUSES, f"{device['id']}.{field}={status!r}"
