"""Guards the honesty fix in ``app/data/device_catalog.json`` (K-D): the file
used to claim "official datasheets" for all 23 SKUs, which an audit proved
false (see device-catalog-claim-audit.md). This locks in that the claim stays
gone and every SKU carries a per-field evidence block instead of silence.
"""
from __future__ import annotations

import json
import re
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


def test_no_device_uses_the_all_placeholder():
    """K-D2: the old `{"_all": {...}}` shortcut claimed every field was unchecked
    even after the 2026-09-08 audit proved 23/23 SKUs had in fact been checked.
    It must never come back as a way to avoid writing per-field evidence.
    """
    data = _load()
    for device in data["devices"]:
        assert "_all" not in device["evidence"], device["id"]


def test_evidence_keys_are_real_fields_on_the_device():
    """Every key inside a device's `evidence` block must be a field that
    actually exists on that device, so evidence can't silently drift from the
    schema it claims to document. No exceptions needed: `model` (used by
    cambium-epmp1000 and cambium-epmp3000, whose model numbers don't match
    the vendor's official ordering info) is itself a regular top-level field
    on every device, so it passes this check like any other.
    """
    data = _load()
    for device in data["devices"]:
        for field in device["evidence"]:
            assert field in device, (
                f"{device['id']}.evidence has key {field!r} which is not a "
                "field on the device"
            )


def test_no_verified_evidence_still_claims_uncorrected():
    """K-D3: 29 fields across 17 SKUs got their catalog values corrected to
    match the vendor source their evidence cites. A `status: V` entry means
    "the vendor value is known and the catalog now reflects it" — it must
    never still say the old value was left in place, or the evidence would
    be lying about its own correction (see device-catalog-correction-table.md).
    """
    data = _load()
    for device in data["devices"]:
        for field, entry in device["evidence"].items():
            if entry.get("status") == "V":
                note = entry.get("note", "")
                assert "not corrected" not in note.lower(), (
                    f"{device['id']}.{field} is status V but its note still "
                    f"claims the value was not corrected: {note!r}"
                )


def test_evidence_entries_with_a_source_url_have_a_retrieved_date():
    data = _load()
    date_re = re.compile(r"^\d{4}-\d{2}-\d{2}$")
    for device in data["devices"]:
        for field, entry in device["evidence"].items():
            if "source_url" in entry:
                retrieved = entry.get("retrieved_date")
                assert retrieved, f"{device['id']}.{field} has source_url but no retrieved_date"
                assert date_re.match(retrieved), (
                    f"{device['id']}.{field}.retrieved_date={retrieved!r} not YYYY-MM-DD"
                )
