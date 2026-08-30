"""Device library packs (NG-DL-02) — loader, merge into /device-types, enable/disable.

docs/design/15-DEVICE-LIBRARY-PACKS.md §3.4: slice #1 migrated the 3-device
``olt.json`` library file into ``network/devices/packs/olt/``; slice #2
migrated the remaining 8 orphan category files (33 devices) the same way,
retiring ``network/devices/library/`` entirely. These tests cover (a) every
real pack loads and merges correctly, and (b) the generic loader mechanics
(enable/disable, invalid manifest, unknown pack) against a synthetic pack in
a tmp_path so they don't depend on repo content.
"""
from __future__ import annotations

import json

import pytest

from app.api import device_types as dt

# Every real pack under network/devices/packs/ and its expected device_count
# (network/devices/packs/<id>/manifest.json is the source of truth; this list
# just needs to stay in sync so the parametrized test below is meaningful).
_REAL_PACKS = {
    "olt": 3,
    "routers": 7,
    "switches": 9,
    "firewalls": 3,
    "onu": 3,
    "wireless-ap": 4,
    "optical-transport": 3,
    "cell-site": 3,
    "servers": 3,
}


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


@pytest.mark.parametrize("pack_id,expected_count", sorted(_REAL_PACKS.items()))
def test_real_pack_manifest_and_devices_match_expected_count(pack_id, expected_count):
    packs = {p["id"]: p for p in dt.list_packs()}
    assert pack_id in packs, f"{pack_id} pack manifest not discovered"
    manifest = packs[pack_id]
    assert manifest["enabled"] is True
    assert manifest["device_count"] == expected_count

    devices = [d for d in dt._load_enabled_pack_devices() if d.id.startswith(f"{pack_id}:")]
    assert len(devices) == expected_count
    for d in devices:
        assert d.builtin is True
        assert d.snmp_oids == dt._IF_MIB_OIDS  # falls back when the JSON omits it


async def test_get_device_types_includes_enabled_pack_devices(client):
    resp = await client.get("/api/device-types")
    assert resp.status_code == 200
    ids = {d["id"] for d in resp.json()}
    assert "olt:zte-c320-pizzabox" in ids
    assert "routers:cisco-asr9006-core" in ids
    assert "servers:dell-r760-server" in ids
    # Built-in generic OLT fallback must still be present alongside the pack.
    assert "builtin-olt" in ids


async def test_pack_device_type_carries_vendor_ports_physical(client):
    """N4: the rack faceplate needs real port/physical data, not just
    power/SNMP — _device_json_to_type() must not drop it (backend/app/api/
    device_types.py)."""
    resp = await client.get("/api/device-types")
    by_id = {d["id"]: d for d in resp.json()}
    sw = by_id["switches:cisco-c9300-48p-access"]
    assert sw["vendor"] == "Cisco"
    assert sw["physical"] == {"ru": 1, "form_factor": "1U-stackable"}
    assert sw["ports"], "pack device must carry its ports[] through to the API"
    assert all("type" in p and "count" in p for p in sw["ports"])


async def test_node_created_with_device_type_id_round_trips_via_api(client):
    """N4: POST /nodes with device_type_id (the device-library create path,
    frontend/src/lib/mapDeploy.ts deployAt()) stores and returns it, and it
    survives a plain PATCH of an unrelated field."""
    pid = (await client.post("/api/projects", json={"name": "p"})).json()["id"]
    resp = await client.post(
        "/api/nodes",
        json={
            "project_id": pid,
            "name": "sw1",
            "kind": "switch",
            "device_type_id": "switches:cisco-c9300-48p-access",
        },
    )
    assert resp.status_code == 201, resp.text
    node = resp.json()
    assert node["device_type_id"] == "switches:cisco-c9300-48p-access"

    got = await client.get(f"/api/nodes/{node['id']}")
    assert got.json()["device_type_id"] == "switches:cisco-c9300-48p-access"

    # PATCHing an unrelated field must not clear it.
    patched = await client.patch(f"/api/nodes/{node['id']}", json={"name": "sw1-renamed"})
    assert patched.json()["device_type_id"] == "switches:cisco-c9300-48p-access"

    # Explicit null clears it (device_type_id is nullable, like intent/site_id).
    cleared = await client.patch(f"/api/nodes/{node['id']}", json={"device_type_id": None})
    assert cleared.json()["device_type_id"] is None


async def test_node_created_without_device_type_id_defaults_to_none(client):
    pid = (await client.post("/api/projects", json={"name": "p"})).json()["id"]
    resp = await client.post(
        "/api/nodes", json={"project_id": pid, "name": "r1", "kind": "router"}
    )
    assert resp.json()["device_type_id"] is None


def test_builtin_device_type_has_no_vendor_ports_physical():
    """_BUILTIN entries never had this data — must stay None, not crash."""
    builtin = next(d for d in dt._BUILTIN if d.id == "builtin-switch")
    assert builtin.vendor is None
    assert builtin.ports is None
    assert builtin.physical is None


async def test_all_pack_device_ids_are_prefixed_and_collision_free(client):
    resp = await client.get("/api/device-types")
    ids = [d["id"] for d in resp.json()]
    assert len(ids) == len(set(ids)), "duplicate device-type ids in /device-types"
    assert len(ids) == len(dt._BUILTIN) + sum(_REAL_PACKS.values())

    builtin_ids = {d.id for d in dt._BUILTIN}
    for pack_id in _REAL_PACKS:
        pack_ids = [i for i in ids if i.startswith(f"{pack_id}:")]
        assert pack_ids, f"no devices found for pack {pack_id}"
        for pid in pack_ids:
            assert pid not in builtin_ids


async def test_disable_then_enable_routers_pack_via_api(client, monkeypatch, tmp_path):
    state_file = tmp_path / "packs_enabled.json"
    monkeypatch.setattr(dt, "_PACKS_STATE_FILE", state_file)

    resp = await client.post("/api/device-packs/routers/disable")
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False

    resp = await client.get("/api/device-types")
    ids = {d["id"] for d in resp.json()}
    assert "routers:cisco-asr9006-core" not in ids

    resp = await client.post("/api/device-packs/routers/enable")
    assert resp.status_code == 200
    assert resp.json()["enabled"] is True

    resp = await client.get("/api/device-types")
    ids = {d["id"] for d in resp.json()}
    assert "routers:cisco-asr9006-core" in ids


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
