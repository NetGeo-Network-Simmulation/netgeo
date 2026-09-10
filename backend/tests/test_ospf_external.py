"""OSPF Type-5 AS-external / Type-4 ASBR-summary tests (RFC 2328 §12.4.3-4).

Chain topology (matches test_ospf_multiarea's shape) used by the cross-area
tests, r1 is the ASBR:

    lan1                                                      lan2
 10.1.0.0/24                                               10.2.0.0/24
     |                                                         |
    r1 ---- area 1 ---- r2(ABR) ---- area 0 ---- r3(ABR) ---- r4
        10.0.12.0/30        10.0.23.0/30        10.0.34.0/30
"""
from __future__ import annotations

from app.models import Topology
from engine.netstack import Network
from engine.netstack.protocols.ospf import AsbrSummaryLsa, AsExternalLsa, OspfProcess
from engine.netstack.routing import Router

EXT_PREFIX = "203.0.113.0/24"
EXT_NEXT_HOP = "10.1.0.254"


def _chain(redistribute: dict | None = None) -> Network:
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

    if redistribute is not None:
        r1.add_static_route(EXT_PREFIX, EXT_NEXT_HOP)

    OspfProcess(r1, router_id="1.1.1.1", hello_interval=1.0,
                areas={"eth0": 1, "lan": 1}, redistribute=redistribute)
    OspfProcess(r2, router_id="2.2.2.2", hello_interval=1.0,
                areas={"eth0": 1, "eth1": 0})
    OspfProcess(r3, router_id="3.3.3.3", hello_interval=1.0,
                areas={"eth0": 0, "eth1": 2})
    OspfProcess(r4, router_id="4.4.4.4", hello_interval=1.0,
                areas={"eth0": 2, "lan": 2})
    net.start()
    net.run(until=20.0)
    return net


def _pair(redistribute: dict | None = None) -> Network:
    """r1(ASBR) --- r2, single area 0 — isolates E1/E2 metric math and
    ASBR-reachability tests from any cross-area Type-4 concern."""
    net = Network(seed=5)
    r1 = net.add_device(Router("r1"))
    r2 = net.add_device(Router("r2"))
    net.connect("l12", net.add_iface(r1, "eth0", ["10.0.12.1/30"]),
                net.add_iface(r2, "eth0", ["10.0.12.2/30"]))
    if redistribute is not None:
        r1.add_static_route(EXT_PREFIX, EXT_NEXT_HOP)
    OspfProcess(r1, router_id="1.1.1.1", hello_interval=1.0, areas={"eth0": 0},
                redistribute=redistribute)
    OspfProcess(r2, router_id="2.2.2.2", hello_interval=1.0, areas={"eth0": 0})
    net.start()
    net.run(until=10.0)
    return net


def _proc(net: Network, name: str) -> OspfProcess:
    return next(p for p in net.devices[name].processes if p.proto == "ospf")


def _route(net: Network, router: str, prefix: str):
    dev = net.devices[router]
    return next((r for r in dev.routes if str(r.prefix) == prefix), None)


def test_asbr_redistributes_static_floods_type5_cross_area():
    net = _chain({"static": {"metric": 20, "metric_type": 2}})
    p4 = _proc(net, "r4")
    assert any(
        isinstance(lsa, AsExternalLsa) and lsa.router_id == "1.1.1.1"
        for lsa in p4.lsdb[2].values()
    ), "Type-5 never reached area 2's LSDB"
    r = _route(net, "r4", EXT_PREFIX)
    assert r is not None and r.source == "ospf"
    assert r.metric == 20, "E2 (default): cost must be the external metric alone"


def test_type4_required_for_cross_area_external_route():
    net = _chain({"static": {"metric": 20, "metric_type": 2}})
    p4 = _proc(net, "r4")
    t4 = next(
        (lsa for lsa in p4.lsdb[2].values()
         if isinstance(lsa, AsbrSummaryLsa) and lsa.asbr_id == "1.1.1.1"),
        None,
    )
    assert t4 is not None, "r3 (the ABR bridging area 0 -> area 2) must originate Type-4"
    assert t4.router_id == "3.3.3.3"
    assert _route(net, "r4", EXT_PREFIX) is not None

    # Prove the dependency directly: strip the Type-4 out from under r4 and
    # re-run SPF with nothing else changed — the external route must vanish
    # because r4 can no longer compute a path to the ASBR (RFC 2328 §16.3).
    del p4.lsdb[2][t4.key]
    p4._run_spf(net)
    assert _route(net, "r4", EXT_PREFIX) is None


def test_e1_adds_internal_cost_e2_does_not():
    net_e2 = _pair({"static": {"metric": 20, "metric_type": 2}})
    net_e1 = _pair({"static": {"metric": 20, "metric_type": 1}})
    r_e2 = _route(net_e2, "r2", EXT_PREFIX)
    r_e1 = _route(net_e1, "r2", EXT_PREFIX)
    assert r_e2 is not None and r_e1 is not None
    assert r_e2.metric == 20, "E2 must not add the internal cost to the ASBR"
    internal_cost = r_e1.metric - 20
    assert internal_cost > 0, "E1 must add a positive internal cost to the ASBR"
    assert r_e1.metric == 20 + internal_cost != r_e2.metric


def test_internal_route_beats_external_for_same_prefix():
    """r1 redistributes its *own* LAN prefix (already reachable inter-area
    via Type-3) at a deliberately low external metric — if rank beat metric
    correctly, r4 keeps the inter-area route unchanged."""
    baseline = _chain(None)
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
    r1.add_static_route("10.1.0.0/24", "10.1.0.254")  # bogus static, same /24 as its own LAN
    OspfProcess(r1, router_id="1.1.1.1", hello_interval=1.0, areas={"eth0": 1, "lan": 1},
                redistribute={"static": {"metric": 1, "metric_type": 2}})
    OspfProcess(r2, router_id="2.2.2.2", hello_interval=1.0, areas={"eth0": 1, "eth1": 0})
    OspfProcess(r3, router_id="3.3.3.3", hello_interval=1.0, areas={"eth0": 0, "eth1": 2})
    OspfProcess(r4, router_id="4.4.4.4", hello_interval=1.0, areas={"eth0": 2, "lan": 2})
    net.start()
    net.run(until=20.0)

    r_baseline = _route(baseline, "r4", "10.1.0.0/24")
    r_contested = _route(net, "r4", "10.1.0.0/24")
    assert r_baseline is not None and r_contested is not None
    assert r_contested.metric == r_baseline.metric, (
        "inter-area (Type-3) route must win over the same-prefix external "
        "(Type-5) one regardless of the external's lower metric"
    )


def test_intra_area_route_beats_external_for_same_prefix():
    """Single-area chain r1-r2-r3 (no ABRs involved): r1 redistributes a
    static route matching its own LAN prefix, already reachable intra-area
    via RouterLsa transit through r2, at a much lower external metric.
    Intra-area rank must still win (RFC 2328 §16.4.1) — same ``offer()``
    funnel as the inter-area case above, just confined to one area."""

    def _build(redistribute):
        net = Network(seed=5)
        r1 = net.add_device(Router("r1"))
        r2 = net.add_device(Router("r2"))
        r3 = net.add_device(Router("r3"))
        net.add_iface(r1, "lan", ["10.1.0.1/24"])
        net.connect("l12", net.add_iface(r1, "eth0", ["10.0.12.1/30"]),
                    net.add_iface(r2, "eth0", ["10.0.12.2/30"]))
        net.connect("l23", net.add_iface(r2, "eth1", ["10.0.23.1/30"]),
                    net.add_iface(r3, "eth0", ["10.0.23.2/30"]))
        if redistribute is not None:
            r1.add_static_route("10.1.0.0/24", "10.1.0.254")  # bogus, same /24 as its LAN
        OspfProcess(r1, router_id="1.1.1.1", hello_interval=1.0,
                    areas={"eth0": 0, "lan": 0}, redistribute=redistribute)
        OspfProcess(r2, router_id="2.2.2.2", hello_interval=1.0, areas={"eth0": 0, "eth1": 0})
        OspfProcess(r3, router_id="3.3.3.3", hello_interval=1.0, areas={"eth0": 0})
        net.start()
        net.run(until=10.0)
        return net

    baseline = _build(None)
    net = _build({"static": {"metric": 1, "metric_type": 2}})
    r_baseline = _route(baseline, "r3", "10.1.0.0/24")
    r_contested = _route(net, "r3", "10.1.0.0/24")
    assert r_baseline is not None and r_contested is not None
    assert r_contested.metric == r_baseline.metric, (
        "intra-area route must win over the same-prefix external one "
        "regardless of the external's lower metric"
    )


def test_external_route_withdrawn_when_redistribution_removed():
    net = _pair({"static": {"metric": 20, "metric_type": 2}})
    assert _route(net, "r2", EXT_PREFIX) is not None
    _proc(net, "r1").set_redistribute(net, None)
    net.run_for(5.0)
    assert _route(net, "r2", EXT_PREFIX) is None


def test_external_route_withdrawn_when_asbr_unreachable():
    net = _pair({"static": {"metric": 20, "metric_type": 2}})
    assert _route(net, "r2", EXT_PREFIX) is not None
    net.set_link_state("l12", up=False)
    net.run_for(30.0)   # past dead_interval (4s) + resync
    assert _route(net, "r2", EXT_PREFIX) is None


def test_netlab_intent_redistribute():
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
                    "intent": {
                        "static_routes": [{"prefix": EXT_PREFIX, "next_hop": EXT_NEXT_HOP}],
                        "ospf": {
                            "enabled": True, "router_id": "1.1.1.1", "hello": 1,
                            "areas": {"eth0": 0},
                            "redistribute": {"static": {"metric": 20, "metric_type": 2}},
                        },
                    },
                },
                {
                    "id": "n2", "project_id": "p1", "name": "r2", "kind": "router",
                    "interfaces": [
                        {"id": "i2", "node_id": "n2", "name": "eth0", "ip": ["10.0.12.2/30"]},
                    ],
                    "intent": {"ospf": {"enabled": True, "router_id": "2.2.2.2", "hello": 1,
                                        "areas": {"eth0": 0}}},
                },
            ],
            "links": [
                {"id": "l1", "project_id": "p1", "a_iface": "i1", "b_iface": "i2"},
            ],
        }
    )
    net = build_network(topo)
    net.start()
    net.run(until=10.0)
    r = _route(net, "r2", EXT_PREFIX)
    assert r is not None and r.metric == 20


def test_external_lsa_replay_determinism():
    a = _chain({"static": {"metric": 20, "metric_type": 2}})
    b = _chain({"static": {"metric": 20, "metric_type": 2}})
    assert a.ledger.seq == b.ledger.seq
    assert a.ledger.hash() == b.ledger.hash()
