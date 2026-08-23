"""Continuous lab runner — the Jalur B (netstack) equivalent of
``test_sim_service.py``'s realtime-run tests.

Guards ``app.services.netlab.LabRunManager``: /lab's background
run/pause/resume/step/stop lifecycle and its ``sim.tick`` telemetry, matching
the contract Jalur A's ``SimManager`` already provides (S3-a — prerequisite
for retiring Jalur A without losing user-facing functionality).
"""
from __future__ import annotations

import asyncio

import pytest

from app.models import Interface, Link, Node, Project, Topology
from app.services.events import get_bus
from app.services.netlab import get_lab_manager, get_lab_run_manager


def _topo(pid: str) -> Topology:
    proj = Project(id=pid, name=pid)
    a = Node(
        id=f"{pid}-A", project_id=pid, name="A", kind="host",
        interfaces=[Interface(id=f"{pid}-A-e0", node_id=f"{pid}-A", name="e0",
                               ip=["10.0.0.1/24"])],
    )
    b = Node(
        id=f"{pid}-B", project_id=pid, name="B", kind="host",
        interfaces=[Interface(id=f"{pid}-B-e0", node_id=f"{pid}-B", name="e0",
                               ip=["10.0.0.2/24"])],
    )
    link = Link(id=f"{pid}-lAB", project_id=pid, a_iface=f"{pid}-A-e0",
                b_iface=f"{pid}-B-e0", bandwidth=1000, delay=1.0, status="up")
    return Topology(project=proj, nodes=[a, b], links=[link])


@pytest.fixture(autouse=True)
def _fresh_labs():
    get_lab_manager()._labs.clear()
    get_lab_run_manager()._runs.clear()
    yield
    get_lab_manager()._labs.clear()
    get_lab_run_manager()._runs.clear()


async def test_lab_run_pause_resume_stop_lifecycle():
    topo = _topo("p-lifecycle")
    pid = topo.project.id
    mgr = get_lab_run_manager()

    start = await mgr.start(topo)
    assert start["state"] == "running"

    paused = mgr.pause(pid)
    assert paused["state"] == "paused"
    t_paused = paused["sim_time"]

    # give the parked driver task several loop turns; time must not advance.
    for _ in range(5):
        await asyncio.sleep(0)
    assert mgr.status(pid)["sim_time"] == t_paused

    resumed = mgr.resume(pid)
    assert resumed["state"] == "running"

    stopped = await mgr.stop(pid)
    assert stopped["state"] == "idle"


async def test_lab_step_advances_deterministically():
    """Two identical (same seed) topologies stepped once from a fresh lab
    must land on the same sim_time and metrics — no wall-clock leakage."""
    mgr = get_lab_run_manager()

    r1 = await mgr.step(_topo("p-det-1"))
    r2 = await mgr.step(_topo("p-det-2"))

    assert r1["sim_time"] == r2["sim_time"]
    assert r1["metrics"] == r2["metrics"]
    assert r1["state"] == r2["state"]


async def test_lab_emits_sim_tick_with_metrics():
    """Same capture pattern as test_sim_service.py's
    test_realtime_run_streams_sim_tick_and_completes: subscribe to the bus,
    start a run, drain a few loop turns, assert on the last sim.tick."""
    topo = _topo("p-tick")
    pid = topo.project.id
    bus = get_bus()
    mgr = get_lab_run_manager()
    received: list[dict] = []

    async def collect() -> None:
        async with bus.subscription(pid) as events:
            async for ev in events:
                received.append(ev)

    collector = asyncio.create_task(collect())
    await asyncio.sleep(0)  # let the subscription register before we publish

    try:
        started = await mgr.start(topo)
        assert started["state"] == "running"
        for _ in range(20):
            await asyncio.sleep(0)
    finally:
        await mgr.stop(pid)
        collector.cancel()

    ticks = [e for e in received if e.get("type") == "sim.tick"]
    assert ticks, "expected at least one sim.tick to be broadcast"
    tick = ticks[-1]
    assert set(tick) == {"type", "t", "metrics", "state"}
    assert "delivered" in tick["metrics"]
    assert "dropped" in tick["metrics"]


async def test_lab_stop_is_idempotent():
    topo = _topo("p-idem")
    pid = topo.project.id
    mgr = get_lab_run_manager()

    await mgr.start(topo)
    first = await mgr.stop(pid)
    second = await mgr.stop(pid)  # must not raise
    assert first == second == {
        "project_id": pid, "state": "idle", "sim_time": 0.0, "metrics": {},
    }
