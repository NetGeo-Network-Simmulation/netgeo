# NetGeo — Engine Guide (`backend/engine/netstack/`)

Covers the packet-realistic engine that backs `/lab` (see
[`ARCHITECTURE.md`](ARCHITECTURE.md) for how it fits alongside `/simulate`
and `emul`). This is the engine to read before writing a protocol
(`ADDING-A-PROTOCOL.md`) or a test that touches timers (`TESTING.md`).

## Core object model

- **`Device`** (`engine/netstack/device.py`) — base class every node type
  shares: owns `Interface`s, ARP resolution with a pending-packet queue,
  ICMP echo. `Host` (same file) is a full end-host (ARP, ping, DHCP client,
  DNS stub resolver). `Router` (`routing.py`) and `Switch`
  (`switching.py`) subclass the L3/L2 sides respectively.
- **`Interface`** (`engine/netstack/iface.py`) — one NIC: MAC/IPs, MTU,
  speed, VLAN membership. **`LinkAttachment`** (same file) is the wire
  between two interfaces — propagation delay, QoS config, capture hook.
- **Frames** (`engine/netstack/frames.py`) — the actual wire objects:
  `EthernetFrame`, `ArpPacket`, `Ipv4Packet`/`Ipv6Packet`,
  `IcmpMessage`/`Icmpv6Message`, `UdpSegment`, `DhcpMessage`, `DnsMessage`.
  These are what gets captured (`capture.py`) and exported to pcapng.
- **`Network`** (`engine/netstack/network.py`) — one runnable lab: owns the
  scheduler, a seeded RNG, capture manager, every `Device`, every
  `LinkAttachment`, and ping/traceroute session tracking. `Network.run()`
  just delegates to the scheduler.

## The DES kernel (shared with the `/simulate` engine)

`engine/events.py` + `engine/scheduler.py` — the same kernel both engine
paths use.

- `SimEvent(time, type, handler, payload, node_id)` — a pure data record.
- `EventQueue` is a min-heap keyed on **`(time, type, seq)`**. `type` is an
  `IntEnum` (`EventType`) that doubles as tie-break priority — control-plane
  events (`LINK_UP`/`LINK_DOWN`/`NODE_UP`/`NODE_DOWN`) sort before
  data-plane ones (`PACKET_TX`/`PACKET_RX`) at the same timestamp, and
  `TIMER` sorts after both. `seq` is a monotonic counter assigned on push —
  the final tie-breaker that makes two events at the same `(time, type)`
  pop in the order they were scheduled, not heap-arbitrary order.
- `Scheduler.run()` pops the earliest event, advances `now` to its
  timestamp, calls `event.handler(context, event)`, and repeats until the
  queue empties, a horizon (`until`) is hit, or `dispatch_cap` (total
  lifetime dispatches, used by `/seek` — see below) is reached.

## Why determinism is not negotiable

Two features depend on **bit-for-bit reproducible runs** given the same
topology + seed, not just "close enough":

1. **`/seek` (time travel)** — `LabManager.seek()`
   (`backend/app/services/netlab.py`) rebuilds the `Network` from scratch,
   sets `scheduler.dispatch_cap` to the target event sequence number, and
   replays the journal (below) up to that point. If two runs of the same
   inputs could produce even slightly different event orderings, stepping
   backward and forward would show a different history each time —
   indistinguishable from a bug to the person debugging with it.
2. **Grading** (education workspace) compares a student's run against an
   expected outcome. Non-deterministic ordering would produce false
   positives/negatives unrelated to whether the student's topology is
   actually correct.

`backend/tests/test_bgp_fsm.py::test_bgp_fsm_replay_determinism` and
`test_lint_determinism.py` are the enforcement tests — see
[`TESTING.md`](TESTING.md).

## The journal + `/seek` replay contract

`Lab` (`backend/app/services/netlab.py`, class `Lab`) wraps a `Network`
with a **stimulus journal**: every operation that can perturb the sim
(`do_run`, `do_ping`, `do_traceroute`, `do_cli`, `do_set_link_qos`, `do_mode`)
is a `do_*` method that appends a journal entry (`{"kind", "seq_before",
"args"}`) *before* executing. `seq_before` is `net.ledger.seq` at record
time — the ledger (`engine/ledger.py`) is a running SHA-256 over every
dispatched event, incremented once per event.

`LabManager.seek(topo, target_seq)`:
1. Rebuilds a fresh `Network` from the topology (same seed).
2. Caps the scheduler (`scheduler.dispatch_cap = target_seq`).
3. Re-applies journal entries whose `seq_before < target_seq` via
   `Lab._apply()` (a non-recording replay path — it must not itself grow
   the journal, or seeking would corrupt future entries).
4. Discards journal entries at/after the target — new stimuli from that
   point rewrite the future, matching Packet Tracer's step-back behavior.

This only works because rebuild + replay is guaranteed to reproduce the
exact same event stream — the same determinism contract as above, applied
to a partial replay instead of a full one.

## Timer sequence-guard (hard rule)

Every protocol timer in `netstack/protocols/` (BGP, VRRP, OSPF, IS-IS, …)
uses the same pattern, best documented in `bgp.py`
(`_arm_hold`/`_hold_fired`, `_arm_connect_retry`/`_connect_retry_fired`):

```python
peer.hold_seq += 1
seq = peer.hold_seq
net.scheduler.schedule_after(hold_time, SimEvent(
    time=0.0, type=EventType.TIMER,
    handler=lambda _c, _e: self._hold_fired(net, peer, seq),
    node_id=self.router.node_id,
))

def _hold_fired(self, net, peer, seq):
    if seq != peer.hold_seq:
        return  # superseded by a newer generation — do nothing
    ...
```

Why this is mandatory: the scheduler has **no cancel operation** — an
armed `SimEvent` sits in the heap until its time comes, whatever the FSM
does in the meantime. A protocol state machine that closes a session,
retries, or resets *must* invalidate any timer armed by the previous
"generation" of that timer, or the stale one fires later and mutates state
it no longer has authority over — including re-arming *itself* into a
second live chain that runs forever alongside the real one. Bump the
counter **every time you arm the timer, including at the top of whatever
teardown/reset path might race it** (see `bgp.py`'s `_begin_connect` and
`_close_session`, which both bump `connect_retry_seq` even when they may
not themselves re-arm). The full incident this rule comes from —
including why it went undetected by 8 passing tests — is in
[`TESTING.md`](TESTING.md).

## Adding a protocol

See [`ADDING-A-PROTOCOL.md`](ADDING-A-PROTOCOL.md) — it's a step-by-step
recipe derived from `protocols/bgp.py`, not repeated here.
