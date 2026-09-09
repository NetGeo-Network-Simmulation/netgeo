"""DHCP lease lifetime tests: T1 renew, T2 rebind, expiry (RFC 2131 §4.4.5).

T1/T2/expiry share one epoch number per client interface (host side) or per
mac (server side) -- a successful renewal bumps the epoch, so a stale timer
from before the renewal fires late and no-ops (same idiom as ARP aging's
_arp_age_seq, see test_arp_aging.py).
"""
from __future__ import annotations

from ipaddress import IPv4Address, IPv4Network

from engine.netstack import Network
from engine.netstack.addr import BROADCAST_MAC
from engine.netstack.device import Host
from engine.netstack.routing import DhcpPool, Router

POOL_NET = IPv4Network("192.168.88.0/24")
POOL_GW = IPv4Address("192.168.88.1")


def dhcp_pair(lease_s: float) -> tuple[Network, Host, Router]:
    net = Network(seed=4)
    h = net.add_device(Host("h1"))
    r = net.add_device(Router("gw"))
    net.connect("lan", net.add_iface(h, "eth0"), net.add_iface(r, "eth0", ["192.168.88.1/24"]))
    r.add_dhcp_pool(DhcpPool(network=POOL_NET, gateway=POOL_GW, lease_s=lease_s))
    return net, h, r


def dhcp_requests(net: Network, after: float = 0.0) -> list:
    return [
        rec for rec in net.capture.records(limit=2000)
        if rec.layers.get("dhcp", {}).get("op") == "request" and rec.time > after
    ]


def test_dhcp_t1_renewal_is_unicast_to_server():
    net, h, _r = dhcp_pair(lease_s=20.0)
    net.start()
    h.dhcp_discover(net)
    net.run_for(1.0)  # DORA completes
    assert h.interfaces["eth0"].ips, "host must be bound before T1"

    net.run_for(9.5)  # t~10.5: past T1 (0.5 * 20)
    renewals = dhcp_requests(net, after=2.0)
    assert renewals, "expected a RENEWING REQUEST at T1"
    renewal = renewals[0]
    assert renewal.layers["eth"]["dst"] != BROADCAST_MAC
    assert renewal.layers["ipv4"]["dst"] == str(POOL_GW)


def test_dhcp_renewal_success_keeps_same_address_past_original_deadline():
    net, h, _r = dhcp_pair(lease_s=20.0)
    net.start()
    h.dhcp_discover(net)
    net.run_for(1.0)
    ip0 = h.interfaces["eth0"].ips[0]

    net.run_for(19.5)  # t~20.5: past the ORIGINAL lease deadline (T1 renewed it at t~10)
    assert h.interfaces["eth0"].ips == [ip0], "renewal must keep the same address"


def test_dhcp_t2_broadcast_then_expiry_release_when_server_unreachable():
    net, h, r = dhcp_pair(lease_s=20.0)
    net.start()
    h.dhcp_discover(net)
    net.run_for(1.0)
    assert h.interfaces["eth0"].ips

    r.dhcp_pools.clear()  # server "disappears" -- unicast renewal at T1 goes unanswered
    net.run_for(9.5)   # t~10.5: past T1
    net.run_for(7.5)   # t~18.0: past T2 (0.875 * 20 = 17.5)

    rebinds = dhcp_requests(net, after=15.0)
    assert rebinds, "expected a REBINDING broadcast REQUEST at T2"
    rebind = rebinds[0]
    assert rebind.layers["eth"]["dst"] == BROADCAST_MAC
    assert rebind.layers["ipv4"]["dst"] == "255.255.255.255"

    net.run_for(3.0)  # t~21: past expiry (lease_s=20) -- no ack ever came
    assert not h.interfaces["eth0"].ips, "lease expired with no renewal -> address released"


def test_dhcp_client_seq_guard_stale_timer_is_noop():
    net, h, _r = dhcp_pair(lease_s=20.0)
    net.start()
    h.dhcp_discover(net)
    net.run_for(1.0)
    iface = h.interfaces["eth0"]
    ip0 = iface.ips[0]
    stale_seq = h._dhcp_lease_seq[iface.name]
    h._dhcp_lease_seq[iface.name] = stale_seq + 1  # simulate a renewal that already bumped the epoch

    before = net.capture.total_records
    h._dhcp_renew(net, iface, stale_seq)
    h._dhcp_rebind(net, iface, stale_seq)
    h._dhcp_expire(net, iface, stale_seq)

    assert net.capture.total_records == before, "stale timers must not transmit anything"
    assert iface.ips == [ip0], "stale timers must not touch the (renewed) lease"


def test_dhcp_server_seq_guard_stale_timer_does_not_evict_renewed_lease():
    net = Network(seed=9)
    r = net.add_device(Router("gw"))
    pool = DhcpPool(network=IPv4Network("10.0.0.0/24"), gateway=IPv4Address("10.0.0.1"), lease_s=5.0)
    r.add_dhcp_pool(pool)
    mac = "aa:bb:cc:dd:ee:01"
    pool.allocate(mac)

    r._arm_lease_expiry(net, pool, mac)
    stale_seq = pool.lease_seq[mac]
    r._arm_lease_expiry(net, pool, mac)  # renewal: bumps the epoch

    r._lease_expire(pool, mac, stale_seq)  # the OLD (pre-renewal) timer fires late
    assert mac in pool.leases, "must no-op, not evict the renewed lease"

    r._lease_expire(pool, mac, pool.lease_seq[mac])  # the LIVE timer fires
    assert mac not in pool.leases


def test_server_lease_expiry_returns_address_to_pool_for_reuse():
    net = Network(seed=3)
    r = net.add_device(Router("gw"))
    pool = DhcpPool(network=IPv4Network("10.0.0.0/24"), gateway=IPv4Address("10.0.0.1"), lease_s=5.0)
    r.add_dhcp_pool(pool)
    mac_a = "aa:bb:cc:dd:ee:01"
    ip_a = pool.allocate(mac_a)
    r._arm_lease_expiry(net, pool, mac_a)

    net.run_for(5.1)
    assert mac_a not in pool.leases, "expired lease must return the address to the pool"

    mac_b = "aa:bb:cc:dd:ee:02"
    ip_b = pool.allocate(mac_b)
    assert ip_b == ip_a, "freed address must be available to another client"
