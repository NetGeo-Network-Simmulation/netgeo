# NetGeo — Architecture

Orientation for new contributors. This is the map; `CONTRIBUTING.md` §"Kode
ada di mana" is the choke-point warning; the code is the ground truth.

## What NetGeo is

A self-hosted network simulation / planning / digital-twin platform.
FastAPI (async) backend, React 18 + TypeScript frontend, PostgreSQL for
persistence (in-memory store is the dev/test default — see
`backend/README.md` §6). The backend embeds its own discrete-event
simulation engine; it does not shell out to a separate service.

```
React frontend (frontend/src/)
        │  REST + WebSocket (frontend/src/api/client.ts)
        ▼
FastAPI app (backend/app/)
        │  app/services/* → engine/*   (one direction only, engine never imports app)
        ▼
engine/  (backend/engine/) — TWO LIVE execution paths + one dead one
```

## Three execution paths, not two

The engine directory holds two independent systems that happen to share the
`backend/engine/` package. A third, `emul`, is wired into the data model but
has no caller. Read this section before touching either.

### 1. `/simulate` — the flow engine (live, being phased out)

`POST /api/simulate` → `backend/app/services/sim.py` → `engine.NetworkModel`
/ `engine.NodeRuntime` (`backend/engine/model.py`, `backend/engine/runtime.py`).
A `networkx`-backed graph model, shortest-path forwarding, loss/MTU/TTL —
no protocol state machines. Deterministic via the same `(time, type, seq)`
event queue described below (`backend/engine/events.py`,
`backend/engine/scheduler.py` — this scheduler is the one both engines
share).

This is the older of the two engines and it is **being retired** — it
predates `netstack` and doesn't model real protocol behavior. It is still
live (tests pass, the endpoint is called by the frontend's realtime
simulation view), and the fate of its realtime-animation UI feature is an
**open decision**, not a settled one. Don't assume it is scheduled for
deletion in any particular slice; don't build new features on it either.

### 2. `/lab` — the packet-realistic netstack (live, the future)

`POST/GET /api/lab/*` → `backend/app/services/netlab.py` (the
`LabManager`/`Lab` classes) → `engine.netstack.Network`
(`backend/engine/netstack/`). This is a from-scratch packet-level stack:
real `Device`/`Interface`/frame objects, L2 (MAC learning, VLANs, STP,
LAG/LACP), L3 (routing, NAT, ACLs, DHCP, DNS), and full protocol processes
— OSPF, IS-IS, BGP (RFC 4271 §8 six-state FSM), MPLS/LDP, Segment Routing,
VRRP, VXLAN/EVPN. It backs grading, `/seek` time-travel, packet capture, and
the live device console (`/ws/console/{node_id}`). See
[`ENGINE-GUIDE.md`](ENGINE-GUIDE.md) for how it works.

This is where new protocol and forwarding work goes. See
[`ADDING-A-PROTOCOL.md`](ADDING-A-PROTOCOL.md).

### 3. `emul` — NOS emulation (data-model field exists, zero callers)

`backend/app/models/schemas.py` defines `NodeMode` with a `emul` value
(`NodeMode.emul`, alongside `sim`), and `backend/engine/emulation/adaptor.py`
defines an `EmulationAdaptor` ABC + `NullEmulationAdaptor` no-op fallback.
`engine/simulation.py` (the `/simulate`-path `Simulation` class) constructs a
`NullEmulationAdaptor` by default and can accept a real one via its
constructor — but nothing in `app/` ever passes one, and nothing outside
`engine/emulation/` and `engine/simulation.py` references
`EmulationAdaptor` at all. There is no route, no service, no UI control that
selects `emul` mode and does anything with it. **This is dead code today,
not a hidden live path** — verify with
`grep -rn EmulationAdaptor backend/ --include=*.py` before trusting any
older doc (including `backend/engine/README.md`'s own diagram) that implies
otherwise.

## Why two real engines, on purpose

This is a deliberate design, not accidental duplication:

- **`/lab`'s netstack** is deterministic Python simulation — cheap, fast,
  reproducible, no external dependencies. It is kept **permanently**: it is
  what makes `/seek` (rebuild + replay the journal to any event) and
  automated grading possible at all, neither of which is achievable against
  a live, non-replayable process.
- A **real NOS emulation mode** (FRR/OVS containers, see the `emul` field
  above) is planned as a *second, explicitly-chosen* mode for when a
  contributor or student needs to see genuine vendor/NOS behavior rather
  than a model of it — chosen per-node, not a replacement for the
  simulation path.

The trade-off is realism vs. cost/determinism, and the project's answer is
"offer both, pick per use case" rather than picking one. What's missing
today is the actual emulation backend behind `EmulationAdaptor` — that's
why it's dead code rather than a second live path.

## Where the two known-misleading docs fit

`backend/engine/README.md` and `backend/README.md` describe `model.py` /
`runtime.py` as the primary engine and list OSPF/BGP/IS-IS as roadmap items.
Both are wrong today — those protocols have been live in
`engine/netstack/protocols/` for a long time. They've been corrected
alongside this document (see their own top-of-file notes) rather than left
as a trap for new readers.

## Where to go next

- File-level map + choke-points (`netlab.py`, `frontend/src/api/client.ts`):
  `CONTRIBUTING.md` §"Kode ada di mana" — don't duplicate it here.
- Netstack internals, determinism contract: [`ENGINE-GUIDE.md`](ENGINE-GUIDE.md)
- Adding a protocol: [`ADDING-A-PROTOCOL.md`](ADDING-A-PROTOCOL.md)
- Running and writing tests: [`TESTING.md`](TESTING.md)
- Frontend structure: [`FRONTEND-GUIDE.md`](FRONTEND-GUIDE.md)
