"""Device library packs (NG-DL-02) — loader, merge into /device-types, enable/disable.

docs/design/15-DEVICE-LIBRARY-PACKS.md §3.4: bootstrap slice #1 migrates the
3-device ``olt.json`` library file into ``network/devices/packs/olt/``. These
tests cover (a) the real ``olt`` pack loads and merges correctly, and (b) the
generic loader mechanics (enable/disable, invalid manifest, unknown pack)
against a synthetic pack in a tmp_path so they don't depend on repo content.
"""
from __future__ import annotations

import json

from app.api import device_types as dt


def test_real_olt_pack_is_enabled_by_default_and_merges_into_device_types():
    packs = dt.list_packs()
    olt = next((p for p in packs if p["id"] == "olt"), None)
    assert olt is not None, "olt pack manifest not discovered"
    assert olt["enabled"] is True
    assert olt["device_count"] == 3

    devices = dt._load_enabled_pack_devices()
    olt_devices = [d for d in devices if d.id.startswith("olt:")]
    assert len(olt_devices) == 3
    ids = {d.id for d in olt_devices}
    assert ids == {
        "olt:huawei-ma5800-x7-chassis",
        "olt:zte-c320-pizzabox",
        "olt:nokia-isam-fx-olt",
    }
    huawei = next(d for d in olt_devices if d.id == "olt:huawei-ma5800-x7-chassis")
    assert huawei.category == "fiber"
    assert huawei.icon == "olt"
    assert huawei.builtin is True
    assert huawei.power_watts_idle == 1200
    assert huawei.power_watts_max == 3000
    assert huawei.snmp_oids == dt._IF_MIB_OIDS


async def test_get_device_types_includes_enabled_pack_devices(client):
    resp = await client.get("/api/device-types")
    assert resp.status_code == 200
    ids = {d["id"] for d in resp.json()}
    assert "olt:zte-c320-pizzabox" in ids
    # Built-in generic OLT fallback must still be present alongside the pack.
    assert "builtin-olt" in ids


async def test_disable_then_enable_olt_pack_via_api(client, monkeypatch, tmp_path):
    state_file = tmp_path / "packs_enabled.json"
    monkeypatch.setattr(dt, "_PACKS_STATE_FILE", state_file)

    resp = await client.post("/api/device-packs/olt/disable")
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False

    resp = await client.get("/api/device-types")
    ids = {d["id"] for d in resp.json()}
    assert "olt:zte-c320-pizzabox" not in ids

    resp = await client.post("/api/device-packs/olt/enable")
    assert resp.status_code == 200
    assert resp.json()["enabled"] is True

    resp = await client.get("/api/device-types")
    ids = {d["id"] for d in resp.json()}
    assert "olt:zte-c320-pizzabox" in ids


async def test_enable_unknown_pack_404s(client):
    resp = await client.post("/api/device-packs/does-not-exist/enable")
    assert resp.status_code == 404


def test_loader_skips_pack_with_invalid_manifest_without_crashing(tmp_path, monkeypatch):
    packs_dir = tmp_path / "packs"
    bad = packs_dir / "broken"
    bad.mkdir(parents=True)
    (bad / "manifest.json").write_text("{not valid json", encoding="utf-8")

    monkeypatch.setattr(dt, "_PACKS_DIR", packs_dir)
    monkeypatch.setattr(dt, "_PACKS_STATE_FILE", tmp_path / "packs_enabled.json")

    assert dt.list_packs() == []
    assert dt._load_enabled_pack_devices() == []


def test_loader_skips_device_missing_required_fields(tmp_path, monkeypatch):
    packs_dir = tmp_path / "packs"
    pack = packs_dir / "demo"
    (pack / "devices").mkdir(parents=True)
    (pack / "manifest.json").write_text(
        json.dumps({"id": "demo", "name": "Demo", "enabled_by_default": True}),
        encoding="utf-8",
    )
    (pack / "devices" / "d.json").write_text(
        json.dumps({"devices": [
            {"id": "ok-one", "display_name": "OK One", "kind": "switch"},
            {"display_name": "Missing id", "kind": "switch"},
            {"id": "missing-kind", "display_name": "Missing kind"},
        ]}),
        encoding="utf-8",
    )

    monkeypatch.setattr(dt, "_PACKS_DIR", packs_dir)
    monkeypatch.setattr(dt, "_PACKS_STATE_FILE", tmp_path / "packs_enabled.json")

    devices = dt._load_enabled_pack_devices()
    assert [d.id for d in devices] == ["demo:ok-one"]
    assert devices[0].category == "wired"


def test_enable_override_persists_across_calls(tmp_path, monkeypatch):
    packs_dir = tmp_path / "packs"
    pack = packs_dir / "demo"
    (pack / "devices").mkdir(parents=True)
    (pack / "manifest.json").write_text(
        json.dumps({"id": "demo", "name": "Demo", "enabled_by_default": False}),
        encoding="utf-8",
    )
    (pack / "devices" / "d.json").write_text(
        json.dumps({"devices": [{"id": "x", "display_name": "X", "kind": "router"}]}),
        encoding="utf-8",
    )

    state_file = tmp_path / "packs_enabled.json"
    monkeypatch.setattr(dt, "_PACKS_DIR", packs_dir)
    monkeypatch.setattr(dt, "_PACKS_STATE_FILE", state_file)

    assert dt._load_enabled_pack_devices() == []  # off by default

    dt._set_pack_enabled("demo", True)
    assert state_file.exists()
    assert [d.id for d in dt._load_enabled_pack_devices()] == ["demo:x"]

    dt._set_pack_enabled("demo", False)
    assert dt._load_enabled_pack_devices() == []
