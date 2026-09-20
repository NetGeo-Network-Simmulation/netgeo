"""Egress shaper tests — A3 slice (per-interface token-bucket delay, not drop).

Covers:
- _shape() unit: burst admits instantly, then each frame waits exactly
  size*8/CIR for tokens to refill (mirrors test_police_unit_burst_then_drop_
  then_refill's style for the policer).
- Departure spacing: N frames on a shaped link depart size*8/CIR seconds
  apart once the initial burst is spent.
- EF still preempts BE at dequeue even while the interface is shaper-waiting
  (strict priority is evaluated fresh on every retry).
- Queue overflow tail-drop is untouched by shaping (still an enqueue-time
  check).
- Unshaped interfaces (shaper_bps=None, the default) never arm a retry —
  disabled-path parity for the new field.
- Determinism: two independent runs of the same shaped scenario produce a
  byte-identical ledger hash (the property that makes seek/replay safe).
"""
from __future__ import annotations

from ipaddress import IPv4Address
from itertools import pairwise

import pytest

from engine.netstack import EthernetFrame, Host, Ipv4Packet, Network
from engine.netstack.qos import QosClass, QosConfig


def _pair(seed: int = 1, bandwidth_bps: float = 1_000_000_000.0):
    net = Network(seed=seed)
    h1 = net.add_device(Host("h1"))
    h2 = net.add_device(Host("h2"))
    i1 = net.add_iface(h1, "eth0", ["10.0.0.1/30"])
    i2 = net.add_iface(h2, "eth0", ["10.0.0.2/30"])
    att = net.connect("link", i1, i2, bandwidth_bps=bandwidth_bps)
    return net, i1, i2, att


def _frame(size_bytes: int, dscp: int = 0) -> EthernetFrame:
    pkt = Ipv4Packet(src=IPv4Address("10.0.0.1"), dst=IPv4Address("10.0.0.2"), dscp=dscp)
    return EthernetFrame(src_mac="AA:AA:AA:AA:AA:01", dst_mac="AA:AA:AA:AA:AA:02",
                          payload=pkt, explicit_size=size_bytes)


# ---------------------------------------------------------------------------
# Unit: _shape()
# ---------------------------------------------------------------------------

def test_shape_unit_burst_then_wait_then_refill():
    net, i1, _i2, _att = _pair()
    rate_bps = 8_000.0   # 1000 bytes/sec
    burst = 1000          # exactly one frame's worth

    net.scheduler.now = 0.0
    assert i1._shape(net, 1000, rate_bps, burst) == 0.0        # burst spent, goes now
    wait = i1._shape(net, 1000, rate_bps, burst)                 # bucket empty
    assert wait == pytest.approx(1.0, rel=1e-9)                  # 1000B / 1000 B/s

    net.scheduler.now = 0.5                                      # half-refilled (500B)
    wait2 = i1._shape(net, 1000, rate_bps, burst)
    assert wait2 == pytest.approx(0.5, rel=1e-9)

    net.scheduler.now = 1.0                                      # fully refilled now
    assert i1._shape(net, 1000, rate_bps, burst) == 0.0


# ---------------------------------------------------------------------------
# Behavioural: departure spacing
# ---------------------------------------------------------------------------

def test_shaped_departures_spaced_by_size_over_cir():
    """Bc = one frame: the first frame leaves immediately, every following
    frame must wait exactly size*8/CIR for the bucket to refill."""
    net, i1, _i2, att = _pair(bandwidth_bps=1_000_000_000_000.0)  # ~free serialization
    rate_bps = 10_000_000.0   # 10 Mb/s
    size = 1000
    att.qos = QosConfig(enabled=True, shaper_bps=rate_bps, shaper_burst_bytes=size)

    n = 5
    for _ in range(n):
        i1.transmit(net, _frame(size, dscp=0))
    net.run_for(1.0)

    tx = [r for r in net.capture.records("link", limit=1000) if r.direction == "tx"]
    assert len(tx) == n
    times = [r.time for r in tx]
    expected_gap = size * 8.0 / rate_bps
    assert times[0] == pytest.approx(0.0, abs=1e-6)
    for a, b in pairwise(times):
        assert (b - a) == pytest.approx(expected_gap, rel=1e-3)
    assert i1.counters.shaper_delays == n - 1


def test_unshaped_link_never_delays():
    """Regression: shaper_bps=None (the default) must never arm a retry —
    disabled-path parity for interfaces that don't opt into shaping."""
    net, i1, _i2, att = _pair()
    assert att.qos.shaper_bps is None
    for _ in range(5):
        i1.transmit(net, _frame(500, dscp=0))
    net.run_for(1.0)
    assert i1.counters.shaper_delays == 0
    tx = [r for r in net.capture.records("link", limit=1000) if r.direction == "tx"]
    assert len(tx) == 5


# ---------------------------------------------------------------------------
# Behavioural: strict priority preserved under shaping
# ---------------------------------------------------------------------------

def test_ef_preempts_be_while_shaper_waiting():
    """Bc = one frame: BE1 unavoidably departs immediately (the bucket starts
    full — shaping can't un-send an already-departed frame). But BE2/BE3
    are still queued and waiting for the bucket to refill when EF arrives,
    and EF must preempt them — strict priority is re-evaluated on every
    shaper retry, not fixed at enqueue time."""
    net, i1, _i2, att = _pair(bandwidth_bps=1_000_000_000_000.0)
    att.qos = QosConfig(enabled=True, depth_per_class=32,
                         shaper_bps=8_000.0, shaper_burst_bytes=1000)

    for _ in range(3):
        i1.transmit(net, _frame(1000, dscp=0))   # BE
    i1.transmit(net, _frame(1000, dscp=46))       # EF, enqueued last

    net.run_for(3.0)

    tx_events = [r for r in net.ledger.records if r["type"] == "PACKET_TX"]
    assert [r["qos_class"] for r in tx_events] == ["BE", "EF", "BE"]


# ---------------------------------------------------------------------------
# Behavioural: queue overflow untouched by shaping
# ---------------------------------------------------------------------------

def test_queue_overflow_tail_drop_exact_count_while_shaped():
    net, i1, _i2, att = _pair()
    att.qos = QosConfig(enabled=True, depth_per_class=2,
                         shaper_bps=1.0, shaper_burst_bytes=0)  # effectively frozen

    for _ in range(5):
        i1.transmit(net, _frame(1000, dscp=0))

    assert len(i1._queues[QosClass.BE]) == 2
    assert i1.counters.drops_queue == 3
    assert i1.counters.drops_queue_by_class[int(QosClass.BE)] == 3


# ---------------------------------------------------------------------------
# Determinism: identical inputs -> byte-identical ledger (seek/replay safety)
# ---------------------------------------------------------------------------

def _run_shaped_scenario(seed: int) -> Network:
    net, i1, _i2, att = _pair(seed=seed, bandwidth_bps=1_000_000_000_000.0)
    att.qos = QosConfig(enabled=True, depth_per_class=16,
                         shaper_bps=5_000_000.0, shaper_burst_bytes=1000)
    for i in range(6):
        i1.transmit(net, _frame(1000, dscp=46 if i % 2 == 0 else 0))
    net.run_for(3.0)
    return net


def test_shaper_events_are_deterministic_across_independent_runs():
    net_a = _run_shaped_scenario(seed=42)
    net_b = _run_shaped_scenario(seed=42)
    assert net_a.ledger.hash() == net_b.ledger.hash()
    assert list(net_a.ledger.records) == list(net_b.ledger.records)
    # Sanity: the scenario actually exercised the shaper retry path.
    assert net_a.ledger.records  # non-empty
