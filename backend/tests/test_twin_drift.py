"""NG-TW-03 drift-diff tests.

Acceptance gates per §1.7 of docs/design/14-ENGINE-WAVE2-ARCH.md:
- import → snapshot saved; GET /drift shows no drift (clean report)
- mutate intent (add static route) → drifted=True, diff contains route line
- node without snapshot → has_snapshot=False
- re-import resets drift
- project /drift summary filters non-imported nodes
"""
from __future__ import annotations

# ── fixture configs ──────────────────────────────────────────────────────────

_IOS = """
hostname R1
interface GigabitEthernet0/0
 ip address 10.0.1.1 255.255.255.0
ip route 0.0.0.0 0.0.0.0 10.0.1.254
"""

# Same config with an extra static route added (simulates intent drift).
_IOS_DRIFTED_EXTRA_ROUTE = """
hostname R1
interface GigabitEthernet0/0
 ip address 10.0.1.1 255.255.255.0
ip route 0.0.0.0 0.0.0.0 10.0.1.254
ip route 192.168.99.0 255.255.255.0 10.0.1.1
"""


# ── helper ───────────────────────────────────────────────────────────────────

async def _import(client, pid: str, vendor: str, text: str) -> dict:
    resp = await client.post(
        f"/api/projects/{pid}/import-config", json={"vendor": vendor, "text": text}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# ── tests ────────────────────────────────────────────────────────────────────

async def test_import_saves_snapshot_and_clean_report(client):
    """After a fresh import the node must report no drift (has_snapshot=True, drifted=False)."""
    pid = (await client.post("/api/projects", json={"name": "p"})).json()["id"]
    node = await _import(client, pid, "ios", _IOS)
    nid = node["id"]

    resp = await client.get(f"/api/projects/{pid}/nodes/{nid}/drift")
    assert resp.status_code == 200, resp.text
    report = resp.json()
    assert report["has_snapshot"] is True
    assert report["drifted"] is False
    assert report["diff"] == ""
    assert report["imported_at"] is not None


async def test_drift_detected_after_intent_change(client):
    """Mutating intent (add static route) after import must produce drifted=True."""
    pid = (await client.post("/api/projects", json={"name": "p"})).json()["id"]
    node = await _import(client, pid, "ios", _IOS)
    nid = node["id"]

    # Add a static route to the node's intent via PATCH.
    current_intent = node["intent"]
    new_routes = current_intent.get("static_routes", []) + [
        {"prefix": "192.168.99.0/24", "next_hop": "10.0.1.1"}
    ]
    patch_resp = await client.patch(
        f"/api/nodes/{nid}",
        json={"intent": {**current_intent, "static_routes": new_routes}},
    )
    assert patch_resp.status_code == 200, patch_resp.text

    resp = await client.get(f"/api/projects/{pid}/nodes/{nid}/drift")
    assert resp.status_code == 200, resp.text
    report = resp.json()
    assert report["has_snapshot"] is True
    assert report["drifted"] is True
    # The diff must contain evidence of the added route.
    assert "192.168.99" in report["diff"]


async def test_node_without_snapshot_has_snapshot_false(client):
    """A node created manually (not via import) must return has_snapshot=False."""
    pid = (await client.post("/api/projects", json={"name": "p"})).json()["id"]
    n_resp = await client.post(
        "/api/nodes", json={"project_id": pid, "name": "Manual-R1"}
    )
    assert n_resp.status_code == 201, n_resp.text
    nid = n_resp.json()["id"]

    resp = await client.get(f"/api/projects/{pid}/nodes/{nid}/drift")
    assert resp.status_code == 200, resp.text
    report = resp.json()
    assert report["has_snapshot"] is False
    assert report["drifted"] is False
    assert report["diff"] == ""
    assert report["imported_at"] is None


async def test_reimport_creates_clean_snapshot(client):
    """Each import creates a fresh node with a clean snapshot (has_snapshot=True, drifted=False).

    The import endpoint always creates a new node (store auto-renames on collision).
    This verifies that the snapshot is saved and the drift report is clean immediately
    after a fresh import — i.e. re-importing config state resets drift on the new node.
    """
    pid = (await client.post("/api/projects", json={"name": "p"})).json()["id"]

    # First import.
    node1 = await _import(client, pid, "ios", _IOS)

    # Mutate first node's intent to create drift.
    current_intent = node1["intent"]
    extra = [{"prefix": "192.168.99.0/24", "next_hop": "10.0.1.1"}]
    await client.patch(
        f"/api/nodes/{node1['id']}",
        json={"intent": {**current_intent, "static_routes": extra}},
    )
    first_drift = (await client.get(
        f"/api/projects/{pid}/nodes/{node1['id']}/drift"
    )).json()
    assert first_drift["drifted"] is True

    # Second import with config that includes the extra route — a new node is created.
    node2 = await _import(client, pid, "ios", _IOS_DRIFTED_EXTRA_ROUTE)
    assert node2["id"] != node1["id"]   # distinct node

    # The new node's snapshot must be clean (just imported).
    new_report = (await client.get(
        f"/api/projects/{pid}/nodes/{node2['id']}/drift"
    )).json()
    assert new_report["has_snapshot"] is True
    assert new_report["drifted"] is False


async def test_project_drift_filters_non_imported(client):
    """GET /projects/{pid}/drift must only include imported nodes."""
    pid = (await client.post("/api/projects", json={"name": "p"})).json()["id"]

    # One imported node.
    await _import(client, pid, "ios", _IOS)

    # One manually created node (no imported=True intent flag).
    await client.post("/api/nodes", json={"project_id": pid, "name": "Manual"})

    resp = await client.get(f"/api/projects/{pid}/drift")
    assert resp.status_code == 200, resp.text
    reports = resp.json()
    # Only the imported node appears.
    assert len(reports) == 1
    assert reports[0]["node_name"] == "R1"
    assert reports[0]["has_snapshot"] is True


async def test_drift_404_unknown_node_or_project(client):
    """GET drift for nonexistent node/project must return 404."""
    pid = (await client.post("/api/projects", json={"name": "p"})).json()["id"]
    assert (await client.get(f"/api/projects/{pid}/nodes/ghost/drift")).status_code == 404
    assert (await client.get("/api/projects/ghost/nodes/x/drift")).status_code == 404
    assert (await client.get("/api/projects/ghost/drift")).status_code == 404
