"""Persistent FRR containers for one active pure-emulation project lab."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

from engine.emulation.podman_adaptor import (
    CONTAINER_PREFIX,
    LINK_NETWORK_PREFIX,
    PodmanAdaptor,
    socket_reachable,
)
from engine.model import InterfaceModel, LinkModel
from engine.netstack.device import Device

from app.exceptions.base import SimulationError
from app.models import NodeMode, Topology

if os.name == "posix" and hasattr(os, "getuid"):
    _runtime_dir = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
    if not _runtime_dir.is_dir():
        _runtime_dir = Path(tempfile.gettempdir()) / f"netgeo-{os.getuid()}"
    LEASE_PATH: Path | None = _runtime_dir / "netgeo-emulation.lock"
else:
    LEASE_PATH = None


class EmulationLeaseUnavailable(SimulationError):
    pass


@dataclass
class CaptureSession:
    project_id: str
    node_id: str
    interface_id: str
    fingerprint: str
    path: str
    task: asyncio.Task | None = None
    state: str = "running"
    pcap: bytes | None = None
    error: str | None = None


class EmulLabManager:
    def __init__(self) -> None:
        self._labs: dict[str, tuple[str, PodmanAdaptor, list[str], list[str]]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._lease_fd: int | None = None
        self._lease_pid: int | None = None
        self._captures: dict[str, CaptureSession] = {}

    def _holds_lease(self) -> bool:
        return self._lease_fd is not None and self._lease_pid == os.getpid()

    def acquire_lease(self) -> None:
        if LEASE_PATH is None or os.name != "posix":
            raise EmulationLeaseUnavailable("emulation unavailable: Podman rootless requires Linux")
        import fcntl

        if self._lease_fd is not None and self._lease_pid == os.getpid():
            return
        if self._lease_fd is not None:  # inherited across fork; never share the parent's lease
            os.close(self._lease_fd)
            self._lease_fd = None
            self._lease_pid = None
        LEASE_PATH.parent.mkdir(mode=0o700, exist_ok=True)
        runtime_stat = LEASE_PATH.parent.stat()
        if runtime_stat.st_uid != os.getuid() or runtime_stat.st_mode & 0o077:
            raise EmulationLeaseUnavailable("emulation unavailable: insecure runtime directory")
        fd = os.open(LEASE_PATH, os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(fd)
            raise EmulationLeaseUnavailable("emulation unavailable: another NetGeo backend owns the Podman lab") from exc
        self._lease_fd = fd
        self._lease_pid = os.getpid()

    def release_lease(self) -> None:
        if self._lease_fd is not None:
            import fcntl

            fd, self._lease_fd = self._lease_fd, None
            if self._lease_pid == os.getpid():
                fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
            self._lease_pid = None

    def _lock(self, project_id: str) -> asyncio.Lock:
        return self._locks.setdefault(project_id, asyncio.Lock())

    async def destroy(self, project_id: str) -> None:
        async with self._lock(project_id):
            await self._destroy(project_id)

    async def shutdown_all(self) -> None:
        """Try every lab even if one project fails to clean up."""
        errors = []
        try:
            for project_id in list(self._labs):
                try:
                    await self.destroy(project_id)
                except Exception as exc:  # noqa: BLE001 - isolate per-project cleanup
                    errors.append(f"{project_id}: {exc}")
        finally:
            self.release_lease()
        if errors:
            raise SimulationError("emulation shutdown cleanup failed: " + "; ".join(errors))

    async def reconcile_stale(self) -> None:
        """Remove only our labeled containers and link networks after a forced exit.

        Names and all three ownership labels must match. Older/unlabeled
        resources and other Podman workloads are never touched.
        """
        self.acquire_lease()
        if not socket_reachable():
            return
        adaptor = PodmanAdaptor()
        client = adaptor._get_client()

        def owned(labels) -> bool:
            return bool(labels and labels.get("netgeo.managed") == "true"
                        and labels.get("netgeo.project") and labels.get("netgeo.lab"))

        def network_labels(network):
            attrs = network.attrs or {}
            return attrs.get("Labels") or attrs.get("labels") or {}

        networks = [n for n in client.networks.list()
                    if n.name.startswith(LINK_NETWORK_PREFIX)
                    and owned(network_labels(n))
                    and network_labels(n).get("netgeo.link") == n.name[len(LINK_NETWORK_PREFIX):]]
        containers = [c for c in client.containers.list(all=True)
                      if c.name.startswith(CONTAINER_PREFIX)
                      and owned(c.labels)
                      and (c.labels or {}).get("netgeo.kind") == "frr"]
        errors = []
        for network in networks:
            try:
                await adaptor.destroy_link(network.name[len(LINK_NETWORK_PREFIX):])
            except Exception as exc:  # noqa: BLE001 - continue with other resources
                errors.append(f"network {network.name}: {exc}")
        for container in containers:
            try:
                await adaptor.destroy(container.name[len(CONTAINER_PREFIX):])
            except Exception as exc:  # noqa: BLE001 - continue with other resources
                errors.append(f"container {container.name}: {exc}")
        if errors:
            raise SimulationError("stale NetGeo cleanup failed: " + "; ".join(errors))

    async def _destroy(self, project_id: str) -> None:
        for capture_id, session in list(self._captures.items()):
            if session.project_id == project_id:
                await self.cancel_capture(project_id, capture_id)
        lab = self._labs.get(project_id)
        if lab is None:
            return
        self.acquire_lease()
        _, adaptor, nodes, links = lab
        errors = []
        for link_id in reversed(links):
            try:
                await adaptor.destroy_link(link_id)
            except Exception as exc:  # noqa: BLE001 - continue cleaning remaining resources
                errors.append(exc)
        for node_id in reversed(nodes):
            try:
                await adaptor.destroy(node_id)
            except Exception as exc:  # noqa: BLE001 - continue cleaning remaining resources
                errors.append(exc)
        if errors:
            raise SimulationError(f"emulation cleanup failed: {errors[0]}")
        self._labs.pop(project_id, None)

    async def _get(self, topo: Topology) -> PodmanAdaptor:
        if not self._holds_lease():
            await self.reconcile_stale()
        pid = topo.project.id
        fingerprint = self._fingerprint(topo)
        cached = self._labs.get(pid)
        if cached and cached[0] == fingerprint:
            return cached[1]
        await self._destroy(pid)
        if topo.project.mode != "pure-emul" or any(n.mode != NodeMode.emul for n in topo.nodes):
            raise SimulationError("mixed-mode forwarding unsupported: all lab nodes must be emulated")
        adaptor = PodmanAdaptor(project_id=pid, lab_id=fingerprint[:16], lease_guard=self._holds_lease)
        if hasattr(adaptor, "use_local_capture_image"):
            adaptor.use_local_capture_image()
        nodes: list[str] = []
        links: list[str] = []
        self._labs[pid] = (fingerprint, adaptor, nodes, links)
        try:
            for node in topo.nodes:
                nodes.append(node.id)  # also clean a partially created container
                await adaptor.spawn(Device(name=node.name, node_id=node.id, nos=str(node.nos), mode="emul"))
            ifaces = {i.id: (node, i) for node in topo.nodes for i in node.interfaces}
            for link in topo.links:
                if link.a_iface not in ifaces or link.b_iface not in ifaces:
                    raise SimulationError(f"emulation link {link.id!r} has an unknown endpoint")
                a_node, a = ifaces[link.a_iface]
                b_node, b = ifaces[link.b_iface]
                links.append(link.id)  # clean a partially created network
                await adaptor.wire_link(
                    LinkModel(id=link.id, a_iface=a.id, b_iface=b.id),
                    InterfaceModel(id=a.id, node_id=a_node.id, name=a.name),
                    InterfaceModel(id=b.id, node_id=b_node.id, name=b.name),
                )
        except Exception:
            await self._destroy(pid)
            raise
        return adaptor

    @staticmethod
    def _fingerprint(topo: Topology) -> str:
        return hashlib.sha256(json.dumps(
            {"nodes": [n.model_dump(mode="json", exclude={"config_ref"}) for n in topo.nodes],
             "links": [l.model_dump(mode="json") for l in topo.links]},
            sort_keys=True,
        ).encode()).hexdigest()

    def capture_available(self, topo: Topology) -> bool:
        lab = self._labs.get(topo.project.id)
        return bool(lab and lab[0] == self._fingerprint(topo) and self._holds_lease()
                    and getattr(lab[1], "capture_enabled", False))

    async def start_capture(self, topo: Topology, node_id: str, interface_id: str, duration: int = 15) -> str:
        pid = topo.project.id
        if sys.platform != "linux":
            raise SimulationError("emulated capture unsupported: rootless Podman requires Linux")
        if not 1 <= duration <= 15:
            raise SimulationError("capture duration must be 1 to 15 seconds")
        async with self._lock(pid):
            node = next((n for n in topo.nodes if n.id == node_id), None)
            if topo.project.mode != "pure-emul" or node is None or node.mode != NodeMode.emul or str(node.nos) != "frr":
                raise SimulationError("capture target must be an FRR node in this pure-emul project")
            if not any(i.id == interface_id for i in node.interfaces):
                raise SimulationError("interface does not belong to capture node")
            link = next((l for l in topo.links if interface_id in (l.a_iface, l.b_iface)), None)
            if link is None:
                raise SimulationError("capture interface is not linked")
            adaptor = await self._get(topo)
            if not adaptor.capture_enabled:
                raise SimulationError("emulated capture unsupported: local FRR capture image is unavailable; build it explicitly")
            if sum(s.project_id == pid for s in self._captures.values()) >= 4 or len(self._captures) >= 32:
                raise SimulationError("capture session limit reached; delete an earlier capture")
            if any(s.project_id == pid and s.node_id == node_id and s.state == "running" for s in self._captures.values()):
                raise SimulationError("one active capture per node is allowed")
            interface = await asyncio.to_thread(adaptor.capture_interface, node_id, link.id)
            capture_id = uuid.uuid4().hex
            path = f"/tmp/netgeo-capture-{capture_id}.pcap"
            session = CaptureSession(pid, node_id, interface_id, self._fingerprint(topo), path)
            self._captures[capture_id] = session
            session.task = asyncio.create_task(self._run_capture(adaptor, session, interface, duration))
            return capture_id

    async def _run_capture(self, adaptor: PodmanAdaptor, session: CaptureSession, interface: str, duration: int) -> None:
        try:
            data = await asyncio.to_thread(adaptor.capture_pcap, session.node_id, interface, session.path, duration)
            if session.state == "running" and self._holds_lease():
                session.pcap = data
                session.state = "complete"
        except Exception as exc:  # noqa: BLE001 - present bounded failure without leaking container details
            if session.state == "running":
                session.error = type(exc).__name__
                session.state = "error"

    async def capture_status(self, topo: Topology, capture_id: str) -> dict:
        session = self._captures.get(capture_id)
        if session is None or session.project_id != topo.project.id:
            raise SimulationError("capture not found in this project")
        if session.fingerprint != self._fingerprint(topo) or not self._holds_lease():
            await self.cancel_capture(topo.project.id, capture_id)
            raise SimulationError("capture expired with the lab topology or lease")
        return {"id": capture_id, "node_id": session.node_id, "interface_id": session.interface_id,
                "state": session.state, "size": len(session.pcap or b""), "error": session.error}

    async def capture_bytes(self, topo: Topology, capture_id: str) -> bytes:
        status = await self.capture_status(topo, capture_id)
        if status["state"] != "complete":
            raise SimulationError("capture result is not complete")
        return self._captures[capture_id].pcap or b""

    async def cancel_capture(self, project_id: str, capture_id: str) -> None:
        session = self._captures.get(capture_id)
        if session is None or session.project_id != project_id:
            raise SimulationError("capture not found in this project")
        session.state = "cancelled"
        session.pcap = None
        lab = self._labs.get(project_id)
        if lab and self._holds_lease():
            try:
                await asyncio.to_thread(lab[1].stop_capture, session.node_id, session.path)
            except Exception:
                pass  # destroy still removes owned container; timeout caps capture at 15s
        if session.task:
            await session.task
        self._captures.pop(capture_id, None)

    async def ping(self, topo: Topology, src_id: str, dst_id: str, link_id: str, count: int) -> dict:
        pid = topo.project.id
        async with self._lock(pid):
            adaptor = await self._get(topo)
            dest_ip = await adaptor.link_address(dst_id, link_id)
            result = await adaptor.ping(src_id, dest_ip, count=count)
            return {**result, "src": src_id, "dst": dst_id, "path": "emulation"}

    async def push_config(self, topo: Topology, node_id: str, content: str, fmt: str) -> dict:
        async with self._lock(topo.project.id):
            if topo.project.mode != "pure-emul" or not any(
                n.id == node_id and n.mode == NodeMode.emul and str(n.nos) == "frr"
                for n in topo.nodes
            ):
                raise SimulationError("configuration target must be an FRR node in a pure-emul project")
            adaptor = await self._get(topo)
            return await adaptor.push_config(node_id, content, fmt)

    async def command(self, topo: Topology, node_id: str, command: str) -> str:
        async with self._lock(topo.project.id):
            if topo.project.mode != "pure-emul" or not any(
                n.id == node_id and n.mode == NodeMode.emul and str(n.nos) == "frr"
                for n in topo.nodes
            ):
                raise SimulationError("console target must be an FRR node in a pure-emul project")
            adaptor = await self._get(topo)
            return await adaptor.command(node_id, command)


_manager = EmulLabManager()


def get_emul_lab_manager() -> EmulLabManager:
    return _manager
