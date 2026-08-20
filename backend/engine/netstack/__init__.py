"""NetGeo packet-realistic simulation stack.

This package implements the detailed (packet-level) simulation engine on top
of the deterministic DES kernel in :mod:`engine.scheduler` / :mod:`engine.events`.

Public surface:
    - :class:`engine.netstack.network.Network` — build & run a lab
    - :class:`engine.netstack.device.Host`
    - :class:`engine.netstack.switching.Switch`
    - :class:`engine.netstack.routing.Router`
    - :mod:`engine.netstack.cli` — per-NOS CLI over live device state
"""
from engine.netstack.addr import MacAddr, mac_from_int
from engine.netstack.device import Device, Host
from engine.netstack.frames import (
    ArpPacket,
    BpduFrame,
    DhcpMessage,
    DnsMessage,
    EthernetFrame,
    IcmpMessage,
    Icmpv6Message,
    Ipv4Packet,
    Ipv6Packet,
    TcpSegment,
    UdpSegment,
)
from engine.netstack.iface import Interface, LinkAttachment
from engine.netstack.network import Network, PingReport, TracerouteReport
from engine.netstack.routing import AclRule, NatBinding, Route, Route6, Router
from engine.netstack.switching import Switch

__all__ = [
    "AclRule",
    "ArpPacket",
    "BpduFrame",
    "Device",
    "DhcpMessage",
    "DnsMessage",
    "EthernetFrame",
    "Host",
    "IcmpMessage",
    "Icmpv6Message",
    "Interface",
    "Ipv4Packet",
    "Ipv6Packet",
    "LinkAttachment",
    "MacAddr",
    "NatBinding",
    "Network",
    "PingReport",
    "Route",
    "Route6",
    "Router",
    "Switch",
    "TcpSegment",
    "TracerouteReport",
    "UdpSegment",
    "mac_from_int",
]
