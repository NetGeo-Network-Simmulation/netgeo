"""NG-SIM-03 — VRRPv3 first-hop redundancy.

Covers master election by priority, ARP for the VIP answered with the
virtual MAC, deterministic failover within the RFC bound (3×adv + skew),
priority preemption on recovery, host traffic surviving a master failure,
and the advert's on-wire synthesis for pcapng.
"""
from __future__ import annotations

from ipaddress import IPv4Address

from engine.netstack import Network
from engine.netstack.cli import CliSession
from engine.netstack.device import Host
from engine.netstack.protocols.vrrp import VrrpProcess, virtual_mac
from engine.netstack.routing import Router
from engine.netstack.switching import Switch

VIP = "192.168.1.254"


def _fhrp_lab() -> tuple[Network, Router, Router, Host]:
    """h1 -- sw -- (r1 prio 120, r2 prio 100) -- upstream server."""
    net = Network(seed=21)
    h1 = net.add_device(Host("h1"))
    sw = net.add_device(Switch("sw"))
    r1, r2 = net.add_device(Router("r1")), net.add_device(Router("r2"))
    srv = net.add_device(Host("srv"))

    ih = net.add_iface(h1, "eth0", ["192.168.1.10/24"])
    h1.default_gateway = IPv4Address(VIP)
    net.connect("l-h", ih, net.add_iface(sw, "gi0/1"))
    net.connect("l-r1", net.add_iface(r1, "eth0", ["192.168.1.2/24"]),
                net.add_iface(sw, "gi0/2"))
    net.connect("l-r2", net.add_iface(r2, "eth0", ["192.168.1.3/24"]),
                net.add_iface(sw, "gi0/3"))
    # Upstream: both routers reach the server subnet via their own links.
    isrv = net.add_iface(srv, "eth0", ["10.9.0.10/24"])
    ssw = net.add_device(Switch("sw-up"))
    net.connect("l-srv", isrv, net.add_iface(ssw, "gi0/1"))
    net.connect("l-r1-up", net.add_iface(r1, "eth1", ["10.9.0.1/24"]),
                net.add_iface(ssw, "gi0/2"))
    net.connect("l-r2-up", net.add_iface(r2, "eth1", ["10.9.0.2/24"]),
                net.add_iface(ssw, "gi0/3"))
    # VRRP on both sides, like a real deployment — the server's gateway is a
    # VIP too, so the return path survives a master failure.
    srv.default_gateway = IPv4Address("10.9.0.254")

    VrrpProcess(r1, iface_name="eth0", vrid=10, vip=VIP, priority=120)
    VrrpProcess(r2, iface_name="eth0", vrid=10, vip=VIP, priority=100)
    VrrpProcess(r1, iface_name="eth1", vrid=20, vip="10.9.0.254", priority=120)
    VrrpProcess(r2, iface_name="eth1", vrid=20, vip="10.9.0.254", priority=100)
    return net, r1, r2, h1


def _vrrp(dev) -> VrrpProcess:
    return next(p for p in dev.processes if getattr(p, "proto", "") == "vrrp")


def test_master_election_by_priority():
    net, r1, r2, _h1 = _fhrp_lab()
    net.run_for(15.0)
    assert _vrrp(r1).state == "master"
    assert _vrrp(r2).state == "backup"
    assert str(virtual_mac(10)) in r1.mac_aliases
    assert IPv4Address(VIP) in r1.ip_aliases
    assert str(virtual_mac(10)) not in r2.mac_aliases


def test_host_pings_vip_and_learns_virtual_mac():
    net, _r1, _r2, h1 = _fhrp_lab()
    net.run_for(15.0)
    rep = net.ping("h1", VIP, count=3)
    assert rep.received == 3
    mac, _iface = h1.arp_table[IPv4Address(VIP)]
    assert mac == virtual_mac(10)


def test_failover_within_master_down_interval():
    net, r1, r2, _h1 = _fhrp_lab()
    net.run_for(15.0)
    assert _vrrp(r1).state == "master"

    t_fail = net.now
    net.set_device_power("r1", False)
    net.run_for(10.0)

    backup = _vrrp(r2)
    assert backup.state == "master"
    took_over_at = next(t for t, s in backup.transitions if s == "master")
    # RFC 9568: 3×adv + skew = 3×1.0 + (256-100)/256 ≈ 3.61 s (+ margin).
    assert took_over_at - t_fail <= backup.master_down_interval + 0.5


def test_traffic_survives_failover():
    net, r1, _r2, _h1 = _fhrp_lab()
    net.run_for(15.0)
    assert net.ping("h1", "10.9.0.10", count=2).received == 2
    net.set_device_power("r1", False)
    net.run_for(10.0)  # let r2 take over + gratuitous ARP re-teach the switch
    rep = net.ping("h1", "10.9.0.10", count=3)
    assert rep.received == 3, rep.as_dict()


def test_preemption_on_recovery():
    net, r1, r2, _h1 = _fhrp_lab()
    net.run_for(15.0)
    net.set_device_power("r1", False)
    net.run_for(10.0)
    assert _vrrp(r2).state == "master"

    net.set_device_power("r1", True)
    net.run_for(10.0)
    # Higher priority + preempt: r1 must reclaim mastership; r2 steps down.
    assert _vrrp(r1).state == "master"
    assert _vrrp(r2).state == "backup"
    assert str(virtual_mac(10)) not in r2.mac_aliases


def test_power_off_relinquishes_master_state():
    """F47 kelas B: powering off the master must not leave it internally
    claiming state=="master" (with the VIP/vmac still aliased) through the
    outage — before the fix, only the advert *send* was guarded, so the
    state/aliases were stale and a power-on made it reclaim mastership
    instantly, unvalidated, opening a dual-master window against whatever
    took over meanwhile."""
    net, r1, r2, _h1 = _fhrp_lab()
    net.run_for(15.0)
    r1_vrrp = _vrrp(r1)
    assert r1_vrrp.state == "master"

    net.set_device_power("r1", False)
    # No sim time elapsed — this isolates the state change from any timer
    # racing, and proves the transition is synchronous with power-off, not
    # something a stale master would only discover on its next advert tick.
    assert r1_vrrp.state != "master", (
        "state stayed 'master' straight through power-off — the resume "
        "path will reclaim mastership without ever validating it"
    )
    assert str(virtual_mac(10)) not in r1.mac_aliases
    assert IPv4Address(VIP) not in r1.ip_aliases

    # Drive it through a full power-cycle and check for a duplicate timer
    # chain, which is the risk the fix has to avoid: relinquishing on
    # power-off must not leave an old timer alive alongside the fresh one
    # armed on resume.
    net.run_for(10.0)   # r2 takes over while r1 stays off
    r2_vrrp = _vrrp(r2)
    assert r2_vrrp.state == "master"

    net.set_device_power("r1", True)
    net.run_for(10.0)   # r1 must re-earn mastership, not assume it
    assert r1_vrrp.state == "master"
    assert r2_vrrp.state == "backup"

    # Exactly one "master" transition per side across the whole cycle — a
    # duplicate chain would show up as extra racing become_master() calls.
    r1_masters = [s for _t, s in r1_vrrp.transitions if s == "master"]
    r2_masters = [s for _t, s in r2_vrrp.transitions if s == "master"]
    assert len(r1_masters) == 2, r1_vrrp.transitions   # initial + reclaim
    assert len(r2_masters) == 1, r2_vrrp.transitions   # the one takeover

    # And a duplicate chain would double the on-wire advert cadence —
    # confirm r1 (now master again) still sends at exactly one per
    # adv_interval, not two.
    def _r1_adverts() -> int:
        return sum(
            1 for r in net.capture.records(link_id="l-r1", limit=5000)
            if r.iface.startswith("r1:") and r.direction == "tx"
            and "VRRPv3 advertisement" in r.info
        )

    before = _r1_adverts()
    net.run_for(5.0)
    sent = _r1_adverts() - before
    assert 3 <= sent <= 6, (
        f"expected ~5 adverts in 5s at adv_interval=1.0, got {sent} "
        "— looks like a duplicate tick chain"
    )


def test_vrrp_deterministic_and_on_wire():
    def build():
        n, *_ = _fhrp_lab()
        n.run_for(15.0)
        return n

    a, b = build(), build()
    assert a.ledger.hash() == b.ledger.hash()

    adverts = [
        r for r in a.capture.records(limit=2000)
        if "VRRPv3" in r.info and "vrid=10" in r.info
    ]
    assert adverts
    wire = adverts[-1].wire
    assert wire[12:14] == b"\x08\x00" and wire[14 + 9] == 112  # IPv4 proto 112
    assert wire[14 + 20] == (3 << 4) | 1                        # v3, advert
    assert wire[14 + 21] == 10                                  # vrid
    # dst 224.0.0.18 -> multicast MAC 01:00:5e:00:00:12
    assert wire[0:6] == bytes.fromhex("01005e000012")


def test_cli_show_vrrp_and_standby():
    net, r1, _r2, _h1 = _fhrp_lab()
    net.run_for(15.0)
    sess = CliSession(net, r1)
    out = sess.execute("show vrrp")
    assert "master" in out and VIP in out and "00:00:5e:00:01:0a" in out
    # ios-like operators get the HSRP vocabulary too.
    assert "Standby brief" in sess.execute("show standby")
