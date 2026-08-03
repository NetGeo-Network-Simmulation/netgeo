# NetGeo — Engine Simulasi

Kernel **discrete-event simulation (DES)** + lapisan **adaptor emulasi** untuk
NetGeo. Mendukung visi MASTER_SPEC §1: *ribuan node* dengan model hybrid —
**sim** ringan (deterministik, cepat) ↔ **emul** akurat (NOS nyata via
containerlab/Docker), dapat beralih per-node.

> Engine **tidak meng-import lapisan web/DB**. Ia bekerja atas `NetworkModel`
> in-memory dan menghasilkan hasil/metrik JSON-able. Inilah yang membuatnya
> bisa diuji unit dan di-embed tanpa FastAPI/Postgres.

---

## 1. Model hybrid sim + emul

```
                       NetworkModel (graph topologi, networkx)
                                     │
              ┌──────────────────────┴───────────────────────┐
              │                                               │
        node mode="sim"                                 node mode="emul"
              │                                               │
        NodeRuntime                                   EmulationAdaptor (ABC)
   (control+data plane Python)                  ┌────────────┴───────────┐
   forwarding shortest-path,              containerlab / Docker / Podman │
   loss/MTU/TTL, RIB protokol            (NOS nyata: IOS/Junos/FRR/...)  │
              │                                               │
              └──────────────► LinkModel ◄────────────────────┘
                    (delay propagasi + serialization + loss)
                    titik temu sim↔emul = batas link (veth shim)
```

- **Kernel DES selalu memiliki jam virtual & model link.** Baik node sim maupun
  emul, propagasi antar-node dimodelkan oleh `LinkModel`
  (`transit_delay = serialization(size, bandwidth) + delay`).
- **Node sim**: perilaku berjalan sebagai `NodeRuntime` Python — murah, ribuan
  node muat dalam satu proses.
- **Node emul**: internal node berjalan di container nyata; engine meng-*inject*
  paket yang menyeberang batas sim↔emul ke veth container (dan sebaliknya).
  Kontraknya `EmulationAdaptor` — engine **tidak** meng-import Docker langsung.
- `NullEmulationAdaptor`: fallback no-op bila runtime container tak tersedia
  (CI / run murni-sim). Node `emul` diperlakukan sebagai `sim`, status jelas
  dilaporkan ke UI ("emulation unavailable").

---

## 2. Komponen

| Modul | Tanggung jawab |
|---|---|
| `events.py` | `SimEvent`, `EventType` (prioritas), `EventQueue` (min-heap) |
| `scheduler.py` | `Scheduler` — drain queue, majukan jam, dispatch handler |
| `model.py` | `NetworkModel` (networkx) + `Node/Link/InterfaceModel` (= §4) |
| `packet.py` | `Packet` — unit data-plane (TTL, ukuran, jalur) |
| `runtime.py` | `NodeRuntime` — forwarding default (shortest-path, drop-aware) |
| `protocols/` | subclass `NodeRuntime` per protokol (static, lalu OSPF/BGP) |
| `emulation/` | `EmulationAdaptor` (ABC) + `NullEmulationAdaptor` |
| `simulation.py` | `Simulation` — perekat: inject, run, run_realtime, snapshot |

---

## 3. Determinisme (wajib)

Reproducibility adalah kontrak (§1, dan §5 "verify before deploy"):

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

---

## 6. Roadmap engine

- Protokol dinamis: OSPFv3, IS-IS, BGP (subclass `NodeRuntime`, populate RIB
  dari event TIMER/PACKET_RX). Spec protokol dimiliki `network-engineer`
  (`network/protocols/`) — lihat `../NEEDS.md`.
- Adaptor `containerlab` konkret di `emulation/`.
- Flow-level model + sharding multi-worker (Redis-backed run-manager).
