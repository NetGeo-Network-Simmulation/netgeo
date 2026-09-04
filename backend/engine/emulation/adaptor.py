"""Abstract emulation backend + a null implementation.

The ABC is intentionally narrow: lifecycle (spawn/destroy), config push, console
attach, and link wiring. Concrete adaptors (containerlab/Docker/Podman) realise
these against a real runtime. Keeping the surface small is what lets a single
simulation mix ``sim`` and ``emul`` nodes transparently — the orchestrator only
ever talks to this interface.

All methods are ``async`` because real backends do network/subprocess I/O; the
web layer is already async (FastAPI), so this composes naturally and never
blocks the event loop.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum

from engine.model import InterfaceModel, LinkModel
from engine.netstack.device import Device


class EmulationStatus(str, Enum):
    """Lifecycle state of an emulated node's backing container."""

    ABSENT = "absent"
    PROVISIONING = "provisioning"
    RUNNING = "running"
    DEGRADED = "degraded"
    STOPPED = "stopped"
    FAILED = "failed"


@dataclass(slots=True)
class EmulatedNodeHandle:
    """Opaque handle to a running emulated node returned by an adaptor."""

    node_id: str
    container_id: str
    status: EmulationStatus = EmulationStatus.ABSENT
    mgmt_ip: str | None = None
    meta: dict[str, str] = field(default_factory=dict)


class EmulationAdaptor(ABC):
    """Contract every emulation backend must satisfy.

    Implementations live alongside this module (e.g. ``containerlab.py``).
    """

    name: str = "abstract"

    @abstractmethod
    async def spawn(self, device: Device) -> EmulatedNodeHandle:
        """Create and boot the container that emulates ``device``.

        ``device.nos`` is the kind key into :data:`engine.emulation.kinds.KINDS`
        (e.g. ``"frr"``); ``device.node_id`` identifies the resulting handle.
        """

    @abstractmethod
    async def destroy(self, node_id: str) -> None:
        """Tear down the container backing ``node_id`` (idempotent)."""

    @abstractmethod
    async def push_config(self, node_id: str, config: str, fmt: str = "cli") -> None:
        """Apply a generated config artifact (cli|netconf|yaml) to the node."""

    @abstractmethod
    async def wire_link(self, link: LinkModel, a: InterfaceModel, b: InterfaceModel) -> None:
        """Create the veth/bridge/network realising ``link`` between the nodes
        owning interfaces ``a`` and ``b``.

        ``LinkModel`` alone only names interface ids, not which node each one
        belongs to — callers must resolve and pass the two ``InterfaceModel``
        endpoints explicitly (N-3).
        """

    @abstractmethod
    async def destroy_link(self, link_id: str) -> None:
        """Tear down whatever ``wire_link`` created for this link (idempotent)."""

    @abstractmethod
    async def status(self, node_id: str) -> EmulationStatus:
        """Current lifecycle state of the backing container."""

    @abstractmethod
    async def attach_console(self, node_id: str):
        """Return an async byte stream for ``/ws/console/{node_id}`` relay.

        Implementations should yield ``bytes`` chunks (e.g. an ``AsyncIterator``)
        so the WebSocket layer can forward console output to the UI.
        """


class NullEmulationAdaptor(EmulationAdaptor):
    """No-op adaptor: lets a pure-sim run proceed without a container runtime.

    Used in CI and whenever Docker/containerlab is unavailable. Any node set to
    ``mode="emul"`` is treated as ``sim`` for the run, and a clear status is
    reported so the UI can surface "emulation unavailable".
    """

    name = "null"

    async def spawn(self, device: Device) -> EmulatedNodeHandle:
        return EmulatedNodeHandle(
            node_id=device.node_id,
            container_id="",
            status=EmulationStatus.ABSENT,
            meta={"reason": "no emulation runtime configured"},
        )

    async def destroy(self, node_id: str) -> None:
        return None

    async def push_config(self, node_id: str, config: str, fmt: str = "cli") -> None:
        return None

    async def wire_link(self, link: LinkModel, a: InterfaceModel, b: InterfaceModel) -> None:
        return None

    async def destroy_link(self, link_id: str) -> None:
        return None

    async def status(self, node_id: str) -> EmulationStatus:
        return EmulationStatus.ABSENT

    async def attach_console(self, node_id: str):
        async def _empty():
            if False:  # pragma: no cover - makes this an async generator
                yield b""
        return _empty()
