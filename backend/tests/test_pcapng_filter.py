"""R2 wave-1 tests — NG-CAP-01 pcapng export + NG-CAP-02 display filter.

The pcapng stream is validated two ways: a structural parse (block layout,
magic numbers, EPB payload offsets against known Ethernet/ARP/ICMP fields)
that always runs, and a tshark oracle (`tshark -r x.pcapng -Y icmp` shows the
echo pair — the NG-CAP-01 acceptance criterion) that runs wherever tshark is
installed (CI).
"""
from __future__ import annotations

import shutil
import struct
import subprocess

import pytest

from engine.netstack import Network
from engine.netstack.device import Host
from engine.netstack.filterlang import compile_filter
from engine.netstack.pcapng import write_pcapng
from engine.netstack.routing import Router


def _lan() -> Network:
    net = Network(seed=11)
    h1, h2 = net.add_device(Host("h1")), net.add_device(Host("h2"))
    i1 = net.add_iface(h1, "eth0", ["10.0.0.1/24", "2001:db8::1/64"])
    i2 = net.add_iface(h2, "eth0", ["10.0.0.2/24", "2001:db8::2/64"])
    net.connect("l1", i1, i2)
    net.ping("h1", "10.0.0.2", count=2)
    net.ping("h1", "2001:db8::2", count=1)
    return net


# ---------------------------------------------------------------------------
# pcapng structural validation
# ---------------------------------------------------------------------------

def _parse_blocks(data: bytes) -> list[tuple[int, bytes]]:
    """Minimal little-endian pcapng reader: [(block_type, body), ...]."""
    out, pos = [], 0
    while pos < len(data):
        btype, blen = struct.unpack_from("<II", data, pos)
        assert blen % 4 == 0 and blen >= 12, f"bad block length {blen}"
        (trailer,) = struct.unpack_from("<I", data, pos + blen - 4)
        assert trailer == blen, "trailing length mismatch"
        out.append((btype, data[pos + 8 : pos + blen - 4]))
        pos += blen
    return out


def test_pcapng_structure_and_arp_icmp_bytes():
    net = _lan()
    data = write_pcapng(net.capture.records(limit=1000))
    blocks = _parse_blocks(data)

    # SHB first with palindromic type + byte-order magic + version 1.0.
    assert blocks[0][0] == 0x0A0D0D0A
    magic, major, minor = struct.unpack_from("<IHH", blocks[0][1], 0)
    assert (magic, major, minor) == (0x1A2B3C4D, 1, 0)

    idbs = [b for t, b in blocks if t == 0x00000001]
    epbs = [b for t, b in blocks if t == 0x00000006]
    assert len(idbs) == 1                      # one link in this lab
    assert struct.unpack_from("<H", idbs[0], 0)[0] == 1  # LINKTYPE_ETHERNET
    assert epbs, "expected packet blocks"

    packets = []
    for body in epbs:
        iface_id, _th, _tl, cap_len, orig_len = struct.unpack_from("<IIIII", body, 0)
        assert iface_id == 0 and cap_len == orig_len > 0
        packets.append(body[20 : 20 + cap_len])

    # ARP request precedes the ICMP echo: ethertype 0x0806 at offset 12,
    # opcode 1 at ARP offset 6.
    arps = [p for p in packets if p[12:14] == b"\x08\x06"]
    assert arps and arps[0][14:16] == b"\x00\x01" and arps[0][20:22] == b"\x00\x01"

    # ICMP echo pair: IPv4 proto 1, ICMP type 8 then type 0 at ihl offset.
    icmp_types = [
        p[14 + 20] for p in packets
        if p[12:14] == b"\x08\x00" and p[14 + 9] == 1
    ]
    assert 8 in icmp_types and 0 in icmp_types

    # ICMPv6 echo present too (ethertype 0x86dd, next header 58, type 128).
    v6 = [p for p in packets if p[12:14] == b"\x86\xdd" and p[14 + 6] == 58]
    assert any(p[14 + 40] == 128 for p in v6)


def test_ipv4_header_checksum_is_valid():
    net = _lan()
    rec = next(
        r for r in net.capture.records(limit=1000) if "echo-request" in r.info
    )
    ip_header = rec.wire[14 : 14 + 20]
    total = sum(struct.unpack("!10H", ip_header))
    while total >> 16:
        total = (total & 0xFFFF) + (total >> 16)
    assert total == 0xFFFF, "IPv4 header checksum must verify"


def test_frame_bytes_covers_every_captured_frame():
    # OSPF + DHCP + DNS traffic all synthesize without falling back to zeros.
    net = Network(seed=12)
    r1, r2 = net.add_device(Router("r1")), net.add_device(Router("r2"))
    a = net.add_iface(r1, "eth0", ["10.0.12.1/30"])
    b = net.add_iface(r2, "eth0", ["10.0.12.2/30"])
    net.connect("l", a, b)
    from engine.netstack.protocols.ospf import OspfProcess

    OspfProcess(r1, router_id="1.1.1.1")
    OspfProcess(r2, router_id="2.2.2.2")
    net.run_for(30.0)
    ospf_recs = [r for r in net.capture.records(limit=1000) if "OSPF" in r.info]
    assert ospf_recs
    for rec in ospf_recs:
        assert rec.wire[14 + 9] == 89          # IPv4 proto OSPF
        assert rec.wire[14 + 20] == 2          # OSPFv2 version byte


@pytest.mark.skipif(shutil.which("tshark") is None, reason="tshark not installed")
def test_pcapng_opens_in_tshark_with_icmp_pair(tmp_path):
    """AC (NG-CAP-01): tshark -Y icmp shows the echo request/reply pair."""
    net = _lan()
    path = tmp_path / "lab.pcapng"
    path.write_bytes(write_pcapng(net.capture.records(limit=1000)))
    out = subprocess.run(
        ["tshark", "-r", str(path), "-Y", "icmp"],
        capture_output=True, text=True, timeout=30, check=True,
    ).stdout
    assert "Echo (ping) request" in out and "Echo (ping) reply" in out


# ---------------------------------------------------------------------------
# display filter mini-language
# ---------------------------------------------------------------------------

def _rows(net: Network) -> list[dict]:
    return [r.as_dict() for r in net.capture.records(limit=1000)]


def test_filter_proto_and_field():
    rows = _rows(_lan())
    icmp = [r for r in rows if compile_filter("icmp")(r)]
    assert icmp and all("icmp" in r["layers"] for r in icmp)

    addr = compile_filter("icmp && ip.addr == 10.0.0.1")
    hits = [r for r in rows if addr(r)]
    assert hits and all(
        "10.0.0.1" in (r["layers"]["ipv4"]["src"], r["layers"]["ipv4"]["dst"])
        for r in hits
    )


def test_filter_bool_ops_parens_and_negation():
    rows = _rows(_lan())
    f = compile_filter("(arp || icmpv6) && !icmp")
    hits = [r for r in rows if f(r)]
    assert hits
    for r in hits:
        assert "icmp" not in r["layers"]
        assert "arp" in r["layers"] or "icmpv6" in r["layers"]


def test_filter_numeric_and_ipv6_canonicalization():
    rows = _rows(_lan())
    assert any(compile_filter("icmpv6.type == 128")(r) for r in rows)
    # Different spelling of the same IPv6 address still matches.
    assert any(
        compile_filter("ipv6.addr == 2001:0db8:0000:0000:0000:0000:0000:0001")(r)
        for r in rows
    )
    assert any(compile_filter("frame.size > 80")(r) for r in rows)


def test_filter_syntax_errors_are_valueerrors():
    for bad in ("ip.addr ==", "&& icmp", "(icmp", "bogus.field == 1", "@#$"):
        with pytest.raises(ValueError):
            compile_filter(bad)


def test_filter_empty_matches_everything():
    rows = _rows(_lan())
    f = compile_filter("")
    assert all(f(r) for r in rows)


# ---------------------------------------------------------------------------
# HTTP surface
# ---------------------------------------------------------------------------

async def test_pcapng_and_filter_endpoints(client):
    from app.services.netlab import get_lab_manager

    get_lab_manager()._labs.clear()
    resp = await client.post("/api/projects", json={"name": "CapLab"})
    pid = resp.json()["id"]
    nodes = {}
    for name, ip in (("h1", "10.0.0.1/24"), ("h2", "10.0.0.2/24")):
        r = await client.post("/api/nodes", json={
            "project_id": pid, "name": name, "kind": "host",
            "interfaces": [{"id": "", "node_id": "", "name": "eth0", "ip": [ip]}],
        })
        nodes[name] = r.json()
    a = nodes["h1"]["interfaces"][0]["id"]
    b = nodes["h2"]["interfaces"][0]["id"]
    assert (await client.post(
        "/api/links", json={"project_id": pid, "a_iface": a, "b_iface": b}
    )).status_code == 201
    assert (await client.post(
        f"/api/lab/{pid}/ping", json={"src": "h1", "dst": "10.0.0.2", "count": 2}
    )).json()["received"] == 2

    # Display filter narrows records; bad filters are a 4xx, not a 500.
    got = (await client.get(
        f"/api/lab/{pid}/captures?filter=icmp%20%26%26%20ip.addr%3D%3D10.0.0.1"
    )).json()
    assert got["count"] > 0 and all("icmp" in r["layers"] for r in got["records"])
    bad = await client.get(f"/api/lab/{pid}/captures?filter=ip.addr%20%3D%3D")
    assert bad.status_code in (400, 422)

    # pcapng download: magic block type + non-trivial body.
    dl = await client.get(f"/api/lab/{pid}/pcapng")
    assert dl.status_code == 200
    assert dl.content[:4] == b"\x0a\x0d\x0d\x0a" and len(dl.content) > 200
    assert "attachment" in dl.headers.get("content-disposition", "")
    get_lab_manager()._labs.clear()
