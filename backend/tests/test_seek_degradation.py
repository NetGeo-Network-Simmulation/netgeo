"""NG-N6 / N-6b: honest degradation for /seek and grading-exact on non-pure-sim
projects. Pure logic gating (Project.capabilities, derived from Project.mode,
itself derived from the project's actual Node.mode set — see
derive_project_mode) — no podman, no engine emulation involved.
"""
from __future__ import annotations

import pytest

from app.services.netlab import get_lab_manager


@pytest.fixture(autouse=True)
def _fresh_labs():
    get_lab_manager()._labs.clear()
    yield
    get_lab_manager()._labs.clear()


async def _mk_node(client, pid: str, name: str, mode: str) -> None:
    resp = await client.post(
        "/api/nodes", json={"project_id": pid, "name": name, "mode": mode}
    )
    assert resp.status_code == 201, resp.text


async def _mk_project(client, mode: str = "pure-sim") -> str:
    """Create a project whose *derived* mode (N-6b) is ``mode`` — by adding
    real nodes of the matching NodeMode, never by poking stored state, so
    these tests prove the outside-observable contract."""
    resp = await client.post("/api/projects", json={"name": "N6"})
    assert resp.status_code == 201
    pid = resp.json()["id"]
    if mode == "pure-emul":
        await _mk_node(client, pid, "e1", "emul")
    elif mode == "mixed":
        await _mk_node(client, pid, "s1", "sim")
        await _mk_node(client, pid, "e1", "emul")
    return pid


@pytest.mark.parametrize("mode", ["pure-emul", "mixed"])
async def test_seek_rejected_for_non_pure_sim(client, mode):
    pid = await _mk_project(client, mode)
    resp = await client.post(f"/api/lab/{pid}/seek", json={"seq": 0})
    assert resp.status_code == 409, resp.text
    body = resp.json()
    assert body["error"] == "seek_unavailable"
    assert body["reason"]


async def test_seek_still_works_for_pure_sim(client):
    pid = await _mk_project(client)
    resp = await client.post(f"/api/lab/{pid}/seek", json={"seq": 0})
    assert resp.status_code == 200, resp.text
    assert resp.json()["seq"] == 0


@pytest.mark.parametrize(
    "mode,expected", [("pure-sim", (True, True)), ("pure-emul", (False, False)), ("mixed", (False, False))]
)
async def test_project_capabilities(client, mode, expected):
    pid = await _mk_project(client, mode)
    resp = await client.get(f"/api/projects/{pid}")
    assert resp.status_code == 200
    caps = resp.json()["capabilities"]
    assert (caps["seek"], caps["grading_exact"]) == expected


@pytest.mark.parametrize(
    "mode,expected", [("pure-sim", (True, True)), ("pure-emul", (False, False))]
)
async def test_lab_status_capabilities(client, mode, expected):
    pid = await _mk_project(client, mode)
    resp = await client.get(f"/api/lab/{pid}/status")
    assert resp.status_code == 200
    caps = resp.json()["capabilities"]
    assert (caps["seek"], caps["grading_exact"]) == expected


async def test_grading_flags_non_exact_for_emulated_project(client):
    aid = (
        await client.post(
            "/api/activities", json={"name": "n6", "instructions": "x", "checks": []}
        )
    ).json()["id"]

    pid_sim = await _mk_project(client)
    report = (
        await client.post(f"/api/activities/{aid}/grade", json={"project_id": pid_sim})
    ).json()
    assert report["exact"] is True

    pid_emul = await _mk_project(client, "pure-emul")
    report = (
        await client.post(f"/api/activities/{aid}/grade", json={"project_id": pid_emul})
    ).json()
    assert report["exact"] is False
