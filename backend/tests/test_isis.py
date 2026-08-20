"""IS-IS tests — NG-SIM-07 (level-2 single-area IGP: adjacency, LSP, SPF).

Topology (hello=1s so convergence is fast in sim time), no static routes —
reachability must come from IS-IS alone:

    r1 ---- r2 ---- r3 ---- lan3 (10.3.0.0/24)
       10.0.12.0/30   10.0.23.0/30
"""
from __future__ import annotations

from app.models import Topology
from engine.netstack import Network
from engine.netstack.protocols.isis import IsisProcess
from engine.netstack.routing import Router


def _net() -> Network:
    net = Network(seed=7)
    r1 = net.add_device(Router("r1"))
    r2 = net.add_device(Router("r2"))
    r3 = net.add_device(Router("r3"))

    net.connect("l12", net.add_iface(r1, "eth0", ["10.0.12.1/30"]),
                net.add_iface(r2, "eth0", ["10.0.12.2/30"]))
    net.connect("l23", net.add_iface(r2, "eth1", ["10.0.23.1/30"]),
                net.add_iface(r3, "eth0", ["10.0.23.2/30"]))
    net.add_iface(r3, "lan", ["10.3.0.1/24"])

    IsisProcess(r1, system_id="1921.6800.1001", hello_interval=1.0)
    IsisProcess(r2, system_id="1921.6800.1002", hello_interval=1.0)
    IsisProcess(r3, system_id="1921.6800.1003", hello_interval=1.0)
    net.start()
    net.run(until=20.0)
    return net


def _proc(net: Network, name: str) -> IsisProcess:
    return next(p for p in net.devices[name].processes if p.proto == "isis")


def _route(net: Network, router: str, prefix: str):
    dev = net.devices[router]
    return next((r for r in dev.routes if str(r.prefix) == prefix), None)


def test_adjacencies_up_on_both_ends():
    net = _net()
    p2 = _proc(net, "r2")
    # r2 sits in the middle: adjacencies to both r1 and r3, both "up".
    assert {a.system_id for a in p2.neighbors.values()} == {
        "1921.6800.1001", "1921.6800.1003",
    }
    assert all(a.state == "up" for a in p2.neighbors.values())
    # And the far ends see r2 as up too.
    assert all(a.state == "up" for a in _proc(net, "r1").neighbors.values())
    assert all(a.state == "up" for a in _proc(net, "r3").neighbors.values())


def test_r1_learns_far_subnet_via_isis():
    net = _net()
    r = _route(net, "r1", "10.3.0.0/24")
    assert r is not None and r.source == "isis"
    # Next hop is r2's near interface (learned from its IIH), reached via eth0.
    assert str(r.next_hop) == "10.0.12.2"
    assert r.iface_name == "eth0"


def test_multihop_ping_reachable_via_isis_only():
    net = _net()
    # No static routes anywhere — reachability is IS-IS-installed only.
    for name in ("r1", "r2", "r3"):
        assert not [r for r in net.devices[name].routes if r.source == "static"]
    rep = net.ping("r1", "10.3.0.1", count=3)
    assert rep.received == 3, rep.as_dict()


def test_replay_determinism():
    a, b = _net(), _net()
    assert a.ledger.seq == b.ledger.seq
    assert a.ledger.hash() == b.ledger.hash()


def test_netlab_isis_intent():
    """The stored-topology intent contract: isis.enabled + interfaces + metric."""
    from app.services.netlab import build_network

    topo = Topology.model_validate(
        {
            "project": {"id": "p1", "name": "t"},
            "nodes": [
                {
                    "id": "n1", "project_id": "p1", "name": "r1", "kind": "router",
                    "interfaces": [
                        {"id": "i1", "node_id": "n1", "name": "eth0", "ip": ["10.0.12.1/30"]},
                    ],
                    "intent": {"isis": {"enabled": True, "system_id": "1921.6800.1001",
                                        "level": 2, "hello": 1, "interfaces": ["eth0"]}},
                },
                {
                    "id": "n2", "project_id": "p1", "name": "r2", "kind": "router",
                    "interfaces": [
                        {"id": "i2", "node_id": "n2", "name": "eth0", "ip": ["10.0.12.2/30"]},
                        {"id": "i3", "node_id": "n2", "name": "lan", "ip": ["10.9.0.1/24"]},
                    ],
                    "intent": {"isis": {"enabled": True, "system_id": "1921.6800.1002",
                                        "level": 2, "hello": 1,
                                        "interfaces": {"eth0": 10, "lan": 10}}},
                },
            ],
            "links": [
                {"id": "l1", "project_id": "p1", "a_iface": "i1", "b_iface": "i2"},
            ],
        }
    )
    net = build_network(topo)
    net.start()
    net.run(until=15.0)
    # r1 learns r2's LAN through the IS-IS adjacency.
    assert _route(net, "r1", "10.9.0.0/24") is not None
    assert _route(net, "r1", "10.9.0.0/24").source == "isis"
