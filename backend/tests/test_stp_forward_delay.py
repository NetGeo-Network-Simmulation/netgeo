"""STP Listening/Learning + Forward Delay tests — P-3 (IEEE 802.1D-2004
§8.4 Port State Transitions).

A port moving onto the forwarding track (root or designated) passes through
Listening (no forwarding, no MAC learning) then Learning (MAC learning, still
no forwarding) before Forwarding, one Forward Delay per phase. A port moving
to Blocking does so immediately — that direction must never be delayed.

Most tests drive the low-level ``_enter_blocking`` / ``_enter_forwarding_track``
methods directly (white-box, same rationale as ``test_bgp_fsm.py``'s
``_pending_connect_retry_timers``: there's no public "force this port's role"
API, and reaching into the scheduler's heap is the only way to observe a
leaked/duplicate delay chain that a symptom-only assertion could miss).
"""
from __future__ import annotations

from engine.netstack import Network
from engine.netstack.addr import BROADCAST_MAC
from engine.netstack.frames import EthernetFrame
from engine.netstack.switching import STP_FORWARD_DELAY, Switch


def _pair(seed: int = 1) -> tuple[Network, Switch, object]:
    """A switch with one port to a (passive) peer switch."""
    net = Network(seed=seed)
    sw = net.add_device(Switch("sw"))
    peer = net.add_device(Switch("peer"))
    p = net.add_iface(sw, "gi0/1")
    q = net.add_iface(peer, "gi0/1")
    net.connect("l1", p, q)
    net.start()  # settles ports to their default designated/forwarding state
    return net, sw, p


def _pending_forward_delay_timers(net: Network, iface) -> int:
    """Count scheduled Listening->Learning or Learning->Forwarding callbacks
    bound to this exact interface (matched by identity in the handler's
    closure) — mirrors ``_pending_connect_retry_timers`` in test_bgp_fsm.py."""
    n = 0
    for entry in net.scheduler.queue._heap:
        ev = entry[-1]
        handler = getattr(ev, "handler", None)
        if handler is None:
            continue
        qual = handler.__qualname__
        if "_enter_forwarding_track" not in qual and "_enter_learning" not in qual:
            continue
        cells = [c.cell_contents for c in (handler.__closure__ or ())]
        if any(c is iface for c in cells):
            n += 1
    return n


def test_stp_new_designated_port_delays_forwarding():
    net, sw, p = _pair()
    sw._enter_blocking(p)
    assert p.stp_role == "blocked" and p.stp_state == "blocking"

    sw._enter_forwarding_track(net, p, "designated")
    t0 = net.now  # anchor absolute times here — a delay armed inside a
    # dispatched event re-bases from *that event's* time, not from whatever
    # a later run_for() overshoots to, so boundaries must be computed from t0
    # rather than chained run_for() calls (which drift past a boundary).
    assert p.stp_role == "designated"
    assert p.stp_state == "listening"  # not immediately forwarding
    assert _pending_forward_delay_timers(net, p) == 1

    net.run(until=t0 + STP_FORWARD_DELAY - 0.01)
    assert p.stp_state == "listening"  # first Forward Delay not elapsed yet

    net.run(until=t0 + STP_FORWARD_DELAY + 0.01)
    assert p.stp_state == "learning"  # first Forward Delay elapsed

    net.run(until=t0 + 2 * STP_FORWARD_DELAY - 0.01)
    assert p.stp_state == "learning"  # second Forward Delay not elapsed yet

    net.run(until=t0 + 2 * STP_FORWARD_DELAY + 0.01)
    assert p.stp_state == "forwarding"  # second Forward Delay elapsed
    assert _pending_forward_delay_timers(net, p) == 0


def test_stp_transition_to_blocking_is_immediate():
    """Regression: 802.1D §8.4 only delays the way *into* the forwarding
    track. A port that must block does so on the very same tick."""
    net = Network(seed=5)
    sw = net.add_device(Switch("sw", priority=8192))
    root = net.add_device(Switch("root", priority=4096))
    p1, p2 = net.add_iface(sw, "gi0/1"), net.add_iface(sw, "gi0/2")
    r1, r2 = net.add_iface(root, "gi0/1"), net.add_iface(root, "gi0/2")
    net.connect("l1", p1, r1)
    net.connect("l2", p2, r2)
    net.start()
    # Two parallel links to the same (lower-priority) root: one port wins the
    # root-port tiebreak, the other must block. One BPDU round-trip settles
    # this — nowhere near a Forward Delay (2.0s).
    net.run_for(0.2)

    blocked = [i for i in (p1, p2) if i.stp_state == "blocking"]
    assert len(blocked) == 1, f"expected exactly one blocked port, got {blocked}"
    # RSTP-a (802.1w): both links go to the same neighbour bridge ("root"),
    # so the losing port is specifically a backup, not the generic "blocked"
    # tag — see switching.py's _recompute_roles docstring.
    assert blocked[0].stp_role == "backup"
    assert _pending_forward_delay_timers(net, blocked[0]) == 0


def test_stp_listening_port_does_not_learn_or_forward():
    net, sw, p = _pair()
    sw._enter_blocking(p)
    sw._enter_forwarding_track(net, p, "designated")
    assert p.stp_state == "listening"

    frame = EthernetFrame(src_mac="aa:aa:aa:aa:aa:01", dst_mac=BROADCAST_MAC)
    drops_before = net.drops.get("stp_blocked", 0)
    sw.on_frame(net, p, frame)
    assert net.drops.get("stp_blocked", 0) == drops_before + 1
    assert (1, "aa:aa:aa:aa:aa:01") not in sw.mac_table


def test_stp_learning_port_learns_but_does_not_forward():
    net, sw, p = _pair()
    sw._enter_blocking(p)
    sw._enter_forwarding_track(net, p, "designated")
    net.run_for(STP_FORWARD_DELAY + 0.01)
    assert p.stp_state == "learning"

    frame = EthernetFrame(src_mac="aa:aa:aa:aa:aa:02", dst_mac=BROADCAST_MAC)
    drops_before = net.drops.get("stp_blocked", 0)
    sw.on_frame(net, p, frame)
    assert (1, "aa:aa:aa:aa:aa:02") in sw.mac_table  # learned...
    assert net.drops.get("stp_blocked", 0) == drops_before + 1  # ...but not forwarded


def test_stp_forward_delay_seq_guard_ignores_stale_callback():
    """A port that flips role twice in quick succession (before its first
    Forward Delay elapses) must not let the stale first chain's callback
    advance state out of turn. Without the per-port sequence guard this
    assertion fails: the stale t=2.0 callback would blindly set
    ``stp_state = "learning"`` even though the live (second) chain only
    started at t≈0.8 and shouldn't reach Learning until t≈2.8."""
    net, sw, p = _pair()
    sw._enter_blocking(p)

    sw._enter_forwarding_track(net, p, "designated")  # chain A, seq=N, starts t=0
    net.run_for(0.8)
    assert p.stp_state == "listening"

    sw._enter_blocking(p)  # abort chain A
    sw._enter_forwarding_track(net, p, "designated")  # chain B, seq=N+2, starts t=0.8
    assert p.stp_state == "listening"

    # Two scheduled callbacks now sit in the heap (chain A's stale one, due
    # ~t=2.0, and chain B's live one, due ~t=2.8) — there is no cancellation
    # API, only the seq check at fire time.
    assert _pending_forward_delay_timers(net, p) == 2

    net.run_for(1.3)  # t ~= 2.1: chain A's stale callback has fired and no-opped
    assert p.stp_state == "listening"  # still — chain B hasn't reached t=2.8 yet

    net.run_for(0.9)  # t ~= 3.0: chain B's Forward Delay has elapsed
    assert p.stp_state == "learning"

    net.run_for(2.0)  # let both chains fully drain
    assert p.stp_state == "forwarding"
    assert _pending_forward_delay_timers(net, p) == 0
