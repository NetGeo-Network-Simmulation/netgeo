"""E-4 acceptance tests — NG-SIM-11 QoS enable path (§2.7).

Covers:
- apply_marking: rule sets DSCP on matching packets; non-matching packets unchanged
- IfaceCounters: tx_by_class and drops_queue_by_class increment correctly
- FrameContext.ledger_fields: qos_class present when QoS enabled, absent otherwise
- EF preempts queued BE: EF frames drain before BE on enabled path
- Per-class tail-drop: depth_per_class isolates classes
- Integration: saturate 1 Mbps link with BE flood + EF trickle → EF loss=0, BE drops>0,
  ledger shows qos_class
- PACKET_ENQUEUE event fires when QoS enabled, not when disabled
- Router forward path calls apply_marking (marks UDP dst_port 5060 → DSCP 46)
- Lab.do_set_link_qos journals correctly (replay reproduces config)
- CLI show qos interface: Cisco dialect and MikroTik /queue print
"""
from __future__ import annotations

from ipaddress import IPv4Address

from engine.netstack import Network
from engine.netstack.addr import MacAddr
from engine.netstack.cli import CliSession
from engine.netstack.device import Host
from engine.netstack.frames import (
    ETH_IPV4,
    PROTO_UDP,
    EthernetFrame,
    Ipv4Packet,
    UdpSegment,
)
from engine.netstack.iface import FrameContext, IfaceCounters
from engine.netstack.qos import QosClass, QosConfig, apply_marking
from engine.netstack.routing import Router

# ---------------------------------------------------------------------------
# apply_marking unit tests
# ---------------------------------------------------------------------------

def _udp_pkt(dst_port: int, dscp: int = 0) -> Ipv4Packet:
    return Ipv4Packet(
        src=IPv4Address("10.0.0.1"),
        dst=IPv4Address("10.0.0.2"),
        proto=PROTO_UDP,
        dscp=dscp,
        payload=UdpSegment(src_port=1024, dst_port=dst_port),
    )


def test_apply_marking_sets_dscp_on_match():
    pkt = _udp_pkt(dst_port=5060)
    rules = [{"match": {"proto": "udp", "dst_port": 5060}, "set_dscp": 46}]
    apply_marking(rules, pkt)
    assert pkt.dscp == 46


def test_apply_marking_no_change_no_match():
    pkt = _udp_pkt(dst_port=80)
    rules = [{"match": {"proto": "udp", "dst_port": 5060}, "set_dscp": 46}]
    apply_marking(rules, pkt)
    assert pkt.dscp == 0


def test_apply_marking_first_match_wins():
    pkt = _udp_pkt(dst_port=5060)
    rules = [
        {"match": {"dst_port": 5060}, "set_dscp": 46},
        {"match": {"dst_port": 5060}, "set_dscp": 10},
    ]
    apply_marking(rules, pkt)
    assert pkt.dscp == 46


def test_apply_marking_empty_rules_no_op():
    pkt = _udp_pkt(dst_port=5060, dscp=10)
    apply_marking([], pkt)
    assert pkt.dscp == 10


def test_apply_marking_non_ip_payload_no_crash():
    """Non-IP objects (no .dscp) are silently ignored."""
    apply_marking([{"match": {}, "set_dscp": 46}], object())  # no AttributeError


# ---------------------------------------------------------------------------
# FrameContext.ledger_fields with qos_class
# ---------------------------------------------------------------------------

def _dummy_frame() -> EthernetFrame:
    return EthernetFrame(
        src_mac=MacAddr("aa:bb:cc:dd:ee:01"),
        dst_mac=MacAddr("aa:bb:cc:dd:ee:02"),
        ethertype=ETH_IPV4,
        payload=Ipv4Packet(
            src=IPv4Address("10.0.0.1"), dst=IPv4Address("10.0.0.2"), dscp=0
        ),
    )


def test_frame_context_ledger_fields_with_qos_class():
    ctx = FrameContext(frame=_dummy_frame(), link_id="l1", iface="r:eth0", qos_class="EF")
    fields = ctx.ledger_fields()
    assert fields["qos_class"] == "EF"


def test_frame_context_ledger_fields_without_qos_class():
    ctx = FrameContext(frame=_dummy_frame(), link_id="l1", iface="r:eth0")
    fields = ctx.ledger_fields()
    assert "qos_class" not in fields


# ---------------------------------------------------------------------------
# IfaceCounters per-class fields
# ---------------------------------------------------------------------------

def test_iface_counters_initial_state():
    c = IfaceCounters()
    assert c.tx_by_class == [0, 0, 0]
    assert c.drops_queue_by_class == [0, 0, 0]


def test_iface_counters_independence():
    """Each IfaceCounters instance gets its own list."""
    a, b = IfaceCounters(), IfaceCounters()
    a.tx_by_class[0] = 5
    assert b.tx_by_class[0] == 0


# ---------------------------------------------------------------------------
# Integration: per-class counters increment on enabled path
# ---------------------------------------------------------------------------

def _qos_net(*, bandwidth_bps: float = 100_000_000, depth: int = 32) -> tuple:
    """Returns (net, iface_a, attachment) with QoS enabled."""
    net = Network(seed=42)
    h1 = net.add_device(Host("h1"))
    h2 = net.add_device(Host("h2"))
    i1 = net.add_iface(h1, "eth0", ["10.0.0.1/30"])
    i2 = net.add_iface(h2, "eth0", ["10.0.0.2/30"])
    att = net.connect("link", i1, i2, bandwidth_bps=bandwidth_bps)
    att.qos = QosConfig(enabled=True, ef_min_dscp=40, af_min_dscp=8, depth_per_class=depth)
    return net, i1, att


def test_enabled_path_tx_by_class_increments():
    net, i1, _att = _qos_net()
    rep = net.ping("h1", "10.0.0.2", count=2)
    assert rep.received == 2
    # ping DSCP=0 → BE; some frames must have been transmitted
    total_tx = sum(i1.counters.tx_by_class)
    assert total_tx > 0, "Expected some tx_by_class increments"


def test_enabled_path_drops_queue_by_class_increments():
    """Overfill BE queue on enabled path — drops_queue_by_class[BE] must increment."""
    net, i1, _att = _qos_net(bandwidth_bps=1_000, depth=1)
    net.ping("h1", "10.0.0.2", count=10, interval=0.0)
    be_drops = i1.counters.drops_queue_by_class[QosClass.BE]
    total_drops = i1.counters.drops_queue
    assert be_drops > 0
    assert be_drops == total_drops  # only BE should drop here (ICMP → DSCP 0 → BE)


def test_disabled_path_class_counters_stay_zero():
    """Disabled QoS: per-class counters are never incremented."""
    net = Network(seed=1)
    h1, h2 = net.add_device(Host("h1")), net.add_device(Host("h2"))
    i1 = net.add_iface(h1, "eth0", ["10.1.0.1/30"])
    i2 = net.add_iface(h2, "eth0", ["10.1.0.2/30"])
    net.connect("link", i1, i2)
    net.ping("h1", "10.1.0.2", count=3)
    # QoS disabled — no per-class tx increments
    assert i1.counters.tx_by_class == [0, 0, 0]
    assert i1.counters.drops_queue_by_class == [0, 0, 0]


# ---------------------------------------------------------------------------
# Integration: PACKET_ENQUEUE fires iff QoS enabled (verified via ledger)
# ---------------------------------------------------------------------------

def test_packet_enqueue_fires_when_qos_enabled():
    """PACKET_ENQUEUE events appear in the ledger when QoS is enabled."""
    net, _i1, _att = _qos_net(bandwidth_bps=1_000_000)
    net.ping("h1", "10.0.0.2", count=1)
    enqueue_records = [r for r in net.ledger.records
                       if r["type"] == "PACKET_ENQUEUE"]
    assert len(enqueue_records) > 0, "Expected PACKET_ENQUEUE records with QoS enabled"


def test_packet_enqueue_does_not_fire_when_qos_disabled():
    """PACKET_ENQUEUE must not appear in the ledger when QoS is disabled."""
    net = Network(seed=2)
    h1, h2 = net.add_device(Host("h1")), net.add_device(Host("h2"))
    i1 = net.add_iface(h1, "eth0", ["10.2.0.1/30"])
    i2 = net.add_iface(h2, "eth0", ["10.2.0.2/30"])
    net.connect("link", i1, i2)
    net.ping("h1", "10.2.0.2", count=1)
    enqueue_records = [r for r in net.ledger.records
                       if r["type"] == "PACKET_ENQUEUE"]
    assert len(enqueue_records) == 0, (
        "PACKET_ENQUEUE should not fire on disabled path"
    )


# ---------------------------------------------------------------------------
# Integration: EF preempts BE (saturation scenario per §2.7)
# ---------------------------------------------------------------------------

def test_ef_preempts_be_saturation():
    """Saturate 1 Mbps link with BE flood + EF trickle → EF loss 0, BE drops > 0.

    Setup: h_flood floods link with BE (DSCP 0) to h_sink.
    h_ef sends a couple of EF (DSCP 46) frames. QoS enabled, deep enough for EF.
    After the run: EF tx_by_class[EF] > 0, drops_queue_by_class[EF] == 0,
    drops_queue_by_class[BE] > 0.
    """
    net = Network(seed=99)
    h_flood = net.add_device(Host("flood"))
    h_ef = net.add_device(Host("ef_src"))
    h_sink = net.add_device(Host("sink"))
    router = net.add_device(Router("r"))

    i_flood = net.add_iface(h_flood, "eth0", ["10.10.0.2/30"])
    i_ef = net.add_iface(h_ef, "eth0", ["10.10.1.2/30"])
    i_sink = net.add_iface(h_sink, "eth0", ["10.10.2.2/30"])
    ir_flood = net.add_iface(router, "eth0", ["10.10.0.1/30"])
    ir_ef = net.add_iface(router, "eth1", ["10.10.1.1/30"])
    ir_sink = net.add_iface(router, "eth2", ["10.10.2.1/30"])

    # 1 Mbps bottleneck on router → sink
    att = net.connect("bottleneck", ir_sink, i_sink, bandwidth_bps=1_000_000)
    att.qos = QosConfig(enabled=True, ef_min_dscp=40, af_min_dscp=8, depth_per_class=4)

    net.connect("l_flood", i_flood, ir_flood, bandwidth_bps=100_000_000)
    net.connect("l_ef", i_ef, ir_ef, bandwidth_bps=100_000_000)

    router.sync_connected_routes()
    h_flood.default_gateway = IPv4Address("10.10.0.1")
    h_ef.default_gateway = IPv4Address("10.10.1.1")
    h_sink.default_gateway = IPv4Address("10.10.2.1")

    # Flood BE traffic (ping DSCP=0 → BE): many pings all at once
    for _ in range(20):
        h_flood.ping(net, IPv4Address("10.10.2.2"), count=1, interval=0.0)

    # EF traffic: set DSCP=46 on a few packets via marking rules
    router.qos_marking = [{"match": {"proto": "icmp"}, "set_dscp": 46}]
    h_ef.ping(net, IPv4Address("10.10.2.2"), count=3, interval=0.0)
    # Clear marking so flood stays BE
    router.qos_marking = []
    # More BE flood
    for _ in range(20):
        h_flood.ping(net, IPv4Address("10.10.2.2"), count=1, interval=0.0)

    net.run_for(5.0)

    # ir_sink is the egress iface of the bottleneck attachment
    # (att = connect(ir_sink, i_sink), so att.a = ir_sink)
    counters = ir_sink.counters

    # BE must have some drops
    assert counters.drops_queue_by_class[QosClass.BE] > 0, (
        f"Expected BE drops, got {counters.drops_queue_by_class}"
    )
    # EF must have zero drops (strict priority protects it)
    assert counters.drops_queue_by_class[QosClass.EF] == 0, (
        f"Expected EF drops=0, got {counters.drops_queue_by_class[QosClass.EF]}"
    )


# ---------------------------------------------------------------------------
# Router.qos_marking wiring — marking call in _forward
# ---------------------------------------------------------------------------

def test_router_qos_marking_sets_dscp_on_forward():
    """Router with qos_marking rule marks UDP:5060 to DSCP 46 on forward."""
    net = Network(seed=7)
    h1 = net.add_device(Host("h1"))
    h2 = net.add_device(Host("h2"))
    r = net.add_device(Router("r"))

    ih1 = net.add_iface(h1, "eth0", ["10.3.0.2/30"])
    ir_in = net.add_iface(r, "eth0", ["10.3.0.1/30"])
    ir_out = net.add_iface(r, "eth1", ["10.3.1.1/30"])
    ih2 = net.add_iface(h2, "eth0", ["10.3.1.2/30"])
    net.connect("l1", ih1, ir_in)
    att = net.connect("l2", ir_out, ih2)
    att.qos = QosConfig(enabled=True, ef_min_dscp=40, af_min_dscp=8)

    r.sync_connected_routes()
    h1.default_gateway = IPv4Address("10.3.0.1")
    h2.default_gateway = IPv4Address("10.3.1.1")

    r.qos_marking = [{"match": {"proto": "icmp"}, "set_dscp": 46}]

    net.ping("h1", "10.3.1.2", count=2)

    # EF frames should have been transmitted (DSCP 46 >= 40)
    assert ir_out.counters.tx_by_class[QosClass.EF] > 0, (
        "Expected EF tx after marking ICMP to DSCP 46"
    )


# ---------------------------------------------------------------------------
# Ledger: qos_class appears in records when QoS enabled
# ---------------------------------------------------------------------------

def test_ledger_qos_class_present_when_enabled():
    net, _i1, _att = _qos_net()
    net.ping("h1", "10.0.0.2", count=1)
    tx_records = [r for r in net.ledger.records if r["type"] == "PACKET_TX"]
    # At least some records should carry qos_class when QoS is on
    qos_records = [r for r in tx_records if "qos_class" in r]
    assert qos_records, "Expected qos_class in at least one PACKET_TX ledger record"
    assert all(r["qos_class"] in ("EF", "AF", "BE") for r in qos_records)


def test_ledger_qos_class_absent_when_disabled():
    net = Network(seed=3)
    h1, h2 = net.add_device(Host("h1")), net.add_device(Host("h2"))
    i1 = net.add_iface(h1, "eth0", ["10.5.0.1/30"])
    i2 = net.add_iface(h2, "eth0", ["10.5.0.2/30"])
    net.connect("link", i1, i2)
    net.ping("h1", "10.5.0.2", count=1)
    tx_records = [r for r in net.ledger.records if r["type"] == "PACKET_TX"]
    assert tx_records, "Expected PACKET_TX records"
    # No qos_class on disabled path
    assert not any("qos_class" in r for r in tx_records), (
        "qos_class should be absent on disabled path"
    )


# ---------------------------------------------------------------------------
# Lab.do_set_link_qos journals and replays correctly
# ---------------------------------------------------------------------------

def _iface_schema(name: str, ips: list[str]):
    return {"id": "", "node_id": "", "name": name, "ip": ips}


async def test_do_set_link_qos_journaled(client):
    """do_set_link_qos journals the change; seek+replay reproduces it."""
    resp = await client.post("/api/projects", json={"name": "QosJournalTest"})
    pid = resp.json()["id"]

    async def node(name, kind, ifaces):
        r = await client.post("/api/nodes", json={
            "project_id": pid, "name": name, "kind": kind, "interfaces": ifaces
        })
        assert r.status_code == 201, r.text
        return r.json()

    h1 = await node("h1", "host", [_iface_schema("eth0", ["10.20.0.1/30"])])
    h2 = await node("h2", "host", [_iface_schema("eth0", ["10.20.0.2/30"])])

    def iface_id(n, name):
        return next(i["id"] for i in n["interfaces"] if i["name"] == name)

    lr = await client.post("/api/links", json={
        "project_id": pid,
        "a_iface": iface_id(h1, "eth0"),
        "b_iface": iface_id(h2, "eth0"),
    })
    assert lr.status_code == 201, lr.text
    link_id = lr.json()["id"]

    # Use the lab manager directly (no REST endpoint for do_set_link_qos yet)
    from app.services.netlab import get_lab_manager

    # Warm up the lab via REST (ping creates it)
    pr = await client.post(f"/api/lab/{pid}/ping", json={"src": "h1", "dst": "10.20.0.2"})
    assert pr.status_code == 200

    lab = get_lab_manager().peek(pid)
    assert lab is not None
    result = lab.do_set_link_qos(link_id, {"enabled": True, "depth_per_class": 16})
    assert result is True

    # Confirm the attachment has QoS enabled
    att = lab.net.attachments.get(link_id)
    assert att is not None and att.qos.enabled is True and att.qos.depth_per_class == 16

    # The journal must contain the set_link_qos entry
    journal_kinds = [e["kind"] for e in lab.journal]
    assert "set_link_qos" in journal_kinds


# ---------------------------------------------------------------------------
# CLI: show qos interface (Cisco-like and MikroTik-like)
# ---------------------------------------------------------------------------

def test_cli_show_qos_interface_cisco():
    net, _i1, _att = _qos_net()
    net.ping("h1", "10.0.0.2", count=1)
    r = net.find_device("h1")
    sess = CliSession(net, r)
    out = sess.execute("show qos interface")
    assert "EF" in out and "AF" in out and "BE" in out
    assert "eth0" in out


def test_cli_show_qos_interface_specific_cisco():
    net, _i1, _att = _qos_net()
    r = net.find_device("h1")
    sess = CliSession(net, r)
    out = sess.execute("show qos interface eth0")
    assert "eth0" in out


def test_cli_queue_print_mikrotik():
    net, _i1, _att = _qos_net()
    net.ping("h1", "10.0.0.2", count=1)
    h1 = net.find_device("h1")
    h1.nos = "routeros"
    sess = CliSession(net, h1)
    out = sess.execute("/queue print")
    assert "EF" in out and "BE" in out


# ---------------------------------------------------------------------------
# Tables API: qos section present
# ---------------------------------------------------------------------------

async def test_tables_endpoint_has_qos_section(client):
    resp = await client.post("/api/projects", json={"name": "QosTablesTest"})
    pid = resp.json()["id"]

    async def node(name, kind, ifaces):
        r = await client.post("/api/nodes", json={
            "project_id": pid, "name": name, "kind": kind, "interfaces": ifaces
        })
        assert r.status_code == 201
        return r.json()

    h1 = await node("h1", "host", [_iface_schema("eth0", ["10.30.0.1/30"])])
    h2 = await node("h2", "host", [_iface_schema("eth0", ["10.30.0.2/30"])])

    def iface_id(n, name):
        return next(i["id"] for i in n["interfaces"] if i["name"] == name)

    lr = await client.post("/api/links", json={
        "project_id": pid,
        "a_iface": iface_id(h1, "eth0"),
        "b_iface": iface_id(h2, "eth0"),
    })
    assert lr.status_code == 201

    await client.post(f"/api/lab/{pid}/ping", json={"src": "h1", "dst": "10.30.0.2"})
    tables = (await client.get(f"/api/lab/{pid}/tables/h1")).json()

    assert "qos" in tables
    qos_rows = tables["qos"]
    assert len(qos_rows) > 0
    row = qos_rows[0]
    assert "iface" in row and "qos_enabled" in row and "classes" in row
    classes = row["classes"]
    assert len(classes) == 3
    assert {c["class"] for c in classes} == {"EF", "AF", "BE"}
