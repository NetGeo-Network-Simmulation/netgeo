"""First concrete :class:`EmulationAdaptor`: spawns real NOS containers via
the rootless Podman REST socket (``podman-py``).

``spawn``/``destroy`` landed in N-2; ``wire_link``/``destroy_link`` land here
in N-3 (one podman network per link, explicit static IPs — never netavark's
own IPAM, see ``ip_alloc.py``). ``push_config`` (N-5) and ``attach_console``
(ws follow-up) are still stubbed to raise ``NotImplementedError`` on purpose
rather than silently no-op.
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
from engine.emulation.ip_alloc import link_subnet
from engine.emulation.kinds import KINDS
from engine.model import InterfaceModel, LinkModel
from engine.netstack.device import Device

SOCKET_PATH = f"/run/user/{os.getuid()}/podman/podman.sock"
CONTAINER_PREFIX = "netgeo-"
LINK_NETWORK_PREFIX = "netgeo-link-"


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
            # bridge, not the rootless default (pasta) — pasta-mode containers
            # can't join the extra per-link networks wire_link() attaches (N-3).
            network_mode="bridge",
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

    async def wire_link(self, link: LinkModel, a: InterfaceModel, b: InterfaceModel) -> None:
        client = self._get_client()
        cidr, gateway, ip_a, ip_b = link_subnet(link.id)
        network = client.networks.create(
            f"{LINK_NETWORK_PREFIX}{link.id}",
            driver="bridge",
            labels={"netgeo.managed": "true", "netgeo.link": link.id},
            ipam={"Config": [{"Subnet": cidr, "Gateway": gateway}]},
        )
        container_a = client.containers.get(f"{CONTAINER_PREFIX}{a.node_id}")
        container_b = client.containers.get(f"{CONTAINER_PREFIX}{b.node_id}")
        network.connect(container_a, ipv4_address=ip_a)
        network.connect(container_b, ipv4_address=ip_b)

    async def destroy_link(self, link_id: str) -> None:
        client = self._get_client()
        network_name = f"{LINK_NETWORK_PREFIX}{link_id}"
        try:
            network = client.networks.get(network_name)
        except NotFound:
            return
        # The network's own "containers" field (libpod inspect) is not
        # reliably populated on every podman version/timing (see
        # test_link_e2e.py's CI regression) — ask each container directly
        # instead, via its own NetworkSettings.Networks, which is stable.
        for container in client.containers.list(all=True, sparse=False):
            if network_name in ((container.attrs.get("NetworkSettings") or {}).get("Networks") or {}):
                try:
                    network.disconnect(container, force=True)
                except (NotFound, APIError):
                    pass
        # Do not swallow a genuine removal failure (e.g. still in use because
        # a disconnect above silently failed) — that hid this exact bug.
        try:
            network.remove()
        except NotFound:
            pass

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
