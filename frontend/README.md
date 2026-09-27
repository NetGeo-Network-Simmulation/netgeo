# NetGeo — Frontend (UI Desktop-class)

UI **desktop-class** ala macOS / Windows 11 untuk platform simulasi & emulasi
jaringan **NetGeo**. Window manager + dock + glassmorphism, canvas topologi
real-time (React Flow), panel properti, terminal console, dan config viewer —
tetap **ringan** (target < 200 MB RAM idle, mulus di Linux).

Area ini milik agent `frontend-architecture-advisor`. Hanya menulis di
`frontend/`. Kontrak data & endpoint mengikuti `MASTER_SPEC.md` (§4).

---

## 1. Tech Stack

| Lapisan | Pilihan |
|---|---|
| Framework | **React 18 + TypeScript** (strict) |
| Build | **Vite 5** (ESM, code-split, proxy dev) |
| Styling | **Tailwind 3.4** + design tokens glassmorphism, `darkMode: 'class'` |
| Canvas | **@xyflow/react (React Flow 12)** — drag-drop, zoom/pan, minimap |
| Server-state | **TanStack Query 5** (cache/dedupe REST) |
| Client-state | **Zustand** (graph, window manager, UI) |
| Realtime | **WebSocket native** (auto-reconnect, tanpa socket.io) |
| HTTP | **axios** (interceptor error ternormalisasi) |
| Ikon | **lucide-react** |

Konsisten dengan proyek author (secureops / storagehub): aksen Apple-blue
`#007AFF`, tipografi Inter, surface glass `backdrop-blur`.

---

## 2. Cara menjalankan (dev)

```bash
cd frontend
npm install
npm run dev          # Vite di http://localhost:5180
```

Vite mem-*proxy* `/api` dan `/ws` ke backend FastAPI (default
`http://localhost:8000`, lihat `vite.config.ts`). Jadi browser cukup bicara ke
**satu origin** → tidak ada masalah CORS di dev dan paritas dengan prod.

Override backend dev proxy via `.env.local` (lihat `.env.example`):
`VITE_BACKEND_ORIGIN`. Untuk server terpisah, pilih Remote / online di Settings → Runtime.

Skrip lain: `npm run build` (tsc + vite build), `npm run preview`,
`npm run typecheck`, `npm run lint`.

---

## 3. Arsitektur Komponen

Pola: **shell desktop** (OS-like) yang menjadi tuan rumah **app windows**.
Setiap "aplikasi" adalah satu jendela yang bisa dibuka/ditutup dari Dock.

```
src/
├── main.tsx              # entry: QueryClientProvider + globals.css
├── App.tsx               # komposisi shell: MenuBar + WindowHost + Dock, bootstrap project/topology/WS
│
├── components/
│   ├── shell/            # OS shell
│   │   ├── MenuBar.tsx       # bar atas: mark, project, status WS, SimulationBar, tema, jam
│   │   ├── Dock.tsx          # peluncur app (toggle window) + indikator running
│   │   ├── Window.tsx        # chrome jendela glass: traffic-lights, drag, min/max, focus, Esc
│   │   └── WindowHost.tsx    # compositor: map WindowKind -> body komponen
│   │
│   ├── canvas/
│   │   ├── TopologyCanvas.tsx # React Flow: drag-drop node, link, zoom/pan, minimap
│   │   └── DeviceNode.tsx     # node renderer custom (memoized) per kind/status
│   │
│   ├── NodePalette.tsx        # device palette draggable (HTML5 DnD)
│   ├── PropertiesPanel.tsx    # inspektur node terpilih (name/NOS/mode/interfaces)
│   ├── ConsolePanel.tsx       # terminal device via /ws/console/{node_id}
│   ├── ConfigViewer.tsx       # artefak config (ForgeOS: 1 intent -> banyak NOS)
│   ├── ScenariosPanel.tsx     # daftar skenario per project
│   └── SimulationBar.tsx      # transport play/pause/step/stop + speed
│
├── store/                # Zustand
│   ├── topologyStore.ts      # graph (Map nodes/links), seleksi, reducer event WS
│   ├── windowStore.ts        # window manager: z-order, focus, geometri, min/max
│   └── uiStore.ts            # tema (persist), state simulasi, project aktif
│
├── api/
│   ├── types.ts              # model data = MASTER_SPEC §4 (sumber kebenaran tipe)
│   ├── client.ts             # REST: projects/nodes/links/scenarios/simulate/configs
│   └── ws.ts                 # RealtimeChannel: auto-reconnect, heartbeat, typed
│
├── hooks/
│   ├── useTopologyChannel.ts # bind /ws/topology -> topologyStore
│   └── useConsoleChannel.ts  # bind /ws/console/{id}, ring-buffer output
│
├── data/deviceCatalog.ts     # template perangkat untuk palette (seed UI)
├── theme/
│   ├── tokens.ts             # design tokens glass + warna node + applyTheme()
│   └── globals.css           # Tailwind layers, util .glass, scrollbar, RF overrides
└── lib/cn.ts                 # class-name joiner (clsx)
```

### Alur data

```
REST (TanStack Query)  ─┐
                        ├─▶ Zustand stores ─▶ komponen (render)
WebSocket (channels) ───┘        ▲
        edit lokal/optimistic ───┘ (PATCH balik ke REST)
```

- **Server = sumber kebenaran.** Snapshot awal via REST
  (`GET /projects/{id}/topology`), lalu `/ws/topology` menjaga kesegaran
  (status node/link, `sim.tick`).
- **Edit optimistik:** create node/link & drag posisi langsung diterapkan ke
  store untuk responsif, lalu di-`PATCH`/`POST` ke backend; id sementara
  (`tmp-…`) diganti id asli saat respons tiba.
- **State terbagi tiga kategori** sesuai sifatnya: server-state (Query),
  graph/window/ui-state (Zustand), URL-state (belum dipakai — kandidat untuk
  deep-link project/seleksi).

---

## 4. State Management — kenapa begini

| Jenis state | Tool | Alasan |
|---|---|---|
| Data dari API | TanStack Query | cache, dedupe, retry, invalidation gratis |
| Graph topologi | Zustand (`Map`) | ribuan node → update O(1), hindari scan array tiap tick |
| Window manager | Zustand | z-order/focus/geometri murni client, sering berubah |
| Tema / sim / project | Zustand + localStorage (tema) | global, ringan, persist selektif |

Graph disimpan sebagai `Map<id, …>` (bukan array) supaya update per-node/link
tidak memicu rekonstruksi seluruh list — krusial untuk target skala besar.

---

## 5. Strategi Ringan & Performa (target < 200 MB idle)

1. **Code-splitting manual** (`vite.config.ts`): `react`, `flow`
   (React Flow), `query` dipisah dari bundle shell → first paint window manager
   cepat, canvas dimuat saat dibutuhkan.
2. **`onlyRenderVisibleElements`** di React Flow + node renderer **memoized**
   → hanya node yang berubah & terlihat yang re-render.
3. **Realtime, bukan polling.** Satu WS untuk topologi (+ per-node console),
   heartbeat membuang koneksi mati. Tidak ada interval polling yang membakar
   CPU/RAM saat idle.
4. **Move flood-control.** Drag node commit lokal tiap frame (via rAF) tapi
   **persist ke backend hanya saat drag-stop**, bukan tiap pixel.
5. **Ring-buffer console** (cap 2.000 baris) → sesi panjang tidak menggelembung
   memori.
6. **Terminal ringan** (`<pre>` mono), bukan xterm.js penuh; highlighter config
   sengaja ditunda di balik dynamic import bila nanti perlu — menjaga bundle.
7. **Glass via CSS** (`backdrop-filter`), bukan gambar/canvas blur → murah,
   GPU-composited.

---

## 6. Glassmorphism & Theming

- `theme/tokens.ts` = satu sumber: recipe glass per mode, gradient desktop,
  warna per **kind** node (router/switch/host/ap/olt/firewall/server), warna
  status link, dan `applyTheme()` yang menulis CSS custom properties + toggle
  kelas `dark`.
- Util `.glass` / `.glass-strong` di `globals.css` dipakai window chrome, dock,
  menu bar, dan panel.
- **Mode terang/gelap**: default mengikuti `prefers-color-scheme`, override
  persist di `localStorage`. Toggle di MenuBar.

---

## 7. Aksesibilitas & State UX

- Kontrol jendela = `<button>` ber-`aria-label`; jendela `role="dialog"`,
  **Esc** menutup jendela fokus; Dock pakai `aria-pressed`.
- `prefers-reduced-motion` dihormati (animasi dekoratif dinonaktifkan).
- Setiap panel data punya **loading / empty / error** sebagai state kelas satu
  (PropertiesPanel, ConfigViewer, ScenariosPanel, ConsolePanel).
- Fokus input console autofokus; scrollbar tipis ramah-glass (`.nf-scroll`).

---

## 8. Integrasi Backend (kontrak)

REST (`api/client.ts`) dan WS (`api/ws.ts`) mengikuti `MASTER_SPEC` §4:

```
GET    /api/projects, /api/projects/{id}, /api/projects/{id}/topology
POST   /api/nodes        PATCH /api/nodes/{id}     DELETE /api/nodes/{id}
POST   /api/links        PATCH /api/links/{id}     DELETE /api/links/{id}
GET    /api/scenarios?project_id=...
POST   /api/simulate (+ /pause /resume /step /stop)
POST   /api/configs/generate      GET /api/configs?node_id=...
WS     /ws/topology
WS     /ws/console/{node_id}
```

Tipe di `api/types.ts` **harus tetap sinkron** dengan schema Pydantic backend
(Node, Interface, Link, Project, Scenario, ConfigArtifact).

---

## 9. Status & Langkah Lanjut

Fondasi nyata & kohesif sudah ada (struktur, store, API, komponen kunci).
Logika berat sengaja placeholder di beberapa titik. Kandidat berikutnya:

- **Project picker** + URL-state (deep-link project & seleksi node).
- **Interface-aware linking**: dialog pilih port saat membuat link (kini
  memakai port bebas pertama / fallback node-id untuk edge optimistik).
- **i18n** (ID/EN) selaras secureops/storagehub.
- **PWA / opsi Tauri** untuk paket desktop ringan (MASTER_SPEC §2).
- **Tes**: Vitest + Testing Library untuk store & komponen kritis.
```
