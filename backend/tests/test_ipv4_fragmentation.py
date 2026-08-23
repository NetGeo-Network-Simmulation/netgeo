"""IPv4 fragmentation + reassembly (P-4, RFC 791 sec 3.1/3.2)."""
from __future__ import annotations

from ipaddress import IPv4Address

from engine.netstack import Network
from engine.netstack.device import Host
from engine.netstack.frames import PROTO_ICMP, EthernetFrame, IcmpMessage, Ipv4Packet
from engine.netstack.routing import Router


def _pair(seed: int, mtu: int) -> tuple[Network, Host, Router]:
    """h1 -- r1, one low-MTU link. Destination for the packets below is
    r1's own address, so r1's _local_deliver is the reassembly point."""
    net = Network(seed=seed)
    h1 = net.add_device(Host("h1"))
    r1 = net.add_device(Router("r1"))
    i_h1 = net.add_iface(h1, "eth0", ["10.0.1.1/24"])
    i_r1 = net.add_iface(r1, "eth0", ["10.0.1.254/24"])
    net.connect("a", i_h1, i_r1, mtu=mtu)
    net.start()
    return net, h1, r1


def test_fragmentation_df0_splits_and_reassembles():
    net, h1, r1 = _pair(seed=7, mtu=576)

    # Intercept the point where a fully-reassembled ICMP echo would be
    # answered -- proof the split payload arrived whole at L4, without also
    # exercising the (separately DF=1-by-default, out of scope here) reply path.
    delivered = []
    r1._handle_icmp_to_self = lambda net, pkt: delivered.append(pkt)

    h1.send_ip(
        net,
        Ipv4Packet(
            src=IPv4Address("10.0.1.1"),
            dst=IPv4Address("10.0.1.254"),
            proto=PROTO_ICMP,
            ttl=64,
            dont_fragment=False,
            payload=IcmpMessage(type=8, ident=1, seq=1, data_len=1400),
        ),
    )
    net.run_for(5.0)

    assert len(delivered) == 1
    pkt = delivered[0]
    assert pkt.payload.data_len == 1400
    assert not pkt.more_fragments and pkt.fragment_offset == 0
    assert net.drops.get("mtu_exceeded", 0) == 0
    assert r1._frag_bufs == {}

    tx = [r for r in net.capture.records(link_id="a") if r.direction == "tx"]
    req_frags = sorted(
        (
            r for r in tx
            if r.layers.get("ipv4", {}).get("dst") == "10.0.1.254"
            and r.layers["ipv4"]["identification"] != 0
        ),
        key=lambda r: r.layers["ipv4"]["fragment_offset"],
    )
    assert len(req_frags) >= 2
    ident = req_frags[0].layers["ipv4"]["identification"]
    for f in req_frags[:-1]:
        assert f.layers["ipv4"]["identification"] == ident
        assert f.layers["ipv4"]["more_fragments"] is True
        assert f.layers["ipv4"]["fragment_offset"] % 8 == 0
    assert req_frags[-1].layers["ipv4"]["more_fragments"] is False
    assert req_frags[-1].layers["ipv4"]["fragment_offset"] % 8 == 0


def test_fragmentation_df1_still_dropped_with_icmp():
    # Regression: DF=1 through a router hop whose next link has a lower MTU
    # -- same topology shape as the pre-existing test_mtu_violation test.
    net = Network(seed=8)
    h1 = net.add_device(Host("h1"))
    r1 = net.add_device(Router("r1"))
    h2 = net.add_device(Host("h2"))
    net.connect("a", net.add_iface(h1, "eth0", ["10.0.1.1/24"]),
                net.add_iface(r1, "eth0", ["10.0.1.254/24"]))
    net.connect("b", net.add_iface(r1, "eth1", ["10.0.2.254/24"]),
                net.add_iface(h2, "eth0", ["10.0.2.1/24"]), mtu=576)
    h1.default_gateway = IPv4Address("10.0.1.254")
    h2.default_gateway = IPv4Address("10.0.2.254")
    net.start()

    ident = h1.ping(net, IPv4Address("10.0.2.1"), count=1, size=1400)
    net.run_for(5.0)

    rep = net.pings[ident]
    assert rep.received == 0
    assert rep.errors and "unreachable(code=4)" in rep.errors[0]
    assert net.drops.get("mtu_exceeded", 0) == 1


def test_reassembly_timeout_drops_and_sends_icmp():
    net, h1, r1 = _pair(seed=9, mtu=576)
    i_h1 = h1.iface("eth0")
    i_r1 = r1.iface("eth0")

    errors = []
    net.on_icmp = lambda device, pkt, icmp: errors.append(icmp)

    # Hand-build a single fragment (offset 0, more_fragments=True) and feed
    # it straight into r1 -- the rest of the datagram never arrives.
    frag0 = Ipv4Packet(
        src=IPv4Address("10.0.1.1"),
        dst=IPv4Address("10.0.1.254"),
        proto=PROTO_ICMP,
        ttl=64,
        dont_fragment=False,
        identification=999,
        more_fragments=True,
        fragment_offset=0,
        frag_len=552,
        payload=IcmpMessage(type=8, ident=3, seq=1, data_len=1400),
    )
    r1.on_frame(
        net, i_r1,
        EthernetFrame(src_mac=i_h1.mac, dst_mac=i_r1.mac, payload=frag0),
    )
    key = (frag0.src, frag0.dst, frag0.proto, frag0.identification)
    assert key in r1._frag_bufs

    net.run_for(40.0)  # past FRAG_REASSEMBLY_TIMEOUT (30s)

    assert key not in r1._frag_bufs
    assert len(errors) == 1
    assert errors[0].type == 11 and errors[0].code == 1
