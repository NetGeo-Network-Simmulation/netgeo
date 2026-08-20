"""Benchmark harness — NG-NFR-02 budgets, warn-only in R0.

Each benchmark asserts nothing about speed yet (budgets flip to hard
failures at R6 per the roadmap); breaches emit a UserWarning so CI logs
show the regression without blocking merges.
"""
from __future__ import annotations

import time
import warnings
from ipaddress import IPv4Address

from engine.events import EventType, SimEvent
from engine.netstack import Network
from engine.netstack.device import Host
from engine.netstack.routing import Router
from engine.scheduler import Scheduler

# Budgets (reference laptop, see docs/design/01-FEATURE-SPEC.md NG-NFR-02).
KERNEL_EVENTS_PER_SEC = 100_000
CHAIN_BUILD_RUN_SECONDS = 2.0


def _warn_if(cond: bool, msg: str) -> None:
    if cond:
        warnings.warn(msg, stacklevel=2)


def test_kernel_dispatch_throughput():
    sched = Scheduler()
    n = 50_000
    for i in range(n):
        sched.schedule(SimEvent(time=i * 1e-6, type=EventType.TIMER, handler=None))
    t0 = time.perf_counter()
    dispatched = sched.run()
    dt = time.perf_counter() - t0
    assert dispatched == n
    rate = n / dt
    _warn_if(
        rate < KERNEL_EVENTS_PER_SEC,
        f"kernel dispatch {rate:,.0f} ev/s < budget {KERNEL_EVENTS_PER_SEC:,} ev/s",
    )


def test_packet_lod_router_chain():
    """h1 - r1..r8 - h2, static routes; build + converge + ping under budget."""
    t0 = time.perf_counter()
    net = Network(seed=1)
    h1 = net.add_device(Host("h1"))
    h2 = net.add_device(Host("h2"))
    routers = [net.add_device(Router(f"r{i}")) for i in range(1, 9)]

    net.connect("lan1", net.add_iface(h1, "eth0", ["192.168.1.10/24"]),
                net.add_iface(routers[0], "eth0", ["192.168.1.1/24"]))
    for i in range(len(routers) - 1):
        a, b = routers[i], routers[i + 1]
        net.connect(f"c{i}", net.add_iface(a, "eth1", [f"10.0.{i}.1/30"]),
                    net.add_iface(b, "eth2", [f"10.0.{i}.2/30"]))
    net.connect("lan2", net.add_iface(routers[-1], "eth0", ["192.168.2.1/24"]),
                net.add_iface(h2, "eth0", ["192.168.2.10/24"]))

    h1.default_gateway = IPv4Address("192.168.1.1")
    h2.default_gateway = IPv4Address("192.168.2.1")
    for i, r in enumerate(routers):
        if i < len(routers) - 1:            # toward h2's LAN via right neighbor
            r.add_static_route("192.168.2.0/24", f"10.0.{i}.2")
        if i > 0:                           # toward h1's LAN via left neighbor
            r.add_static_route("192.168.1.0/24", f"10.0.{i-1}.1")

    report = net.ping("h1", "192.168.2.10", count=4)
    dt = time.perf_counter() - t0
    assert report.received == 4, f"chain ping failed: {report.as_dict()}"
    _warn_if(
        dt > CHAIN_BUILD_RUN_SECONDS,
        f"router-chain build+run {dt:.2f}s > budget {CHAIN_BUILD_RUN_SECONDS}s",
    )
