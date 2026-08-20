"""CLI emulation tests — Cisco-like and MikroTik-like dialects on live labs."""
from __future__ import annotations

from ipaddress import IPv4Address

from engine.netstack import Network
from engine.netstack.cli import CliSession
from engine.netstack.device import Host
from engine.netstack.protocols.ospf import OspfProcess
from engine.netstack.routing import Router
from engine.netstack.switching import Switch


def build_lab() -> Network:
    net = Network(seed=1)
    h1 = net.add_device(Host("h1"))
    r1 = net.add_device(Router("r1"))
    r2 = net.add_device(Router("r2", nos="routeros"))
    h2 = net.add_device(Host("h2"))
    net.connect("lan1", net.add_iface(h1, "eth0", ["192.168.1.10/24"]),
                net.add_iface(r1, "eth0", ["192.168.1.1/24"]))
    net.connect("core", net.add_iface(r1, "eth1", ["10.0.12.1/30"]),
                net.add_iface(r2, "eth1", ["10.0.12.2/30"]))
    net.connect("lan2", net.add_iface(r2, "eth0", ["192.168.2.1/24"]),
                net.add_iface(h2, "eth0", ["192.168.2.10/24"]))
    h1.default_gateway = IPv4Address("192.168.1.1")
    h2.default_gateway = IPv4Address("192.168.2.1")
    r1 = net.devices["r1"]
    r2 = net.devices["r2"]
    assert isinstance(r1, Router) and isinstance(r2, Router)
    r1.add_static_route("192.168.2.0/24", "10.0.12.2")
    r2.add_static_route("192.168.1.0/24", "10.0.12.1")
    net.start()
    return net


def test_prompt_and_mode_navigation():
    net = build_lab()
    s = CliSession(net, net.devices["r1"])
    assert s.prompt == "r1>"
    s.execute("enable")
    assert s.prompt == "r1#"
    s.execute("conf t")
    assert s.prompt == "r1(config)#"
    s.execute("interface eth0")
    assert s.prompt == "r1(config-if)#"
    s.execute("end")
    assert s.prompt == "r1#"


def test_show_ip_route_and_interface_brief():
    net = build_lab()
    s = CliSession(net, net.devices["r1"])
    route_out = s.execute("show ip route")
    assert "C    192.168.1.0/24" in route_out
    assert "S    192.168.2.0/24 [1/0] via 10.0.12.2" in route_out
    brief = s.execute("show ip interface brief")
    assert "eth0" in brief and "192.168.1.1/24" in brief


def test_cli_ping_runs_the_simulator():
    net = build_lab()
    s = CliSession(net, net.devices["h1"])
    out = s.execute("ping 192.168.2.10 3")
    assert "Success rate 100% (3/3)" in out
    assert "rtt min/avg/max" in out


def test_cli_traceroute_lists_hops():
    net = build_lab()
    s = CliSession(net, net.devices["h1"])
    out = s.execute("traceroute 192.168.2.10")
    assert "192.168.1.1" in out
    assert "10.0.12.2" in out
    assert "Trace complete." in out


def test_config_mode_sets_ip_and_static_route():
    net = build_lab()
    r1 = net.devices["r1"]
    assert isinstance(r1, Router)
    s = CliSession(net, r1)
    s.execute("enable")
    s.execute("conf t")
    s.execute("interface eth0")
    assert s.execute("ip address 192.168.1.254 255.255.255.0") == ""
    s.execute("exit")
    assert s.execute("ip route 172.16.0.0/16 10.0.12.2") == ""
    s.execute("end")
    assert r1.interfaces["eth0"].ips[0] == __import__("ipaddress").IPv4Interface("192.168.1.254/24")
    assert r1.lookup(IPv4Address("172.16.5.5")) is not None


def test_switch_show_commands():
    net = Network(seed=1)
    h1 = net.add_device(Host("h1"))
    h2 = net.add_device(Host("h2"))
    sw = net.add_device(Switch("sw1"))
    net.connect("l1", net.add_iface(h1, "eth0", ["10.0.0.1/24"]),
                net.add_iface(sw, "gi0/1"))
    net.connect("l2", net.add_iface(h2, "eth0", ["10.0.0.2/24"]),
                net.add_iface(sw, "gi0/2"))
    net.ping("h1", "10.0.0.2", count=1)
    s = CliSession(net, sw)
    mac_out = s.execute("show mac address-table")
    assert "gi0/1" in mac_out and "gi0/2" in mac_out
    stp_out = s.execute("show spanning-tree")
    assert "designated" in stp_out
    vlan_out = s.execute("show vlan")
    assert "access" in vlan_out


def test_ospf_neighbor_show():
    net = Network(seed=1)
    r1 = net.add_device(Router("r1"))
    r2 = net.add_device(Router("r2"))
    net.connect("x", net.add_iface(r1, "eth0", ["10.0.0.1/30"]),
                net.add_iface(r2, "eth0", ["10.0.0.2/30"]))
    OspfProcess(r1, router_id="1.1.1.1", hello_interval=0.5)
    OspfProcess(r2, router_id="2.2.2.2", hello_interval=0.5)
    net.start()
    net.run_for(5.0)
    s = CliSession(net, r1)
    out = s.execute("show ip ospf neighbor")
    assert "2.2.2.2" in out and "full" in out


def test_mikrotik_dialect():
    net = build_lab()
    r2 = net.devices["r2"]
    s = CliSession(net, r2)
    assert s.dialect == "mikrotik"
    assert s.prompt == "[admin@r2] > "
    addr_out = s.execute("/ip address print")
    assert "192.168.2.1/24" in addr_out
    route_out = s.execute("/ip route print")
    assert "192.168.1.0/24" in route_out
    ping_out = s.execute("/ping 192.168.1.1 count=2")
    assert "(2/2)" in ping_out


def test_unknown_commands_are_polite():
    net = build_lab()
    s = CliSession(net, net.devices["r1"])
    assert s.execute("frobnicate").startswith("% Invalid")
    m = CliSession(net, net.devices["r2"])
    assert "bad command" in m.execute("/frobnicate")
