"""Device console backend fields (docs/design/stitch-html/clay/device-console).

P5 needs two real gaps filled: per-port admin enable/disable + PoE on/off
(new Interface booleans, no new endpoint — the existing generic node PATCH
already used by links.py for peer_link_id carries them), and a PoE budget
number sourced from the device pack (DeviceType.poe_budget_w), which must be
None (not fabricated) when the pack's vendor datasheet never published one.
"""
from __future__ import annotations

from app.api import device_types as dt


async def _mk_project(client) -> str:
    resp = await client.post("/api/projects", json={"name": "ConsoleTest"})
    assert resp.status_code == 201
    return resp.json()["id"]


async def test_interface_admin_and_poe_default(client):
    """New interfaces default admin-enabled, PoE off — existing topologies
    keep behaving exactly as before this field was added."""
    pid = await _mk_project(client)
    resp = await client.post(
        "/api/nodes",
        json={
            "project_id": pid,
            "name": "sw1",
            "kind": "switch",
            "interfaces": [{"id": "", "node_id": "", "name": "eth0", "ip": []}],
        },
    )
    assert resp.status_code == 201, resp.text
    iface = resp.json()["interfaces"][0]
    assert iface["admin_enabled"] is True
    assert iface["poe_enabled"] is False


async def test_port_admin_and_poe_toggle_round_trips_via_node_patch(client):
    """The console's Port Settings tab toggles land through the same generic
    PATCH /api/nodes/{id} (interfaces: [...]) links.py already relies on."""
    pid = await _mk_project(client)
    created = await client.post(
        "/api/nodes",
        json={
            "project_id": pid,
            "name": "sw1",
            "kind": "switch",
            "interfaces": [{"id": "", "node_id": "", "name": "eth0", "ip": []}],
        },
    )
    node = created.json()
    iface = node["interfaces"][0]
    iface["admin_enabled"] = False
    iface["poe_enabled"] = True

    patched = await client.patch(f"/api/nodes/{node['id']}", json={"interfaces": [iface]})
    assert patched.status_code == 200, patched.text
    out = patched.json()["interfaces"][0]
    assert out["admin_enabled"] is False
    assert out["poe_enabled"] is True


def test_device_type_exposes_poe_budget_when_pack_publishes_it():
    devices = dt._load_enabled_pack_devices()
    c9300 = next(d for d in devices if d.id == "switches:cisco-c9300-48p-access")
    assert c9300.poe_budget_w == 740


def test_device_type_poe_budget_is_none_when_pack_omits_it():
    """Several packs deliberately omit fields the vendor never published —
    that must surface as None, never a guessed number."""
    devices = dt._load_enabled_pack_devices()
    c9500 = next(d for d in devices if d.id == "switches:cisco-c9500-distribution")
    assert c9500.poe_budget_w is None

    # Builtins (non-pack catalog) never had this field at all — also None.
    builtin_switch = next(d for d in dt._BUILTIN if d.id == "builtin-switch")
    assert builtin_switch.poe_budget_w is None


async def test_get_device_types_serializes_poe_budget_field(client):
    resp = await client.get("/api/device-types")
    assert resp.status_code == 200
    by_id = {d["id"]: d for d in resp.json()}
    assert by_id["switches:cisco-c9300-48p-access"]["poe_budget_w"] == 740
    assert by_id["switches:cisco-c9500-distribution"]["poe_budget_w"] is None
