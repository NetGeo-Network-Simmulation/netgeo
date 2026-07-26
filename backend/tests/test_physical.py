"""Physical plant tests (NG-PH-01/02/03).

Covers the teachable failure at the heart of R3: an over-length cable run
degrades its link to ``errored`` and lengthens propagation delay; shortening it
deterministically restores the link. Split into pure-function unit tests (fast,
no HTTP) and end-to-end API tests proving the effect is visible in the topology
the UI / lab reads.
"""
from __future__ import annotations

import pytest

from app.models import (
    Cable,
    CableMedia,
    Link,
    LinkStatus,
    Project,
    Topology,
)
from app.services.physical import CABLE_SPECS, apply_physical, link_effects


def _topo_with_cable(length_m: float, media=CableMedia.cat6, status=LinkStatus.up):
    link = Link(id="l1", project_id="p1", a_iface="a", b_iface="b", delay=0.0, status=status)
    cable = Cable(id="c1", project_id="p1", link_id="l1", media=media, length_m=length_m)
    return Topology(
        project=Project(id="p1", name="lab"), links=[link], cables=[cable]
    )


# --- pure function units ----------------------------------------------------
def test_over_length_cat6_errors_the_link():
    # AC NG-PH-03: 120 m Cat6 (rated 100 m) → link errors out.
    topo = apply_physical(_topo_with_cable(120.0))
    assert topo.links[0].status == LinkStatus.errored


def test_within_length_restores_the_link():
    # Shorten to 90 m → back up, deterministically.
    topo = apply_physical(_topo_with_cable(90.0))
    assert topo.links[0].status == LinkStatus.up


def test_at_exactly_max_length_is_still_up():
    topo = apply_physical(_topo_with_cable(100.0))
    assert topo.links[0].status == LinkStatus.up


def test_propagation_delay_accumulates_from_length():
    eff = link_effects(
        [Cable(id="c", project_id="p", link_id="l", media=CableMedia.cat6, length_m=100.0)]
    )
    # 100 m * 5.56 ns/m = 556 ns = 0.000556 ms
    assert eff.added_delay_ms == pytest.approx(0.000556, rel=1e-3)
    assert eff.total_length_m == 100.0
    assert eff.over_length is False


def test_apply_folds_delay_into_link():
    topo = apply_physical(_topo_with_cable(90.0))
    assert topo.links[0].delay == pytest.approx(90 * 5.56 * 1e-6, rel=1e-3)


def test_admin_down_link_is_not_overridden_by_physics():
    # Physics never re-enables or relabels an operator's explicit choice.
    topo = apply_physical(_topo_with_cable(120.0, status=LinkStatus.admin_down))
    assert topo.links[0].status == LinkStatus.admin_down


def test_no_cables_is_a_noop_same_object():
    link = Link(id="l1", project_id="p1", a_iface="a", b_iface="b")
    topo = Topology(project=Project(id="p1", name="x"), links=[link])
    assert apply_physical(topo) is topo


def test_every_media_has_a_spec():
    for media in CableMedia:
        assert media in CABLE_SPECS


def test_fiber_run_far_longer_than_copper_is_fine():
    # 5 km of single-mode is well within reach — no error.
    topo = apply_physical(_topo_with_cable(5000.0, media=CableMedia.smf_os2))
    assert topo.links[0].status == LinkStatus.up


# --- API end-to-end ---------------------------------------------------------
async def _project_with_link(client) -> tuple[str, str]:
    pid = (await client.post("/api/projects", json={"name": "branch"})).json()["id"]
    for name, iface, ip in [("r1", "r1-e0", "10.0.0.1/24"), ("r2", "r2-e0", "10.0.0.2/24")]:
        await client.post(
            "/api/nodes",
            json={
                "project_id": pid,
                "name": name,
                "kind": "router",
                "interfaces": [{"id": iface, "node_id": "", "name": "eth0", "ip": [ip]}],
            },
        )
    link = await client.post(
        "/api/links",
        json={"project_id": pid, "a_iface": "r1-e0", "b_iface": "r2-e0", "type": "copper"},
    )
    return pid, link.json()["id"]


async def test_overlength_cable_errors_link_in_topology_then_shortening_restores(client):
    # Full AC NG-PH-03 through the real API + store seam.
    pid, link_id = await _project_with_link(client)

    cable = await client.post(
        "/api/cables",
        json={"project_id": pid, "link_id": link_id, "media": "cat6", "length_m": 120.0},
    )
    assert cable.status_code == 201, cable.text
    cable_id = cable.json()["id"]

    topo = (await client.get(f"/api/projects/{pid}/topology")).json()
    assert topo["links"][0]["status"] == "errored"

    plant = (await client.get(f"/api/projects/{pid}/plant")).json()
    assert plant["links"][link_id]["over_length"] is True
    assert plant["links"][link_id]["over_media"] == "cat6"

    # shorten to 90 m
    patched = await client.patch(f"/api/cables/{cable_id}", json={"length_m": 90.0})
    assert patched.status_code == 200, patched.text

    topo = (await client.get(f"/api/projects/{pid}/topology")).json()
    assert topo["links"][0]["status"] == "up"
    plant = (await client.get(f"/api/projects/{pid}/plant")).json()
    assert plant["links"][link_id]["over_length"] is False


async def test_cable_on_unknown_link_is_404(client):
    pid = (await client.post("/api/projects", json={"name": "x"})).json()["id"]
    resp = await client.post(
        "/api/cables",
        json={"project_id": pid, "link_id": "ghost", "media": "cat6", "length_m": 10.0},
    )
    assert resp.status_code == 404


async def test_deleting_link_cascades_its_cables(client):
    pid, link_id = await _project_with_link(client)
    cable_id = (
        await client.post(
            "/api/cables",
            json={"project_id": pid, "link_id": link_id, "media": "cat6", "length_m": 10.0},
        )
    ).json()["id"]

    await client.delete(f"/api/links/{link_id}")
    assert (await client.get(f"/api/cables/{cable_id}")).status_code == 404


async def test_place_node_in_rack_persists(client):
    # NG-PH-01: a device placed into a rack keeps its RU coordinates.
    pid = (await client.post("/api/projects", json={"name": "dc"})).json()["id"]
    site = await client.post("/api/sites", json={"project_id": pid, "name": "HQ"})
    assert site.status_code == 201
    rack = await client.post(
        "/api/racks", json={"project_id": pid, "site_id": site.json()["id"], "name": "R1"}
    )
    assert rack.status_code == 201
    rack_id = rack.json()["id"]

    node = (
        await client.post(
            "/api/nodes", json={"project_id": pid, "name": "sw1", "kind": "switch"}
        )
    ).json()
    patched = await client.patch(
        f"/api/nodes/{node['id']}",
        json={"rack_id": rack_id, "ru_start": 10, "ru_span": 2},
    )
    assert patched.status_code == 200, patched.text
    body = patched.json()
    assert body["rack_id"] == rack_id
    assert body["ru_start"] == 10
    assert body["ru_span"] == 2


# --- sites: geo + CRUD (NG-PH-01 A1) -----------------------------------------
async def test_site_has_coordinates(client):
    pid = (await client.post("/api/projects", json={"name": "p"})).json()["id"]
    resp = await client.post(
        "/api/sites",
        json={"project_id": pid, "name": "HQ", "lat": -6.2, "lon": 106.8},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["lat"] == -6.2 and body["lon"] == 106.8


async def test_site_get_list_patch_delete(client):
    pid = (await client.post("/api/projects", json={"name": "p"})).json()["id"]
    site = (
        await client.post("/api/sites", json={"project_id": pid, "name": "HQ"})
    ).json()
    sid = site["id"]

    listed = await client.get("/api/sites", params={"project_id": pid})
    assert listed.status_code == 200
    assert [s["id"] for s in listed.json()] == [sid]

    got = await client.get(f"/api/sites/{sid}")
    assert got.status_code == 200
    assert got.json()["name"] == "HQ"

    patched = await client.patch(f"/api/sites/{sid}", json={"lat": 1.0, "lon": 2.0})
    assert patched.status_code == 200, patched.text
    assert patched.json()["lat"] == 1.0 and patched.json()["lon"] == 2.0

    deleted = await client.delete(f"/api/sites/{sid}")
    assert deleted.status_code == 200
    assert (await client.get(f"/api/sites/{sid}")).status_code == 404


async def test_site_delete_clears_rack_and_node_back_reference(client):
    pid = (await client.post("/api/projects", json={"name": "p"})).json()["id"]
    site = (
        await client.post("/api/sites", json={"project_id": pid, "name": "HQ"})
    ).json()
    rack = (
        await client.post(
            "/api/racks", json={"project_id": pid, "site_id": site["id"], "name": "R1"}
        )
    ).json()
    node = (
        await client.post(
            "/api/nodes", json={"project_id": pid, "name": "sw1", "kind": "switch"}
        )
    ).json()
    await client.patch(f"/api/nodes/{node['id']}", json={"rack_id": rack["id"]})

    await client.delete(f"/api/sites/{site['id']}")

    topo = (await client.get(f"/api/projects/{pid}/topology")).json()
    rk = next(r for r in topo["racks"] if r["id"] == rack["id"])
    nd = next(n for n in topo["nodes"] if n["id"] == node["id"])
    assert rk["site_id"] is None
    assert nd["site_id"] is None


async def test_unknown_site_operations_are_404(client):
    assert (await client.get("/api/sites/ghost")).status_code == 404
    assert (await client.patch("/api/sites/ghost", json={"name": "x"})).status_code == 404
    assert (await client.delete("/api/sites/ghost")).status_code == 404


# --- Node.site_id invariant (NG-PH-01 A1.3) ----------------------------------
async def test_placing_node_in_rack_inherits_site_id(client):
    pid = (await client.post("/api/projects", json={"name": "p"})).json()["id"]
    site = (
        await client.post("/api/sites", json={"project_id": pid, "name": "HQ"})
    ).json()
    rack = (
        await client.post(
            "/api/racks", json={"project_id": pid, "site_id": site["id"], "name": "R1"}
        )
    ).json()
    node = (
        await client.post(
            "/api/nodes", json={"project_id": pid, "name": "sw1", "kind": "switch"}
        )
    ).json()

    patched = await client.patch(
        f"/api/nodes/{node['id']}", json={"rack_id": rack["id"]}
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["site_id"] == site["id"]


async def test_cross_site_placement_is_rejected(client):
    pid = (await client.post("/api/projects", json={"name": "p"})).json()["id"]
    site_a = (
        await client.post("/api/sites", json={"project_id": pid, "name": "A"})
    ).json()
    site_b = (
        await client.post("/api/sites", json={"project_id": pid, "name": "B"})
    ).json()
    rack_a = (
        await client.post(
            "/api/racks", json={"project_id": pid, "site_id": site_a["id"], "name": "RA"}
        )
    ).json()
    node = (
        await client.post(
            "/api/nodes",
            json={"project_id": pid, "name": "sw1", "kind": "switch", "site_id": site_b["id"]},
        )
    ).json()
    assert node["site_id"] == site_b["id"]

    # Placing a node explicitly owned by site B into a rack in site A must be
    # rejected at the point of placement, not silently reconciled.
    resp = await client.patch(
        f"/api/nodes/{node['id']}", json={"rack_id": rack_a["id"]}
    )
    assert resp.status_code == 409, resp.text

    # ...and the node's rack_id/site_id are unchanged (no partial write).
    unchanged = (await client.get(f"/api/nodes/{node['id']}")).json()
    assert unchanged["rack_id"] is None
    assert unchanged["site_id"] == site_b["id"]
