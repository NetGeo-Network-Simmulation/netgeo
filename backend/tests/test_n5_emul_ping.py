"""N-5: ping through the API actually reaching real containers.

The mixed-mode rejection test is pure sim-side bookkeeping (no podman touched,
runs everywhere). The real-container test is marked ``podman`` like the rest
of the emulation suite (``test_podman_adaptor.py``/``test_link_e2e.py``) —
skipped, not failed, when the socket is unreachable — and cross-verifies
cleanup against ``podman ps`` rather than trusting the API's own response.
"""
from __future__ import annotations

import subprocess
from uuid import uuid4

import pytest

from app.services.emul_lab import EmulLabManager
from app.services.netlab import get_lab_manager
from engine.emulation.podman_adaptor import (
    CONTAINER_PREFIX,
    LINK_NETWORK_PREFIX,
    PodmanAdaptor,
    socket_reachable,
)
from engine.model import InterfaceModel, LinkModel
from engine.netstack.device import Device

pytestmark = pytest.mark.podman

skip_no_podman = pytest.mark.skipif(
    not socket_reachable(), reason="podman.socket unreachable — see PodmanSocketUnreachable"
)


@pytest.fixture(autouse=True)
def _fresh_labs():
    get_lab_manager()._labs.clear()
    yield
    get_lab_manager()._labs.clear()


def _network_exists(name: str) -> bool:
    return subprocess.run(
        ["podman", "network", "inspect", name], capture_output=True, check=False
    ).returncode == 0


def _podman_state(container_name: str) -> str | None:
    out = subprocess.run(
        ["podman", "ps", "-a", "--filter", f"name=^{container_name}$", "--format", "{{.State}}"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return out or None


def _podman_id(container_name: str) -> str:
    return subprocess.run(
        ["podman", "inspect", container_name, "--format", "{{.Id}}"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


def _podman_label(container_name: str, label: str) -> str:
    return subprocess.run(
        ["podman", "inspect", container_name, "--format", f"{{{{index .Config.Labels \"{label}\"}}}}"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


def _force_cleanup(node_ids: tuple[str, str], link_id: str) -> None:
    for node_id in node_ids:
        subprocess.run(["podman", "rm", "-f", f"{CONTAINER_PREFIX}{node_id}"],
                       capture_output=True, check=False)
    subprocess.run(["podman", "network", "rm", "-f", f"{LINK_NETWORK_PREFIX}{link_id}"],
                   capture_output=True, check=False)


def _iface(name: str, ips: list[str]):
    return {"id": "", "node_id": "", "name": name, "ip": ips}


async def _mk_project(client) -> str:
    resp = await client.post("/api/projects", json={"name": "N5EmulPing"})
    assert resp.status_code == 201
    return resp.json()["id"]


async def _mk_node(client, pid: str, name: str, mode: str, ifaces):
    body = {
        "project_id": pid,
        "name": name,
        "kind": "router",
        "nos": "frr",
        "mode": mode,
        "interfaces": ifaces,
    }
    resp = await client.post("/api/nodes", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _mk_link(client, pid: str, a: str, b: str):
    resp = await client.post(
        "/api/links", json={"project_id": pid, "a_iface": a, "b_iface": b}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


@pytest.fixture
def _cleanup_containers():
    """Belt-and-suspenders: the endpoint itself must clean up (that's what
    this test proves), but never leave a container behind on the dev machine
    if the assertion that proves it fails first."""
    names: list[str] = []
    yield names
    for name in names:
        subprocess.run(["podman", "rm", "-f", name], capture_output=True, check=False)


@skip_no_podman
async def test_ping_between_two_emul_nodes_reaches_real_containers(client, _cleanup_containers):
    pid = await _mk_project(client)
    r1 = await _mk_node(client, pid, "r1", "emul", [_iface("eth1", [])])
    r2 = await _mk_node(client, pid, "r2", "emul", [_iface("eth1", [])])
    _cleanup_containers += [f"{CONTAINER_PREFIX}{r1['id']}", f"{CONTAINER_PREFIX}{r2['id']}"]
    link = await _mk_link(client, pid, r1["interfaces"][0]["id"], r2["interfaces"][0]["id"])

    resp = await client.post(
        f"/api/lab/{pid}/ping", json={"src": "r1", "dst": "r2", "count": 2}
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["path"] == "emulation"
    assert data["received"] == 2, data
    assert data["loss_pct"] == 0.0

    # A second ping must use the same containers and direct link.
    ids = [_podman_id(f"{CONTAINER_PREFIX}{n['id']}") for n in (r1, r2)]
    assert all(_podman_state(f"{CONTAINER_PREFIX}{n['id']}") == "running" for n in (r1, r2))
    assert _network_exists(f"{LINK_NETWORK_PREFIX}{link['id']}")
    for node in (r1, r2):
        name = f"{CONTAINER_PREFIX}{node['id']}"
        assert _podman_label(name, "netgeo.managed") == "true"
        assert _podman_label(name, "netgeo.project") == pid
        assert _podman_label(name, "netgeo.lab")
    second = await client.post(f"/api/lab/{pid}/ping", json={"src": "r2", "dst": "r1", "count": 1})
    assert second.status_code == 200, second.text
    assert second.json()["received"] == 1
    assert ids == [_podman_id(f"{CONTAINER_PREFIX}{n['id']}") for n in (r1, r2)]

    deleted = await client.delete(f"/api/projects/{pid}")
    assert deleted.status_code == 204, deleted.text
    assert _podman_state(f"{CONTAINER_PREFIX}{r1['id']}") is None
    assert _podman_state(f"{CONTAINER_PREFIX}{r2['id']}") is None
    assert not _network_exists(f"{LINK_NETWORK_PREFIX}{link['id']}")


async def test_mixed_mode_ping_rejected_clearly(client):
    """One emul + one sim node: reject with a clear error, never silently
    fall back to a wrong (sim-only) answer. No podman touched (no marker
    needed) — the rejection happens purely from stored node metadata."""
    pid = await _mk_project(client)
    r1 = await _mk_node(client, pid, "r1", "emul", [_iface("eth1", [])])
    h1 = await _mk_node(client, pid, "h1", "sim", [_iface("eth0", ["10.0.0.2/30"])])
    await _mk_link(client, pid, r1["interfaces"][0]["id"], h1["interfaces"][0]["id"])

    resp = await client.post(
        f"/api/lab/{pid}/ping", json={"src": "r1", "dst": "h1", "count": 1}
    )
    assert resp.status_code != 200
    assert "mixed-mode" in resp.text or "mode=emul" in resp.text


@skip_no_podman
async def test_reconcile_stale_labeled_lab_leaves_other_resources_alone():
    suffix = uuid4().hex[:12]
    project_id = f"reconcile-{suffix}"
    node_a, node_b, link_id = f"a-{suffix}", f"b-{suffix}", f"l-{suffix}"
    adaptor = PodmanAdaptor(project_id=project_id, lab_id="stale-test")
    try:
        for node_id in (node_a, node_b):
            await adaptor.spawn(Device(name=node_id, node_id=node_id, nos="frr", mode="emul"))
        await adaptor.wire_link(
            LinkModel(id=link_id, a_iface="ia", b_iface="ib"),
            InterfaceModel(id="ia", node_id=node_a, name="eth1"),
            InterfaceModel(id="ib", node_id=node_b, name="eth1"),
        )
        assert _network_exists(f"{LINK_NETWORK_PREFIX}{link_id}")
        await EmulLabManager().reconcile_stale()
        assert not _network_exists(f"{LINK_NETWORK_PREFIX}{link_id}")
        assert _podman_state(f"{CONTAINER_PREFIX}{node_a}") is None
        assert _podman_state(f"{CONTAINER_PREFIX}{node_b}") is None
        # Podman's own default bridge is outside the NetGeo ownership labels.
        assert _network_exists("podman")
        await EmulLabManager().reconcile_stale()  # idempotent
    finally:
        _force_cleanup((node_a, node_b), link_id)
