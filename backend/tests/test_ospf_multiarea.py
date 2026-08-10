"""OSPF multi-area tests — NG-SIM-05 (ABR, inter-area routes, default originate).

Topology used throughout (hello=1s so convergence is fast in sim time):

    lan1                                                      lan2
 10.1.0.0/24                                               10.2.0.0/24
     |                                                         |
    r1 ---- area 1 ---- r2(ABR) ---- area 0 ---- r3(ABR) ---- r4
        10.0.12.0/30        10.0.23.0/30        10.0.34.0/30
"""
from __future__ import annotations

import pytest

from app.models import Topology
from engine.netstack import Network
from engine.netstack.routing import Router
from engine.netstack.protocols.ospf import OspfProcess


def _net(default_originate: bool = False) -> Network:
    net = Network(seed=5)
    r1 = net.add_device(Router("r1"))
    r2 = net.add_device(Router("r2"))
    r3 = net.add_device(Router("r3"))
    r4 = net.add_device(Router("r4"))

    net.add_iface(r1, "lan", ["10.1.0.1/24"])
    net.add_iface(r4, "lan", ["10.2.0.1/24"])
    net.connect("l12", net.add_iface(r1, "eth0", ["10.0.12.1/30"]),
                net.add_iface(r2, "eth0", ["10.0.12.2/30"]))
    net.connect("l23", net.add_iface(r2, "eth1", ["10.0.23.1/30"]),
                net.add_iface(r3, "eth0", ["10.0.23.2/30"]))
    net.connect("l34", net.add_iface(r3, "eth1", ["10.0.34.1/30"]),
                net.add_iface(r4, "eth0", ["10.0.34.2/30"]))

    OspfProcess(r1, router_id="1.1.1.1", hello_interval=1.0,
                areas={"eth0": 1, "lan": 1})
    OspfProcess(r2, router_id="2.2.2.2", hello_interval=1.0,
                areas={"eth0": 1, "eth1": 0}, default_originate=default_originate)
    OspfProcess(r3, router_id="3.3.3.3", hello_interval=1.0,
                areas={"eth0": 0, "eth1": 2})
    OspfProcess(r4, router_id="4.4.4.4", hello_interval=1.0,
                areas={"eth0": 2, "lan": 2})
    net.start()
    net.run(until=20.0)
    return net


def _proc(net: Network, name: str) -> OspfProcess:
    return next(p for p in net.devices[name].processes if p.proto == "ospf")


def _route(net: Network, router: str, prefix: str):
    dev = net.devices[router]
    return next((r for r in dev.routes if str(r.prefix) == prefix), None)


def test_adjacency_only_within_same_area():
    net = _net()
    p2 = _proc(net, "r2")
    assert {(n.router_id, n.area) for n in p2.neighbors.values()} == {
        ("1.1.1.1", 1), ("3.3.3.3", 0),
    }
    assert all(n.state == "full" for n in p2.neighbors.values())
    # r1 and r3 never form an adjacency (no shared link/area).
    assert ("3.3.3.3", 0) not in _proc(net, "r1").neighbors
    assert _proc(net, "r2").is_abr and _proc(net, "r3").is_abr
    assert not _proc(net, "r1").is_abr


def test_inter_area_routes_and_end_to_end_ping():
    net = _net()
    # r1 (pure area-1) must learn area-2's LAN through two ABRs.
    r = _route(net, "r1", "10.2.0.0/24")
    assert r is not None and r.source.startswith("ospf")
    assert str(r.next_hop) == "10.0.12.2"
    # And symmetric: r4 learns area-1's LAN.
    assert _route(net, "r4", "10.1.0.0/24") is not None
    rep = net.ping("r1", "10.2.0.1", count=3)
    assert rep.received == 3, rep.as_dict()


def test_intra_area_beats_inter_area():
    net = _net()
    # r2 hears 10.0.23.0/30 both as connected/intra (its own link) and could
    # see summaries — the RIB must keep the intra/connected entry.
    r = _route(net, "r2", "10.0.23.0/30")
    assert r is not None and r.source in ("connected", "ospf")


def test_default_originate_into_leaf_area():
    net = _net(default_originate=True)
    r = _route(net, "r1", "0.0.0.0/0")
    assert r is not None and str(r.next_hop) == "10.0.12.2"
    # The backbone side does not get the leaf-area default.
    assert _route(net, "r3", "0.0.0.0/0") is None


def test_failover_withdraws_inter_area_route():
    net = _net()
    assert _route(net, "r1", "10.2.0.0/24") is not None
    net.set_link_state("l23", up=False)          # cut the backbone
    net.run(until=net.now + 30.0)                # > dead interval (4s) + resync
    assert _route(net, "r1", "10.2.0.0/24") is None


def test_multiarea_replay_determinism():
    a, b = _net(), _net()
    assert a.ledger.seq == b.ledger.seq
    assert a.ledger.hash() == b.ledger.hash()


def test_netlab_intent_areas(client_less_build=None):
    """The stored-topology intent contract: ospf.areas + default_originate."""
    from app.services.netlab import build_network

    topo = Topology.model_validate(
        {
            "project": {"id": "p1", "name": "t"},
            "nodes": [
                {
                    "id": "n1", "project_id": "p1", "name": "r1", "kind": "router",
                    "interfaces": [
                        {"id": "i1", "node_id": "n1", "name": "eth0", "ip": ["10.0.12.1/30"]},
                        {"id": "i2", "node_id": "n1", "name": "lan", "ip": ["10.1.0.1/24"]},
                    ],
                    "intent": {"ospf": {"enabled": True, "router_id": "1.1.1.1",
                                        "hello": 1, "areas": {"eth0": 1, "lan": 1}}},
                },
                {
                    "id": "n2", "project_id": "p1", "name": "r2", "kind": "router",
                    "interfaces": [
                        {"id": "i3", "node_id": "n2", "name": "eth0", "ip": ["10.0.12.2/30"]},
                        {"id": "i4", "node_id": "n2", "name": "eth1", "ip": ["10.0.99.1/30"]},
                    ],
                    "intent": {"ospf": {"enabled": True, "router_id": "2.2.2.2",
                                        "hello": 1,
                                        "areas": {"eth0": 1, "eth1": 0},
                                        "default_originate": True}},
                },
            ],
            "links": [
                {"id": "l1", "project_id": "p1", "a_iface": "i1", "b_iface": "i3"},
            ],
        }
    )
    net = build_network(topo)
    proc = _proc(net, "r1")
    assert proc.iface_area("eth0") == 1
    net.start()
    net.run(until=10.0)
    assert _route(net, "r1", "0.0.0.0/0") is not None


def test_adjacency_recovers_after_power_cycle():
    """F36/F47 kelas A: set_device_power() only flips a flag — nothing used
    to reschedule the hello tick. Before the fix, _tick()'s `if not
    powered_on: return` dropped the timer chain forever, so a router that
    came back up never sent another hello and the adjacency stayed dead."""
    net = Network(seed=7)
    r1 = net.add_device(Router("r1"))
    r2 = net.add_device(Router("r2"))
    net.connect("l", net.add_iface(r1, "eth0", ["10.0.0.1/30"]),
                net.add_iface(r2, "eth0", ["10.0.0.2/30"]))
    OspfProcess(r1, router_id="1.1.1.1", hello_interval=1.0, areas={"eth0": 0})
    OspfProcess(r2, router_id="2.2.2.2", hello_interval=1.0, areas={"eth0": 0})
    net.start()
    net.run(until=5.0)
    p1 = _proc(net, "r1")
    assert p1.neighbors and all(n.state == "full" for n in p1.neighbors.values())

    net.set_device_power("r2", on=False)
    net.run_for(10.0)   # past dead_interval (4s) — adjacency must drop
    assert not p1.neighbors

    net.set_device_power("r2", on=True)
    net.run_for(10.0)   # several hello cycles after power-on
    assert p1.neighbors and any(n.state == "full" for n in p1.neighbors.values()), (
        "adjacency did not recover after power-on — hello tick stayed dead"
    )

    # No duplicate tick chain: the fix must gate the *work*, not spawn a
    # second reschedule — r2's hello cadence should stay at one every
    # hello_interval, not double from a parallel chain.
    def _r2_hellos() -> int:
        return sum(
            1 for r in net.capture.records(link_id="l", limit=5000)
            if r.iface.startswith("r2:") and r.direction == "tx" and "Hello" in r.info
        )

    before = _r2_hellos()
    net.run_for(5.0)
    sent = _r2_hellos() - before
    assert 3 <= sent <= 6, (
        f"expected ~5 hellos in 5s at hello_interval=1.0, got {sent} "
        "— looks like a duplicate tick chain"
    )
