"""P-4b: ICMP echo-reply DF inheritance + host-side MTU drop accounting."""
from __future__ import annotations

from ipaddress import IPv4Address

from engine.netstack import Network
from engine.netstack.device import Host
from engine.netstack.frames import PROTO_ICMP, IcmpMessage, Ipv4Packet
from engine.netstack.routing import Router


def _pair(seed: int, mtu: int) -> tuple[Network, Host, Router]:
    """h1 -- r1, one low-MTU link (same shape as test_ipv4_fragmentation.py)."""
    net = Network(seed=seed)
    h1 = net.add_device(Host("h1"))
    r1 = net.add_device(Router("r1"))
    i_h1 = net.add_iface(h1, "eth0", ["10.0.1.1/24"])
    i_r1 = net.add_iface(r1, "eth0", ["10.0.1.254/24"])
    net.connect("a", i_h1, i_r1, mtu=mtu)
    net.start()
    return net, h1, r1


def test_echo_reply_inherits_df_from_request():
    """A DF=0 echo too big for the link fragments and reassembles fine at
    r1 (P-4). Its reply used to default to DF=1 and get silently dropped
    on the way back (P-4b bug) -- it must now inherit DF=0 and arrive."""
    net, h1, _r1 = _pair(seed=41, mtu=576)
    ident = net.new_ping_session(h1, IPv4Address("10.0.1.254"), count=1)
    net.ping_sent(ident, 1)
    h1.send_ip(
        net,
        Ipv4Packet(
            src=IPv4Address("10.0.1.1"),
            dst=IPv4Address("10.0.1.254"),
            proto=PROTO_ICMP,
            ttl=64,
            dont_fragment=False,
            payload=IcmpMessage(type=8, ident=ident, seq=1, data_len=1400),
        ),
    )
    net.run_for(5.0)

    rep = net.pings[ident]
    assert rep.received == 1
    assert net.drops.get("mtu_exceeded", 0) == 0


def test_echo_reply_keeps_df_when_request_had_df():
    """Regression: a normal DF=1 echo (the common case) still gets a DF=1
    reply -- the fix must not flip DF for requests that already had it."""
    net, h1, r1 = _pair(seed=42, mtu=1500)
    seen_df: list[bool] = []
    orig_send_ip = r1.send_ip

    def spy(net_, pkt):
        icmp = pkt.payload
        if pkt.proto == PROTO_ICMP and isinstance(icmp, IcmpMessage) and icmp.type == 0:
            seen_df.append(pkt.dont_fragment)
        return orig_send_ip(net_, pkt)

    r1.send_ip = spy

    h1.ping(net, IPv4Address("10.0.1.254"), count=1, size=56)
    net.run_for(2.0)

    assert seen_df == [True]


def test_host_mtu_drop_is_counted():
    """A host sending an oversized DF=1 packet on its own link must drop it
    observably via net.drops, not raise (missing on_mtu_drop) or vanish."""
    net = Network(seed=43)
    h1 = net.add_device(Host("h1"))
    h2 = net.add_device(Host("h2"))
    net.connect(
        "a",
        net.add_iface(h1, "eth0", ["10.0.0.1/24"]),
        net.add_iface(h2, "eth0", ["10.0.0.2/24"]),
        mtu=576,
    )
    net.start()

    ident = h1.ping(net, IPv4Address("10.0.0.2"), count=1, size=1400)
    net.run_for(2.0)

    assert net.drops.get("mtu_exceeded", 0) == 1
    # A plain host stays silent on its own oversized send (see
    # Device.on_mtu_drop docstring) -- no ICMP error, just an observed drop.
    assert net.pings[ident].received == 0
