"""ECMP for IPv6: same deterministic per-flow selection as IPv4 (test_ecmp.py),
routed through the same shared ``routing._lpm`` -- ``Route6``/``Route`` have
the same ad/metric/prefix/next_hop/iface_name shape, so nothing v6-specific
had to be invented. ``flow_key_v6`` uses the identical byte layout as
``flow_key_v4`` (see ``routing._flow_key``), just fed IPv6 addresses.
"""
from __future__ import annotations

from ipaddress import IPv6Address, IPv6Network

from engine.netstack.frames import (
    PROTO_ICMPV6,
    PROTO_TCP,
    Icmpv6Message,
    Ipv6Packet,
    TcpSegment,
)
from engine.netstack.routing import Route6, Router, flow_key_v6

DST_NET = IPv6Network("2001:db8:10::/64")
NH_A = IPv6Address("2001:db8:1::1")
NH_B = IPv6Address("2001:db8:1::2")


def _ecmp_router() -> Router:
    r = Router("r1")
    r.routes6 = [
        Route6(prefix=DST_NET, next_hop=NH_A, iface_name=None, source="static", metric=0),
        Route6(prefix=DST_NET, next_hop=NH_B, iface_name=None, source="static", metric=0),
    ]
    return r


def _tcp_pkt(src: str, dst: str = "2001:db8:10::5", sport: int = 1234, dport: int = 80) -> Ipv6Packet:
    return Ipv6Packet(
        src=IPv6Address(src), dst=IPv6Address(dst), proto=PROTO_TCP,
        payload=TcpSegment(src_port=sport, dst_port=dport),
    )


def test_single_route_behaviour_unchanged():
    r = Router("r1")
    r.routes6 = [Route6(prefix=DST_NET, next_hop=NH_A, iface_name=None, source="static")]
    pkt = _tcp_pkt("2001:db8:20::1")
    assert r.lookup6(pkt.dst, flow_key_v6(pkt)).next_hop == NH_A
    assert r.lookup6(pkt.dst).next_hop == NH_A  # no flow_key at all: same result


def test_traffic_from_different_flows_splits_across_both_next_hops():
    r = _ecmp_router()
    seen = set()
    for i in range(50):
        pkt = _tcp_pkt(f"2001:db8:20::{i + 1}", sport=1000 + i)
        route = r.lookup6(pkt.dst, flow_key_v6(pkt))
        seen.add(route.next_hop)
    assert seen == {NH_A, NH_B}


def test_one_flow_always_takes_the_same_next_hop():
    r = _ecmp_router()
    pkt = _tcp_pkt("2001:db8:20::9", sport=54321, dport=443)
    key = flow_key_v6(pkt)
    chosen = {r.lookup6(pkt.dst, key).next_hop for _ in range(20)}
    assert len(chosen) == 1


def test_icmpv6_degrades_to_3_tuple_and_stays_sticky():
    r = _ecmp_router()
    pkt = Ipv6Packet(
        src=IPv6Address("2001:db8:20::9"), dst=IPv6Address("2001:db8:10::5"),
        proto=PROTO_ICMPV6, payload=Icmpv6Message(type=128, code=0),
    )
    key = flow_key_v6(pkt)
    assert len(key) == 33  # 16 (src) + 16 (dst) + 1 (proto), no ports
    chosen = {r.lookup6(pkt.dst, key).next_hop for _ in range(10)}
    assert len(chosen) == 1


def test_unequal_cost_routes_never_form_an_ecmp_group():
    r = Router("r1")
    r.routes6 = [
        Route6(prefix=DST_NET, next_hop=NH_A, iface_name=None, source="static", metric=0),  # ad=1
        Route6(prefix=DST_NET, next_hop=NH_B, iface_name=None, source="ospf", metric=0),     # ad=110
    ]
    for i in range(20):
        pkt = _tcp_pkt(f"2001:db8:21::{i + 1}", sport=2000 + i)
        assert r.lookup6(pkt.dst, flow_key_v6(pkt)).next_hop == NH_A


def test_flow_to_path_assignment_is_identical_across_pythonhashseed():
    """Same journal-replay-determinism proof as the v4 test, in-process: the
    hash is zlib.crc32 (not builtin hash()), shared code path with v4."""
    import os
    import subprocess
    import sys

    script = """
import sys
sys.path.insert(0, {backend_dir!r})
from ipaddress import IPv6Address, IPv6Network
from engine.netstack.frames import PROTO_TCP, Ipv6Packet, TcpSegment
from engine.netstack.routing import Route6, Router, flow_key_v6

r = Router("r1")
net = IPv6Network("2001:db8:10::/64")
r.routes6 = [
    Route6(prefix=net, next_hop=IPv6Address("2001:db8:1::1"), iface_name=None, source="static"),
    Route6(prefix=net, next_hop=IPv6Address("2001:db8:1::2"), iface_name=None, source="static"),
]
results = []
for i in range(30):
    pkt = Ipv6Packet(
        src=IPv6Address(f"2001:db8:20::{{i + 1}}"), dst=IPv6Address("2001:db8:10::5"),
        proto=PROTO_TCP, payload=TcpSegment(src_port=1000 + i, dst_port=80),
    )
    results.append(str(r.lookup6(pkt.dst, flow_key_v6(pkt)).next_hop))
print(",".join(results))
"""
    backend_dir = str(__file__).rsplit("/tests/", 1)[0]
    script = script.format(backend_dir=backend_dir)

    def run(seed: str) -> str:
        out = subprocess.run(
            [sys.executable, "-c", script],
            env={"PYTHONHASHSEED": seed, "PATH": os.environ.get("PATH", "")},
            capture_output=True, text=True, check=True, cwd=backend_dir,
        )
        return out.stdout.strip()

    assert run("0") == run("1") == run("random")
