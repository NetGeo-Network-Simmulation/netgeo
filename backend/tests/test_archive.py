"""Project archive round-trip tests (NG-WS-03).

Proves the R3 exit criterion — "archive round-trips across a fresh install" —
at the data level: a project exported to a JSON envelope and re-imported
reproduces the same topology (node names, links, physical plant, rack
placement) but with brand-new ids, and the import is independent of the
original. Also proves physical effects (an over-length cable erroring its link)
survive the round-trip, tying NG-WS-03 back to NG-PH-03.
"""
from __future__ import annotations


async def _build_project(client) -> dict:
    """Create a project with 2 nodes + a link, a site+rack with a node placed
    in it, and an over-length cable. Returns handy ids/names for assertions."""
    pid = (await client.post("/api/projects", json={"name": "campus"})).json()["id"]

    for name, iface, ip in [("r1", "r1-e0", "10.0.0.1/24"), ("r2", "r2-e0", "10.0.0.2/24")]:
        resp = await client.post(
            "/api/nodes",
            json={
                "project_id": pid,
                "name": name,
                "kind": "router",
                "interfaces": [{"id": iface, "node_id": "", "name": "eth0", "ip": [ip]}],
            },
        )
        assert resp.status_code == 201, resp.text

    link = await client.post(
        "/api/links",
        json={"project_id": pid, "a_iface": "r1-e0", "b_iface": "r2-e0", "type": "copper"},
    )
    assert link.status_code == 201, link.text
    link_id = link.json()["id"]

    # Physical plant: a site + rack, a switch placed into the rack.
    site_id = (
        await client.post(
            "/api/sites", json={"project_id": pid, "name": "HQ", "lat": -6.2, "lon": 106.8}
        )
    ).json()["id"]
    rack_id = (
        await client.post(
            "/api/racks", json={"project_id": pid, "site_id": site_id, "name": "R1"}
        )
    ).json()["id"]
    sw = (
        await client.post(
            "/api/nodes", json={"project_id": pid, "name": "sw1", "kind": "switch"}
        )
    ).json()
    await client.patch(
        f"/api/nodes/{sw['id']}", json={"rack_id": rack_id, "ru_start": 10, "ru_span": 2}
    )

    # An over-length Cat6 run (120 m > 100 m) — errors the link (NG-PH-03).
    cable = await client.post(
        "/api/cables",
        json={"project_id": pid, "link_id": link_id, "media": "cat6", "length_m": 120.0},
    )
    assert cable.status_code == 201, cable.text

    return {"pid": pid, "link_id": link_id, "rack_id": rack_id}


async def test_export_import_round_trips(client):
    orig = await _build_project(client)
    pid = orig["pid"]

    archive = (await client.get(f"/api/projects/{pid}/archive")).json()
    assert archive["format"] == "netgeo-archive"
    assert archive["version"] == 1
    assert {n["name"] for n in archive["nodes"]} == {"r1", "r2", "sw1"}

    imported = await client.post("/api/projects/import", json=archive)
    assert imported.status_code == 201, imported.text
    new_pid = imported.json()["id"]
    assert new_pid != pid  # fresh project id

    src = (await client.get(f"/api/projects/{pid}/topology")).json()
    dst = (await client.get(f"/api/projects/{new_pid}/topology")).json()

    # Same node names, preserved.
    assert {n["name"] for n in dst["nodes"]} == {n["name"] for n in src["nodes"]}
    # Same link count; endpoints still resolve to real interfaces in the copy.
    assert len(dst["links"]) == len(src["links"]) == 1
    iface_ids = {i["id"] for n in dst["nodes"] for i in n["interfaces"]}
    link = dst["links"][0]
    assert link["a_iface"] in iface_ids and link["b_iface"] in iface_ids
    # ...but ids are fresh, not carried over from the source.
    src_iface_ids = {i["id"] for n in src["nodes"] for i in n["interfaces"]}
    assert iface_ids.isdisjoint(src_iface_ids)

    # Cable present with same media/length.
    assert len(dst["cables"]) == 1
    assert dst["cables"][0]["media"] == "cat6"
    assert dst["cables"][0]["length_m"] == 120.0
    # ...and it realizes a link that actually exists in the imported project.
    assert dst["cables"][0]["link_id"] in {l["id"] for l in dst["links"]}

    # Rack placement preserved (sw1 still 2 RU at RU 10 in a rack).
    sw = next(n for n in dst["nodes"] if n["name"] == "sw1")
    assert sw["ru_start"] == 10 and sw["ru_span"] == 2
    assert sw["rack_id"] in {rk["id"] for rk in dst["racks"]}
    assert sw["rack_id"] != orig["rack_id"]  # remapped

    # Site geo + sw1's site_id (inherited from the rack) survive, remapped.
    site = next(s for s in dst["sites"] if s["name"] == "HQ")
    assert site["lat"] == -6.2 and site["lon"] == 106.8
    assert sw["site_id"] == site["id"]


async def test_overlength_cable_effect_survives_round_trip(client):
    # NG-PH-03 verdict must be reproduced by the imported copy.
    orig = await _build_project(client)
    archive = (await client.get(f"/api/projects/{orig['pid']}/archive")).json()
    new_pid = (await client.post("/api/projects/import", json=archive)).json()["id"]

    dst = (await client.get(f"/api/projects/{new_pid}/topology")).json()
    assert dst["links"][0]["status"] == "errored"

    plant = (await client.get(f"/api/projects/{new_pid}/plant")).json()
    verdict = next(iter(plant["links"].values()))
    assert verdict["over_length"] is True
    assert verdict["over_media"] == "cat6"


async def test_import_is_independent_of_original(client):
    orig = await _build_project(client)
    archive = (await client.get(f"/api/projects/{orig['pid']}/archive")).json()
    new_pid = (await client.post("/api/projects/import", json=archive)).json()["id"]

    # Tear the original down entirely — deleting nodes cascades its links/cables.
    src = (await client.get(f"/api/projects/{orig['pid']}/topology")).json()
    for node in src["nodes"]:
        await client.delete(f"/api/nodes/{node['id']}")
    gone = (await client.get(f"/api/projects/{orig['pid']}/topology")).json()
    assert gone["nodes"] == [] and gone["links"] == []

    # The imported copy is untouched.
    dst = (await client.get(f"/api/projects/{new_pid}/topology")).json()
    assert len(dst["nodes"]) == 3
    assert len(dst["links"]) == 1
    assert dst["links"][0]["status"] == "errored"


async def test_import_bad_envelope_is_4xx(client):
    for bad in ({}, {"format": "wrong", "version": 1}, {"format": "netgeo-archive"},
                {"format": "netgeo-archive", "version": 99, "project": {}}):
        resp = await client.post("/api/projects/import", json=bad)
        assert 400 <= resp.status_code < 500, (bad, resp.status_code, resp.text)
        assert resp.status_code != 500


async def test_import_malformed_entity_is_4xx_not_500(client):
    # Valid envelope shell but a broken node payload (unknown field, forbid).
    archive = {
        "format": "netgeo-archive",
        "version": 1,
        "project": {"id": "x", "name": "p"},
        "nodes": [{"id": "n", "project_id": "x", "name": "r", "bogus": 1}],
    }
    resp = await client.post("/api/projects/import", json=archive)
    assert 400 <= resp.status_code < 500, resp.text
