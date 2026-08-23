"""Wire-byte synthesis + pcapng export (NG-CAP-01).

Two halves:

- :func:`frame_bytes` turns a modelled :class:`EthernetFrame` into the bytes
  that would have been on the wire — real header layouts and checksums for
  the protocols the engine models (Ethernet II, 802.1Q, ARP, IPv4/IPv6,
  ICMP/ICMPv6 incl. NDP, UDP, TCP, OSPFv2 hello/LSU, BGP, DNS, DHCP/BOOTP,
  802.3+LLC STP BPDUs). Unknown payloads become zero fill of modelled size.
- :func:`write_pcapng` wraps captured wire bytes in a pcapng stream (one SHB,
  one IDB per link, one EPB per record) that Wireshark/tshark open natively.

Reference: IETF draft-ietf-opsawg-pcapng (SHB 0x0A0D0D0A, byte-order magic
0x1A2B3C4D, IDB type 1 / LINKTYPE_ETHERNET=1, EPB type 6, µs timestamps).
"""
from __future__ import annotations

import struct
from ipaddress import IPv4Address, IPv6Address

from engine.netstack.frames import (
    ETH_ARP,
    ETH_IPV4,
    ETH_IPV6,
    PROTO_ICMP,
    PROTO_ICMPV6,
    PROTO_OSPF,
    PROTO_TCP,
    PROTO_UDP,
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

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _mac(m: str) -> bytes:
    return bytes(int(p, 16) for p in str(m).split(":"))


def _cksum(data: bytes) -> int:
    """RFC 1071 internet checksum."""
    if len(data) % 2:
        data += b"\x00"
    total = sum(struct.unpack(f"!{len(data) // 2}H", data))
    while total >> 16:
        total = (total & 0xFFFF) + (total >> 16)
    return (~total) & 0xFFFF


def _rid(rid: str) -> bytes:
    """Router-id / IPv4 dotted string -> 4 bytes (zeros on parse failure)."""
    try:
        return IPv4Address(rid).packed
    except ValueError:
        return b"\x00\x00\x00\x00"


# ---------------------------------------------------------------------------
# L4+ payload synthesis
# ---------------------------------------------------------------------------

def _icmp_bytes(m: IcmpMessage) -> bytes:
    if m.type in (8, 0):
        body = struct.pack("!BBHHH", m.type, m.code, 0, m.ident & 0xFFFF, m.seq & 0xFFFF)
        body += bytes(m.data_len)
    else:
        # Error: 4B unused + embedded original IPv4 header + 8 payload bytes.
        src, dst = m.original or ("0.0.0.0", "0.0.0.0")
        inner = struct.pack(
            "!BBHHHBBH4s4s",
            0x45, 0, 28, 0, 0x4000, 64, PROTO_ICMP, 0,
            IPv4Address(src).packed, IPv4Address(dst).packed,
        )
        inner += struct.pack("!BBHHH", 8, 0, 0, m.orig_ident & 0xFFFF, m.orig_seq & 0xFFFF)[:8]
        body = struct.pack("!BBHI", m.type, m.code, 0, 0) + inner
    return body[:2] + struct.pack("!H", _cksum(body)) + body[4:]


def _icmpv6_body(m: Icmpv6Message) -> bytes:
    def ll_opt(kind: int) -> bytes:
        return struct.pack("!BB", kind, 1) + _mac(m.ll_addr) if m.ll_addr else b""

    head = struct.pack("!BBH", m.type, m.code, 0)
    if m.type in (128, 129):
        return head + struct.pack("!HH", m.ident & 0xFFFF, m.seq & 0xFFFF) + bytes(m.data_len)
    if m.type in (135, 136):  # NS / NA (+ source/target link-layer option)
        flags = 0x60000000 if m.type == 136 else 0  # NA: solicited+override
        target = m.target.packed if m.target else bytes(16)
        return head + struct.pack("!I", flags) + target + ll_opt(1 if m.type == 135 else 2)
    if m.type == 133:  # RS
        return head + struct.pack("!I", 0) + ll_opt(1)
    if m.type == 134:  # RA + prefix-information options
        body = head + struct.pack("!BBHII", 64, 0, m.router_lifetime & 0xFFFF, 0, 0)
        body += ll_opt(1)
        for pfx in m.prefixes:
            try:
                net, plen = pfx.split("/")
                body += struct.pack("!BBBB", 3, 4, int(plen), 0xC0)  # on-link+auto
                body += struct.pack("!III", 2_592_000, 604_800, 0)
                body += IPv6Address(net).packed
            except ValueError:
                continue
        return body
    # Errors: 4B unused + as much of the invoking packet as we model (zeros).
    return head + struct.pack("!I", 0) + bytes(48)


def _ospf_bytes(pkt: Ipv4Packet) -> bytes:
    p = pkt.payload
    kind = getattr(type(p), "__name__", "")
    if kind == "OspfHello":
        body = struct.pack("!4sHBBI4s4s", b"\xff\xff\xff\x00",
                           int(p.hello_interval), 0x02, 1,
                           int(p.dead_interval), b"\x00" * 4, b"\x00" * 4)
        for n in p.neighbors_seen:
            body += _rid(n)
        return _ospf_header(1, p.router_id, body)
    if kind == "OspfLsu":
        body = struct.pack("!I", len(p.lsas))
        for lsa in p.lsas:
            links = getattr(lsa, "links", None)
            lsa_type = 1 if links is not None else 3
            hdr = struct.pack("!HBB4s4sIHH", 1, 0x02, lsa_type, _rid(lsa.router_id),
                              _rid(lsa.router_id), lsa.seq & 0xFFFFFFFF, 0,
                              lsa.wire_size)
            if links is not None:  # router LSA
                lbody = struct.pack("!BBH", 0, 0, len(links))
                for _kind, _ref, cost in links:
                    lbody += _rid(_ref if "." in str(_ref).split("/")[0] else "0.0.0.0")[:4]
                    lbody += struct.pack("!IBBH", 0, 0, 0, cost & 0xFFFF)
            else:  # type-3 summary: mask + metric
                from ipaddress import IPv4Network as _Net

                pfx = _Net(lsa.prefix)
                lbody = pfx.netmask.packed + struct.pack("!I", lsa.metric & 0xFFFFFF)
            body += hdr + lbody
        return _ospf_header(4, getattr(p.lsas[0], "router_id", "0.0.0.0") if p.lsas else "0.0.0.0", body)
    return bytes(getattr(p, "wire_size", 0))


def _ospf_header(msg_type: int, router_id: str, body: bytes) -> bytes:
    head = struct.pack("!BBH4s4sHHQ", 2, msg_type, 24 + len(body),
                       _rid(router_id), b"\x00" * 4, 0, 0, 0)
    out = head + body
    ck = _cksum(out)
    return out[:12] + struct.pack("!H", ck) + out[14:]


def _bgp_bytes(seg: TcpSegment) -> bytes:
    p = seg.payload
    kind = getattr(type(p), "__name__", "")
    marker = b"\xff" * 16
    if kind == "BgpOpen":
        return marker + struct.pack("!HBBHH4sB", 29, 1, 4, p.asn & 0xFFFF,
                                    int(p.hold_time), _rid(p.router_id), 0)
    if kind == "BgpKeepalive":
        return marker + struct.pack("!HB", 19, 4)
    if kind == "BgpUpdate":
        size = p.wire_size
        return marker + struct.pack("!HB", size, 2) + bytes(size - 19)
    return bytes(getattr(p, "wire_size", 0))


def _dns_bytes(m: DnsMessage) -> bytes:
    flags = 0x0100 if m.op == "query" else 0x8180
    out = struct.pack("!HHHHHH", m.xid & 0xFFFF, flags, 1,
                      1 if (m.op != "query" and m.answer) else 0, 0, 0)
    qname = b"".join(
        bytes([len(part)]) + part.encode() for part in m.qname.split(".") if part
    ) + b"\x00"
    out += qname + struct.pack("!HH", 1, 1)  # QTYPE A, QCLASS IN
    if m.op != "query" and m.answer:
        out += b"\xc0\x0c" + struct.pack("!HHIH", 1, 1, 300, 4)
        out += IPv4Address(m.answer).packed
    return out


def _dhcp_bytes(m: DhcpMessage) -> bytes:
    ops = {"discover": 1, "offer": 2, "request": 3, "ack": 5, "nak": 6}
    msg_type = ops.get(m.op, 1)
    bootp_op = 1 if m.op in ("discover", "request") else 2
    chaddr = (_mac(m.client_mac) if m.client_mac else bytes(6)) + bytes(10)
    yiaddr = IPv4Address(m.your_ip).packed if m.your_ip else bytes(4)
    siaddr = IPv4Address(m.server_ip).packed if m.server_ip else bytes(4)
    out = struct.pack("!BBBBIHH", bootp_op, 1, 6, 0, m.xid & 0xFFFFFFFF, 0, 0x8000)
    out += bytes(4) + yiaddr + siaddr + bytes(4) + chaddr + bytes(64) + bytes(128)
    out += b"\x63\x82\x53\x63"                       # magic cookie
    out += struct.pack("!BBB", 53, 1, msg_type)      # DHCP message type
    if m.subnet_mask and m.subnet_mask.isdigit():
        from ipaddress import IPv4Network
        mask = IPv4Network(f"0.0.0.0/{m.subnet_mask}").netmask.packed
        out += struct.pack("!BB", 1, 4) + mask
    if m.gateway:
        out += struct.pack("!BB", 3, 4) + IPv4Address(m.gateway).packed
    if m.dns:
        out += struct.pack("!BB", 6, 4) + IPv4Address(m.dns).packed
    out += b"\xff"                                   # end option
    return out.ljust(300, b"\x00")


def _l4_payload_bytes(app) -> bytes:
    if isinstance(app, DnsMessage):
        return _dns_bytes(app)
    if isinstance(app, DhcpMessage):
        return _dhcp_bytes(app)
    if app is None:
        return b""
    return bytes(getattr(app, "wire_size", 0))


def _udp_bytes(seg: UdpSegment, pseudo: bytes) -> bytes:
    payload = _l4_payload_bytes(seg.payload)
    length = 8 + len(payload)
    head = struct.pack("!HHHH", seg.src_port, seg.dst_port, length, 0)
    ck = _cksum(pseudo + struct.pack("!H", length) + head + payload) or 0xFFFF
    return head[:6] + struct.pack("!H", ck) + payload


def _tcp_bytes(seg: TcpSegment, pseudo: bytes) -> bytes:
    if getattr(type(seg.payload), "__name__", "").startswith("Bgp"):
        payload = _bgp_bytes(seg)
    else:
        payload = _l4_payload_bytes(seg.payload)
    flag_bits = {"SYN": 0x02, "SYN-ACK": 0x12, "ACK": 0x10, "PSH": 0x18,
                 "FIN": 0x11, "RST": 0x04}.get(seg.flags, 0x18)
    head = struct.pack("!HHIIBBHHH", seg.src_port, seg.dst_port, 0, 0,
                       5 << 4, flag_bits, 65535, 0, 0)
    total = head + payload
    ck = _cksum(pseudo + struct.pack("!H", len(total)) + total)
    return total[:16] + struct.pack("!H", ck) + total[18:]


def _vrrp_bytes(pkt: Ipv4Packet, pseudo: bytes) -> bytes:
    """VRRPv3 advertisement (RFC 9568 §5): checksum over pseudo-header."""
    adv = pkt.payload
    max_adv_cs = max(1, int(adv.adv_interval * 100)) & 0x0FFF
    body = struct.pack(
        "!BBBBHH", (3 << 4) | 1, adv.vrid & 0xFF, adv.priority & 0xFF,
        len(adv.ips), max_adv_cs, 0,
    )
    for ip in adv.ips:
        body += IPv4Address(ip).packed
    ck = _cksum(pseudo + struct.pack("!H", len(body)) + body)
    return body[:6] + struct.pack("!H", ck) + body[8:]


# ---------------------------------------------------------------------------
# L3 synthesis
# ---------------------------------------------------------------------------

def _ipv4_bytes(pkt: Ipv4Packet) -> bytes:
    pseudo = pkt.src.packed + pkt.dst.packed + struct.pack("!BB", 0, pkt.proto)
    is_frag = pkt.more_fragments or pkt.fragment_offset
    if is_frag:
        # ponytail: a non-zero-offset fragment carries no L4 header on the
        # real wire either; zero-fill rather than truncate a serialized
        # header mid-way, the length/flags/offset fields are what matters.
        payload = bytes(pkt.frag_len)
    elif pkt.proto == PROTO_ICMP and isinstance(pkt.payload, IcmpMessage):
        payload = _icmp_bytes(pkt.payload)
    elif pkt.proto == PROTO_UDP and isinstance(pkt.payload, UdpSegment):
        payload = _udp_bytes(pkt.payload, pseudo)
    elif pkt.proto == PROTO_TCP and isinstance(pkt.payload, TcpSegment):
        payload = _tcp_bytes(pkt.payload, pseudo)
    elif pkt.proto == PROTO_OSPF:
        payload = _ospf_bytes(pkt)
    elif pkt.proto == 112 and getattr(type(pkt.payload), "__name__", "") == "VrrpAdvert":
        payload = _vrrp_bytes(pkt, pseudo)
    else:
        payload = bytes(getattr(pkt.payload, "wire_size", pkt.payload_len))
    total_len = 20 + len(payload)
    flags = (
        (0x4000 if pkt.dont_fragment else 0)
        | (0x2000 if pkt.more_fragments else 0)
        | ((pkt.fragment_offset // 8) & 0x1FFF)
    )
    head = struct.pack("!BBHHHBBH4s4s", 0x45, pkt.dscp << 2, total_len,
                       pkt.identification & 0xFFFF, flags, pkt.ttl, pkt.proto, 0,
                       pkt.src.packed, pkt.dst.packed)
    head = head[:10] + struct.pack("!H", _cksum(head)) + head[12:]
    return head + payload


def _ipv6_bytes(pkt: Ipv6Packet) -> bytes:
    if pkt.proto == PROTO_ICMPV6 and isinstance(pkt.payload, Icmpv6Message):
        body = _icmpv6_body(pkt.payload)
        pseudo = pkt.src.packed + pkt.dst.packed + struct.pack(
            "!IHBB", len(body), 0, 0, PROTO_ICMPV6
        )
        ck = _cksum(pseudo + body)
        payload = body[:2] + struct.pack("!H", ck) + body[4:]
    elif pkt.proto == PROTO_UDP and isinstance(pkt.payload, UdpSegment):
        payload = _udp_bytes(
            pkt.payload, pkt.src.packed + pkt.dst.packed + struct.pack("!BB", 0, PROTO_UDP)
        )
    elif pkt.proto == PROTO_TCP and isinstance(pkt.payload, TcpSegment):
        payload = _tcp_bytes(
            pkt.payload, pkt.src.packed + pkt.dst.packed + struct.pack("!BB", 0, PROTO_TCP)
        )
    else:
        payload = bytes(getattr(pkt.payload, "wire_size", pkt.payload_len))
    head = struct.pack("!IHBB", (6 << 28) | (pkt.dscp << 22), len(payload),
                       pkt.proto, pkt.hop_limit)
    return head + pkt.src.packed + pkt.dst.packed + payload


# ---------------------------------------------------------------------------
# L2 synthesis
# ---------------------------------------------------------------------------

def frame_bytes(frame: EthernetFrame) -> bytes:
    """Synthesize the on-wire bytes of a modelled frame (no FCS)."""
    p = frame.payload
    dst, src = _mac(frame.dst_mac), _mac(frame.src_mac)

    if isinstance(p, BpduFrame):  # 802.3 + LLC, not Ethernet II
        body = b"\x42\x42\x03" + _bpdu_bytes(p)
        return dst + src + struct.pack("!H", len(body)) + body

    if isinstance(p, ArpPacket):
        payload = struct.pack(
            "!HHBBH", 1, ETH_IPV4, 6, 4, 1 if p.op == "request" else 2
        ) + _mac(p.sender_mac) + p.sender_ip.packed + _mac(p.target_mac) + p.target_ip.packed
        ethertype = ETH_ARP
    elif isinstance(p, Ipv4Packet):
        payload, ethertype = _ipv4_bytes(p), ETH_IPV4
    elif isinstance(p, Ipv6Packet):
        payload, ethertype = _ipv6_bytes(p), ETH_IPV6
    else:
        payload, ethertype = bytes(getattr(p, "wire_size", 46)), frame.ethertype

    tag = b""
    if frame.vlan is not None:
        tag = struct.pack("!HH", 0x8100, frame.vlan & 0x0FFF)
    head = dst + src + tag + struct.pack("!H", ethertype)
    out = head + payload
    return out.ljust(60, b"\x00")  # minimum Ethernet frame (without FCS)


def _bpdu_bytes(p: BpduFrame) -> bytes:
    def bridge_id(bid: str) -> bytes:
        try:
            prio, mac = bid.split(".", 1)
            return struct.pack("!H", int(prio) & 0xFFFF) + _mac(mac)
        except (ValueError, IndexError):
            return bytes(8)

    return struct.pack("!HBBB", 0, 0, 0, 0) + bridge_id(p.root_id) + struct.pack(
        "!I", p.root_cost
    ) + bridge_id(p.bridge_id) + struct.pack("!HHHHH", p.port_id, 0, 20 * 256,
                                             2 * 256, 15 * 256)


# ---------------------------------------------------------------------------
# pcapng writer
# ---------------------------------------------------------------------------

def _block(block_type: int, body: bytes) -> bytes:
    pad = (-len(body)) % 4
    total = 12 + len(body) + pad
    return (struct.pack("<II", block_type, total) + body + bytes(pad)
            + struct.pack("<I", total))


def _option(code: int, value: bytes) -> bytes:
    pad = (-len(value)) % 4
    return struct.pack("<HH", code, len(value)) + value + bytes(pad)


_END_OPT = struct.pack("<HH", 0, 0)


def write_pcapng(records, link_order: list[str] | None = None) -> bytes:
    """Encode capture records as a pcapng byte stream.

    ``records``: iterable of CaptureRecord (must carry ``wire`` bytes).
    One IDB per distinct link (µs timestamps, LINKTYPE_ETHERNET).
    """
    records = list(records)
    links: list[str] = list(link_order or [])
    for r in records:
        if r.link_id not in links:
            links.append(r.link_id)
    iface_idx = {link: i for i, link in enumerate(links)}

    shb_body = struct.pack("<IHHq", 0x1A2B3C4D, 1, 0, -1)
    shb_body += _option(4, b"NetGeo packet-level simulator") + _END_OPT
    out = _block(0x0A0D0D0A, shb_body)

    for link in links:
        idb_body = struct.pack("<HHI", 1, 0, 0)  # LINKTYPE_ETHERNET, snaplen 0
        idb_body += _option(2, f"link:{link}".encode()) + _END_OPT
        out += _block(0x00000001, idb_body)

    seen_tx: set[tuple[str, int]] = set()
    for r in sorted(records, key=lambda r: (r.time, r.frame_id)):
        # A frame is written once per link (tx side); rx duplicates skipped.
        key = (r.link_id, r.frame_id)
        if r.direction == "rx" and key in seen_tx:
            continue
        if r.direction == "tx":
            seen_tx.add(key)
        wire = getattr(r, "wire", b"") or b""
        if not wire:
            continue
        ts = int(r.time * 1_000_000)  # µs, matching the default if_tsresol
        epb_body = struct.pack(
            "<IIIII", iface_idx[r.link_id], (ts >> 32) & 0xFFFFFFFF,
            ts & 0xFFFFFFFF, len(wire), len(wire)
        )
        pad = (-len(wire)) % 4
        epb_body += wire + bytes(pad)
        if r.direction == "drop":
            epb_body += _option(1, f"dropped at {r.iface}".encode()) + _END_OPT
        out += _block(0x00000006, epb_body)

    return out
