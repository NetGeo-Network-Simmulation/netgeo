"""R1 tests — NG-SIM-02 (IPv6 end-to-end) + NG-SIM-01 (simulation mode).

Engine level: NDP resolution, routed ping6/traceroute6, SLAAC, ICMPv6 errors,
dual-stack replay determinism. CLI level: v6 show/config commands in both
dialects. HTTP level (exit gate): a dual-stack 5-node lab stepped through an
ICMPv6 exchange in the ledger, then seek/step-back reproducing events
byte-identically.
"""
from __future__ import annotations

from ipaddress import IPv6Address

import pytest

from app.services.netlab import get_lab_manager
from engine.netstack import Network
from engine.netstack.cli import CliSession
from engine.netstack.device import Host
from engine.netstack.routing import Router
from engine.netstack.switching import Switch


@pytest.fixture(autouse=True)
def _fresh_labs():
    get_lab_manager()._labs.clear()
    yield
    get_lab_manager()._labs.clear()


# ---------------------------------------------------------------------------
# Engine: NG-SIM-02
# ---------------------------------------------------------------------------

def _routed6() -> Network:
    """a -- r1 -- r2 -- b, static v6 routes + host gateways."""
    net = Network(seed=3)
    a, b = net.add_device(Host("a")), net.add_device(Host("b"))
    r1, r2 = net.add_device(Router("r1")), net.add_device(Router("r2"))
    ia = net.add_iface(a, "eth0", ["2001:db8:a::10/64"])
    ib = net.add_iface(b, "eth0", ["2001:db8:b::10/64"])
    net.connect("la", ia, net.add_iface(r1, "eth0", ["2001:db8:a::1/64"]))
    net.connect("lx", net.add_iface(r1, "eth1", ["2001:db8:ab::1/64"]),
                net.add_iface(r2, "eth0", ["2001:db8:ab::2/64"]))
    net.connect("lb", ib, net.add_iface(r2, "eth1", ["2001:db8:b::1/64"]))
    a.default_gateway6 = IPv6Address("2001:db8:a::1")
    b.default_gateway6 = IPv6Address("2001:db8:b::1")
    r1.add_static_route6("2001:db8:b::/64", "2001:db8:ab::2")
    r2.add_static_route6("2001:db8:a::/64", "2001:db8:ab::1")
    return net


def test_ndp_and_same_link_ping6():
    net = Network(seed=1)
    h1, h2 = net.add_device(Host("h1")), net.add_device(Host("h2"))
    i1 = net.add_iface(h1, "eth0", ["2001:db8:1::1/64"])
    i2 = net.add_iface(h2, "eth0", ["2001:db8:1::2/64"])
    net.connect("l1", i1, i2)
    rep = net.ping("h1", "2001:db8:1::2", count=3)
    assert rep.received == 3 and rep.loss_pct == 0.0
    # NDP resolved and cached on both ends.
    assert IPv6Address("2001:db8:1::2") in h1.nd_cache
    assert any(str(ip).startswith("fe80::") for ip in h2.nd_cache)


def test_link_local_ping6():
    net = Network(seed=5)
    h1, h2 = net.add_device(Host("h1")), net.add_device(Host("h2"))
    i1 = net.add_iface(h1, "eth0", ["2001:db8::1/64"])
    i2 = net.add_iface(h2, "eth0", ["2001:db8::2/64"])
    net.connect("l1", i1, i2)
    rep = net.ping("h1", str(i2.link_local.ip), count=2)
    assert rep.received == 2


def test_routed_ping6_and_traceroute6():
    net = _routed6()
    rep = net.ping("a", "2001:db8:b::10", count=3)
    assert rep.received == 3
    tr = net.traceroute("a", "2001:db8:b::10")
    assert tr.reached
    hops = tr.as_dict()["hops"]
    assert [h["address"] for h in hops] == [
        "2001:db8:a::1", "2001:db8:ab::2", "2001:db8:b::10",
    ]


def test_icmpv6_unreachable_and_hop_limit():
    net = _routed6()
    # No route on r1 for this prefix -> ICMPv6 unreachable back to the source.
    rep = net.ping("a", "2001:db8:dead::1", count=1)
    assert rep.received == 0 and any("unreachable" in e for e in rep.errors)
    # hop_limit=1 dies at the first router -> time-exceeded (traceroute's engine).
    rep2 = net.pings[
        net.ping("a", "2001:db8:b::10", count=1, run_after=False).ident
    ]
    # send a probe with hlim 1 by driving the host API directly
    a = net.find_device("a")
    ident = a.ping(net, IPv6Address("2001:db8:b::10"), count=1, ttl=1)
    net.run_for(5.0)
    assert any("time-exceeded" in e for e in net.pings[ident].errors)


def test_slaac_autoconfig_and_ra_gateway():
    net = Network(seed=4)
    h, rt = net.add_device(Host("h")), net.add_device(Router("gw"))
    ih = net.add_iface(h, "eth0", [])
    ih.slaac = True
    net.connect("l", ih, net.add_iface(rt, "eth0", ["2001:db8:5::1/64"]))
    rt.enable_ra(interval=30)
    net.run_for(2.0)
    assert ih.ips6 and str(ih.ips6[0].network) == "2001:db8:5::/64"
    assert h.default_gateway6 is not None  # learned from RA (link-local)
    assert net.ping("h", "2001:db8:5::1", count=2).received == 2


def test_dual_stack_determinism():
    def build(seed: int) -> Network:
        n = Network(seed=seed)
        x, y = n.add_device(Host("x")), n.add_device(Host("y"))
        ix = n.add_iface(x, "eth0", ["10.0.0.1/24", "2001:db8::1/64"])
        iy = n.add_iface(y, "eth0", ["10.0.0.2/24", "2001:db8::2/64"])
        n.connect("l", ix, iy)
        n.ping("x", "10.0.0.2", count=2)
        n.ping("x", "2001:db8::2", count=2)
        return n

    a, b = build(9), build(9)
    assert a.ledger.seq == b.ledger.seq > 0
    assert a.ledger.hash() == b.ledger.hash()


def test_ledger_records_carry_frame_context():
    net = _routed6()
    net.ping("a", "2001:db8:b::10", count=1)
    rx = [r for r in net.ledger.records if r["type"] == "PACKET_RX"]
    assert rx, "expected PACKET_RX records"
    icmp6 = [r for r in rx if "ICMPv6" in r.get("info", "")]
    assert icmp6, "ledger should describe ICMPv6 frames"
    sample = icmp6[0]
    assert sample["link"] and sample["iface"] and sample["size"] > 0


# ---------------------------------------------------------------------------
# CLI: dual-stack dialects
# ---------------------------------------------------------------------------

def test_cli_ipv6_show_and_ping():
    net = _routed6()
    net.ping("a", "2001:db8:b::10", count=1)  # warm ND caches
    r1 = net.find_device("r1")
    sess = CliSession(net, r1)
    routes = sess.execute("show ipv6 route")
    assert "2001:db8:b::/64" in routes and "S" in routes
    neigh = sess.execute("show ipv6 neighbors")
    assert "2001:db8:ab::2" in neigh
    brief = sess.execute("show ipv6 interface brief")
    assert "fe80::" in brief and "2001:db8:a::1/64" in brief
    ping_out = sess.execute("ping 2001:db8:b::10 2")
    assert "Success rate 100%" in ping_out
    trace = sess.execute("traceroute 2001:db8:b::10")
    assert "2001:db8:ab::2" in trace and "Trace complete." in trace


def test_cli_ipv6_config_and_mikrotik():
    net = Network(seed=6)
    r = net.add_device(Router("r1"))
    h = net.add_device(Host("pc", nos="routeros"))
    ir = net.add_iface(r, "eth0", [])
    ih = net.add_iface(h, "eth0", ["2001:db8:7::2/64"])
    net.connect("l", ir, ih)
    sess = CliSession(net, r)
    for cmd in ("enable", "conf t", "interface eth0",
                "ipv6 address 2001:db8:7::1/64", "end"):
        sess.execute(cmd)
    assert "2001:db8:7::/64" in sess.execute("show ipv6 route")
    sess.execute("conf t")
    sess.execute("ipv6 route 2001:db8:8::/64 2001:db8:7::2")
    sess.execute("end")
    assert "2001:db8:8::/64" in sess.execute("show ipv6 route")

    mt = CliSession(net, h)
    out = mt.execute("/ipv6 address print")
    assert "2001:db8:7::2/64" in out and "fe80::" in out
    ping_out = mt.execute("/ping 2001:db8:7::1 count=2")
    assert "Success rate 100%" in ping_out


# ---------------------------------------------------------------------------
# HTTP exit gate: dual-stack 5-node lab, simulation mode, step + seek
# ---------------------------------------------------------------------------

def _iface(name: str, ips: list[str]):
    return {"id": "", "node_id": "", "name": name, "ip": ips}


async def _dual_stack_lab(client) -> str:
    """5 nodes: h1 -- sw -- r1 -- r2 -- h2, dual-stack addressing."""
    resp = await client.post("/api/projects", json={"name": "R1DualStack"})
    pid = resp.json()["id"]

    async def node(name, kind, ifaces, intent=None):
        body = {"project_id": pid, "name": name, "kind": kind, "interfaces": ifaces}
        if intent:
            body["intent"] = intent
        r = await client.post("/api/nodes", json=body)
        assert r.status_code == 201, r.text
        return r.json()

    h1 = await node(
        "h1", "host", [_iface("eth0", ["192.168.1.10/24", "2001:db8:1::10/64"])],
        {"gateway": "192.168.1.1", "gateway6": "2001:db8:1::1"})
    sw = await node("sw", "switch", [_iface("gi0/1", []), _iface("gi0/2", [])])
    r1 = await node(
        "r1", "router",
        [_iface("eth0", ["192.168.1.1/24", "2001:db8:1::1/64"]),
         _iface("eth1", ["10.0.0.1/30", "2001:db8:12::1/64"])],
        {"static_routes": [{"prefix": "192.168.2.0/24", "next_hop": "10.0.0.2"}],
         "static_routes6": [{"prefix": "2001:db8:2::/64",
                             "next_hop": "2001:db8:12::2"}]})
    r2 = await node(
        "r2", "router",
        [_iface("eth0", ["10.0.0.2/30", "2001:db8:12::2/64"]),
         _iface("eth1", ["192.168.2.1/24", "2001:db8:2::1/64"])],
        {"static_routes": [{"prefix": "192.168.1.0/24", "next_hop": "10.0.0.1"}],
         "static_routes6": [{"prefix": "2001:db8:1::/64",
                             "next_hop": "2001:db8:12::1"}]})
    h2 = await node(
        "h2", "host", [_iface("eth0", ["192.168.2.10/24", "2001:db8:2::10/64"])],
        {"gateway": "192.168.2.1", "gateway6": "2001:db8:2::1"})

    def iface_id(node_json, name):
        return next(i["id"] for i in node_json["interfaces"] if i["name"] == name)

    for a, b in (
        (iface_id(h1, "eth0"), iface_id(sw, "gi0/1")),
        (iface_id(sw, "gi0/2"), iface_id(r1, "eth0")),
        (iface_id(r1, "eth1"), iface_id(r2, "eth0")),
        (iface_id(r2, "eth1"), iface_id(h2, "eth0")),
    ):
        r = await client.post(
            "/api/links", json={"project_id": pid, "a_iface": a, "b_iface": b}
        )
        assert r.status_code == 201, r.text
    return pid


async def test_dual_stack_lab_ping_both_families(client):
    pid = await _dual_stack_lab(client)
    v4 = await client.post(f"/api/lab/{pid}/ping",
                           json={"src": "h1", "dst": "192.168.2.10", "count": 2})
    assert v4.status_code == 200 and v4.json()["received"] == 2, v4.text
    v6 = await client.post(f"/api/lab/{pid}/ping",
                           json={"src": "h1", "dst": "2001:db8:2::10", "count": 2})
    assert v6.status_code == 200 and v6.json()["received"] == 2, v6.text


async def test_simulation_mode_step_through_icmpv6(client):
    """Exit gate: user steps through an ICMPv6 exchange in the ledger."""
    pid = await _dual_stack_lab(client)

    mode = await client.post(f"/api/lab/{pid}/mode", json={"mode": "simulation"})
    assert mode.status_code == 200 and mode.json()["mode"] == "simulation"

    ping = await client.post(f"/api/lab/{pid}/ping",
                             json={"src": "h1", "dst": "2001:db8:2::10", "count": 1})
    body = ping.json()
    assert body["mode"] == "simulation" and body["done"] is False  # queued, not run

    # Step until the echo-reply lands back at h1 (bounded loop).
    seen_icmp6 = []
    for _ in range(60):
        step = (await client.post(f"/api/lab/{pid}/step", json={"events": 20})).json()
        seen_icmp6 += [r for r in step["records"]
                       if "ICMPv6 echo" in r.get("info", "")]
        if any("echo-reply" in r["info"] and r["node"] and r["type"] == "PACKET_RX"
               for r in seen_icmp6):
            break
    kinds = {("echo-request" in r["info"], r["type"]) for r in seen_icmp6}
    assert (True, "PACKET_TX") in kinds and any(
        "echo-reply" in r["info"] for r in seen_icmp6
    ), seen_icmp6[:5]


async def test_seek_step_back_reproduces_events_byte_identically(client):
    """AC (NG-SIM-01): stepping back to event N and forward again reproduces
    events N+1.. byte-identically."""
    pid = await _dual_stack_lab(client)
    ping = await client.post(f"/api/lab/{pid}/ping",
                             json={"src": "h1", "dst": "2001:db8:2::10", "count": 2})
    assert ping.status_code == 200 and ping.json()["received"] == 2

    led = (await client.get(f"/api/lab/{pid}/ledger?limit=1")).json()
    total, full_hash = led["total"], led["hash"]
    n = total // 2

    # Original records N+1..total.
    orig = (await client.get(
        f"/api/lab/{pid}/ledger?from_seq={n}&limit=2000")).json()["records"]

    seek = (await client.post(f"/api/lab/{pid}/seek", json={"seq": n})).json()
    assert seek["seq"] == n and seek["mode"] == "simulation"

    step = (await client.post(
        f"/api/lab/{pid}/step", json={"events": total - n})).json()
    assert step["seq"] == total
    replay = (await client.get(
        f"/api/lab/{pid}/ledger?from_seq={n}&limit=2000")).json()
    assert replay["records"] == orig            # byte-identical event stream
    assert replay["hash"] == full_hash          # incremental hash matches too


async def test_seek_discards_rewritten_future(client):
    pid = await _dual_stack_lab(client)
    await client.post(f"/api/lab/{pid}/ping",
                      json={"src": "h1", "dst": "192.168.2.10", "count": 1})
    led = (await client.get(f"/api/lab/{pid}/ledger?limit=1")).json()
    n = led["total"] // 3
    seek = (await client.post(f"/api/lab/{pid}/seek", json={"seq": n})).json()
    assert seek["seq"] == n
    # A fresh stimulus after seeking rewrites the future deterministically.
    ping = await client.post(f"/api/lab/{pid}/ping",
                             json={"src": "h1", "dst": "2001:db8:1::1", "count": 1})
    assert ping.status_code == 200
    step = (await client.post(f"/api/lab/{pid}/step", json={"duration": 10.0})).json()
    assert step["seq"] > n
