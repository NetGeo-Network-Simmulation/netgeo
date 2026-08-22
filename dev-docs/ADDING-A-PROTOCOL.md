# NetGeo — Adding a Protocol

A real recipe, derived from `backend/engine/netstack/protocols/bgp.py`
(recently rebuilt into a full RFC 4271 §8 six-state FSM with NOTIFICATION —
`git show 18778ca`) and `sr.py`. Read [`ENGINE-GUIDE.md`](ENGINE-GUIDE.md)
first for the object model and the timer sequence-guard rule — this doc
assumes it.

## 1. Where the file goes

New protocol → new module in `backend/engine/netstack/protocols/`, one file
per protocol (`bgp.py`, `ospf.py`, `isis.py`, `mpls.py`, `sr.py`, `vrrp.py`,
`vxlan.py` are the existing set). No shared base class exists — each
process is a plain class that duck-types onto `Router.processes` (see
`routing.py`: `self.processes: list = []`, iterated with `.on_packet(net,
iface, pkt)` for delivery and `proc = self` calls for its own timers).

Shape to follow (from `bgp.py`):

```python
class MyProtocolProcess:
    proto = "myproto"          # short id, used in logs / capture layers

    def __init__(self, router: Router, ...):
        self.router = router
        router.processes.append(self)   # register with the device

    def start(self, net: Network) -> None:
        ...  # arm initial timers, kick off adjacency formation

    def on_packet(self, net: Network, iface: Interface, pkt: Ipv4Packet) -> None:
        ...  # dispatch on message type
```

If the protocol needs its own wire messages, add small `@dataclass`es near
the top of the file (see `BgpOpen`/`BgpUpdate`/`BgpKeepalive`/
`BgpNotification` in `bgp.py`) rather than reusing another protocol's frame
shapes — they get captured via `ledger_fields()`/`layers` and shown in
pcapng, so keep them protocol-specific and small.

If the protocol is a state machine (BGP, VRRP), define its states as a
module-level tuple (`STATES = ("idle", "connect", ...)` in `bgp.py`) and
drive every transition through one `_set_state()` helper that also appends
to a `transitions` list on the peer/session object — that list is what
tests and the UI read to answer "what did this session actually do".

## 2. Timers — non-negotiable

Every timer arm/fire pair needs the sequence-guard pattern from
`ENGINE-GUIDE.md` — a per-timer counter on the peer/session object, bumped
on every arm (including in teardown paths that might not re-arm), checked
in the fire handler before doing anything. Copy `bgp.py`'s
`_arm_hold`/`_hold_fired` or `_arm_connect_retry`/`_connect_retry_fired`
verbatim in shape. Skipping this is how v1.2.079's timer leak happened —
see [`TESTING.md`](TESTING.md) for the incident and the test rule it left
behind.

## 3. Registering it: `netlab.py` is the choke-point

`backend/app/services/netlab.py::build_network()` is the **only** place
that turns a node's `intent` dict (the JSON config from the topology model)
into live protocol processes. Every existing protocol follows the same
three-line shape there — read an `intent.get("myproto")` block, check an
`enabled`/presence flag, construct the process:

```python
ospf_cfg = intent.get("ospf") or {}
if ospf_cfg.get("enabled"):
    OspfProcess(dev, router_id=ospf_cfg.get("router_id"), ...)
```

Add your block next to the existing ones (OSPF/IS-IS/BGP/VRRP/MPLS/VRF/
VXLAN are all there, in that order). Also document the new `intent` shape
in `netlab.py`'s module docstring (the big comment block at the top listing
every recognized `intent` key) — that docstring is the only reference for
what a topology's `intent` field accepts.

`netlab.py` is a CODEOWNERS-guarded choke-point (see `CONTRIBUTING.md`) —
expect review from the area owner on any PR touching it.

## 4. Design-rationale precedent: bespoke vs. shared machinery

`sr.py` (Segment Routing) chose to flood its own small `SrSidAdvert`
LSA-style message riding the sibling `LdpProcess`'s adjacency table, rather
than extending a shared flooding/LSA tuple across protocols. The reasoning
documented in its module docstring: SR's only real differentiator over LDP
is formula-based label imposition (`srgb_base + node_sid`), so it rides
LDP's already-discovered adjacencies (`ldp.adj`) instead of running its own
hello — a deliberate, named simplification (see its `# ponytail:` comments)
rather than an oversight. When your protocol could plausibly share
machinery with an existing one, look at whether riding that existing
process (like SR rides LDP) is cheaper and more honest than building
independent adjacency/flooding logic — but say so explicitly in a
docstring/comment either way, so the next reader doesn't have to reverse
the decision from the diff.

## 5. Test pattern

See [`TESTING.md`](TESTING.md) for the full rules; the short version for a
new protocol module:

- One `test_<protocol>.py` file, using `Network(seed=<fixed>)` +
  `net.add_device(Router(...))` + your process, same shape as
  `test_bgp_fsm.py`'s `_pair()` helper.
- Cover every state transition your FSM can make, not just the happy path
  to whatever the "working" end state is.
- If the protocol has timers, add at least one test that counts **pending
  scheduler events** for that timer kind (the
  `_pending_connect_retry_timers`-style helper in `test_bgp_fsm.py`) across
  a reset/retry scenario — asserting on the final state or a transitions
  list will not catch a leaked duplicate timer chain.
- Add a `test_bgp_fsm_replay_determinism`-style test: build the same
  topology twice, run both to the same horizon, assert
  `net1.ledger.seq == net2.ledger.seq` and `net1.ledger.hash() ==
  net2.ledger.hash()`.
