# NetGeo — Backend

Backend NetGeo: **FastAPI (async) + engine simulasi discrete-event**.
Mengimplementasikan permukaan REST/WebSocket pada MASTER_SPEC §4 dan
men-*generate* config multi-vendor (ForgeOS: satu intent → banyak NOS, §5).

Selaras gaya proyek author (secureops/storagehub): app-factory FastAPI,
envelope respons `{success,data,message}` / `{success,error}`, settings via
`pydantic-settings`, hierarki `AppException`.

---

## 1. Arsitektur (diagram modul as-text)

```
                         HTTP / WebSocket (React frontend)
                                      │
┌─────────────────────────────────────────────────────────────────────┐
│  app/  (lapisan web — FastAPI, async)                                 │
│                                                                       │
│  main.py ── create_app() ── CORS ── error handlers ── routers         │
│     │                                                                 │
│     ├── api/                 (REST §4, mirror frontend client.ts)     │
│     │   ├── projects.py      GET/POST /projects, /{id}/topology       │
│     │   ├── nodes.py         POST/GET/PATCH/DELETE /nodes             │
│     │   ├── links.py         POST/PATCH/DELETE /links                 │
│     │   ├── scenarios.py     GET /scenarios?project_id                │
│     │   ├── simulate.py      POST /simulate (+ pause/resume/step/stop)│
│     │   ├── configs.py       POST /configs/generate, GET /configs     │
│     │   ├── ws.py            WS /ws/topology, /ws/console/{node_id}   │
│     │   └── deps.py          repo() + translate_not_found()           │
│     │                                                                 │
│     ├── services/            (logika domain)                          │
│     │   ├── sim.py           Topology → engine.NetworkModel → run     │
│     │   └── configgen.py     Node(+intent) → Jinja2 → config vendor   │
│     │                                                                 │
│     ├── store/               (persistensi — Dependency Inversion)     │
│     │   ├── memory.py        MemoryRepository  ← DEFAULT (dev/test)   │
│     │   └── postgres.py      PostgresRepository (sketsa, prod)        │
│     │                                                                 │
│     ├── models/schemas.py    Pydantic v2 = kontrak §4                 │
│     ├── core/                config, logging, errors                  │
│     ├── exceptions/          AppException + SimulationError           │
│     └── utils/               response envelope, ids (uuid4)           │
└───────────────────────────────────┬───────────────────────────────────┘
                                     │  (services memanggil engine — satu arah)
┌───────────────────────────────────▼───────────────────────────────────┐
│  engine/  (kernel simulasi — TANPA dependensi web/DB)                  │
│                                                                        │
│  simulation.py  Simulation ── Scheduler ── EventQueue (heap, deterministik) │
│  model.py       NetworkModel (networkx) ── NodeModel/LinkModel/InterfaceModel│
│  runtime.py     NodeRuntime (forwarding shortest-path, loss, MTU, TTL) │
│  packet.py      Packet                                                 │
│  netstack/protocols/  OSPF/BGP/IS-IS/MPLS/VRRP proses per-Router       │
│  emulation/     EmulationAdaptor (ABC) + NullEmulationAdaptor          │
│                 → containerlab/Docker untuk node mode="emul"           │
└────────────────────────────────────────────────────────────────────────┘
```

Prinsip kunci: **engine tidak meng-import app**. Aliran dependensi hanya satu
arah (`app/services → engine`), sehingga kernel bisa diuji & dipakai ulang tanpa
FastAPI/Postgres. Detail desain engine: [`engine/README.md`](engine/README.md).

---

## 2. Tech stack

- **Python 3.12** + **FastAPI** (async), **Pydantic v2**, **Uvicorn**
- **networkx** — representasi graph topologi di engine
- **Jinja2** (SandboxedEnvironment) — template config per-vendor (`config-gen/`)
- **PostgreSQL** (`asyncpg` + SQLAlchemy 2.0 async) — target produksi
- **Redis** — state realtime / pub-sub fan-out WS / job queue (roadmap)
- Test: **pytest** + **pytest-asyncio** + **httpx**

> Default store **in-memory** agar backend langsung *import-able & runnable*
> tanpa Postgres/Redis hidup (gaya smoke-test secureops/storagehub).

---

## 3. Menjalankan

```bash
cd backend
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # opsional; default sudah jalan

# server dev
uvicorn app.main:app --reload --port 8000
#   Swagger : http://localhost:8000/docs
#   Health  : http://localhost:8000/api/health
```

### Smoke test (import OK)

```bash
python -c "from app.main import app; print('netgeo backend imports OK')"
```

### Test suite

```bash
pytest            # 20 test: engine determinisme, config-gen multi-vendor, API
```

---

## 4. Alur request → engine / config-gen

**Simulasi** (`POST /api/simulate`)

```
client → api/simulate.start_simulation
       → store.topology(project_id)                 # ambil Topology (§4)
       → services/sim.run_once(topo, seed, horizon)
            → build_model(topo)                      # Topology → NetworkModel
                                                     #   Mbps→bit/s, ms→s
            → Simulation(model, seed).inject(probe)  # paket uji antar host
            → sim.run()                              # drain EventQueue (DES)
       ← {state:"completed", result:{delivered,dropped,avg_latency_s,...}}
```
Determinisme: `EventQueue` mengurut `(time, type, seq)` dan semua keputusan
acak (loss) memakai `random.Random(seed)` — run dengan model+seed sama identik
(kontrak "verify in simulation before deploy", §5).

**Config-gen ForgeOS** (`POST /api/configs/generate`)

```
client → api/configs.generate_config
       → store.get_node(node_id)
       → services/configgen.build_artifact(node, vendor)
            → vendor_for(node, requested)            # default: node.nos
            → _context(node)                         # node + intent → konteks rata
            → Jinja2 SandboxedEnvironment.render()   # config-gen/templates/<vendor>.j2
       → store.add_config(artifact)                  # riwayat append-only
       → store.update_node(config_ref=artifact.id)
       ← ConfigArtifact{vendor, format, content, generated_at}
```
Satu `node.intent` (mis. BGP/OSPF/EVPN) bisa dirender ke `ios/junos/eos/routeros/
vyos/frr/forgeos` — itulah "satu intent → banyak config vendor".

---

## 5. Permukaan API (§4)

| Method | Path | Keterangan |
|---|---|---|
| GET/POST | `/api/projects` | daftar / buat project |
| GET | `/api/projects/{id}` | detail project |
| GET | `/api/projects/{id}/topology` | project + nodes + links |
| POST | `/api/nodes` | buat node (+ interfaces) |
| GET/PATCH/DELETE | `/api/nodes/{id}` | detail / ubah / hapus (cascade link) |
| POST | `/api/links` | buat link |
| PATCH/DELETE | `/api/links/{id}` | ubah / hapus |
| GET | `/api/scenarios?project_id=` | daftar scenario |
| POST | `/api/simulate` | jalankan run; `+ /{id}/pause\|resume\|step\|stop` |
| POST | `/api/configs/generate` | generate config (multi-vendor / ForgeOS) |
| GET | `/api/configs?node_id=` | riwayat artifact node |
| WS | `/ws/topology` | status node/link, telemetry realtime |
| WS | `/ws/console/{node_id}` | stream konsol node emulasi |

Sukses mengembalikan model §4 langsung; error memakai envelope
`{"success":false,"error":{"code","message"}}`.

---

## 6. Lapisan store & jalan ke produksi

API hanya bergantung pada *surface* `MemoryRepository` (Dependency Inversion),
jadi pindah ke Postgres **tidak mengubah satu pun handler API**:

- `store/memory.py` — default, async-lock, untuk dev & test.
- `store/postgres.py` — **sketsa** SQLAlchemy 2.0 async (asyncpg) yang
  meng-implementasi surface yang sama. DDL kanonik (index/constraint) dimiliki
  agent `db-devops-architect` di `infra/db/schema.sql`. Aktifkan dengan
  mengganti `app/store/get_repo()`.

---

## 7. Kebutuhan lintas-area

Lihat [`NEEDS.md`](NEEDS.md): template `config-gen/` (dimiliki `network-engineer`),
skema DB `infra/`, dan kontrak tipe frontend.
