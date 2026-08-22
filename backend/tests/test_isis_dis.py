"""IS-IS DIS election tests — P-1b (ISO 10589 §8.4.5: LAN Designated IS
election, priority desc / system-id desc tiebreak, preemptive — no BDIS).

Topology: routers on one broadcast LAN via a Switch (hello=1s so convergence
is fast in sim time). Unlike OSPF's DR/BDR, IS-IS adjacency state is not
gated by election role — every IS on a LAN reaches "up" with every other IS
regardless of who is DIS (see test_isis.py for that baseline); what these
tests check is only which system-id ``_iface_dis`` converges on.
"""
from __future__ import annotations

from engine.netstack import Network
from engine.netstack.protocols.isis import IsisProcess
from engine.netstack.routing import Router
from engine.netstack.switching import Switch


def _lan(net: Network, routers: list[tuple[Router, str]]) -> None:
    sw = net.add_device(Switch(f"sw-{net.now}-{len(net.devices)}"))
    for idx, (r, ip) in enumerate(routers):
        net.connect(
            f"l{idx}", net.add_iface(r, "eth0", [ip]), net.add_iface(sw, f"gi0/{idx}")
        )


def _proc(net: Network, name: str) -> IsisProcess:
    return next(p for p in net.devices[name].processes if p.proto == "isis")


def _dis(net: Network, name: str, iface: str = "eth0") -> str | None:
    return _proc(net, name)._iface_dis.get(iface)


def test_isis_dis_election_highest_priority_wins():
    net = Network(seed=61)
    routers = {n: net.add_device(Router(n)) for n in ("r1", "r2", "r3")}
    _lan(net, [
        (routers["r1"], "10.0.0.1/24"),
        (routers["r2"], "10.0.0.2/24"),
        (routers["r3"], "10.0.0.3/24"),
    ])
    IsisProcess(routers["r1"], system_id="0000.0000.0001", hello_interval=1.0)
    IsisProcess(routers["r2"], system_id="0000.0000.0002", hello_interval=1.0,
                priorities={"eth0": 100})
    IsisProcess(routers["r3"], system_id="0000.0000.0003", hello_interval=1.0,
                priorities={"eth0": 50})
    net.start()
    net.run(until=10.0)

    assert _dis(net, "r1") == "0000.0000.0002"
    assert _dis(net, "r2") == "0000.0000.0002"
    assert _dis(net, "r3") == "0000.0000.0002"


def test_isis_dis_election_is_preemptive():
    net = Network(seed=63)
    r1 = net.add_device(Router("r1"))
    r2 = net.add_device(Router("r2"))
    _lan(net, [(r1, "10.0.0.1/24"), (r2, "10.0.0.2/24")])
    IsisProcess(r1, system_id="0000.0000.0001", hello_interval=1.0)
    IsisProcess(r2, system_id="0000.0000.0002", hello_interval=1.0)
    net.start()
    net.run(until=6.0)
    # Equal default priority (64) -> tiebreak by system-id descending.
    assert _dis(net, "r1") == "0000.0000.0002"

    # r3 joins later with a much higher priority — unlike OSPF's non-
    # preemptive DR, ISO 10589 §8.4.5 says the incumbent DIS *must* yield.
    r3 = net.add_device(Router("r3"))
    sw = next(d for d in net.devices.values() if d.kind == "switch")
    net.connect("l-late", net.add_iface(r3, "eth0", ["10.0.0.3/24"]),
                net.add_iface(sw, "gi0/late"))
    p3 = IsisProcess(r3, system_id="0000.0000.0099", hello_interval=1.0,
                      priorities={"eth0": 200})
    p3.start(net)  # joining after net.start(): kick off its own hello loop
    net.run(until=20.0)

    assert _dis(net, "r1") == "0000.0000.0099"  # preempted
    assert _dis(net, "r3") == "0000.0000.0099"


def test_isis_dis_tiebreak_deterministic():
    def _run():
        net = Network(seed=67)
        r1 = net.add_device(Router("r1"))
        r2 = net.add_device(Router("r2"))
        _lan(net, [(r1, "10.0.0.1/24"), (r2, "10.0.0.2/24")])
        IsisProcess(r1, system_id="0000.0000.0001", hello_interval=1.0)
        IsisProcess(r2, system_id="0000.0000.0002", hello_interval=1.0)
        net.start()
        net.run(until=6.0)
        return _dis(net, "r1"), _dis(net, "r2")

    first = _run()
    second = _run()
    assert first == second == ("0000.0000.0002", "0000.0000.0002")


def test_isis_dis_failover_when_dis_dies():
    net = Network(seed=71)
    r1 = net.add_device(Router("r1"))
    r2 = net.add_device(Router("r2"))
    r3 = net.add_device(Router("r3"))
    _lan(net, [(r1, "10.0.0.1/24"), (r2, "10.0.0.2/24"), (r3, "10.0.0.3/24")])
    IsisProcess(r1, system_id="0000.0000.0001", hello_interval=1.0, hold_multiplier=4,
                priorities={"eth0": 200})   # DIS
    IsisProcess(r2, system_id="0000.0000.0002", hello_interval=1.0, hold_multiplier=4,
                priorities={"eth0": 100})
    IsisProcess(r3, system_id="0000.0000.0003", hello_interval=1.0, hold_multiplier=4)
    net.start()
    net.run(until=6.0)
    assert _dis(net, "r2") == "0000.0000.0001"

    net.set_device_power("r1", on=False)
    net.run(until=net.now + 10.0)  # > hold_time (4s): r1 expires everywhere

    assert _dis(net, "r2") == "0000.0000.0002"
    assert _dis(net, "r3") == "0000.0000.0002"


def test_isis_p2p_link_has_no_dis():
    net = Network(seed=73)
    r1 = net.add_device(Router("r1"))
    r2 = net.add_device(Router("r2"))
    net.connect("l", net.add_iface(r1, "eth0", ["10.0.0.1/30"]),
                net.add_iface(r2, "eth0", ["10.0.0.2/30"]))
    IsisProcess(r1, system_id="0000.0000.0001", hello_interval=1.0)
    IsisProcess(r2, system_id="0000.0000.0002", hello_interval=1.0)
    net.start()
    net.run(until=6.0)

    assert all(a.state == "up" for a in _proc(net, "r1").neighbors.values())
    assert all(a.state == "up" for a in _proc(net, "r2").neighbors.values())
    assert "eth0" not in _proc(net, "r1")._iface_dis
    assert "eth0" not in _proc(net, "r2")._iface_dis
