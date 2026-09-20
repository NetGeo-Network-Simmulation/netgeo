"""NPTv6: IPv6-to-IPv6 Network Prefix Translation (RFC 6296).

NAT64/DNS64 (RFC 6146/6147) — the IPv6-only-client-to-IPv4-server case this
mapping does NOT cover — lives in ``test_nat64.py``.

Unlike NAT44 (``NatBinding``), NPTv6 is stateless and 1:1: the mapping is a
pure function of the two configured prefixes, so ``Nptv6Mapping`` carries no
per-flow binding table, no ports — just the prefixes plus one precomputed
checksum-adjustment word (RFC 6296 §3.7). Translation happens in
``Router._forward6``, alongside the existing IPv6 forwarding pipeline.
"""
from __future__ import annotations

from ipaddress import IPv6Address, IPv6Network

import pytest

from app.models import Topology
from app.services.netlab import build_network
from engine.netstack import Network
from engine.netstack.device import Host
from engine.netstack.routing import Nptv6Mapping, Router, _ones_add, _v6_words

INTERNAL = IPv6Network("fd01:203:405::/48")
EXTERNAL = IPv6Network("2001:db8:1::/48")


def _ones_complement_sum(addr: IPv6Address) -> int:
    """Reference (test-only, independent of the engine's own helpers) RFC
    1071 16-bit one's-complement sum over all eight words of an address —
    used to prove checksum-neutrality from the outside."""
    packed = int(addr)
    total = 0
    for i in range(8):
        total += (packed >> (16 * (7 - i))) & 0xFFFF
    while total > 0xFFFF:
        total = (total & 0xFFFF) + (total >> 16)
    return total


def test_mismatched_prefix_lengths_rejected():
    with pytest.raises(ValueError):
        Nptv6Mapping(
            internal=INTERNAL,
            external=IPv6Network("2001:db8:1::/56"),
            inside_ifaces=frozenset({"eth0"}),
            outside_iface="eth1",
        )


def test_translation_is_checksum_neutral_and_leaves_iid_untouched():
    mapping = Nptv6Mapping(
        internal=INTERNAL, external=EXTERNAL,
        inside_ifaces=frozenset({"eth0"}), outside_iface="eth1",
    )
    original = IPv6Address("fd01:203:405:1::10")
    translated = mapping.to_external(original)

    assert translated in EXTERNAL
    assert original not in EXTERNAL

    # Interface identifier (low 64 bits / words 4-7) is bit-for-bit
    # identical — only the subnet-id/adjustment word (word 3) may change.
    assert original.packed[8:] == translated.packed[8:]

    # RFC 6296 §2.6/§3.7: checksum-neutral — the whole-address one's
    # complement sum is unchanged by translation.
    assert _ones_complement_sum(original) == _ones_complement_sum(translated)

    # Stateless, algorithmic, 1:1: translating back reproduces the exact
    # original with no binding table anywhere.
    assert mapping.to_internal(translated) == original


def test_out_of_prefix_address_is_not_a_valid_translation_input():
    """Addresses outside both configured prefixes are the router's job to
    leave alone (tested end-to-end below); at the mapping level, membership
    is a plain prefix containment check with no special-casing to get wrong."""
    mapping = Nptv6Mapping(
        internal=INTERNAL, external=EXTERNAL,
        inside_ifaces=frozenset({"eth0"}), outside_iface="eth1",
    )
    outsider = IPv6Address("2001:db8:12::2")
    assert outsider not in mapping.internal
    assert outsider not in mapping.external


def _nptv6_topology() -> tuple[Network, Host, Router, Router, Host]:
    """pc (internal prefix) -- gw -- server (WAN), plus pc2 attached to a
    third, *inside*-listed interface whose address sits outside the
    internal prefix — proves the address-membership gate, not just the
    interface gate. ``server`` is a Router (not a Host) purely so it can
    carry a static route back to the NPTv6 external prefix — it still
    answers pings like any L3Device."""
    net = Network(seed=42)
    pc = net.add_device(Host("pc"))
    pc2 = net.add_device(Host("pc2"))
    gw = net.add_device(Router("gw"))
    server = net.add_device(Router("server"))

    net.connect(
        "lan",
        net.add_iface(pc, "eth0", ["fd01:203:405:1::10/64"]),
        net.add_iface(gw, "eth0", ["fd01:203:405:1::1/64"]),
    )
    net.connect(
        "lan2",
        net.add_iface(pc2, "eth0", ["2001:db8:99::10/64"]),
        net.add_iface(gw, "eth2", ["2001:db8:99::1/64"]),
    )
    net.connect(
        "wan",
        net.add_iface(gw, "eth1", ["2001:db8:12::1/64"]),
        net.add_iface(server, "eth0", ["2001:db8:12::2/64"]),
    )
    pc.default_gateway6 = IPv6Address("fd01:203:405:1::1")
    pc2.default_gateway6 = IPv6Address("2001:db8:99::1")
    server.add_static_route6(str(EXTERNAL), "2001:db8:12::1")
    server.add_static_route6("2001:db8:99::/64", "2001:db8:12::1")

    gw.enable_nptv6(INTERNAL, EXTERNAL, inside=["eth0", "eth2"], outside="eth1")
    return net, pc, gw, server, pc2


def test_nptv6_outbound_rewrites_source_and_reply_round_trips():
    net, _pc, _gw, _server, _pc2 = _nptv6_topology()
    report = net.ping("pc", "2001:db8:12::2", count=3)
    assert report.received == 3

    wan_frames = net.capture.records(link_id="wan", limit=500)
    assert wan_frames
    # The internal address must never appear on the WAN wire.
    assert not any(
        "fd01:203:405:1::10" in r.layers.get("ipv6", {}).get("src", "")
        for r in wan_frames
    )
    echo_requests = [
        r for r in wan_frames
        if r.layers.get("icmpv6", {}).get("name") == "echo-request"
    ]
    assert echo_requests
    for r in echo_requests:
        assert IPv6Address(r.layers["ipv6"]["src"]) in EXTERNAL
        # Destination (server's real address) is untouched by NPTv6.
        assert r.layers["ipv6"]["dst"] == "2001:db8:12::2"


def test_nptv6_address_outside_internal_prefix_passes_through_unchanged():
    """pc2 sits on an ``inside`` interface, but its address is not in the
    internal prefix — the address gate (not just the interface gate) must
    keep it untranslated."""
    net, _pc, _gw, _server, _pc2 = _nptv6_topology()
    report = net.ping("pc2", "2001:db8:12::2", count=2)
    assert report.received == 2

    wan_frames = net.capture.records(link_id="wan", limit=500)
    echo_requests = [
        r for r in wan_frames
        if r.layers.get("icmpv6", {}).get("name") == "echo-request"
    ]
    assert echo_requests
    for r in echo_requests:
        assert r.layers["ipv6"]["src"] == "2001:db8:99::10"


# ----- A6: /49-/64 prefixes (RFC 6296 §3.5/§3.7) -------------------------

# Deterministic address pool (no runtime randomness) covering: all words
# 0x0000, all 0xFFFF, mixed, and a swept-bit pattern — enough to exercise
# every full/partial IID word the §3.7 search can land on.
_A6_ADDR_HOST_PARTS = [
    "0:0:0:0",
    "ffff:ffff:ffff:ffff",
    "1:2:3:4",
    "dead:beef:cafe:1",
    "ffff:1:2:3",
    "1:ffff:ffff:ffff",
    "ffff:ffff:ffff:1",
    "abcd:0:ffff:1234",
]
_A6_PLENS = [16, 32, 40, 48, 49, 56, 60, 64]


def _a6_mapping(plen: int) -> Nptv6Mapping:
    # strict=False: masks off host bits instead of raising, so the same
    # base literal works for every prefix length in _A6_PLENS.
    internal = IPv6Network(f"fd01:203:405:600::/{plen}", strict=False)
    external = IPv6Network(f"2001:db8:1:600::/{plen}", strict=False)
    return Nptv6Mapping(
        internal=internal, external=external,
        inside_ifaces=frozenset({"eth0"}), outside_iface="eth1",
    )


def _a6_cases():
    for plen in _A6_PLENS:
        mapping = _a6_mapping(plen)
        for host in _A6_ADDR_HOST_PARTS:
            # Build a concrete internal address: network prefix bits fixed
            # by the /plen network, host bits taken from the pool.
            net_int = int(mapping.internal.network_address)
            host_mask = (1 << (128 - plen)) - 1
            host_int = int(IPv6Address("::" + host)) & host_mask
            addr = IPv6Address(net_int | host_int)
            yield plen, mapping, addr


def test_a6_checksum_neutral_and_round_trip_for_all_plens_and_addresses():
    for plen, mapping, addr in _a6_cases():
        translated = mapping.to_external(addr)
        if translated is None:
            # RFC 6296 §3.7: only possible when every IID word is 0xFFFF.
            assert plen > 48
            assert all(w == 0xFFFF for w in _v6_words(addr)[4:8])
            continue

        # (1) whole-address one's-complement sum is checksum-neutral.
        assert _ones_complement_sum(addr) == _ones_complement_sum(translated), (plen, addr)

        # (2) round-trip internal -> external -> internal is identity,
        # *except* the one RFC-documented case (§4.2/Appendix B) where the
        # adjusted word's original value made the mapping non-invertible
        # under the 0xFFFF->0x0000 suppression rule (a config the RFC tells
        # operators to avoid, not a bug in the translator).
        back = mapping.to_internal(translated)
        assert back is not None
        if back != addr:
            words = _v6_words(addr)
            target = 3 if plen <= 48 else next(j for j in (4, 5, 6, 7) if words[j] != 0xFFFF)
            assert words[target] == 0xFFFF, (plen, addr, translated, back)
            continue

        # (3) top `plen` bits equal the external prefix's top `plen` bits.
        assert int(translated) >> (128 - plen) == int(mapping.external.network_address) >> (128 - plen)


def _v6_from_words_test(words: list[int]) -> IPv6Address:
    packed = 0
    for w in words:
        packed = (packed << 16) | w
    return IPv6Address(packed)


def test_a6_0xffff_edge_case_forced_to_zero():
    # /48: adjustment word is bits 48-63 (word 3, RFC 6296 §3.4 — the fixed
    # target for any plen <= 48). The word 3 value that makes
    # ``add1(word3, adjustment) == 0xFFFF`` is solvable directly (RFC 1071
    # one's-complement: word3 = 0xFFFF - adjustment).
    mapping = _a6_mapping(48)
    prefix_words = _v6_words(mapping.internal.network_address)[:3]
    candidate_word3 = _ones_add(0xFFFF, (~mapping._adjustment) & 0xFFFF)
    assert candidate_word3 != 0xFFFF  # would trivially not exercise the rule

    addr = _v6_from_words_test([*prefix_words, candidate_word3, 0, 0, 0, 1])
    assert _ones_add(candidate_word3, mapping._adjustment) == 0xFFFF

    translated = mapping.to_external(addr)
    assert translated is not None
    assert _v6_words(translated)[3] == 0  # §3.2: 0xFFFF result forced to 0x0000


def test_a6_all_iid_words_ffff_is_untranslatable():
    mapping = _a6_mapping(56)
    net_int = int(mapping.internal.network_address)
    words = _v6_words(IPv6Address(net_int))
    words[4:8] = [0xFFFF, 0xFFFF, 0xFFFF, 0xFFFF]  # every candidate IID word saturated
    addr = _v6_from_words_test(words)
    assert mapping.to_external(addr) is None


def test_a6_end_to_end_slash56_site_ping():
    net = Network(seed=7)
    pc = net.add_device(Host("pc"))
    gw = net.add_device(Router("gw"))
    server = net.add_device(Router("server"))

    net.connect(
        "lan",
        net.add_iface(pc, "eth0", ["fd01:203:405:600::10/64"]),
        net.add_iface(gw, "eth0", ["fd01:203:405:600::1/64"]),
    )
    net.connect(
        "wan",
        net.add_iface(gw, "eth1", ["2001:db8:12::1/64"]),
        net.add_iface(server, "eth0", ["2001:db8:12::2/64"]),
    )
    pc.default_gateway6 = IPv6Address("fd01:203:405:600::1")
    server.add_static_route6("2001:db8:1:600::/56", "2001:db8:12::1")

    gw.enable_nptv6(
        IPv6Network("fd01:203:405:600::/56"),
        IPv6Network("2001:db8:1:600::/56"),
        inside=["eth0"], outside="eth1",
    )

    report = net.ping("pc", "2001:db8:12::2", count=3)
    assert report.received == 3

    wan_frames = net.capture.records(link_id="wan", limit=500)
    echo_requests = [
        r for r in wan_frames
        if r.layers.get("icmpv6", {}).get("name") == "echo-request"
    ]
    assert echo_requests
    for r in echo_requests:
        src = IPv6Address(r.layers["ipv6"]["src"])
        assert src in IPv6Network("2001:db8:1:600::/56")
        assert r.layers["ipv6"]["dst"] == "2001:db8:12::2"


def test_nptv6_intent_round_trip_through_topology_build():
    topo = Topology.model_validate({
        "project": {"id": "p1", "name": "t"},
        "nodes": [
            {
                "id": "n1", "project_id": "p1", "name": "gw", "kind": "router",
                "interfaces": [
                    {"id": "i1", "node_id": "n1", "name": "eth0",
                     "ip": ["fd01:203:405:1::1/64"]},
                    {"id": "i2", "node_id": "n1", "name": "eth1",
                     "ip": ["2001:db8:12::1/64"]},
                ],
                "intent": {"nptv6": {
                    "internal": str(INTERNAL), "external": str(EXTERNAL),
                    "inside": ["eth0"], "outside": "eth1",
                }},
            },
        ],
        "links": [],
    })
    net = build_network(topo)
    gw = net.devices["gw"]
    assert isinstance(gw, Router)
    assert gw.nptv6 is not None
    assert gw.nptv6.internal == INTERNAL
    assert gw.nptv6.external == EXTERNAL
    assert gw.nptv6.inside_ifaces == frozenset({"eth0"})
    assert gw.nptv6.outside_iface == "eth1"
