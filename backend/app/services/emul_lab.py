"""Persistent FRR containers for one active pure-emulation project lab."""
from __future__ import annotations

import asyncio
import hashlib
import json

from app.exceptions.base import SimulationError
from app.models import NodeMode, Topology
from engine.emulation.ip_alloc import link_subnet
from engine.emulation.podman_adaptor import (
    CONTAINER_PREFIX,
    LINK_NETWORK_PREFIX,
    PodmanAdaptor,
    socket_reachable,
)
from engine.model import InterfaceModel, LinkModel
from engine.netstack.device import Device


class EmulLabManager:
    def __init__(self) -> None:
        self._labs: dict[str, tuple[str, PodmanAdaptor, list[str], list[str]]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock(self, project_id: str) -> asyncio.Lock:
        return self._locks.setdefault(project_id, asyncio.Lock())

    async def destroy(self, project_id: str) -> None:
        async with self._lock(project_id):
            await self._destroy(project_id)

    async def shutdown_all(self) -> None:
        """Try every lab even if one project fails to clean up."""
        errors = []
        for project_id in list(self._labs):
            try:
                await self.destroy(project_id)
            except Exception as exc:  # noqa: BLE001 - isolate per-project cleanup
                errors.append(f"{project_id}: {exc}")
        if errors:
            raise SimulationError("emulation shutdown cleanup failed: " + "; ".join(errors))

    async def reconcile_stale(self) -> None:
        """Remove only our labeled containers and link networks after a forced exit.

        Names and all three ownership labels must match. Older/unlabeled
        resources and other Podman workloads are never touched.
        """
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
        lab = self._labs.get(project_id)
        if lab is None:
            return
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
        pid = topo.project.id
        fingerprint = hashlib.sha256(json.dumps(
            {"nodes": [n.model_dump(mode="json") for n in topo.nodes],
             "links": [l.model_dump(mode="json") for l in topo.links]},
            sort_keys=True,
        ).encode()).hexdigest()
        cached = self._labs.get(pid)
        if cached and cached[0] == fingerprint:
            return cached[1]
        await self._destroy(pid)
        if topo.project.mode != "pure-emul" or any(n.mode != NodeMode.emul for n in topo.nodes):
            raise SimulationError("mixed-mode forwarding unsupported: all lab nodes must be emulated")
        adaptor = PodmanAdaptor(project_id=pid, lab_id=fingerprint[:16])
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

    async def ping(self, topo: Topology, src_id: str, dst_id: str, link_id: str, count: int) -> dict:
        pid = topo.project.id
        async with self._lock(pid):
            adaptor = await self._get(topo)
            _, _, ip_a, ip_b = link_subnet(link_id)
            link = next(l for l in topo.links if l.id == link_id)
            src = next(n for n in topo.nodes if n.id == src_id)
            src_ifaces = {i.id for i in src.interfaces}
            dest_ip = ip_b if link.a_iface in src_ifaces else ip_a
            result = await adaptor.ping(src_id, dest_ip, count=count)
            return {**result, "src": src_id, "dst": dst_id, "path": "emulation"}


_manager = EmulLabManager()


def get_emul_lab_manager() -> EmulLabManager:
    return _manager
