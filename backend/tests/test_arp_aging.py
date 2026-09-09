"""ARP cache aging/expiry tests (RFC 826, RFC 1122 §2.3.2.1).

A learned ARP entry must not live forever: it gets a TTL, a fresh ARP for
the same IP refreshes that TTL, and expiry removes the entry so the next
packet re-resolves via the existing request/retry/timeout machinery. One
timer per entry, guarded by a per-ip sequence (same idiom as switching.py's
_delay_seq) so a stale timer from before a refresh can never evict a
freshly-learned entry — most tests below drive that guard directly
(white-box, same rationale as test_stp_forward_delay.py).
"""
from __future__ import annotations

from ipaddress import IPv4Address

from engine.netstack import Network
from engine.netstack.device import Host
from engine.netstack.frames import ArpPacket
from engine.netstack.switching import Switch

H2_IP = IPv4Address("10.0.0.2")
H1_IP = IPv4Address("10.0.0.1")


def lan_pair() -> tuple[Network, Host, Host, Switch]:
    net = Network(seed=42)
    h1 = net.add_device(Host("h1"))
    h2 = net.add_device(Host("h2"))
    sw = net.add_device(Switch("sw1"))
    i_h1 = net.add_iface(h1, "eth0", ["10.0.0.1/24"])
    i_h2 = net.add_iface(h2, "eth0", ["10.0.0.2/24"])
    s1 = net.add_iface(sw, "gi0/1")
    s2 = net.add_iface(sw, "gi0/2")
    net.connect("l1", i_h1, s1, delay=0.0001)
    net.connect("l2", i_h2, s2, delay=0.0001)
    return net, h1, h2, sw


def test_arp_entry_expires_after_ttl():
    # ping()'s default run_after runs the sim forward count*interval + 5.0
    # seconds, which would blow past any small TTL before we get to assert
    # anything -- drive it manually instead for precise timing control.
    net, h1, _h2, _sw = lan_pair()
    h1.arp_ttl = 2.0
    net.ping("h1", "10.0.0.2", count=1, run_after=False)
    net.run_for(0.1)  # let ARP resolve + echo complete (same-LAN, delay=0.0001)
    assert H2_IP in h1.arp_table
    net.run_for(2.0)  # t~2.1: past the 2.0s TTL
    assert H2_IP not in h1.arp_table


def test_arp_entry_survives_before_ttl():
    net, h1, _h2, _sw = lan_pair()
    h1.arp_ttl = 2.0
    net.ping("h1", "10.0.0.2", count=1, run_after=False)
    net.run_for(0.1)
    assert H2_IP in h1.arp_table
    net.run_for(1.0)  # t~1.1: still under the 2.0s TTL
    assert H2_IP in h1.arp_table


def test_arp_refresh_extends_life_past_original_deadline():
    net, h1, h2, _sw = lan_pair()
    h1.arp_ttl = 2.0
    net.ping("h1", "10.0.0.2", count=1, run_after=False)  # learned at t~0
    net.run_for(0.1)
    assert H2_IP in h1.arp_table
    net.run_for(1.5)  # t~1.6, still alive
    assert H2_IP in h1.arp_table

    iface = h1.interfaces["eth0"]
    fresh = ArpPacket(
        op="request", sender_mac=str(h2.interfaces["eth0"].mac), sender_ip=H2_IP,
        target_mac="00:00:00:00:00:00", target_ip=H1_IP,
    )
    h1._handle_arp(net, iface, fresh)  # a real ARP for the same IP -> refresh at t~1.6

    net.run_for(1.0)  # t~2.6: past the ORIGINAL t~2.1 deadline
    assert H2_IP in h1.arp_table  # still alive -- refresh worked

    net.run_for(1.5)  # t~4.1: past the REFRESHED t~3.6 deadline
    assert H2_IP not in h1.arp_table


def test_arp_seq_guard_stale_timer_does_not_evict_refreshed_entry():
    net, h1, h2, _sw = lan_pair()
    net.ping("h1", "10.0.0.2", count=1)
    assert H2_IP in h1.arp_table
    stale_seq = h1._arp_age_seq[H2_IP]

    iface = h1.interfaces["eth0"]
    fresh = ArpPacket(
        op="request", sender_mac=str(h2.interfaces["eth0"].mac), sender_ip=H2_IP,
        target_mac="00:00:00:00:00:00", target_ip=H1_IP,
    )
    h1._handle_arp(net, iface, fresh)  # refresh -> seq bumps
    assert h1._arp_age_seq[H2_IP] == stale_seq + 1

    h1._arp_expire(H2_IP, stale_seq)  # the OLD (pre-refresh) timer fires late
    assert H2_IP in h1.arp_table  # must no-op, not evict the refreshed entry

    h1._arp_expire(H2_IP, h1._arp_age_seq[H2_IP])  # the LIVE timer fires
    assert H2_IP not in h1.arp_table


def test_arp_mac_change_replaces_old_entry():
    net, h1, _h2, _sw = lan_pair()
    net.ping("h1", "10.0.0.2", count=1)
    old_mac, _ = h1.arp_table[H2_IP]

    iface = h1.interfaces["eth0"]
    new_mac = "aa:bb:cc:dd:ee:ff"
    changed = ArpPacket(
        op="request", sender_mac=new_mac, sender_ip=H2_IP,
        target_mac="00:00:00:00:00:00", target_ip=H1_IP,
    )
    h1._handle_arp(net, iface, changed)

    new_cached_mac, _ = h1.arp_table[H2_IP]
    assert str(new_cached_mac) != str(old_mac)
    assert str(new_cached_mac) == new_mac


def test_expired_entry_triggers_fresh_arp_resolution():
    net, h1, _h2, _sw = lan_pair()
    h1.arp_ttl = 1.0
    net.ping("h1", "10.0.0.2", count=1)
    net.run_for(1.1)
    assert H2_IP not in h1.arp_table

    report = net.ping("h1", "10.0.0.2", count=1)
    assert report.received == 1  # resolution still works via the existing path
    who_has = [r for r in net.capture.records(limit=1000) if "ARP who-has" in r.info]
    assert len(who_has) >= 2  # original resolution + post-expiry re-resolution
