"""ECMP: equal-cost multi-path with deterministic per-flow next-hop selection.

Route.next_hop is still scalar (unchanged shape) -- an ECMP "group" is just
several Route rows sharing the same prefix, source and metric, sitting side
by side in Router.routes. Selection among a tied group happens inside
``routing._lpm`` via a flow-key hash (``zlib.crc32``, never builtin
``hash()`` -- that one is salted per-process by PYTHONHASHSEED and would
break journal replay).
"""
from __future__ import annotations

import subprocess
import sys
from ipaddress import IPv4Address, IPv4Network

from engine.netstack.frames import (
    PROTO_ICMP,
    PROTO_TCP,
    IcmpMessage,
    Ipv4Packet,
    TcpSegment,
)
from engine.netstack.routing import Route, Router, flow_key_v4

DST_NET = IPv4Network("10.0.0.0/24")
NH_A = IPv4Address("192.168.1.1")
NH_B = IPv4Address("192.168.1.2")


def _ecmp_router() -> Router:
    r = Router("r1")
    r.routes = [
        Route(prefix=DST_NET, next_hop=NH_A, iface_name=None, source="static", metric=0),
        Route(prefix=DST_NET, next_hop=NH_B, iface_name=None, source="static", metric=0),
    ]
    return r


def _tcp_pkt(src: str, dst: str = "10.0.0.5", sport: int = 1234, dport: int = 80) -> Ipv4Packet:
    return Ipv4Packet(
        src=IPv4Address(src), dst=IPv4Address(dst), proto=PROTO_TCP,
        payload=TcpSegment(src_port=sport, dst_port=dport),
    )


def test_single_route_behaviour_unchanged():
    r = Router("r1")
    r.routes = [Route(prefix=DST_NET, next_hop=NH_A, iface_name=None, source="static")]
    pkt = _tcp_pkt("172.16.0.1")
    assert r.lookup(pkt.dst, flow_key_v4(pkt)).next_hop == NH_A
    assert r.lookup(pkt.dst).next_hop == NH_A  # no flow_key at all: same result


def test_traffic_from_different_flows_splits_across_both_next_hops():
    r = _ecmp_router()
    seen = set()
    for i in range(50):
        pkt = _tcp_pkt(f"172.16.0.{i}", sport=1000 + i)
        route = r.lookup(pkt.dst, flow_key_v4(pkt))
        seen.add(route.next_hop)
    assert seen == {NH_A, NH_B}


def test_one_flow_always_takes_the_same_next_hop():
    r = _ecmp_router()
    pkt = _tcp_pkt("172.16.5.9", sport=54321, dport=443)
    key = flow_key_v4(pkt)
    chosen = {r.lookup(pkt.dst, key).next_hop for _ in range(20)}
    assert len(chosen) == 1


def test_icmp_degrades_to_3_tuple_and_stays_sticky():
    r = _ecmp_router()
    pkt = Ipv4Packet(
        src=IPv4Address("172.16.9.9"), dst=IPv4Address("10.0.0.5"),
        proto=PROTO_ICMP, payload=IcmpMessage(type=8, code=0),
    )
    key = flow_key_v4(pkt)
    assert len(key) == 9  # 4 (src) + 4 (dst) + 1 (proto), no ports
    chosen = {r.lookup(pkt.dst, key).next_hop for _ in range(10)}
    assert len(chosen) == 1


def test_unequal_cost_routes_never_form_an_ecmp_group():
    r = Router("r1")
    r.routes = [
        Route(prefix=DST_NET, next_hop=NH_A, iface_name=None, source="static", metric=0),  # ad=1
        Route(prefix=DST_NET, next_hop=NH_B, iface_name=None, source="ospf", metric=0),     # ad=110
    ]
    for i in range(20):
        pkt = _tcp_pkt(f"172.16.1.{i}", sport=2000 + i)
        assert r.lookup(pkt.dst, flow_key_v4(pkt)).next_hop == NH_A

    r2 = Router("r1")
    r2.routes = [
        Route(prefix=DST_NET, next_hop=NH_A, iface_name=None, source="static", metric=0),
        Route(prefix=DST_NET, next_hop=NH_B, iface_name=None, source="static", metric=5),
    ]
    for i in range(20):
        pkt = _tcp_pkt(f"172.16.2.{i}", sport=3000 + i)
        assert r2.lookup(pkt.dst, flow_key_v4(pkt)).next_hop == NH_A


_SUBPROCESS_SCRIPT = """
import sys
sys.path.insert(0, {backend_dir!r})
from ipaddress import IPv4Address, IPv4Network
from engine.netstack.frames import PROTO_TCP, Ipv4Packet, TcpSegment
from engine.netstack.routing import Route, Router, flow_key_v4

r = Router("r1")
net = IPv4Network("10.0.0.0/24")
r.routes = [
    Route(prefix=net, next_hop=IPv4Address("192.168.1.1"), iface_name=None, source="static"),
    Route(prefix=net, next_hop=IPv4Address("192.168.1.2"), iface_name=None, source="static"),
]
results = []
for i in range(30):
    pkt = Ipv4Packet(
        src=IPv4Address(f"172.16.0.{{i}}"), dst=IPv4Address("10.0.0.5"),
        proto=PROTO_TCP, payload=TcpSegment(src_port=1000 + i, dst_port=80),
    )
    results.append(str(r.lookup(pkt.dst, flow_key_v4(pkt)).next_hop))
print(",".join(results))
"""


def _run_with_hashseed(seed: str) -> str:
    backend_dir = str(__file__).rsplit("/tests/", 1)[0]
    script = _SUBPROCESS_SCRIPT.format(backend_dir=backend_dir)
    out = subprocess.run(
        [sys.executable, "-c", script],
        env={"PYTHONHASHSEED": seed, "PATH": __import__("os").environ.get("PATH", "")},
        capture_output=True, text=True, check=True, cwd=backend_dir,
    )
    return out.stdout.strip()


def test_flow_to_path_assignment_is_identical_across_pythonhashseed():
    """Real proof, not an assumption: run the actual selection in three
    separate interpreter processes with different PYTHONHASHSEED values and
    diff their output. zlib.crc32 (not builtin hash()) must make them equal
    -- this is exactly the journal-replay-breaking failure mode the slice
    exists to rule out."""
    out0 = _run_with_hashseed("0")
    out1 = _run_with_hashseed("1")
    out_random = _run_with_hashseed("random")
    assert out0 == out1 == out_random
