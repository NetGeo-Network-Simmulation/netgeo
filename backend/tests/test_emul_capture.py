"""Bounded, owned emulation capture sessions and a real FRR packet proof."""
from __future__ import annotations

import asyncio
import os
import subprocess
import struct
import threading
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.exceptions.base import Conflict, NotFound, SimulationError
from app.models import Interface, Link, Node, Project, Topology
from app.services.emul_lab import CaptureSession, EmulLabManager
from app.services.emul_lab import get_emul_lab_manager
from engine.emulation.podman_adaptor import PodmanAdaptor, socket_reachable
from engine.emulation.kinds import FRR_CAPTURE_IMAGE
from engine.netstack.device import Device


def topology(project="capture-test", interface="a-if"):
    return Topology(
        project=Project(id=project, name="capture", mode="pure-emul"),
        nodes=[
            Node(id="a", project_id=project, name="a", kind="router", nos="frr", mode="emul",
                 interfaces=[Interface(id="a-if", node_id="a", name="eth1")]),
            Node(id="b", project_id=project, name="b", kind="router", nos="frr", mode="emul",
                 interfaces=[Interface(id="b-if", node_id="b", name="eth1")]),
        ],
        links=[Link(id="capture-link", project_id=project, a_iface=interface, b_iface="b-if")],
    )


@pytest.mark.asyncio
async def test_capture_rejects_wrong_node_interface_project_and_duration(monkeypatch):
    manager = EmulLabManager()
    topo = topology()
    async def fake_get(_):
        raise AssertionError("invalid request reached runtime")
    monkeypatch.setattr(manager, "_get", fake_get)
    for node, iface, seconds in (("missing", "a-if", 15), ("a", "b-if", 15),
                                 ("a", "missing", 15), ("a", "a-if", 16), ("a", "a-if", 0)):
        with pytest.raises(SimulationError):
            await manager.start_capture(topo, node, iface, seconds)
    with pytest.raises(NotFound, match="not found"):
        await manager.capture_status(topology("other"), "unknown")


@pytest.mark.asyncio
async def test_capture_missing_image_and_non_linux_are_unsupported(monkeypatch):
    manager = EmulLabManager()
    topo = topology()
    cached = SimpleNamespace(capture_enabled=False)
    async def fake_get(_):
        return cached
    monkeypatch.setattr(manager, "_get", fake_get)
    with pytest.raises(SimulationError, match="/api/lab/capture-test/rebuild"):
        await manager.start_capture(topo, "a", "a-if")
    assert not manager._captures  # starting capture never rebuilds an active lab
    monkeypatch.setattr("app.services.emul_lab.sys.platform", "darwin")
    with pytest.raises(SimulationError, match="requires Linux"):
        await manager.start_capture(topo, "a", "a-if")


@pytest.mark.asyncio
async def test_capture_session_result_cancel_rebuild_and_stale_lease(monkeypatch):
    manager = EmulLabManager()
    manager._lease_fd, manager._lease_pid = 1, __import__("os").getpid()
    topo = topology()
    data = b"\xd4\xc3\xb2\xa1" + bytes(20)
    class FakeAdaptor:
        capture_enabled = True
        def capture_interface(self, node_id, link_id):
            assert (node_id, link_id) == ("a", "capture-link")
            return "eth1"
        def capture_pcap(self, node_id, iface, path, duration):
            assert (node_id, iface, duration) == ("a", "eth1", 15)
            return data
        def stop_capture(self, node_id, path):
            pass
        async def destroy_link(self, link_id):
            pass
        async def destroy(self, node_id):
            pass
    adaptor = FakeAdaptor()
    manager._labs[topo.project.id] = (manager._fingerprint(topo), adaptor, ["a", "b"], ["capture-link"])
    cid = await manager.start_capture(topo, "a", "a-if")
    await manager._captures[cid].task
    assert (await manager.capture_status(topo, cid))["state"] == "complete"
    assert await manager.capture_bytes(topo, cid) == data
    with pytest.raises(NotFound, match="not found"):
        await manager.capture_status(topology("other"), cid)
    topo.links.clear()
    with pytest.raises(Conflict, match="expired"):
        await manager.capture_status(topo, cid)
    assert cid not in manager._captures
    topo = topology()
    manager._labs[topo.project.id] = (manager._fingerprint(topo), adaptor, ["a", "b"], ["capture-link"])
    cid = await manager.start_capture(topo, "a", "a-if")
    await manager._captures[cid].task
    manager._lease_pid = -1
    with pytest.raises(Conflict, match="lease"):
        await manager.capture_status(topo, cid)
    assert cid not in manager._captures
    manager._lease_pid = __import__("os").getpid()
    cid = await manager.start_capture(topo, "a", "a-if")
    await manager._captures[cid].task
    await manager.destroy(topo.project.id)
    assert not manager._captures and not manager._labs
    manager._lease_fd = None


@pytest.mark.asyncio
@pytest.mark.parametrize("stop_fails", [False, True])
async def test_shutdown_erases_active_session_even_when_signal_fails(stop_fails):
    manager = EmulLabManager()
    topo = topology()
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    class FakeAdaptor:
        def stop_capture(self, node_id, path):
            loop.call_soon_threadsafe(stopped.set)
            if stop_fails:
                raise RuntimeError("signal unavailable")
        async def destroy_link(self, link_id):
            pass
        async def destroy(self, node_id):
            pass
    manager._lease_fd, manager._lease_pid = 1, __import__("os").getpid()
    manager._labs[topo.project.id] = (manager._fingerprint(topo), FakeAdaptor(), ["a"], ["capture-link"])
    session = CaptureSession(topo.project.id, "a", "a-if", manager._fingerprint(topo), "/tmp/netgeo-capture-test.pcap")
    session.task = asyncio.create_task(stopped.wait())
    manager._captures["active"] = session
    await manager.destroy(topo.project.id)
    assert stopped.is_set() and not manager._captures and not manager._labs
    manager._lease_fd = None


@pytest.mark.asyncio
async def test_capture_exec_transport_stall_has_wall_clock_bound(monkeypatch):
    manager = EmulLabManager()
    released = threading.Event()
    class StalledAdaptor:
        def capture_pcap(self, node_id, interface, path, duration):
            released.wait(5)
            return b"\xd4\xc3\xb2\xa1" + bytes(20)
    monkeypatch.setattr("app.services.emul_lab.CAPTURE_IO_GRACE_SECONDS", 0.05)
    session = CaptureSession("p", "a", "a-if", "fingerprint", "/tmp/netgeo-capture-test.pcap")
    started = time.monotonic()
    try:
        await manager._run_capture(StalledAdaptor(), session, "eth1", 1)
        assert time.monotonic() - started < 2
        assert session.state == "error" and session.pcap is None
    finally:
        released.set()


def test_container_interface_requires_owned_link_and_no_client_command():
    adaptor = PodmanAdaptor(project_id="p", lab_id="l", lease_guard=lambda: True)
    container = Mock(labels={"netgeo.managed": "true", "netgeo.project": "p", "netgeo.lab": "l", "netgeo.kind": "frr"},
                     status="running", attrs={"NetworkSettings": {"Networks": {
                         "netgeo-link-l": {"IPAddress": "10.1.2.3"}}}})
    container.exec_run.return_value = (0, (b"1: lo: <UP>\n2: eth9@if2: <UP>\n    inet 10.1.2.3/30 scope global eth9\n", b""))
    adaptor._client = Mock()
    adaptor._client.containers.get.return_value = container
    adaptor._client.networks.get.return_value = Mock(attrs={"labels": {
        "netgeo.managed": "true", "netgeo.project": "p", "netgeo.lab": "l", "netgeo.link": "l",
    }})
    assert adaptor.capture_interface("n", "l") == "eth9"
    with pytest.raises(ValueError, match="not attached"):
        adaptor.capture_interface("n", "other")
    adaptor._client.networks.get.return_value.attrs["labels"]["netgeo.project"] = "other"
    with pytest.raises(PermissionError, match="link ownership"):
        adaptor.capture_interface("n", "l")
    adaptor._client.networks.get.return_value.attrs["labels"]["netgeo.project"] = "p"
    container.labels["netgeo.project"] = "other"
    with pytest.raises(PermissionError):
        adaptor.capture_interface("n", "l")
    assert container.exec_run.call_count == 1


@pytest.mark.asyncio
async def test_local_capture_image_is_selected_without_pulling_base():
    adaptor = PodmanAdaptor(project_id="p", lab_id="l", lease_guard=lambda: True)
    client = Mock()
    client.images.exists.side_effect = lambda image: image == FRR_CAPTURE_IMAGE
    container = Mock(id="cid", status="running")
    client.containers.create.return_value = container
    adaptor._client = client
    assert adaptor.use_local_capture_image()
    await adaptor.spawn(Device(name="r", node_id="r", nos="frr", mode="emul"))
    assert client.containers.create.call_args.args[0] == FRR_CAPTURE_IMAGE
    client.images.pull.assert_not_called()


@pytest.mark.asyncio
async def test_capture_api_contract_and_bounds(client, monkeypatch):
    project = (await client.post("/api/projects", json={"name": "capture-api"})).json()
    pid = project["id"]
    availability = await client.get(f"/api/lab/{pid}/status")
    assert f"POST /api/lab/{pid}/rebuild" in availability.json()["emul_capture"]["reason"]
    manager = get_emul_lab_manager()
    start = AsyncMock(return_value="abc123")
    status = AsyncMock(return_value={"id": "abc123", "state": "complete", "size": 24})
    result = AsyncMock(return_value=b"\xd4\xc3\xb2\xa1" + bytes(20))
    cancel = AsyncMock()
    monkeypatch.setattr(manager, "start_capture", start)
    monkeypatch.setattr(manager, "capture_status", status)
    monkeypatch.setattr(manager, "capture_bytes", result)
    monkeypatch.setattr(manager, "cancel_capture", cancel)
    path = f"/api/lab/{pid}/emul-captures"
    bad = await client.post(path, json={"node_id": "n", "interface_id": "i", "duration": 16})
    assert bad.status_code == 422
    bad = await client.post(path, json={"node_id": "n", "interface_id": "i", "filter": "; id"})
    assert bad.status_code == 422
    accepted = await client.post(path, json={"node_id": "n", "interface_id": "i"})
    assert accepted.status_code == 202 and accepted.json() == {"id": "abc123", "state": "running"}
    assert start.await_args.args[1:] == ("n", "i", 15)
    assert (await client.get(path + "/abc123")).json()["state"] == "complete"
    pcap = await client.get(path + "/abc123/pcap")
    assert pcap.status_code == 200 and pcap.content[:4] == b"\xd4\xc3\xb2\xa1"
    assert (await client.delete(path + "/abc123")).status_code == 204
    cancel.assert_awaited_once_with(pid, "abc123")
    status.side_effect = NotFound("capture not found in this project")
    assert (await client.get(path + "/deleted")).status_code == 404
    status.side_effect = Conflict("capture expired with the lab topology or lease")
    assert (await client.get(path + "/expired")).status_code == 409
    result.side_effect = Conflict("capture result is not complete")
    assert (await client.get(path + "/pending/pcap")).status_code == 409
    assert (await client.delete(path + "/expired")).status_code == 409
    status.side_effect = None
    cancel.side_effect = NotFound("capture not found in this project")
    assert (await client.delete(path + "/deleted")).status_code == 404


@pytest.mark.podman
@pytest.mark.skipif(not socket_reachable() and os.getenv("NETGEO_REQUIRE_CAPTURE_PROOF") != "1",
                    reason="rootless Podman socket unavailable")
@pytest.mark.asyncio
async def test_real_rootless_capture_contains_icmp_and_cleans():
    manager = EmulLabManager()
    topo = topology(project="capture-proof")
    try:
        adaptor = await manager._get(topo)
        if not adaptor.capture_enabled:
            if os.getenv("NETGEO_REQUIRE_CAPTURE_PROOF") == "1":
                pytest.fail("CI capture image missing")
            pytest.skip("local capture image unavailable")
        cid = await manager.start_capture(topo, "a", "a-if", 5)
        await asyncio.sleep(1)
        ping = await manager.ping(topo, "b", "a", "capture-link", 2)
        assert ping["received"] == 2
        await manager._captures[cid].task
        pcap = await manager.capture_bytes(topo, cid)
        assert pcap[:4] == b"\xd4\xc3\xb2\xa1"
        records = []
        pos = 24
        while pos + 16 <= len(pcap):
            _, _, incl, _ = struct.unpack_from("<IIII", pcap, pos)
            pos += 16
            records.append(pcap[pos:pos + incl])
            pos += incl
        assert records and any(
            len(frame) > 34 and frame[12:14] == b"\x08\x00" and frame[23] == 1
            and frame[14 + (frame[14] & 15) * 4] in (0, 8)
            for frame in records
        )
        container = adaptor._owned_container("a")
        assert adaptor._exec(container, ["test", "-e", manager._captures[cid].path])[0] != 0
        assert "tcpdump" not in adaptor._exec(container, ["ps", "-o", "args"])[1]
        await manager.cancel_capture(topo.project.id, cid)
        assert not manager._captures
        cid = await manager.start_capture(topo, "a", "a-if", 5)
        path = manager._captures[cid].path
        await asyncio.sleep(0.5)
        cancel_started = time.monotonic()
        await manager.cancel_capture(topo.project.id, cid)
        assert time.monotonic() - cancel_started < 3
        assert not manager._captures
        assert adaptor._exec(container, ["test", "-e", path])[0] != 0
        assert "tcpdump" not in adaptor._exec(container, ["ps", "-o", "args"])[1]
    finally:
        await manager.shutdown_all()
        assert not subprocess.run(
            ["podman", "ps", "-a", "--filter", "label=netgeo.managed=true", "--format", "{{.Names}}"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        assert not subprocess.run(
            ["podman", "network", "ls", "--filter", "label=netgeo.managed=true", "--format", "{{.Name}}"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
