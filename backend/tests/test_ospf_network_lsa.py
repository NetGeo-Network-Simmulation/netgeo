"""OSPF network type + Type-2 Network LSA tests — P-1c (RFC 2328 §9.5
point-to-point vs broadcast network type, §12.4.2 Network LSA, §16.1 SPF
with a transit-network pseudonode).

Topology used by most tests here: 4 routers on one broadcast LAN via a
Switch, same priorities as P-1a's `test_ospf_dr_failure_promotes_bdr` (r4=30
DR, r3=20 BDR, r1/r2 default priority 1 DROther) so the DR/BDR outcome is
predictable without re-deriving it per test.
"""
from __future__ import annotations

from engine.netstack import Network
from engine.netstack.protocols.ospf import NetworkLsa, OspfProcess, RouterLsa
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


def _route(net: Network, router: str, prefix: str):
    dev = net.devices[router]
    return next((r for r in dev.routes if str(r.prefix) == prefix), None)


def _four_router_lan(net: Network, far_prefix: bool = False) -> dict[str, Router]:
    routers = {n: net.add_device(Router(n)) for n in ("r1", "r2", "r3", "r4")}
    _lan(net, [
        (routers["r1"], "10.0.0.1/24"),
        (routers["r2"], "10.0.0.2/24"),
        (routers["r3"], "10.0.0.3/24"),
        (routers["r4"], "10.0.0.4/24"),
    ])
    if far_prefix:
        # Unconnected stub interface behind r4 — same trick
        # test_ospf_multiarea.py uses for its cross-area LAN prefixes.
        net.add_iface(routers["r4"], "lan2", ["10.9.9.1/24"])
    OspfProcess(routers["r1"], router_id="1.1.1.1", hello_interval=1.0, areas={"eth0": 0})
    OspfProcess(routers["r2"], router_id="2.2.2.2", hello_interval=1.0, areas={"eth0": 0})
    OspfProcess(routers["r3"], router_id="3.3.3.3", hello_interval=1.0, areas={"eth0": 0},
                priorities={"eth0": 20})
    OspfProcess(routers["r4"], router_id="4.4.4.4", hello_interval=1.0,
                areas={"eth0": 0, "lan2": 0} if far_prefix else {"eth0": 0},
                priorities={"eth0": 30})
    return routers


def test_ptp_link_has_no_dr_election():
    net = Network(seed=101)
    r1 = net.add_device(Router("r1"))
    r2 = net.add_device(Router("r2"))
    net.connect("l", net.add_iface(r1, "eth0", ["10.0.0.1/30"]),
                net.add_iface(r2, "eth0", ["10.0.0.2/30"]))
    OspfProcess(r1, router_id="1.1.1.1", hello_interval=1.0, dead_interval=4.0, areas={"eth0": 0})
    OspfProcess(r2, router_id="2.2.2.2", hello_interval=1.0, dead_interval=4.0, areas={"eth0": 0})
    net.start()
    net.run(until=6.0)

    p1, p2 = _proc(net, "r1"), _proc(net, "r2")
    assert p1._iface_dr == {} and p2._iface_dr == {}  # no election state on a P2P iface
    assert p1.neighbors and all(n.state == "full" for n in p1.neighbors.values())
    assert p2.neighbors and all(n.state == "full" for n in p2.neighbors.values())

    def pending_wait_timers(node_id) -> int:
        n = 0
        for entry in net.scheduler.queue._heap:
            ev = entry[-1]
            handler = getattr(ev, "handler", None)
            if handler is None or "_arm_wait_timer" not in handler.__qualname__:
                continue
            if ev.node_id == node_id:
                n += 1
        return n

    assert pending_wait_timers(r1.node_id) == 0
    assert pending_wait_timers(r2.node_id) == 0


def test_dr_originates_network_lsa():
    net = Network(seed=103)
    _four_router_lan(net)
    net.start()
    net.run(until=10.0)

    assert _dr_state(net, "r1").dr == "4.4.4.4"  # priority 30 wins, matches P-1a

    for name in ("r1", "r2", "r3", "r4"):
        db = _proc(net, name)._area_db(0)
        net_lsas = [l for l in db.values() if isinstance(l, NetworkLsa)]
        assert len(net_lsas) == 1, f"{name}: expected exactly one Network LSA"
        assert net_lsas[0].attached_routers == ["1.1.1.1", "2.2.2.2", "3.3.3.3", "4.4.4.4"]
        assert net_lsas[0].dr_ip == "10.0.0.4"


def test_router_lsa_uses_transit_link_on_broadcast():
    net = Network(seed=107)
    _four_router_lan(net)
    net.start()
    net.run(until=10.0)

    p1 = _proc(net, "r1")
    lsa = p1._area_db(0)["rtr|1.1.1.1"]
    assert isinstance(lsa, RouterLsa)
    transit = [l for l in lsa.links if l[0] == "transit"]
    ptp = [l for l in lsa.links if l[0] == "ptp"]
    assert ptp == []
    assert len(transit) == 1
    assert transit[0][1] == "10.0.0.4"  # the DR's interface IP


def test_spf_reaches_across_pseudonode():
    net = Network(seed=109)
    _four_router_lan(net, far_prefix=True)
    net.start()
    net.run(until=10.0)

    r = _route(net, "r1", "10.9.9.0/24")
    assert r is not None
    assert str(r.next_hop) == "10.0.0.4"       # r4's own LAN address, direct hop
    assert r.metric == 2                        # 1 (r1's LAN iface) + 1 (r4's stub iface)

    rep = net.ping("r1", "10.9.9.1", count=3)
    assert rep.received == 3, rep.as_dict()


def test_network_lsa_withdrawn_when_dr_dies():
    net = Network(seed=113)
    _four_router_lan(net, far_prefix=True)
    net.start()
    net.run(until=10.0)
    assert _route(net, "r1", "10.9.9.0/24") is not None

    net.set_device_power("r4", on=False)
    net.run(until=net.now + 15.0)  # past dead_interval: re-election + reconverge

    st1 = _dr_state(net, "r1")
    assert st1.dr == "3.3.3.3"  # BDR promoted to DR
    assert _route(net, "r1", "10.9.9.0/24") is None  # died with the old DR, no ghost route

    db = _proc(net, "r1")._area_db(0)
    new_lsa = db.get("net|10.0.0.3")
    assert isinstance(new_lsa, NetworkLsa)
    assert "4.4.4.4" not in new_lsa.attached_routers

    # Survivors still converge and reach each other through the new pseudonode.
    rep = net.ping("r1", "10.0.0.2", count=3)
    assert rep.received == 3, rep.as_dict()
