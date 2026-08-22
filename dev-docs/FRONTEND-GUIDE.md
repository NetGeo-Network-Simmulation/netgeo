# NetGeo — Frontend Guide (`frontend/src/`)

React 18 + TypeScript, Vite, Zustand, Tailwind CSS. This is orientation
only — `CONTRIBUTING.md` §"Kode ada di mana" already covers the
`client.ts` choke-point warning; don't treat this as a second copy of that
rule, just the fuller map around it.

## Structure

```
frontend/src/
├── api/          client.ts (REST), ws.ts (WebSocket), envelope.ts, token.ts, types.ts
├── store/        zustand stores — one per domain (see below)
├── components/   one folder per workspace + shared UI
├── services/     browser-side compute: signalSim.ts (RF), geocodeService.ts, elevation.ts, osmService.ts
├── hooks/, lib/, config/, theme/, data/
```

**Stores** (`store/*.ts`), one per domain: `topologyStore`, `mapStore`,
`labStore`, `rfStore`, `fiberStore`, `eduStore`, `nosStore`, `authStore`,
`collabStore`, `iconStore`, `topoUiStore`, `uiStore`. No cross-store
framework — each is an independent `zustand` `create()`.

**Components** (`components/*`), one folder per workspace: `canvas`
(topology graph), `map` (GIS/RF map), `rack`, `rf`, `fiber`, `plant`,
`lab`, `edu`, `twin`, `problems`, `projects`, `reports`, `config`, `icons`,
`shell` (app chrome), `ui` (shared primitives) — plus top-level panels
(`ConsolePanel.tsx`, `PropertiesPanel.tsx`, `SimulationBar.tsx`, …) that
don't belong to one workspace.

## Choke-points

Two files are single points of contact between the frontend and everything
else — expect owner review on any PR touching them (`CODEOWNERS`):

- **`api/client.ts`** (1,075 lines) — the only API client. Every backend
  endpoint the frontend calls has a corresponding function here; a new
  endpoint needs a new line here before any component can use it.
- **`components/map/MapView.tsx`** (2,406 lines) — the map rendering
  surface (MapLibre GL — see below). Large because it owns the interaction
  layer for both device placement (`deployAt()`) and RF planning
  (`MapDevice`, next section) on the same canvas.

## Map stack: MapLibre GL, not Leaflet

`frontend/package.json` pulls `maplibre-gl`; there is no `leaflet` or
`react-leaflet` dependency. If you find a doc or comment describing
Leaflet-based map code, it's stale — the migration to MapLibre already
happened.

## `MapDevice` vs. `Node` — intentional duplication, not debt

`store/mapStore.ts` (lines 11–29) defines `MapDevice`: a client-only,
session-scoped RF planning sandbox object, never persisted, lost on
refresh. Read the comment at the top of that file in full before touching
it — it explicitly states this is a **boundary, not an oversight**
(v1.2.52): the canonical way to place a real network device is
`lib/mapDeploy.ts`'s `deployAt()`, which creates a backend `Node` (with
`lat`/`lon`/`site_id`) that the topology canvas, rack view, and simulation
engine all see. `MapDevice` survives *only* because the RF Planning
workspace (`RfWorkspace`/`RfLinkBar`/`RfAnalysisPanel`) still computes
PtP/PtMP/coverage against it instead of against real `Node`s — a named,
deliberate trade-off (two device populations on one map), with an explicit
not-yet-started follow-up to unify them onto `Node`/`Radio` data.

**Do not attempt to delete `MapDevice` or "fix" this duplication** as a
drive-by cleanup — an earlier planning document proposed exactly that and
it was not carried out; the maintainer's actual decision, visible in the
code comment, is to keep it until the RF-unification follow-up lands as
its own scoped slice. If you're working on RF planning and want to pick up
that follow-up, treat it as a deliberate, separate PR — not a side effect
of an unrelated change.
