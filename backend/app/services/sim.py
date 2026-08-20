"""Simulation service — adapts the persistence model to the engine.

Builds an engine :class:`NetworkModel` (engine's own shape) from a stored
:class:`Topology`, runs the discrete-event kernel, and returns a JSON-able
result. Bits/s ↔ Mbps conversion happens here: the API/schema speak **Mbps**
(human-friendly), the engine speaks **bits/s** (physics).
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass, field
from functools import lru_cache

from app.models import Topology
from app.services.events import get_bus
from engine import (
    InterfaceModel,
    LinkModel,
    NetworkModel,
    NodeModel,
    Packet,
    Simulation,
    SimulationConfig,
)

logger = logging.getLogger("netgeo.sim")

_MBPS = 1_000_000  # Mbps -> bits/s

# How many events the realtime driver dispatches between telemetry ticks. Small
# enough that pause/stop feel instant; large enough to stay cheap on big runs.
_TICK_BATCH = 128


def build_model(topo: Topology) -> NetworkModel:
    model = NetworkModel()
    for n in topo.nodes:
        model.add_node(
            NodeModel(
                id=n.id,
                name=n.name,
                kind=str(n.kind),
                nos=str(n.nos),
                mode=str(n.mode),
                interfaces=[
                    InterfaceModel(
                        id=i.id,
                        node_id=n.id,
                        name=i.name,
                        type=str(i.type),
                        ips=list(i.ip),
                        mac=i.mac,
                        speed=i.speed * _MBPS,
                        mtu=i.mtu,
                        peer_link_id=i.peer_link_id,
                    )
                    for i in n.interfaces
                ],
                status="up",
            )
        )
    for l in topo.links:
        model.add_link(
            LinkModel(
                id=l.id,
                a_iface=l.a_iface,
                b_iface=l.b_iface,
                type=str(l.type),
                bandwidth=l.bandwidth * _MBPS,
                delay=l.delay / 1000.0,   # ms -> s
                loss=l.loss,
                mtu=l.mtu,
                # Honor operational state: a link toggled down/admin_down in the
                # UI must actually stop carrying traffic. Without this the engine
                # left every link up=True, so disabling a link had no effect on
                # the run (packets were still delivered over a "down" link).
                up=str(l.status) == "up",
            )
        )
    return model


def _seed_probes(sim: Simulation, model: NetworkModel) -> int:
    """Inject one probe from each of the first few nodes to a reachable peer.

    Shared by the synchronous and realtime runners so both exercise the exact
    same traffic — a result you watch live must match the one you get headless.
    """
    node_ids = list(model.nodes)
    probes = 0
    # probe sparsely so large topologies stay cheap
    for src in node_ids[: min(len(node_ids), 8)]:
        for dst in node_ids:
            if src == dst:
                continue
            if model.shortest_path(src, dst):
                sim.inject(Packet(src=src, dst=dst, proto="icmp"))
                probes += 1
                break
    return probes


def build_simulation(
    topo: Topology, seed: int = 0, horizon: float | None = None
) -> tuple[Simulation, int]:
    """Build a ready-to-run :class:`Simulation` with probe traffic injected."""
    model = build_model(topo)
    sim = Simulation(model, SimulationConfig(seed=seed, horizon=horizon))
    probes = _seed_probes(sim, model)
    return sim, probes


def run_once(topo: Topology, seed: int = 0, horizon: float | None = None) -> dict:
    """Build the model, inject probes between reachable hosts, run synchronously,
    and return a result dict. A minimal-but-real proof that the kernel processes
    the user's topology."""
    sim, probes = build_simulation(topo, seed=seed, horizon=horizon)
    result = sim.run().as_dict()
    result["probes_injected"] = probes
    result["topology"] = sim.model.stats
    return result


# --- realtime run manager ---------------------------------------------------
#
# The synchronous ``run_once`` is fine for a headless "give me the numbers" call,
# but the UI's transport bar expects a *live* run: press Play and watch telemetry
# stream while pause/step/stop actually steer the engine. That contract lives
# here. A run is a background task draining the scheduler in small batches and
# publishing ``sim.tick`` events onto the same in-process bus the ``/ws/topology``
# socket subscribes to, so every connected client sees progress without polling.


@dataclass
class _SimRun:
    project_id: str
    sim: Simulation
    probes: int
    state: str = "running"          # idle|running|paused|stopped|completed
    stopped: bool = False
    task: asyncio.Task | None = None
    # gate is *set* while running, *cleared* while paused; the driver awaits it.
    gate: asyncio.Event = field(default_factory=asyncio.Event)

    def status(self) -> dict:
        return {
            "project_id": self.project_id,
            "state": self.state,
            "sim_time": round(self.sim.scheduler.now, 6),
            "metrics": _metrics(self.sim),
        }


def _metrics(sim: Simulation) -> dict[str, float]:
    res = sim.result
    return {
        "delivered": res.delivered,
        "dropped": res.dropped,
        "injected": res.injected,
        "pending_events": len(sim.scheduler.queue),
        "events_dispatched": sim.scheduler.dispatched,
        "avg_latency_s": round(res.avg_latency, 6),
    }


class SimManager:
    """Owns the at-most-one live run per project and its state machine."""

    def __init__(self) -> None:
        self._runs: dict[str, _SimRun] = {}

    # ----- telemetry ----------------------------------------------------
    def _publish(self, run: _SimRun) -> None:
        """Emit a ``sim.tick`` for ``run``. Best-effort: a broadcast failure must
        never crash the engine driver."""
        try:
            get_bus().publish(
                {
                    "type": "sim.tick",
                    "t": round(run.sim.scheduler.now, 6),
                    "metrics": _metrics(run.sim),
                    "state": run.state,
                },
                run.project_id,
            )
        except Exception:  # pragma: no cover - telemetry is best-effort
            logger.exception("sim.tick publish failed for %s", run.project_id)

    # ----- lifecycle ----------------------------------------------------
    async def start(self, topo: Topology, *, seed: int = 0, horizon: float | None = None) -> dict:
        """Start (or restart) a live run for ``topo``'s project."""
        pid = topo.project.id
        await self._cancel(pid)
        sim, probes = build_simulation(topo, seed=seed, horizon=horizon)
        run = _SimRun(project_id=pid, sim=sim, probes=probes, state="running")
        run.gate.set()
        self._runs[pid] = run
        run.task = asyncio.create_task(self._drive(run))
        return run.status()

    async def _drive(self, run: _SimRun) -> None:
        sched = run.sim.scheduler
        try:
            while sched.queue and not run.stopped:
                await run.gate.wait()           # parks here while paused
                if run.stopped:
                    break
                n = sched.run(until=run.sim.config.horizon, max_events=_TICK_BATCH)
                run.sim.result.sim_time = sched.now
                run.sim.result.events_dispatched = sched.dispatched
                self._publish(run)
                if n == 0:                       # horizon reached, nothing left to do
                    break
                await asyncio.sleep(0)           # cooperative yield
            run.state = "stopped" if run.stopped else "completed"
            self._publish(run)
        except asyncio.CancelledError:
            raise
        except Exception:
            run.state = "error"
            self._publish(run)
            logger.exception("sim driver crashed for %s", run.project_id)

    def pause(self, project_id: str) -> dict:
        run = self._runs.get(project_id)
        if run and run.state == "running":
            run.gate.clear()
            run.state = "paused"
            self._publish(run)
        return self.status(project_id)

    def resume(self, project_id: str) -> dict:
        run = self._runs.get(project_id)
        if run and run.state == "paused":
            run.state = "running"
            run.gate.set()
            self._publish(run)
        return self.status(project_id)

    async def step(
        self, topo: Topology, *, seed: int = 0, horizon: float | None = None
    ) -> dict:
        """Advance one batch of events, leaving the run paused.

        Bootstraps a fresh paused run if none exists. Safe to touch the scheduler
        directly: the driver task (if any) is parked at its ``gate``/``sleep``
        await, and the loop is single-threaded, so there is no concurrent access.
        """
        pid = topo.project.id
        run = self._runs.get(pid)
        if run is None or run.state in ("completed", "stopped", "error"):
            sim, probes = build_simulation(topo, seed=seed, horizon=horizon)
            run = _SimRun(project_id=pid, sim=sim, probes=probes, state="paused")
            run.gate.clear()
            self._runs[pid] = run
            run.task = asyncio.create_task(self._drive(run))
        else:
            run.state = "paused"
            run.gate.clear()

        sched = run.sim.scheduler
        n = sched.run(until=run.sim.config.horizon, max_events=_TICK_BATCH)
        run.sim.result.sim_time = sched.now
        run.sim.result.events_dispatched = sched.dispatched
        if not sched.queue or n == 0:
            run.state = "completed"
        self._publish(run)
        return run.status()

    async def stop(self, project_id: str) -> dict:
        await self._cancel(project_id)
        return {"project_id": project_id, "state": "idle", "sim_time": 0.0, "metrics": {}}

    async def _cancel(self, project_id: str) -> None:
        run = self._runs.pop(project_id, None)
        if run is None:
            return
        run.stopped = True
        run.gate.set()  # unblock a paused driver so it can observe `stopped`
        if run.task is not None:
            run.task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await run.task

    def status(self, project_id: str) -> dict:
        run = self._runs.get(project_id)
        if run is None:
            return {"project_id": project_id, "state": "idle", "sim_time": 0.0, "metrics": {}}
        return run.status()


@lru_cache(maxsize=1)
def get_manager() -> SimManager:
    """Process-wide singleton run manager (v0.1 single-worker; Redis-backed in prod)."""
    return SimManager()
