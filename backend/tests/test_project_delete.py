"""Deleting a project removes its persisted graph without touching neighbours."""

from app.models import ConfigArtifact, FiberPath, ImportSnapshot, RfStudy, Scenario
from app.store import get_repo


async def test_delete_project_cascades_and_isolates(client):
    repo = get_repo()
    first = (await client.post("/api/projects", json={"name": "first"})).json()["id"]
    second = (await client.post("/api/projects", json={"name": "second"})).json()["id"]
    node_ids = []
    for pid, name in ((first, "a"), (second, "b")):
        node = (await client.post("/api/nodes", json={"project_id": pid, "name": name, "kind": "router", "interfaces": [{"id": f"{name}-eth0", "node_id": "", "name": "eth0"}]})).json()
        node_ids.append(node["id"])
        await client.post("/api/nodes", json={"project_id": pid, "name": f"{name}-peer", "kind": "router", "interfaces": [{"id": f"{name}-eth1", "node_id": "", "name": "eth0"}]})
        link = (await client.post("/api/links", json={"project_id": pid, "a_iface": f"{name}-eth0", "b_iface": f"{name}-eth1", "type": "copper"})).json()
        await client.post("/api/cables", json={"project_id": pid, "link_id": link["id"], "media": "cat6", "length_m": 2})
        site = (await client.post("/api/sites", json={"project_id": pid, "name": name, "lat": 0, "lon": 0})).json()
        await client.post("/api/racks", json={"project_id": pid, "site_id": site["id"], "name": name})
        await repo.add_scenario(Scenario(id=f"scenario-{name}", project_id=pid, name=name))
        await repo.add_fiber_path(FiberPath(id=f"fiber-{name}", project_id=pid, name=name))
        await repo.add_rf_study(RfStudy(id=f"rf-{name}", project_id=pid, kind="ptp"))
        await repo.add_config(ConfigArtifact(id=f"config-{name}", node_id=node["id"], vendor="ios", content=""))
        await repo.save_import_snapshot(ImportSnapshot(id=f"snap-{name}", node_id=node["id"], project_id=pid, vendor="ios", text=""))

    deleted = await client.delete(f"/api/projects/{first}")
    assert deleted.status_code == 204
    assert (await client.get(f"/api/projects/{first}")).status_code == 404
    assert (await client.delete(f"/api/projects/{first}")).status_code == 404
    assert [p["id"] for p in (await client.get("/api/projects")).json()] == [second]
    assert not any(item.project_id == first for attr in ("_nodes", "_links", "_scenarios", "_sites", "_racks", "_cables", "_fiber_paths", "_rf_studies", "_import_snapshots") for item in getattr(repo, attr).values())
    assert not repo._configs_by_node.get(node_ids[0])
    assert not any(c.node_id == node_ids[0] for c in repo._configs.values())
    assert (await client.get(f"/api/projects/{second}/topology")).json()["nodes"][0]["id"] == node_ids[1]
    assert repo._configs_by_node[node_ids[1]] == ["config-b"]
