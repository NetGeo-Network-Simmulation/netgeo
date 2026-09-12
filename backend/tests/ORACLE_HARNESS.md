# Oracle tests: proving conformance, not just self-consistency

Every other test in this directory checks our simulator against itself —
useful for catching regressions, but it can never prove the simulator
matches the standard it claims to implement. An oracle test runs the
**same topology** on our sim engine and on a real NOS (FRR, today) and
compares only the outcome the RFC actually mandates.

See `test_oracle_ospf.py` for the first (and so far only) case: OSPF
DR/BDR election (RFC 2328 §9.4/§7.3).

## What counts as a valid oracle comparison

Valid — behaviour the standard actually pins down:
- Who gets elected DR/BDR for a given priority/router-id set.
- Whether a change is non-preemptive (a late higher-priority router does
  not unseat an incumbent).
- The routing table a topology converges to.

Not valid — implementation detail, not standard:
- CLI output formatting, log line wording.
- Packet ordering or exact timing.
- Anything FRR does that's a documented *extension* beyond the RFC (if you
  hit one of these, write why in a comment — don't quietly adjust the
  sim to match, and don't quietly skip the mismatch either).

**If a real mismatch turns up, that's the valuable result. Report it —
never adjust the test until it's green again without noting why.**

## Adding the next protocol's case

1. Pick a protocol with an objective, standard-mandated outcome to compare
   (like DR/BDR's deterministic priority/router-id order). Avoid protocols
   whose "correct" behaviour is mostly implementation-defined tie-breaks.
2. Build the identical topology twice: once with `engine.netstack` classes
   (see `_sim_lan_election` in `test_oracle_ospf.py`), once against a real
   FRR container via `PodmanAdaptor` + hand-rolled config (see
   `_spawn_and_wire`/`_configure_ospf`/`_frr_conf`).
3. Speed up FRR's convergence with short hello/dead intervals (this file
   uses hello=1s/dead=4s) — cuts wall-clock election time from FRR's
   default ~10-40s down to a few seconds, matching what the sim already
   uses in its own DR/BDR tests.
4. Enable the daemon you need (`ospfd=yes` etc. in `/etc/frr/daemons`) with
   ONE container restart done *before* any per-link network is attached —
   podman's netavark IPAM reassigns a container's IP on every restart
   (verified experimentally while building this harness, and matches
   research/spike-frr-podman-2.md §1), so a restart after wiring would
   silently move the address a running test depends on. After that one
   restart, push `/etc/frr/frr.conf` and apply it live with `vtysh -b` —
   no second restart needed.
5. Compare only the RFC-guaranteed field (e.g. `show ip ospf interface`'s
   `State` token), never raw CLI text.
6. Guard the whole file with the same `skip_no_podman` pattern every
   podman test uses (`pytest.mark.skipif(not socket_reachable(), ...)`) so
   a machine without podman skips cleanly instead of erroring.
7. Clean up unconditionally: a pytest fixture with `try: yield ... finally:
   subprocess.run(["podman", "rm"/"network rm", "-f", ...])`, verified with
   real `podman` CLI calls, not the adaptor's own return value. An orphaned
   container is a slice failure, full stop — prove it by deliberately
   making a test fail mid-run once during development and checking
   `podman ps -a` / `podman network ls` come back clean anyway.

## CI marker: `oracle`, not `podman`

Oracle tests are marked `pytest.mark.oracle` (registered in `pytest.ini`),
**deliberately not** `pytest.mark.podman`. The CI required merge gate
(`.github/workflows/podman-emulation.yml`, job `podman-smoke`) runs
`pytest -m podman -q` — that selector does not pick up `oracle`-marked
tests, so they never run in the required gate.

Reasoning: oracle tests are slower (image config push + a restart + a
convergence poll, ~10-20s per test) and touch more moving parts (tar
archives into the container, `vtysh -b` reloads, timing-sensitive polls)
than the existing spawn/destroy/wire_link smoke tests. That's more
surface for a flaky required-gate failure than this slice should add
without Surya's sign-off. Run them on purpose instead:

```
pytest -m oracle -q          # just the oracle suite
pytest -q                    # full suite — oracle tests run here too,
                              # since plain `pytest -q` has no marker filter
```

If oracle tests earn a place in CI later (a nightly job, or promotion into
the required gate the way `podman-smoke` itself was promoted — see the
memory vault's nos-emulation-track-plan N-2b bar), add a new workflow step
rather than folding them into `podman-smoke`'s `-m podman` selector.
