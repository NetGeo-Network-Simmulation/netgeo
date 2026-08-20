"""Config import → digital-twin (NG-TW-01).

Turns a device config into the node shape (hostname, interfaces + IPs, static
routes, and OSPF/BGP intent) the platform can instantiate and reason over.
Regex + stdlib only, honest partial parse — unknown stanzas are ignored.

Supported dialects: IOS-like (``ios``/``iosxr``/``nxos``) and RouterOS
(``routeros``/``mikrotik``). ``infer_links`` closes the twin: interfaces that
share an IP subnet across imported nodes get wired together so reachability can
be answered over the imported set.
"""
from __future__ import annotations

import ipaddress
import re
from collections import defaultdict

_HOSTNAME = re.compile(r"^\s*hostname\s+(\S+)", re.MULTILINE)
_INTERFACE = re.compile(r"^\s*interface\s+(\S+)", re.IGNORECASE)
_IP_ADDR = re.compile(r"^\s*ip\s+address\s+(\d+\.\d+\.\d+\.\d+)\s+(\d+\.\d+\.\d+\.\d+)", re.IGNORECASE)
_IP_ROUTE = re.compile(
    r"^\s*ip\s+route\s+(\d+\.\d+\.\d+\.\d+)\s+(\d+\.\d+\.\d+\.\d+)\s+(\S+)", re.IGNORECASE | re.MULTILINE
)
# Router-process blocks: everything indented under the "router <proto> ..." line.
_OSPF_BLOCK = re.compile(r"^router\s+ospf\s+\S+\s*\n((?:[ \t]+.*\n?)*)", re.IGNORECASE | re.MULTILINE)
_BGP_BLOCK = re.compile(r"^router\s+bgp\s+(\d+)\s*\n((?:[ \t]+.*\n?)*)", re.IGNORECASE | re.MULTILINE)
_OSPF_NET = re.compile(
    r"network\s+(\d+\.\d+\.\d+\.\d+)\s+(\d+\.\d+\.\d+\.\d+)\s+area\s+(\S+)", re.IGNORECASE
)
_ROUTER_ID = re.compile(r"router-id\s+(\d+\.\d+\.\d+\.\d+)", re.IGNORECASE)
_BGP_NEIGHBOR = re.compile(r"neighbor\s+(\d+\.\d+\.\d+\.\d+)\s+remote-as\s+(\d+)", re.IGNORECASE)
_BGP_NETWORK = re.compile(
    r"network\s+(\d+\.\d+\.\d+\.\d+)\s+mask\s+(\d+\.\d+\.\d+\.\d+)", re.IGNORECASE
)


def _mask_to_prefix(mask: str) -> int:
    return ipaddress.IPv4Network(f"0.0.0.0/{mask}").prefixlen


def _wildcard_to_network(net: str, wildcard: str) -> ipaddress.IPv4Network:
    netmask = ".".join(str(255 - int(o)) for o in wildcard.split("."))
    return ipaddress.IPv4Network(f"{net}/{netmask}", strict=False)


def _ospf_intent(text: str, interfaces: list[dict]) -> dict | None:
    m = _OSPF_BLOCK.search(text)
    if not m:
        return None
    block = m.group(1)
    nets = [(_wildcard_to_network(n, w), area) for n, w, area in _OSPF_NET.findall(block)]
    areas: dict[str, str] = {}
    for iface in interfaces:
        for cidr in iface["ip"]:
            host = ipaddress.ip_interface(cidr).ip
            for net, area in nets:
                if host in net:
                    areas[iface["name"]] = area
                    break
    rid = _ROUTER_ID.search(block)
    intent = {"enabled": True, "areas": areas}
    if rid:
        intent["router_id"] = rid.group(1)
    return intent


def _bgp_intent(text: str) -> dict | None:
    m = _BGP_BLOCK.search(text)
    if not m:
        return None
    asn, block = int(m.group(1)), m.group(2)
    neighbors = [{"ip": ip, "asn": int(a)} for ip, a in _BGP_NEIGHBOR.findall(block)]
    networks = [
        f"{net}/{_mask_to_prefix(mask)}" for net, mask in _BGP_NETWORK.findall(block)
    ]
    intent = {"asn": asn, "neighbors": neighbors, "networks": networks}
    rid = _ROUTER_ID.search(block)
    if rid:
        intent["router_id"] = rid.group(1)
    return intent


def parse_ios(text: str) -> dict:
    """Parse an IOS-like config into ``{hostname, interfaces, static_routes,
    ospf?, bgp?}``. Each interface is ``{name, ip: ["a.b.c.d/len", ...]}``;
    static routes are ``{prefix, next_hop}``. Unknown lines are ignored."""
    hostname_m = _HOSTNAME.search(text)
    interfaces: list[dict] = []
    current: dict | None = None
    for line in text.splitlines():
        iface_m = _INTERFACE.match(line)
        if iface_m:
            current = {"name": iface_m.group(1), "ip": []}
            interfaces.append(current)
            continue
        if current is not None:
            ip_m = _IP_ADDR.match(line)
            if ip_m:
                current["ip"].append(f"{ip_m.group(1)}/{_mask_to_prefix(ip_m.group(2))}")
    static_routes = [
        {"prefix": f"{net}/{_mask_to_prefix(mask)}", "next_hop": nh}
        for net, mask, nh in _IP_ROUTE.findall(text)
    ]
    out = {
        "hostname": hostname_m.group(1) if hostname_m else "imported",
        "interfaces": interfaces,
        "static_routes": static_routes,
    }
    ospf = _ospf_intent(text, interfaces)
    if ospf:
        out["ospf"] = ospf
    bgp = _bgp_intent(text)
    if bgp:
        out["bgp"] = bgp
    return out


_ROS_IDENTITY = re.compile(r"/system identity.*?name=([^\s]+)", re.DOTALL)
_ROS_KV = re.compile(r"(\S+?)=([^\s]+)")


def parse_routeros(text: str) -> dict:
    """Parse a RouterOS (MikroTik) export into the same shape as ``parse_ios``.

    Section-aware line scan: ``/ip address add address=.../len interface=...``
    become interfaces; ``/ip route add dst-address=... gateway=...`` become
    static routes. OSPF/BGP stanzas are not parsed yet (partial import).
    """
    identity = _ROS_IDENTITY.search(text)
    by_iface: dict[str, dict] = {}
    static_routes: list[dict] = []
    section = ""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("/"):
            section = stripped
            continue
        if not stripped.startswith("add "):
            continue
        kv = dict(_ROS_KV.findall(stripped))
        if section.startswith("/ip address") and "address" in kv and "interface" in kv:
            name = kv["interface"]
            by_iface.setdefault(name, {"name": name, "ip": []})
            addr = kv["address"] if "/" in kv["address"] else f"{kv['address']}/32"
            by_iface[name]["ip"].append(addr)
        elif section.startswith("/ip route") and kv.get("gateway"):
            dst = kv.get("dst-address", "0.0.0.0/0")
            static_routes.append({"prefix": dst, "next_hop": kv["gateway"]})
    return {
        "hostname": identity.group(1) if identity else "imported",
        "interfaces": list(by_iface.values()),
        "static_routes": static_routes,
    }


def infer_links(ifaces: list[tuple[str, str, str]]) -> list[tuple[str, str]]:
    """Pair interface ids that share an IP subnet across *different* nodes.

    ``ifaces`` is ``[(iface_id, node_id, "a.b.c.d/len"), ...]``. Returns
    ``[(iface_id_a, iface_id_b), ...]`` — one link per peer, star-anchored to the
    first interface in each subnet.

    ponytail: a subnet with >2 members is modelled as a star to the anchor
    (reachability-correct); the physically accurate form is a shared switch —
    add one if L2 fidelity (broadcast/MTU) starts to matter.
    """
    groups: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for iface_id, node_id, cidr in ifaces:
        try:
            net = ipaddress.ip_interface(cidr).network
        except ValueError:
            continue
        if net.prefixlen == net.max_prefixlen:  # /32 or /128 loopback — no peer
            continue
        groups[str(net)].append((iface_id, node_id))
    pairs: list[tuple[str, str]] = []
    for members in groups.values():
        anchor_id, anchor_node = members[0]
        for iface_id, node_id in members[1:]:
            if node_id != anchor_node:
                pairs.append((anchor_id, iface_id))
    return pairs


PARSERS = {
    "ios": parse_ios,
    "iosxr": parse_ios,
    "nxos": parse_ios,
    "routeros": parse_routeros,
    "mikrotik": parse_routeros,
}


def parse(vendor: str, text: str) -> dict:
    parser = PARSERS.get(vendor.lower())
    if parser is None:
        raise ValueError(f"no import parser for vendor '{vendor}'")
    return parser(text)
