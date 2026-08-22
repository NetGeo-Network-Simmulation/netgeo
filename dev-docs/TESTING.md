# NetGeo — Testing

## Running

```bash
cd backend
.venv/bin/python -m pytest -q -W ignore::DeprecationWarning
.venv/bin/ruff check .
```

No database needed — the default store is in-memory (`app/store/memory.py`).
This is exactly what CI runs on every PR (`CONTRIBUTING.md`), and what must
stay green: **449 passed / 1 skipped**, ruff clean, as of this writing.

## Structure

`backend/tests/` is flat — 54 files, one per feature area, no nested
suites. The two that matter most for engine work:

- `test_engine.py` — the `/simulate`-path engine (`model.py`/`runtime.py`):
  determinism (`test_run_is_deterministic`), the golden-ledger-style checks.
- `test_netstack.py`, plus one file per protocol
  (`test_bgp_fsm.py`, `test_ospf_multiarea.py`, `test_isis.py`,
  `test_vrrp.py`, `test_mpls_l3vpn.py`, `test_sr.py`,
  `test_evpn_vxlan.py`, …) — the `/lab`-path netstack.
- `test_lint_determinism.py`, `test_ledger.py`, `test_event_queue.py` —
  kernel-level determinism guarantees shared by both engines.
- `test_lab_api.py` — the `/lab` HTTP surface, including `/seek`.

Replay-determinism tests follow one shape everywhere: build the same
topology twice with the same seed, run both to the same horizon, assert
`net1.ledger.seq == net2.ledger.seq` and `net1.ledger.hash() ==
net2.ledger.hash()` (`test_bgp_fsm.py::test_bgp_fsm_replay_determinism` is
a short example).

## Hard rule: timer tests must inspect the scheduler, not the end state

**Incident (v1.2.079).** The BGP FSM rebuild added `_arm_connect_retry` /
`_connect_retry_fired` (the pre-session retry timer) without giving it a
sequence-guard, even though the neighboring `_arm_hold` / `_hold_fired`
(the Hold Timer) already had one. Effect: every retry cycle armed a *new*
timer chain without invalidating the previous one, so a peer stuck
retrying accumulated an unbounded number of live `ConnectRetryTimer`
events in the scheduler — each one a full second, independent generation,
all still capable of firing and re-arming further duplicates. Because the
FSM's own logic was otherwise correct, every duplicate chain eventually
agreed on the same end state ("active") and stopped logging further
transitions — the leak was invisible to both the final `peer.state` and
`peer.transitions`, and it would have silently multiplied `/seek` replay
noise (phantom transitions that never really happened) had it shipped.

**Zero of the 8 original BGP FSM tests caught it** — all 8 asserted on
final state and/or `transitions`, which is exactly the blind spot above.

**The rule going forward:**

1. Every new timer must be sequence-guarded (`ENGINE-GUIDE.md` §"Timer
   sequence-guard").
2. A test for that timer must count **pending events in the scheduler
   heap** — `net.scheduler.queue._heap` — not just check final state or a
   transitions log. `test_bgp_fsm.py`'s `_pending_connect_retry_timers()`
   (line 174) is the reference pattern: it walks `_heap`, matches each
   pending `TIMER` event's `handler.__qualname__` for the arm function
   name, then inspects the handler's closure cells (`handler.__closure__`)
   to match the exact peer/session object by identity. This is
   deliberately white-box — there is no public "how many timers are
   pending for X" API, and adding one just for tests wasn't worth it
   (`# ponytail`-shaped call: reach into the heap instead of building an
   introspection API three call sites will ever use).
3. The regression test must be verified **in both directions**: confirm it
   fails against the code *before* the guard fix, and passes *after*. A
   test that only ever ran against the fixed code proves nothing about
   whether it would have caught the original bug.

`test_bgp_fsm.py::test_connect_retry_timer_does_not_duplicate_across_an_early_close`
is the test this incident produced — read it as the template for any new
timer-heavy protocol test.

## Known test blind spots

These modules are each exercised by exactly **one** test file. Not
necessarily under-tested, but a single file means a single angle — treat
changes here with extra care, and prefer adding a second, differently-shaped
test over trusting the existing one covers a new edge case:

| Module | Only tested by |
|---|---|
| `netstack/filterlang.py` | `test_pcapng_filter.py` |
| `netstack/protocols/isis.py` | `test_isis.py` |
| `netstack/protocols/vrrp.py` | `test_vrrp.py` |
| `netstack/protocols/vxlan.py` | `test_evpn_vxlan.py` |

`netstack/protocols/mpls.py` (`LdpProcess`/`L3vpnProcess`) is covered by
two files, `test_mpls_l3vpn.py` and `test_sr.py` (SR builds on the LDP
adjacency table — see `ADDING-A-PROTOCOL.md` §4) — not the single-file
pattern above, noted here so this table doesn't undercount it.
