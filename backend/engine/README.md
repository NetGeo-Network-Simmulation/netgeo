# NetGeo — Engine (`backend/engine/`)

> **Scope note:** this file documents only the `model.py`/`runtime.py`/
> `emulation/` package — the engine behind `POST /api/simulate`. It is
> **not** where OSPF/IS-IS/BGP/MPLS/VRRP/VXLAN live — those are fully
> implemented, live protocols in the separate `engine/netstack/` package
> (behind `/api/lab`), documented in
> [`../../dev-docs/ENGINE-GUIDE.md`](../../dev-docs/ENGINE-GUIDE.md). See
> [`../../dev-docs/ARCHITECTURE.md`](../../dev-docs/ARCHITECTURE.md) for how
> the two packages (plus the still-uncalled `emulation/` adaptor) fit
> together — read that first if you're new here.

Kernel **discrete-event simulation (DES)** used by the `/simulate` flow
engine: a `networkx`-backed topology graph with shortest-path forwarding,
loss/MTU/TTL modeling, no protocol state machines. `emulation/` defines an
`EmulationAdaptor` ABC for a future NOS-emulation backend
(containerlab/Docker) — it exists in the type system (`NodeMode.emul`) but
has **no live caller today**; treat any diagram or claim below about
sim↔emul switching as the target shape, not current behavior.

> Engine **tidak meng-import lapisan web/DB**. Ia bekerja atas `NetworkModel`
> in-memory dan menghasilkan hasil/metrik JSON-able. Inilah yang membuatnya
> bisa diuji unit dan di-embed tanpa FastAPI/Postgres.

---

## 1. Model saat ini: sim only (emul belum berkabel)

```
                       NetworkModel (graph topologi, networkx)
                                     │
                              node mode="sim"
                                     │
                               NodeRuntime
                     (control+data plane Python)
                forwarding shortest-path, loss/MTU/TTL, RIB protokol
                                     │
                                LinkModel
                    (delay propagasi + serialization + loss)
```

`NodeMode.emul` is a valid value on the data model
(`app/models/schemas.py`) and `Simulation.__init__` accepts an `adaptor:
EmulationAdaptor | None` — but nothing in `app/` ever constructs or passes
a real one, so every run today is effectively all-`sim`. The
`NullEmulationAdaptor` fallback (`emulation/adaptor.py`) is what's active
by default; wiring a real containerlab/Docker adaptor through and having
something call it is unbuilt work, not a bug.

---

## 2. Komponen

| Modul | Tanggung jawab |
|---|---|
| `events.py` | `SimEvent`, `EventType` (prioritas), `EventQueue` (min-heap) — shared kernel, also used by `netstack/` |
| `scheduler.py` | `Scheduler` — drain queue, majukan jam, dispatch handler — shared kernel |
| `model.py` | `NetworkModel` (networkx) + `Node/Link/InterfaceModel` |
| `packet.py` | `Packet` — unit data-plane (TTL, ukuran, jalur) |
| `runtime.py` | `NodeRuntime` — forwarding default (shortest-path, drop-aware) |
| `emulation/` | `EmulationAdaptor` (ABC) + `NullEmulationAdaptor` — no live caller yet |
| `simulation.py` | `Simulation` — perekat: inject, run, run_realtime, snapshot |
| `netstack/` | separate, live packet-realistic engine — see `dev-docs/ENGINE-GUIDE.md`, not this file |

---

## 3. Determinisme (wajib)

Reproducibility adalah kontrak, shared by this engine and `netstack/`
(same `events.py`/`scheduler.py` kernel):

1. `EventQueue` mengurut tuple stabil **`(time, type, seq)`**. `seq` monotonik
   sebagai tie-breaker → event di waktu sama selalu pop dalam urutan masuk.
2. `EventType` rendah = prioritas tinggi: event control-plane (link up/down)
   diproses sebelum data-plane pada timestamp yang sama.
3. Semua keputusan acak (mis. drop `loss`) memakai satu `random.Random(seed)`.

Hasil: model + seed sama → metrik identik bit-for-bit (diuji di
`tests/test_engine.py::test_run_is_deterministic`).

---

## 4. Strategi skalabilitas (menuju ribuan node)

Arsitektur sekarang adalah baseline *single-process, synchronous*. Jalur skala:

### a. Granularitas adaptif (packet ↔ flow)
Default packet-level untuk akurasi. Untuk topologi sangat besar, beralih ke
**flow-level**: hitung satu event "flow" (rate × durasi) alih-alih ribuan paket.
Mengubah O(paket) → O(flow), kunci untuk skala backbone/ISP.

### b. Batching event-loop (realtime)
`Simulation.run_realtime(batch=...)` men-drain queue dalam *batch* lalu
`await asyncio.sleep(0)` → event-loop FastAPI tetap responsif saat streaming
telemetry ke `/ws/topology`. `realtime_factor` memacu run ke wall-clock untuk
tampilan "live".

### c. Sharding topologi (horizontal)
Partisi graph (mis. `networkx`/METIS atau per-area OSPF) ke beberapa **worker
DES**. Event lintas-shard dilewatkan sebagai pesan ber-timestamp; sinkronisasi
jam memakai *conservative* (lookahead = delay link min antar-shard) atau
*optimistic* (Time Warp) untuk paralelisme lebih tinggi. Pemetaan
shard→worker dikoordinasikan via Redis (job queue / state realtime).

### d. Pemisahan compute vs serving
Run berat dijalankan oleh **run-manager** terpisah (proses/worker), bukan di
request handler. API hanya enqueue job + relay hasil. Checkpoint/resume
belum diimplementasikan — `Simulation` tidak menyerialkan antrean/RNG saat ini.

### e. Hemat alokasi pada hot-path
`SimEvent`/`Packet`/model memakai `dataclass(slots=True)`; lookup id via dict
flat; adjacency via `networkx` O(1). Mengurangi overhead GC per-event.

---

## 5. Memakai engine secara langsung

```python
from engine import (NetworkModel, NodeModel, InterfaceModel, LinkModel,
                    Packet, Simulation, SimulationConfig)

m = NetworkModel()
m.add_node(NodeModel(id="a", name="a",
    interfaces=[InterfaceModel(id="a0", node_id="a", name="e0")]))
m.add_node(NodeModel(id="b", name="b",
    interfaces=[InterfaceModel(id="b0", node_id="b", name="e0")]))
m.add_link(LinkModel(id="l1", a_iface="a0", b_iface="b0", delay=0.002))

sim = Simulation(m, SimulationConfig(seed=1))
sim.inject(Packet(src="a", dst="b"))
print(sim.run().as_dict())   # {'delivered': 1, 'dropped': 0, ...}
```

Integrasi dengan lapisan web: `app/services/sim.py` (`build_model` + `run_once`).

Untuk protokol dinamis (OSPF/BGP/IS-IS/MPLS/VRRP/VXLAN) dan simulasi paket
lengkap, pakai `engine.netstack` lewat `app/services/netlab.py` — lihat
`dev-docs/ENGINE-GUIDE.md` dan `dev-docs/ADDING-A-PROTOCOL.md`.

---

## 6. Roadmap engine

- Adaptor `containerlab` konkret di `emulation/`, plus something in `app/`
  that actually constructs and passes it — today `EmulationAdaptor` has no
  caller outside its own package.
- Flow-level model + sharding multi-worker (Redis-backed run-manager).
