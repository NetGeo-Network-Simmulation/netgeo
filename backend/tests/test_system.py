"""Tests for host-system endpoints + the cloud (real-world uplink) node.

Network reachability is environment-dependent, so we assert on response *shape*
(not on whether the box actually has internet) to stay deterministic in CI.
"""
from __future__ import annotations


async def test_system_interfaces_shape(client):
    resp = await client.get("/api/system/interfaces")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert isinstance(data["interfaces"], list)
    assert len(data["interfaces"]) >= 1
    nic = data["interfaces"][0]
    for key in ("name", "ipv4", "ipv6", "is_up", "is_virtual", "is_primary", "mtu"):
        assert key in nic, f"missing {key} in {nic}"
    assert isinstance(nic["ipv4"], list)


async def test_system_internet_shape(client):
    resp = await client.get("/api/system/internet")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert {"online", "latency_ms", "via", "source_ip"} <= set(data)
    assert isinstance(data["online"], bool)


async def test_cloud_node_uplink_roundtrip(client):
    pid = (await client.post("/api/projects", json={"name": "rw", "description": "t"})).json()["id"]

    # Create a cloud node bound to a host adapter via intent.uplink.
    body = {
        "project_id": pid,
        "name": "Internet",
        "kind": "cloud",
        "intent": {"uplink": {"adapter": "eth0", "mode": "nat"}},
    }
    created = await client.post("/api/nodes", json=body)
    assert created.status_code == 201, created.text
    node = created.json()
    assert node["kind"] == "cloud"
    assert node["intent"]["uplink"]["adapter"] == "eth0"

    # Re-bind to a different adapter/mode via PATCH (partial update).
    patched = await client.patch(
        f"/api/nodes/{node['id']}",
        json={"intent": {"uplink": {"adapter": "ens33", "mode": "bridge"}}},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["intent"]["uplink"] == {"adapter": "ens33", "mode": "bridge"}
