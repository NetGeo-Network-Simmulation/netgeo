"""FRR configuration and console trust boundary, including a live Podman check."""
from __future__ import annotations

import asyncio
import subprocess
from unittest.mock import AsyncMock, Mock

import pytest
from app.main import app
from app.models import ConfigArtifact
from app.services.emul_lab import EmulLabManager, get_emul_lab_manager
from app.store import get_repo
from engine.emulation.podman_adaptor import (
    PodmanAdaptor,
    _running_contains,
    socket_reachable,
)
from engine.netstack.device import Device
from fastapi.testclient import TestClient

from tests.conftest import make_auth_headers, make_test_token


def test_adaptor_rejects_unowned_container_before_exec():
    manager = get_emul_lab_manager()
    manager.acquire_lease()
    adaptor = PodmanAdaptor(project_id="project", lab_id="lab", lease_guard=manager._holds_lease)
    container = Mock(labels={"netgeo.managed": "true", "netgeo.project": "other"}, status="running")
    adaptor._client = Mock()
    adaptor._client.containers.get.return_value = container
    try:
        with pytest.raises(PermissionError, match="ownership"):
            adaptor._owned_container("n1")
        container.exec_run.assert_not_called()
    finally:
        manager.release_lease()


def test_adaptor_rejects_unsupported_format_and_multiline_console():
    adaptor = PodmanAdaptor(project_id="p", lab_id="l")
    with pytest.raises(ValueError, match="CLI"):
        asyncio.run(adaptor.push_config("n", "hostname x", "yaml"))
    with pytest.raises(ValueError, match="one nonempty"):
        asyncio.run(adaptor.command("n", "show version\nconfigure terminal"))


def test_adaptor_requires_lease_before_container_lookup():
    adaptor = PodmanAdaptor(project_id="p", lab_id="l")
    adaptor._client = Mock()
    with pytest.raises(PermissionError, match="lease"):
        asyncio.run(adaptor.command("n", "show version"))
    adaptor._client.containers.get.assert_not_called()


def test_local_manager_lease_is_sufficient():
    manager = EmulLabManager()
    manager.acquire_lease()
    adaptor = PodmanAdaptor(project_id="p", lab_id="l", lease_guard=manager._holds_lease)
    adaptor._client = Mock()
    adaptor._client.containers.get.return_value = Mock(
        labels={"netgeo.managed": "true", "netgeo.project": "p", "netgeo.lab": "l", "netgeo.kind": "frr"},
        status="running",
    )
    try:
        assert adaptor._owned_container("n") is adaptor._client.containers.get.return_value
    finally:
        manager.release_lease()


def test_link_address_uses_observed_podman_assignment():
    adaptor = PodmanAdaptor(project_id="p", lab_id="l", lease_guard=lambda: True)
    container = Mock(
        labels={"netgeo.managed": "true", "netgeo.project": "p", "netgeo.lab": "l", "netgeo.kind": "frr"},
        status="running",
        attrs={"NetworkSettings": {"Networks": {"netgeo-link-x": {"IPAddress": "10.201.0.4"}}}},
    )
    adaptor._client = Mock()
    adaptor._client.containers.get.return_value = container
    assert asyncio.run(adaptor.link_address("node", "x")) == "10.201.0.4"
    with pytest.raises(RuntimeError, match="address unavailable"):
        asyncio.run(adaptor.link_address("node", "missing"))


def test_failed_rollback_keeps_file_backup(monkeypatch):
    adaptor = PodmanAdaptor(project_id="p", lab_id="l", lease_guard=lambda: True)
    container = Mock(
        labels={"netgeo.managed": "true", "netgeo.project": "p", "netgeo.lab": "l", "netgeo.kind": "frr"},
        status="running",
    )
    container.put_archive.return_value = True
    adaptor._client = Mock()
    adaptor._client.containers.get.return_value = container
    commands = []

    def execute(argv, demux=True):
        commands.append(argv)
        if argv[0] == "mv" and argv[1].endswith(".backup"):
            return 1, (b"", b"")
        if argv[0] == "vtysh":
            return 0, (b"hostname r1\n", b"")
        return 0, (b"", b"")

    container.exec_run.side_effect = execute
    def fail_apply(_):
        raise RuntimeError("apply failed")

    monkeypatch.setattr(adaptor, "_apply_file", fail_apply)
    with pytest.raises(RuntimeError, match="file rollback failed"):
        asyncio.run(adaptor.push_config("n", "hostname r1\n"))
    cleanup = [argv for argv in commands if argv[:2] == ["rm", "-f"]][-1]
    assert all(not value.endswith(".backup") for value in cleanup)


def test_running_verification_rejects_wrong_stanza_order_and_duplicate():
    desired = """router bgp 65001
 address-family ipv4 unicast
  neighbor 10.0.0.2 activate
  network 203.0.113.0/24
 exit-address-family
router bgp 65002
 address-family ipv4 unicast
  network 198.51.100.0/24
 exit-address-family
"""
    wrong_stanza = """router bgp 65001
 address-family ipv4 unicast
  network 198.51.100.0/24
  neighbor 10.0.0.2 activate
 exit-address-family
router bgp 65002
 address-family ipv4 unicast
  network 203.0.113.0/24
 exit-address-family
"""
    assert {line.strip() for line in desired.splitlines()} == {line.strip() for line in wrong_stanza.splitlines()}
    assert not _running_contains(desired, wrong_stanza)
    assert not _running_contains(desired, desired.replace(
        "  neighbor 10.0.0.2 activate\n  network 203.0.113.0/24",
        "  network 203.0.113.0/24\n  neighbor 10.0.0.2 activate",
    ))
    duplicate = desired.replace("  network 203.0.113.0/24", "  network 203.0.113.0/24\n  network 203.0.113.0/24")
    assert not _running_contains(duplicate, desired)
    assert not _running_contains(desired, duplicate)
    assert _running_contains(desired, desired)


def test_cleanup_cannot_mask_restart_and_rollback_failure(monkeypatch, caplog):
    adaptor = PodmanAdaptor(project_id="p", lab_id="l", lease_guard=lambda: True)
    container = Mock(
        labels={"netgeo.managed": "true", "netgeo.project": "p", "netgeo.lab": "l", "netgeo.kind": "frr"},
        status="running",
    )
    container.put_archive.return_value = True
    adaptor._client = Mock()
    adaptor._client.containers.get.return_value = container

    def execute(argv, demux=True):
        if argv[0] == "mv" and argv[1].endswith(".backup"):
            raise RuntimeError("container stopped during rollback")
        if argv[0] == "rm":
            raise RuntimeError("container unreachable during cleanup")
        if argv[0] == "vtysh":
            return 0, (b"hostname r1\n", b"")
        return 0, (b"", b"")

    container.exec_run.side_effect = execute

    def fail_restart(_):
        raise RuntimeError("restart failed")

    monkeypatch.setattr(adaptor, "_apply_file", fail_restart)
    with pytest.raises(RuntimeError, match="file rollback failed \\(RuntimeError\\)") as raised:
        asyncio.run(adaptor.push_config("n", "hostname r1\n"))
    assert str(raised.value.__cause__) == "restart failed"
    assert "container unreachable during cleanup" not in str(raised.value)
    assert "backup at" in caplog.text


def test_cleanup_cannot_mask_apply_failure_after_successful_rollback(monkeypatch):
    adaptor = PodmanAdaptor(project_id="p", lab_id="l", lease_guard=lambda: True)
    container = Mock(
        labels={"netgeo.managed": "true", "netgeo.project": "p", "netgeo.lab": "l", "netgeo.kind": "frr"},
        status="running",
    )
    container.put_archive.return_value = True
    adaptor._client = Mock()
    adaptor._client.containers.get.return_value = container

    def execute(argv, demux=True):
        if argv[0] == "test" and argv[-1] == "/etc/frr/frr.conf":
            return 1, (b"", b"")
        if argv[0] == "rm" and argv[-1].endswith(".backup"):
            raise RuntimeError("container stopped during cleanup")
        if argv[0] == "vtysh":
            return 0, (b"hostname r1\n", b"")
        return 0, (b"", b"")

    container.exec_run.side_effect = execute

    def fail_apply(_):
        raise RuntimeError("initial restart failed")

    monkeypatch.setattr(adaptor, "_apply_file", fail_apply)
    monkeypatch.setattr(adaptor, "_restart_ready", lambda _: None)
    with pytest.raises(RuntimeError, match="initial restart failed"):
        asyncio.run(adaptor.push_config("n", "hostname r1\n"))


def test_applied_config_reports_cleanup_failure(monkeypatch):
    adaptor = PodmanAdaptor(project_id="p", lab_id="l", lease_guard=lambda: True)
    container = Mock(
        labels={"netgeo.managed": "true", "netgeo.project": "p", "netgeo.lab": "l", "netgeo.kind": "frr"},
        status="running",
    )
    container.put_archive.return_value = True
    adaptor._client = Mock()
    adaptor._client.containers.get.return_value = container

    def execute(argv, demux=True):
        if argv[0] == "rm":
            raise RuntimeError("container stopped")
        if argv[0] == "vtysh":
            return 0, (b"hostname r1\n", b"")
        return 0, (b"", b"")

    container.exec_run.side_effect = execute
    monkeypatch.setattr(adaptor, "_apply_file", lambda _: (0, ""))
    result = asyncio.run(adaptor.push_config("n", "hostname r1\n"))
    assert result == {"applied": True, "verified": True, "cleanup_verified": False}


@pytest.mark.asyncio
async def test_apply_rejects_artifact_and_project_mismatch(client, monkeypatch, caplog):
    project = (await client.post("/api/projects", json={"name": "p"})).json()
    other = (await client.post("/api/projects", json={"name": "q"})).json()
    node = (await client.post("/api/nodes", json={"project_id": project["id"], "name": "r1", "nos": "frr", "mode": "emul"})).json()
    other_node = (await client.post("/api/nodes", json={"project_id": project["id"], "name": "r2", "nos": "frr", "mode": "emul"})).json()
    artifact = ConfigArtifact(id="test-artifact", node_id=node["id"], vendor="frr", content="hostname r1\n")
    await get_repo().add_config(artifact)
    assert (await client.post(f"/api/projects/{other['id']}/nodes/{node['id']}/configs/{artifact.id}/apply")).status_code == 422
    assert (await client.post(f"/api/projects/{project['id']}/nodes/{node['id']}/configs/other/apply")).status_code == 422
    assert (await client.post(f"/api/projects/{project['id']}/nodes/{other_node['id']}/configs/{artifact.id}/apply")).status_code == 422
    unsupported = ConfigArtifact(id="yaml-artifact", node_id=node["id"], vendor="frr", format="yaml", content="secret value")
    await get_repo().add_config(unsupported)
    rejected = await client.post(f"/api/projects/{project['id']}/nodes/{node['id']}/configs/{unsupported.id}/apply")
    assert rejected.status_code == 422 and "secret value" not in rejected.text
    applied = AsyncMock(return_value={"applied": True, "verified": True})
    monkeypatch.setattr(get_emul_lab_manager(), "push_config", applied)
    good = await client.post(f"/api/projects/{project['id']}/nodes/{node['id']}/configs/{artifact.id}/apply")
    assert good.status_code == 200, good.text
    assert good.json()["verified"] is True
    applied.assert_awaited_once()
    unsupported_node = (await client.post("/api/nodes", json={"project_id": project["id"], "name": "r3", "nos": "ios", "mode": "emul"})).json()
    unsupported_artifact = ConfigArtifact(id="ios-artifact", node_id=unsupported_node["id"], vendor="ios", content="hostname r3")
    await get_repo().add_config(unsupported_artifact)
    rejected = await client.post(f"/api/projects/{project['id']}/nodes/{unsupported_node['id']}/configs/{unsupported_artifact.id}/apply")
    assert rejected.status_code == 422
    applied.assert_awaited_once()
    applied.side_effect = RuntimeError("secret value")
    failed = await client.post(f"/api/projects/{project['id']}/nodes/{node['id']}/configs/{artifact.id}/apply")
    assert failed.status_code == 422
    assert "secret value" not in failed.text and "secret value" not in caplog.text


@pytest.mark.asyncio
async def test_capture_explicitly_unsupported_in_emulation(client):
    project = (await client.post("/api/projects", json={"name": "p"})).json()
    await client.post("/api/nodes", json={"project_id": project["id"], "name": "r1", "nos": "frr", "mode": "emul"})
    response = await client.get(f"/api/lab/{project['id']}/captures")
    assert response.status_code == 422
    assert "no tcpdump/dumpcap" in response.text
    response = await client.get(f"/api/lab/{project['id']}/pcapng")
    assert response.status_code == 422
    status = await client.get(f"/api/lab/{project['id']}/status")
    assert status.status_code == 200
    assert status.json()["capture"]["supported"] is False


def test_pure_sim_websocket_console_regression():
    client = TestClient(app)
    project = client.post("/api/projects", json={"name": "sim"}, headers=make_auth_headers()).json()
    node = client.post("/api/nodes", json={"project_id": project["id"], "name": "r1"}, headers=make_auth_headers()).json()
    with client.websocket_connect(f"/ws/console/{node['id']}?token={make_test_token()}") as ws:
        assert ws.receive_json()["type"] == "banner"
        ws.send_json({"type": "input", "data": "show ip route"})
        answer = ws.receive_json()
        assert answer["type"] == "output" and answer["node_id"] == node["id"]
        assert isinstance(answer["data"], str)


@pytest.mark.podman
@pytest.mark.skipif(not socket_reachable(), reason="rootless Podman socket unavailable")
def test_real_websocket_console_and_api_apply():
    manager = get_emul_lab_manager()
    manager.acquire_lease()
    client = TestClient(app)
    project = client.post("/api/projects", json={"name": "emul"}, headers=make_auth_headers()).json()
    node = client.post("/api/nodes", json={
        "project_id": project["id"], "name": "r1", "nos": "frr", "mode": "emul",
        "interfaces": [{"id": "r1-eth0", "node_id": "", "name": "eth0", "ip": ["10.253.77.1/24"], "mtu": 1400}],
    }, headers=make_auth_headers()).json()
    try:
        generated = client.post("/api/configs/generate", json={"node_id": node["id"]}, headers=make_auth_headers())
        assert generated.status_code == 201, generated.text
        artifact = generated.json()
        assert "interface eth0\n ip address 10.253.77.1/24" in artifact["content"]
        assert " mtu 1400" not in artifact["content"]
        response = client.post(
            f"/api/projects/{project['id']}/nodes/{node['id']}/configs/{artifact['id']}/apply",
            headers=make_auth_headers(),
        )
        assert response.status_code == 200, response.text
        assert response.json()["verified"] is True
        with client.websocket_connect(f"/ws/console/{node['id']}?token={make_test_token()}") as ws:
            ws.receive_json()
            ws.send_json({"type": "input", "data": "show running-config"})
            answer = ws.receive_json()
            assert "hostname r1" in answer["data"]
            assert "interface eth0\n ip address 10.253.77.1/24" in answer["data"]
    finally:
        asyncio.run(manager.destroy(project["id"]))
        manager.release_lease()


@pytest.mark.podman
@pytest.mark.skipif(not socket_reachable(), reason="rootless Podman socket unavailable")
@pytest.mark.asyncio
async def test_real_frr_config_and_console():
    manager = get_emul_lab_manager()
    manager.acquire_lease()
    adaptor = PodmanAdaptor(project_id="nos-config-test", lab_id="live", lease_guard=manager._holds_lease)
    node_id = "nos-config-test-r1"
    try:
        await adaptor.spawn(Device(name="r1", node_id=node_id, nos="frr", mode="emul"))
        result = await adaptor.push_config(node_id, "ip route 198.51.100.0/24 10.0.0.1\n")
        assert result == {"applied": True, "verified": True}
        assert "ip route 198.51.100.0/24 10.0.0.1" in await adaptor.command(node_id, "show running-config")
        await adaptor.push_config(node_id, "ip route 203.0.113.0/24 10.0.0.1\n")
        replaced = await adaptor.command(node_id, "show running-config")
        assert "ip route 203.0.113.0/24 10.0.0.1" in replaced
        assert "ip route 198.51.100.0/24 10.0.0.1" not in replaced
        with pytest.raises(RuntimeError):
            await adaptor.push_config(node_id, "hostname should-not-survive\ninvalid frr command\n")
        assert "ip route 203.0.113.0/24 10.0.0.1" in await adaptor.command(node_id, "show running-config")
    finally:
        await adaptor.destroy(node_id)
        manager.release_lease()
        state = await asyncio.to_thread(
            lambda: subprocess.run(
                ["podman", "ps", "-a", "--filter", f"name=^netgeo-{node_id}$", "--format", "{{.Names}}"],
                capture_output=True, text=True, check=True,
            ).stdout.strip()
        )
        assert not state
