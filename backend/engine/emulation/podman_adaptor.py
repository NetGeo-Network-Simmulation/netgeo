"""First concrete :class:`EmulationAdaptor`: spawns real NOS containers via
the rootless Podman REST socket (``podman-py``).

Only ``spawn``/``destroy`` are implemented in this slice (N-2) — ``wire_link``
(N-3), ``push_config`` (N-5) and ``attach_console`` (ws follow-up) are stubbed
to raise ``NotImplementedError`` on purpose rather than silently no-op.
"""
from __future__ import annotations

import os
import socket as _socket

from podman import PodmanClient
from podman.errors import APIError, NotFound

from engine.emulation.adaptor import (
    EmulatedNodeHandle,
    EmulationAdaptor,
    EmulationStatus,
)
from engine.emulation.kinds import KINDS
from engine.model import LinkModel
from engine.netstack.device import Device

SOCKET_PATH = f"/run/user/{os.getuid()}/podman/podman.sock"
CONTAINER_PREFIX = "netgeo-"


def socket_reachable(path: str = SOCKET_PATH) -> bool:
    """True if the rootless Podman REST socket exists and accepts a connection."""
    if not os.path.exists(path):
        return False
    sock = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
    try:
        sock.connect(path)
        return True
    except OSError:
        return False
    finally:
        sock.close()


class PodmanSocketUnreachable(RuntimeError):
    def __init__(self) -> None:
        super().__init__(
            f"podman REST socket unreachable at {SOCKET_PATH} — enable it with: "
            "systemctl --user enable --now podman.socket"
        )


class UnknownKind(RuntimeError):
    pass


class ImageNotLocal(RuntimeError):
    """Raised for EULA-gated kinds whose image the user must fetch themselves."""

    def __init__(self, kind: str, image: str) -> None:
        super().__init__(
            f"kind {kind!r} needs image {image!r}, which is not present locally. "
            f"NetGeo never pulls EULA-gated images — download it yourself per the "
            f"vendor's license (see docs/byoi/{kind}.md), then `podman pull {image}`."
        )


class PodmanAdaptor(EmulationAdaptor):
    """Spawns/destroys containers over the rootless Podman REST socket."""

    name = "podman"

    def __init__(self) -> None:
        self._client = None  # lazy: only import/connect podman on first use

    def _get_client(self):
        if self._client is None:
            if not socket_reachable():
                raise PodmanSocketUnreachable
            self._client = PodmanClient(base_url=f"unix://{SOCKET_PATH}")
        return self._client

    async def spawn(self, device: Device) -> EmulatedNodeHandle:
        kind = device.nos
        spec = KINDS.get(kind)
        if spec is None:
            raise UnknownKind(f"unregistered emulation kind {kind!r}")

        client = self._get_client()
        if not client.images.exists(spec.image):
            if not spec.bundlable:
                raise ImageNotLocal(kind, spec.image)
            client.images.pull(spec.image)

        name = f"{CONTAINER_PREFIX}{device.node_id}"
        container = client.containers.create(
            spec.image,
            name=name,
            cap_add=spec.caps,
            mem_limit=f"{spec.mem_limit_mb}m",
            labels={"netgeo.managed": "true", "netgeo.kind": kind},
            detach=True,
        )
        container.start()
        container.reload()
        return EmulatedNodeHandle(
            node_id=device.node_id,
            container_id=container.id,
            status=EmulationStatus.RUNNING
            if container.status == "running"
            else EmulationStatus.PROVISIONING,
            meta={"name": name, "kind": kind},
        )

    async def destroy(self, node_id: str) -> None:
        client = self._get_client()
        try:
            container = client.containers.get(f"{CONTAINER_PREFIX}{node_id}")
        except NotFound:
            return  # already gone — idempotent
        try:
            container.stop()
        except (NotFound, APIError):
            pass
        try:
            container.remove(force=True)
        except NotFound:
            pass

    async def push_config(self, node_id: str, config: str, fmt: str = "cli") -> None:
        raise NotImplementedError("push_config lands in N-5")

    async def wire_link(self, link: LinkModel) -> None:
        raise NotImplementedError("wire_link lands in N-3")

    async def status(self, node_id: str) -> EmulationStatus:
        client = self._get_client()
        try:
            container = client.containers.get(f"{CONTAINER_PREFIX}{node_id}")
        except NotFound:
            return EmulationStatus.ABSENT
        container.reload()
        return EmulationStatus.RUNNING if container.status == "running" else EmulationStatus.STOPPED

    async def attach_console(self, node_id: str):
        raise NotImplementedError("attach_console lands with ws.py wiring")
