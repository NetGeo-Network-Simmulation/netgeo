"""Address primitives — MAC, IPv4 and IPv6 helpers.

Pure stdlib (``ipaddress``): portable across OSes and CPU architectures.
IPv4/IPv6 addresses are handled as the stdlib ``ipaddress`` types throughout
the stack; this module adds the small pieces the stdlib lacks (MAC addresses,
deterministic MAC allocation, EUI-64 / solicited-node derivation for NDP and
SLAAC).
"""
from __future__ import annotations

from ipaddress import (
    IPv4Address,
    IPv4Interface,
    IPv4Network,
    IPv6Address,
    IPv6Interface,
    IPv6Network,
    ip_address,
    ip_interface,
)
from typing import Self

BROADCAST_MAC = "ff:ff:ff:ff:ff:ff"
STP_MULTICAST_MAC = "01:80:c2:00:00:00"

# Well-known IPv6 group addresses (RFC 4291).
ALL_NODES_V6 = IPv6Address("ff02::1")
ALL_ROUTERS_V6 = IPv6Address("ff02::2")
LINK_LOCAL_NET = IPv6Network("fe80::/10")

# NetGeo's locally-administered OUI for auto-generated MACs.
_OUI = 0x02_50_47  # "P G" — locally administered bit set


class MacAddr(str):
    """A MAC address as a normalized lowercase ``aa:bb:cc:dd:ee:ff`` string.

    Subclassing ``str`` keeps it hashable, JSON-friendly and zero-cost on the
    hot path while giving a place for validation and formatting.
    """

    __slots__ = ()

    def __new__(cls, value: str) -> Self:
        v = value.strip().lower().replace("-", ":")
        parts = v.split(":")
        if len(parts) != 6 or not all(len(p) == 2 and _is_hex(p) for p in parts):
            raise ValueError(f"invalid MAC address: {value!r}")
        return super().__new__(cls, v)

    @property
    def is_broadcast(self) -> bool:
        return self == BROADCAST_MAC

    @property
    def is_multicast(self) -> bool:
        return bool(int(self[:2], 16) & 0x01)


def _is_hex(s: str) -> bool:
    try:
        int(s, 16)
        return True
    except ValueError:
        return False


def mac_from_int(n: int) -> MacAddr:
    """Deterministic MAC from a counter — same topology => same MACs.

    Uses NetGeo's locally-administered OUI so generated MACs can never clash
    with real hardware.
    """
    low = n & 0xFFFFFF
    raw = (_OUI << 24) | low
    return MacAddr(":".join(f"{(raw >> (8 * i)) & 0xFF:02x}" for i in range(5, -1, -1)))


def parse_ip_interface(value: str) -> IPv4Interface:
    """``"10.0.0.1/24"`` -> IPv4Interface. Bare addresses get /32."""
    if "/" not in value:
        value = f"{value}/32"
    return IPv4Interface(value)


def same_subnet(a: IPv4Interface, b: IPv4Address) -> bool:
    """Is ``b`` inside ``a``'s connected network?"""
    return b in a.network


# ---------------------------------------------------------------------------
# IPv6: EUI-64, SLAAC, NDP multicast mapping
# ---------------------------------------------------------------------------

def eui64_suffix(mac: MacAddr | str) -> int:
    """Low 64 bits of an EUI-64 interface identifier from a MAC (RFC 4291)."""
    b = bytes(int(p, 16) for p in str(mac).split(":"))
    eui = bytes([b[0] ^ 0x02, b[1], b[2], 0xFF, 0xFE, b[3], b[4], b[5]])
    return int.from_bytes(eui, "big")


def link_local_for(mac: MacAddr | str) -> IPv6Interface:
    """fe80::/64 link-local address derived from the MAC via EUI-64."""
    return IPv6Interface((int(IPv6Address("fe80::")) | eui64_suffix(mac), 64))


def slaac_address(prefix: IPv6Network, mac: MacAddr | str) -> IPv6Interface:
    """SLAAC address: advertised /64 prefix + EUI-64 interface identifier."""
    return IPv6Interface(
        (int(prefix.network_address) | eui64_suffix(mac), prefix.prefixlen)
    )


def solicited_node(addr: IPv6Address) -> IPv6Address:
    """ff02::1:ffXX:XXXX solicited-node multicast group for ``addr``."""
    return IPv6Address(int(IPv6Address("ff02::1:ff00:0")) | (int(addr) & 0xFFFFFF))


def ipv6_multicast_mac(group: IPv6Address) -> MacAddr:
    """33:33:xx:xx:xx:xx Ethernet mapping of an IPv6 multicast group."""
    low = int(group) & 0xFFFFFFFF
    return MacAddr(
        "33:33:" + ":".join(f"{(low >> (8 * i)) & 0xFF:02x}" for i in range(3, -1, -1))
    )


def ipv4_multicast_mac(group: IPv4Address) -> MacAddr:
    """01:00:5e + low 23 bits — Ethernet mapping of an IPv4 multicast group."""
    low = int(group) & 0x7FFFFF
    return MacAddr(
        "01:00:5e:" + ":".join(f"{(low >> (8 * i)) & 0xFF:02x}" for i in range(2, -1, -1))
    )


def parse_ip_interface6(value: str) -> IPv6Interface:
    """``"2001:db8::1/64"`` -> IPv6Interface. Bare addresses get /128."""
    if "/" not in value:
        value = f"{value}/128"
    return IPv6Interface(value)


__all__ = [
    "ALL_NODES_V6",
    "ALL_ROUTERS_V6",
    "BROADCAST_MAC",
    "LINK_LOCAL_NET",
    "STP_MULTICAST_MAC",
    "IPv4Address",
    "IPv4Interface",
    "IPv4Network",
    "IPv6Address",
    "IPv6Interface",
    "IPv6Network",
    "MacAddr",
    "eui64_suffix",
    "ip_address",
    "ip_interface",
    "ipv4_multicast_mac",
    "ipv6_multicast_mac",
    "link_local_for",
    "mac_from_int",
    "parse_ip_interface",
    "parse_ip_interface6",
    "same_subnet",
    "slaac_address",
    "solicited_node",
]
