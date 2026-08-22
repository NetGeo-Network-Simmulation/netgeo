"""IS-IS pseudonode LSP tests — P-1d (ISO 10589 §7.2.5/§9.8: the DIS of a
broadcast circuit originates a pseudonode LSP for it, keyed
``<dis_system_id>.<circuit_id>``, and SPF treats it as a zero-cost vertex).

Topology used by most tests here: 4 IS's on one broadcast LAN via a Switch,
r4 highest LAN priority (200) so it wins DIS election (ISO 10589 §8.4.5,
P-1b) predictably without re-deriving the outcome per test.
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


def _route(net: Network, router: str, prefix: str):
    dev = net.devices[router]
    return next((r for r in dev.routes if str(r.prefix) == prefix), None)


def _four_is_lan(net: Network, far_prefix: bool = False) -> dict[str, Router]:
    routers = {n: net.add_device(Router(n)) for n in ("r1", "r2", "r3", "r4")}
    _lan(net, [
        (routers["r1"], "10.0.0.1/24"),
        (routers["r2"], "10.0.0.2/24"),
        (routers["r3"], "10.0.0.3/24"),
        (routers["r4"], "10.0.0.4/24"),
    ])
    if far_prefix:
        # Unconnected stub interface behind r4 — same trick test_isis.py's
        # far-subnet check uses.
        net.add_iface(routers["r4"], "lan2", ["10.9.9.1/24"])
    IsisProcess(routers["r1"], system_id="0000.0000.0001", hello_interval=1.0)
    IsisProcess(routers["r2"], system_id="0000.0000.0002", hello_interval=1.0)
    IsisProcess(routers["r3"], system_id="0000.0000.0003", hello_interval=1.0,
                priorities={"eth0": 100})
    IsisProcess(routers["r4"], system_id="0000.0000.0004", hello_interval=1.0,
                priorities={"eth0": 200})
    return routers


def test_dis_originates_pseudonode_lsp():
    net = Network(seed=301)
    _four_is_lan(net)
    net.start()
    net.run(until=10.0)

    assert _dis(net, "r1") == "0000.0000.0004"  # priority 200 wins, matches P-1b

    for name in ("r1", "r2", "r3", "r4"):
        db = _proc(net, name).lsdb
        pseudo = [lsp for lsp in db.values() if lsp.circuit_id]
        assert len(pseudo) == 1, f"{name}: expected exactly one pseudonode LSP"
        lsp = pseudo[0]
        assert lsp.key == "0000.0000.0004.01"
        assert [t for _, t, _ in lsp.links] == [
            "0000.0000.0001", "0000.0000.0002", "0000.0000.0003", "0000.0000.0004",
        ]
        assert all(cost == 0 for _, _, cost in lsp.links)  # pseudonode->router = 0


def test_router_lsp_points_at_pseudonode_on_lan():
    net = Network(seed=303)
    _four_is_lan(net)
    net.start()
    net.run(until=10.0)

    p1 = _proc(net, "r1")
    lsp = p1.lsdb["0000.0000.0001.00"]
    is_links = [l for l in lsp.links if l[0] == "is"]
    assert len(is_links) == 1
    assert is_links[0][1] == "0000.0000.0004.01"  # the DIS's pseudonode LSP-ID


def test_spf_reaches_across_pseudonode():
    net = Network(seed=305)
    _four_is_lan(net, far_prefix=True)
    net.start()
    net.run(until=10.0)

    r = _route(net, "r1", "10.9.9.0/24")
    assert r is not None
    assert str(r.next_hop) == "10.0.0.4"   # r4's own LAN address, direct hop
    assert r.metric == 20                   # 10 (r1's LAN iface) + 0 (pseudonode) + 10 (r4's stub iface)

    rep = net.ping("r1", "10.9.9.1", count=3)
    assert rep.received == 3, rep.as_dict()


def test_pseudonode_lsp_reoriginated_when_dis_changes():
    net = Network(seed=307)
    r1 = net.add_device(Router("r1"))
    r2 = net.add_device(Router("r2"))
    _lan(net, [(r1, "10.0.0.1/24"), (r2, "10.0.0.2/24")])
    net.add_iface(r2, "lan2", ["10.9.9.1/24"])
    IsisProcess(r1, system_id="0000.0000.0001", hello_interval=1.0)
    IsisProcess(r2, system_id="0000.0000.0002", hello_interval=1.0,
                priorities={"eth0": 100})
    net.start()
    net.run(until=6.0)

    p1 = _proc(net, "r1")
    assert p1._iface_dis["eth0"] == "0000.0000.0002"
    old_key = "0000.0000.0002.01"
    assert old_key in p1.lsdb
    assert _route(net, "r1", "10.9.9.0/24") is not None

    # r3 joins later with a much higher priority — DIS election is
    # preemptive (ISO 10589 §8.4.5, P-1b), unlike OSPF's non-preemptive DR.
    r3 = net.add_device(Router("r3"))
    sw = next(d for d in net.devices.values() if d.kind == "switch")
    net.connect("l-late", net.add_iface(r3, "eth0", ["10.0.0.3/24"]),
                net.add_iface(sw, "gi0/late"))
    p3 = IsisProcess(r3, system_id="0000.0000.0003", hello_interval=1.0,
                      priorities={"eth0": 200})
    p3.start(net)  # joining after net.start(): kick off its own hello loop
    net.run(until=20.0)

    assert p1._iface_dis["eth0"] == "0000.0000.0003"  # preempted
    new_key = "0000.0000.0003.01"
    new_lsp = p1.lsdb[new_key]
    assert [t for _, t, _ in new_lsp.links] == [
        "0000.0000.0001", "0000.0000.0002", "0000.0000.0003",
    ]

    # r1's own LSP now points at the new pseudonode, not the old one.
    own_lsp = p1.lsdb["0000.0000.0001.00"]
    is_targets = [t for k, t, _ in own_lsp.links if k == "is"]
    assert is_targets == [new_key]

    # The old pseudonode LSP is still in the LSDB (never purged) but r2
    # withdrew it (empty) on losing DIS -> no ghost route through it.
    old_lsp = p1.lsdb[old_key]
    assert old_lsp.links == []
    assert _route(net, "r1", "10.9.9.0/24") is not None  # still reachable, via r2 direct-ish
    # convergence still works end to end
    rep = net.ping("r1", "10.9.9.1", count=3)
    assert rep.received == 3, rep.as_dict()


def test_p2p_link_has_no_pseudonode():
    net = Network(seed=309)
    r1 = net.add_device(Router("r1"))
    r2 = net.add_device(Router("r2"))
    net.connect("l", net.add_iface(r1, "eth0", ["10.0.0.1/30"]),
                net.add_iface(r2, "eth0", ["10.0.0.2/30"]))
    IsisProcess(r1, system_id="0000.0000.0001", hello_interval=1.0)
    IsisProcess(r2, system_id="0000.0000.0002", hello_interval=1.0)
    net.start()
    net.run(until=6.0)

    p1, p2 = _proc(net, "r1"), _proc(net, "r2")
    assert not any(lsp.circuit_id for lsp in p1.lsdb.values())
    assert not any(lsp.circuit_id for lsp in p2.lsdb.values())

    lsp1 = p1.lsdb["0000.0000.0001.00"]
    is_links = [l for l in lsp1.links if l[0] == "is"]
    assert is_links == [("is", "0000.0000.0002", 10)]  # direct link, no pseudonode
