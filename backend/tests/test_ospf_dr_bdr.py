"""OSPF DR/BDR election tests — P-1a (RFC 2328 §7.3 election algorithm,
§9.4 interface state machine, §10.1 2-Way as the DROther/DROther stop
state).

Topology used by most tests: 4 routers on one broadcast LAN via a Switch
(hello=1s, dead_interval=4s so convergence/failover is fast in sim time).
Four routers (not three) so the "not full-mesh" assertion is meaningful —
with exactly 3 routers every pair necessarily involves the DR or the BDR,
so C(3,2)=3 and "DR/BDR-only" adjacency happen to coincide.
"""
from __future__ import annotations

from engine.netstack import Network
from engine.netstack.protocols.ospf import OspfProcess
from engine.netstack.routing import Router
from engine.netstack.switching import Switch


def _lan(net: Network, routers: list[tuple[Router, str]]) -> None:
    sw = net.add_device(Switch(f"sw-{net.now}-{len(net.devices)}"))
    for idx, (r, ip) in enumerate(routers):
        net.connect(
            f"l{idx}", net.add_iface(r, "eth0", [ip]), net.add_iface(sw, f"gi0/{idx}")
        )


def _proc(net: Network, name: str) -> OspfProcess:
    return next(p for p in net.devices[name].processes if p.proto == "ospf")


def _dr_state(net: Network, name: str, iface: str = "eth0"):
    return _proc(net, name)._iface_dr[iface]


def test_ospf_dr_bdr_election_star_topology():
    net = Network(seed=41)
    routers = {n: net.add_device(Router(n)) for n in ("r1", "r2", "r3", "r4")}
    _lan(net, [
        (routers["r1"], "10.0.0.1/24"),
        (routers["r2"], "10.0.0.2/24"),
        (routers["r3"], "10.0.0.3/24"),
        (routers["r4"], "10.0.0.4/24"),
    ])
    # Priorities pick a deterministic winner/runner-up: r4 > r3 > (r1, r2 default 1).
    OspfProcess(routers["r1"], router_id="1.1.1.1", hello_interval=1.0, areas={"eth0": 0})
    OspfProcess(routers["r2"], router_id="2.2.2.2", hello_interval=1.0, areas={"eth0": 0})
    OspfProcess(routers["r3"], router_id="3.3.3.3", hello_interval=1.0, areas={"eth0": 0},
                priorities={"eth0": 20})
    OspfProcess(routers["r4"], router_id="4.4.4.4", hello_interval=1.0, areas={"eth0": 0},
                priorities={"eth0": 30})
    net.start()
    net.run(until=10.0)

    st = _dr_state(net, "r1")
    assert st.dr == "4.4.4.4" and st.bdr == "3.3.3.3"
    assert _dr_state(net, "r4").state == "dr"
    assert _dr_state(net, "r3").state == "backup"
    assert _dr_state(net, "r1").state == "drother"
    assert _dr_state(net, "r2").state == "drother"

    def full_pairs(name: str) -> set[str]:
        return {n.router_id for n in _proc(net, name).neighbors.values() if n.state == "full"}

    # DR and BDR are Full with everyone; the two DROthers stop at 2-Way with
    # each other — not the full mesh C(4,2)=6.
    assert full_pairs("r4") == {"1.1.1.1", "2.2.2.2", "3.3.3.3"}
    assert full_pairs("r3") == {"1.1.1.1", "2.2.2.2", "4.4.4.4"}
    assert full_pairs("r1") == {"3.3.3.3", "4.4.4.4"}
    assert full_pairs("r2") == {"3.3.3.3", "4.4.4.4"}
    r1_r2 = next(n for n in _proc(net, "r1").neighbors.values() if n.router_id == "2.2.2.2")
    assert r1_r2.state == "2-way"


def test_ospf_dr_election_non_preemptive():
    net = Network(seed=43)
    r1 = net.add_device(Router("r1"))
    r2 = net.add_device(Router("r2"))
    _lan(net, [(r1, "10.0.0.1/24"), (r2, "10.0.0.2/24")])
    OspfProcess(r1, router_id="1.1.1.1", hello_interval=1.0, dead_interval=4.0,
                areas={"eth0": 0})
    OspfProcess(r2, router_id="2.2.2.2", hello_interval=1.0, dead_interval=4.0,
                areas={"eth0": 0})
    net.start()
    net.run(until=6.0)
    dr_before = _dr_state(net, "r1").dr
    assert dr_before in ("1.1.1.1", "2.2.2.2")

    # r3 joins later with a much higher priority — must NOT unseat the DR.
    r3 = net.add_device(Router("r3"))
    sw = next(d for d in net.devices.values() if d.kind == "switch")
    net.connect("l-late", net.add_iface(r3, "eth0", ["10.0.0.3/24"]),
                net.add_iface(sw, "gi0/late"))
    p3 = OspfProcess(r3, router_id="3.3.3.3", hello_interval=1.0, dead_interval=4.0,
                      areas={"eth0": 0}, priorities={"eth0": 200})
    p3.start(net)  # joining after net.start(): kick off its own hello/wait-timer loop
    net.run(until=20.0)

    assert _dr_state(net, "r1").dr == dr_before  # the incumbent DR keeps its seat
    assert _dr_state(net, "r3").state != "dr"    # priority 200 does not unseat it


def test_ospf_dr_failure_promotes_bdr():
    net = Network(seed=47)
    r1 = net.add_device(Router("r1"))
    r2 = net.add_device(Router("r2"))
    r3 = net.add_device(Router("r3"))
    _lan(net, [(r1, "10.0.0.1/24"), (r2, "10.0.0.2/24"), (r3, "10.0.0.3/24")])
    OspfProcess(r1, router_id="1.1.1.1", hello_interval=1.0, dead_interval=4.0,
                areas={"eth0": 0}, priorities={"eth0": 30})   # DR
    OspfProcess(r2, router_id="2.2.2.2", hello_interval=1.0, dead_interval=4.0,
                areas={"eth0": 0}, priorities={"eth0": 20})   # BDR
    OspfProcess(r3, router_id="3.3.3.3", hello_interval=1.0, dead_interval=4.0,
                areas={"eth0": 0})                             # DROther
    net.start()
    net.run(until=6.0)
    assert _dr_state(net, "r2").dr == "1.1.1.1" and _dr_state(net, "r2").bdr == "2.2.2.2"

    net.set_device_power("r1", on=False)
    net.run(until=net.now + 10.0)   # > dead_interval: r1 expires everywhere

    st2 = _dr_state(net, "r2")
    assert st2.dr == "2.2.2.2"      # BDR promoted to DR
    assert st2.bdr == "3.3.3.3"     # a fresh BDR was elected
    assert _dr_state(net, "r2").state == "dr"
    assert _dr_state(net, "r3").state == "backup"


def test_ospf_wait_timer_does_not_duplicate_across_interface_reset():
    """Reflects into the scheduler heap (pattern of
    ``_pending_connect_retry_timers`` in test_bgp_fsm.py) to catch a leaked
    Wait Timer chain that a state-only or log-only assertion would miss."""
    net = Network(seed=53)
    r1 = net.add_device(Router("r1"))
    r2 = net.add_device(Router("r2"))
    net.connect("l", net.add_iface(r1, "eth0", ["10.0.0.1/30"]),
                net.add_iface(r2, "eth0", ["10.0.0.2/30"]))
    OspfProcess(r1, router_id="1.1.1.1", hello_interval=1.0, dead_interval=20.0,
                areas={"eth0": 0})
    OspfProcess(r2, router_id="2.2.2.2", hello_interval=1.0, dead_interval=20.0,
                areas={"eth0": 0})
    net.start()

    def pending() -> int:
        n = 0
        for entry in net.scheduler.queue._heap:
            ev = entry[-1]
            handler = getattr(ev, "handler", None)
            # The lambda scheduled by _arm_wait_timer is what's sitting in
            # the heap — its __qualname__ names the enclosing arm function,
            # not the _wait_timer_fired it calls (same pattern as BGP's
            # _pending_connect_retry_timers matching "_arm_connect_retry").
            if handler is None or "_arm_wait_timer" not in handler.__qualname__:
                continue
            cells = [c.cell_contents for c in (handler.__closure__ or ())]
            if ev.node_id == r1.node_id and any(c == "eth0" for c in cells):
                n += 1
        return n

    assert pending() == 1  # armed once at start()

    # First reset: the power-on doesn't re-arm synchronously — it self-heals
    # on the next hello tick (same F36/F47 "gate the work" pattern the
    # existing power-cycle test relies on) — so one tick has to pass.
    net.set_device_power("r1", on=False)
    net.set_device_power("r1", on=True)
    net.run_for(1.5)
    assert pending() == 2  # the stale initial arm + one fresh resume

    # Second reset, right back-to-back with the first (well before the 20s
    # Wait Timer would ever fire) — the scheduler has no cancel API (same
    # note as the BGP test), so both earlier generations simply sit inert
    # until their sequence-guard check no-ops them.
    net.set_device_power("r1", on=False)
    net.set_device_power("r1", on=True)
    net.run_for(1.5)
    assert pending() == 3  # two stale arms + the latest resume

    # The real risk this guards: re-arming on every subsequent tick instead
    # of once on the up transition. Let several more hello ticks pass with
    # nothing else changing.
    net.run_for(5.0)
    assert pending() == 3  # unchanged — no per-tick pile-up


def test_ospf_priority_zero_never_becomes_dr():
    net = Network(seed=59)
    r1 = net.add_device(Router("r1"))
    r2 = net.add_device(Router("r2"))
    _lan(net, [(r1, "10.0.0.1/24"), (r2, "10.0.0.2/24")])
    OspfProcess(r1, router_id="1.1.1.1", hello_interval=1.0, dead_interval=4.0,
                areas={"eth0": 0}, priorities={"eth0": 0})
    OspfProcess(r2, router_id="2.2.2.2", hello_interval=1.0, dead_interval=4.0,
                areas={"eth0": 0})
    net.start()
    net.run(until=6.0)

    st1 = _dr_state(net, "r1")
    assert st1.state == "drother"
    assert st1.dr == "2.2.2.2"
    assert st1.bdr == ""   # only one eligible router — no BDR to elect
    # A priority-0 router still reaches Full with the DR (RFC: DROther <-> DR).
    r2r1 = next(n for n in _proc(net, "r2").neighbors.values() if n.router_id == "1.1.1.1")
    assert r2r1.state == "full"
