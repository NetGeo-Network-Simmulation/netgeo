"""Top-level simulation orchestrator.

``Simulation`` wires a :class:`NetworkModel`, a :class:`Scheduler`, per-node
:class:`NodeRuntime` instances and an :class:`EmulationAdaptor` into a single
runnable object. It exposes:

    - ``inject(packet, at)``       — schedule traffic onto the network
    - ``run(...)``                 — drain the queue (sync, as-fast-as-possible)
    - ``run_realtime(...)``        — async generator streaming events for WS
    - ``snapshot()`` / ``result`` — state for checkpoint/resume & reporting

Determinism: a seeded ``random.Random`` drives every stochastic decision (loss),
so two runs of the same model + seed are identical — the contract behind
"verify in simulation before deploy" (MASTER_SPEC §5).
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field

from engine.emulation import EmulationAdaptor, NullEmulationAdaptor
from engine.events import EventType, SimEvent
from engine.model import NetworkModel
from engine.packet import Packet
from engine.runtime import NodeRuntime
from engine.scheduler import Scheduler

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class SimulationConfig:
    """Knobs for a run."""

    seed: int = 0
    horizon: float | None = None          # stop after this many sim-seconds
    max_events: int | None = None         # hard event cap (runaway guard)
    realtime_factor: float = 0.0          # 0 = as fast as possible; 1.0 = wall-clock
    metric_interval: float = 1.0          # seconds between METRIC_SAMPLE events


@dataclass(slots=True)
class SimulationResult:
    """Outcome of a run, suitable for JSON serialization back to the API."""

    delivered: int = 0
    dropped: int = 0
    injected: int = 0
    drops_by_reason: dict[str, int] = field(default_factory=dict)
    latencies: list[float] = field(default_factory=list)
    sim_time: float = 0.0
    events_dispatched: int = 0

    @property
    def avg_latency(self) -> float:
        return sum(self.latencies) / len(self.latencies) if self.latencies else 0.0

    def as_dict(self) -> dict:
        return {
            "delivered": self.delivered,
            "dropped": self.dropped,
            "injected": self.injected,
            "drops_by_reason": self.drops_by_reason,
            "avg_latency_s": round(self.avg_latency, 6),
            "sim_time_s": round(self.sim_time, 6),
            "events_dispatched": self.events_dispatched,
        }


class Simulation:
    """Owns one run over a :class:`NetworkModel`."""

    def __init__(
        self,
        model: NetworkModel,
        config: SimulationConfig | None = None,
        adaptor: EmulationAdaptor | None = None,
    ) -> None:
        self.model = model
        self.config = config or SimulationConfig()
        self.adaptor = adaptor or NullEmulationAdaptor()
        self.rng = random.Random(self.config.seed)
        self.scheduler = Scheduler(context=self)
        self.result = SimulationResult()
        self.runtimes: dict[str, NodeRuntime] = {
            nid: NodeRuntime(node, model) for nid, node in model.nodes.items()
        }

    # ----- traffic injection -------------------------------------------
    def inject(self, packet: Packet, at: float = 0.0) -> None:
        """Schedule ``packet`` to enter the network at its source at time ``at``."""
        packet.created_at = at
        self.result.injected += 1
        self.scheduler.schedule(
            SimEvent(
                time=at,
                type=EventType.PACKET_RX,
                handler=self.dispatch_receive,
                payload=packet,
                node_id=packet.src,
            )
        )

    # ----- event dispatch hooks (called by scheduler/runtimes) ---------
    def dispatch_receive(self, _sim, event: SimEvent) -> None:
        runtime = self.runtimes.get(event.node_id or "")
        if runtime is None:
            self.record_drop(event.payload, reason="unknown_node")
            return
        runtime.on_receive(self, event)

    def record_delivery(self, packet: Packet) -> None:
        self.result.delivered += 1
        self.result.latencies.append(self.scheduler.now - packet.created_at)

    def record_drop(self, packet: Packet | None, reason: str) -> None:
        self.result.dropped += 1
        self.result.drops_by_reason[reason] = self.result.drops_by_reason.get(reason, 0) + 1

    # ----- runs ---------------------------------------------------------
    def run(self) -> SimulationResult:
        """Synchronous, as-fast-as-possible run. Returns the result."""
        self.scheduler.run(until=self.config.horizon, max_events=self.config.max_events)
        self.result.sim_time = self.scheduler.now
        self.result.events_dispatched = self.scheduler.dispatched
        return self.result

    async def run_realtime(self, batch: int = 256):
        """Async generator yielding progress dicts for WebSocket streaming.

        Drains the queue in batches so the event loop stays responsive and the
        UI gets incremental telemetry. ``realtime_factor`` paces the run against
        the wall clock (0 = no pacing). Designed to back ``/ws/topology``.
        """
        wall_start = time.monotonic()
        while self.scheduler.queue:
            dispatched = self.scheduler.run(
                until=self.config.horizon, max_events=batch
            )
            if dispatched == 0:
                break
            self.result.sim_time = self.scheduler.now
            self.result.events_dispatched = self.scheduler.dispatched

            if self.config.realtime_factor > 0:
                target = wall_start + self.scheduler.now / self.config.realtime_factor
                drift = target - time.monotonic()
                if drift > 0:
                    await asyncio.sleep(drift)
            else:
                await asyncio.sleep(0)  # cooperative yield

            yield {
                "sim_time": round(self.scheduler.now, 6),
                "delivered": self.result.delivered,
                "dropped": self.result.dropped,
                "pending_events": len(self.scheduler.queue),
            }
        self.result.sim_time = self.scheduler.now
