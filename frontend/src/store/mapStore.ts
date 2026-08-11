/**
 * Map store — state for the satellite map view (UISP Design Center style).
 * Tracks devices placed on the real map (lat/lng), links between them,
 * the active tool, and the selected device.
 *
 * Signal simulation uses:
 *  - Free Space Path Loss (FSPL)
 *  - ITU-R P.838 rain attenuation
 *  - Line-of-sight / Fresnel zone check (async via Open Elevation API)
 *
 * BOUNDARY (UISP-parity slice, v1.2.52): `MapDevice` here is a client-only,
 * session-scoped RF *planning sandbox* — it is never persisted and is lost on
 * refresh. It is intentionally NOT the canonical way to place a network device
 * anymore. The canonical path is `lib/mapDeploy.ts` `deployAt()`, which creates
 * a real backend `Node` (with `lat`/`lon`/`site_id`) that the topology canvas,
 * rack view, and simulation engine all see. `MapDevice` survives only because
 * the RF Planning workspace (`RfWorkspace`/`RfLinkBar`/`RfAnalysisPanel`) still
 * computes PtP/PtMP/coverage against it, not against real `Node`s.
 *
 * FOLLOW-UP (not started, do not assume it's silently in progress): unifying
 * RF planning onto real `Node`/`Radio` data is tracked as "Slice A3 — satukan
 * endpoint RF ke Node backend" in docs/design/16-UISP-PARITY-PLAN.md (§7,
 * Fase A). It means changing `RfLinkBar.useRfEndpoints()`, `RfAnalysisPanel`,
 * `RfCoverageLayer.sites`, and `rfStore` to read `topoNodes` instead of
 * `mapStore.devices` — an M-sized slice, deliberately out of scope for the
 * v1.2.52 UISP-parity Site work that added this comment. Until A3 lands, this
 * duplication (two device populations on one map) is a known, named trade-off,
 * not an oversight. Do not add new general-map features on top of `MapDevice`;
 * new device placement belongs on the `Node` path (`deployAt()`).
 */
import { create } from 'zustand';
import type { LosStatus } from '@/services/signalSim';
import type { GeoResult } from '@/services/geocodeService';
import type { MapTileKey } from '@/config/mapTiles';
import { DEFAULT_TILE } from '@/config/mapTiles';
import { GIS_LAYERS } from '@/config/gisLayers';
import { wirelessApi } from '@/api/client';

/** Per-layer runtime state for the GIS layer tree (visibility + opacity). */
export interface GisLayerState {
  visible: boolean;
  opacity: number;
}

/** Seed the layer state map from the registry's declared defaults. */
function initialGisLayers(): Record<string, GisLayerState> {
  const out: Record<string, GisLayerState> = {};
  for (const l of GIS_LAYERS) {
    out[l.id] = { visible: l.defaultVisible, opacity: l.defaultOpacity };
  }
  return out;
}

export type MapTool = 'select' | 'ap' | 'cpe' | 'tower' | 'measure' | 'profile' | 'deploy' | 'site' | 'site-move';
export type MapDeviceKind = 'ap' | 'cpe' | 'tower';

/**
 * Elevation-profile tool state (Phase B1). The two picked endpoints, the fetched
 * terrain profile + LoS/Fresnel verdict (reused from the backend `los-check`
 * endpoint, which proxies the DEM provider), and request parameters.
 */
export interface ProfileSample {
  distance_m: number;
  elevation_m: number;
}

export interface ProfileData {
  points: ProfileSample[];
  totalDistanceM: number;
  losClear: boolean;
  fresnelClear: boolean;
  worstObstructionM: number;
  minClearanceRatio: number;
  /** F67: "flat_fallback" means the DEM provider was unreachable and every
   *  elevation was flattened to 0 m — the LoS/Fresnel verdict above is based
   *  on flat terrain, not real elevation data. */
  terrainSource: 'dem' | 'flat_fallback';
}

export interface MapDevice {
  id: string;
  name: string;
  kind: MapDeviceKind;
  lat: number;
  lng: number;
  txPower: number;     // dBm (e.g. 20)
  frequency: number;   // GHz (e.g. 5.8)
  range: number;       // meters — max coverage radius for the ring
  antennaHeight: number; // meters AGL (above ground level)
  ip: string;
}

export interface MapLink {
  id: string;
  fromId: string;
  toId: string;
  distance: number;    // meters
  rssi: number;        // dBm (FSPL only, instant)
  los: LosStatus;      // updated async
  rainDb: number;      // dB attenuation from rain
  obstructionDb: number; // dB attenuation from terrain
  fresnelM: number;    // first Fresnel zone radius at midpoint (metres)
}

/** FSPL-based RSSI estimate (no terrain/rain). */
export function calcRssi(txPower: number, distanceM: number, freqGhz: number): number {
  if (distanceM < 1) return txPower;
  const fHz = freqGhz * 1e9;
  const fspl = 20 * Math.log10(distanceM) + 20 * Math.log10(fHz) - 147.55;
  return Math.round((txPower - fspl) * 10) / 10;
}

/** Haversine distance in metres between two lat/lng points. */
export function haversineM(lat1: number, lng1: number, lat2: number, lng2: number): number {
  const R = 6_378_137; // WGS84 semi-major axis (a), spherical model — matches UISP
  const dLat = ((lat2 - lat1) * Math.PI) / 180;
  const dLng = ((lng2 - lng1) * Math.PI) / 180;
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos((lat1 * Math.PI) / 180) *
      Math.cos((lat2 * Math.PI) / 180) *
      Math.sin(dLng / 2) ** 2;
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

/** First Fresnel zone radius (m) at midpoint of a link. */
function fresnelAtMid(distanceM: number, freqGhz: number): number {
  const c = 3e8;
  const lambda = c / (freqGhz * 1e9);
  const d = distanceM / 2;
  return Math.sqrt((lambda * d * d) / (d + d));
}

/** Link quality color from RSSI. */
export function rssiColor(rssi: number): string {
  if (rssi >= -55) return '#34C759'; // strong
  if (rssi >= -70) return '#A3E635'; // good
  if (rssi >= -80) return '#FFCC00'; // fair
  return '#FF453A';                   // weak
}

/**
 * Continuous RSSI→CSS-color ramp for the coverage raster. Same four anchor
 * colours as `rssiColor` / the map's gradient legend (strong green → weak red),
 * but linearly interpolated in RGB so a raster reads as a smooth heat surface.
 * Clamped at both ends: ≥ −55 dBm is fully green, ≤ −95 dBm fully red.
 */
const RSSI_STOPS: [number, number, number, number][] = [
  [-95, 255, 69, 58], // weak — red    (#FF453A)
  [-80, 255, 204, 0], // fair — yellow (#FFCC00)
  [-70, 163, 230, 53], // good — lime   (#A3E635)
  [-55, 52, 199, 89], // strong — green (#34C759)
];
export function rssiRampCss(dbm: number): string {
  if (dbm <= RSSI_STOPS[0]![0]) {
    const s = RSSI_STOPS[0]!;
    return `rgb(${s[1]},${s[2]},${s[3]})`;
  }
  const last = RSSI_STOPS[RSSI_STOPS.length - 1]!;
  if (dbm >= last[0]) return `rgb(${last[1]},${last[2]},${last[3]})`;
  for (let i = 1; i < RSSI_STOPS.length; i++) {
    const hi = RSSI_STOPS[i]!;
    if (dbm <= hi[0]) {
      const lo = RSSI_STOPS[i - 1]!;
      const t = (dbm - lo[0]) / (hi[0] - lo[0]);
      const r = Math.round(lo[1] + t * (hi[1] - lo[1]));
      const g = Math.round(lo[2] + t * (hi[2] - lo[2]));
      const b = Math.round(lo[3] + t * (hi[3] - lo[3]));
      return `rgb(${r},${g},${b})`;
    }
  }
  return `rgb(${last[1]},${last[2]},${last[3]})`; // unreachable
}

/**
 * Pick a coverage-raster grid resolution for a viewport whose ground size has
 * the given width/height ratio. Keeps cells ~square (nicer heat surface) and
 * rows*cols under `cap` (mirrors backend MAX_COVERAGE_CELLS = 4096).
 */
export function coverageGrid(
  aspect: number,
  cap = 4096,
  maxSide = 64,
): { rows: number; cols: number } {
  const a = aspect > 0 && Number.isFinite(aspect) ? aspect : 1;
  let cols = maxSide;
  let rows = maxSide;
  if (a >= 1) rows = Math.max(8, Math.round(maxSide / a));
  else cols = Math.max(8, Math.round(maxSide * a));
  while (rows * cols > cap) {
    cols = Math.max(1, Math.floor(cols * 0.9));
    rows = Math.max(1, Math.floor(rows * 0.9));
  }
  return { rows, cols };
}

/** Link color accounting for LOS status. */
export function linkColor(link: MapLink): string {
  if (link.los === 'blocked') return '#FF453A';
  if (link.los === 'partial') return '#FFCC00';
  return rssiColor(link.rssi - link.rainDb - link.obstructionDb);
}

/** Rain rate description. */
export function rainRateLabel(mmhr: number): string {
  if (mmhr === 0) return 'Clear';
  if (mmhr <= 2.5) return 'Drizzle';
  if (mmhr <= 10) return 'Light Rain';
  if (mmhr <= 25) return 'Moderate Rain';
  if (mmhr <= 50) return 'Heavy Rain';
  return 'Violent Rain';
}

interface MapState {
  devices: Map<string, MapDevice>;
  links: Map<string, MapLink>;
  selectedDeviceId: string | null;
  tool: MapTool;
  showOnboarding: boolean;
  mapCenter: [number, number];
  mapZoom: number;
  mapLayer: MapTileKey;       // active basemap (satellite / street / hybrid / …)
  rainRate: number;     // mm/hr (0 = clear sky)
  checkingLos: boolean; // async LOS check in progress
  gisLayers: Record<string, GisLayerState>; // GIS layer tree state (05_MAP_ENGINE)
  gisPanelOpen: boolean;      // GIS layer panel visibility
  searchResult: GeoResult | null; // active geocoding pick (flyTo + temp marker)
  mapNotice: string | null;   // transient non-blocking notice (auto-clears)
  movingSiteId: string | null; // site armed for reposition — next map click PATCHes its lat/lon

  // Elevation-profile tool (Phase B1)
  profilePts: [number, number][]; // 0..2 picked endpoints [lat, lng]
  profileData: ProfileData | null;
  profileLoading: boolean;
  profileError: string | null;
  profileTxH: number;   // TX antenna height above ground (m)
  profileRxH: number;   // RX antenna height above ground (m)
  profileFreq: number;  // link frequency (GHz) for the Fresnel hint

  // selectors
  deviceList: () => MapDevice[];
  linkList: () => MapLink[];
  selectedDevice: () => MapDevice | null;
  isGisLayerVisible: (id: string) => boolean;

  // mutations
  addDevice: (d: Omit<MapDevice, 'id'>) => string;
  updateDevice: (id: string, patch: Partial<MapDevice>) => void;
  removeDevice: (id: string) => void;
  selectDevice: (id: string | null) => void;
  setTool: (tool: MapTool) => void;
  dismissOnboarding: () => void;
  setMapLayer: (layer: MapTileKey) => void;
  setRainRate: (rate: number) => void;
  setCheckingLos: (v: boolean) => void;
  updateLinkLos: (linkId: string, los: LosStatus, obstructionDb: number) => void;
  toggleGisLayer: (id: string) => void;
  setGisLayerOpacity: (id: string, opacity: number) => void;
  toggleGisPanel: (open?: boolean) => void;
  setSearchResult: (r: GeoResult | null) => void;
  flashNotice: (msg: string) => void;
  /** Arm ('site-move' tool) with a site id, or pass null to disarm back to 'select'. */
  setMovingSiteId: (id: string | null) => void;

  // Elevation-profile tool
  addProfilePoint: (lat: number, lng: number) => void;
  clearProfile: () => void;
  setProfileParams: (patch: { txH?: number; rxH?: number; freq?: number }) => void;
  runProfile: () => Promise<void>;

  /** Rebuild all links (fast, synchronous FSPL only). */
  rebuildLinks: () => void;
  /** Trigger async LOS check for all current links. */
  triggerLosCheck: () => Promise<void>;
}

let seq = 0;
const nextId = (prefix: string) => `${prefix}-${++seq}`;
let noticeTimer: ReturnType<typeof setTimeout> | null = null;

export const useMapStore = create<MapState>((set, get) => ({
  devices: new Map(),
  links: new Map(),
  selectedDeviceId: null,
  tool: 'select',
  showOnboarding: true,
  mapCenter: [-6.2, 106.8], // Jakarta default
  mapZoom: 13,
  mapLayer: DEFAULT_TILE,
  rainRate: 0,
  checkingLos: false,
  gisLayers: initialGisLayers(),
  gisPanelOpen: false,
  searchResult: null,
  mapNotice: null,
  movingSiteId: null,

  profilePts: [],
  profileData: null,
  profileLoading: false,
  profileError: null,
  profileTxH: 15,
  profileRxH: 10,
  profileFreq: 5.8,

  deviceList: () => Array.from(get().devices.values()),
  linkList: () => Array.from(get().links.values()),
  selectedDevice: () => {
    const id = get().selectedDeviceId;
    return id ? (get().devices.get(id) ?? null) : null;
  },
  isGisLayerVisible: (id) => get().gisLayers[id]?.visible ?? false,

  addDevice: (d) => {
    const id = nextId(d.kind);
    set((s) => {
      const devices = new Map(s.devices);
      devices.set(id, { ...d, id });
      return { devices };
    });
    get().rebuildLinks();
    // Trigger async LOS check after a short delay to batch updates.
    setTimeout(() => void get().triggerLosCheck(), 300);
    return id;
  },

  updateDevice: (id, patch) => {
    set((s) => {
      const dev = s.devices.get(id);
      if (!dev) return {};
      const devices = new Map(s.devices);
      devices.set(id, { ...dev, ...patch });
      return { devices };
    });
    get().rebuildLinks();
    setTimeout(() => void get().triggerLosCheck(), 300);
  },

  removeDevice: (id) => {
    set((s) => {
      const devices = new Map(s.devices);
      devices.delete(id);
      const links = new Map(s.links);
      for (const [lid, l] of links) {
        if (l.fromId === id || l.toId === id) links.delete(lid);
      }
      return {
        devices,
        links,
        selectedDeviceId: s.selectedDeviceId === id ? null : s.selectedDeviceId,
      };
    });
  },

  selectDevice: (id) => set({ selectedDeviceId: id }),
  setTool: (tool) => set({ tool }),
  dismissOnboarding: () => set({ showOnboarding: false }),
  setMapLayer: (mapLayer) => set({ mapLayer }),
  setRainRate: (rainRate) => {
    set({ rainRate });
    get().rebuildLinks();
  },
  setCheckingLos: (checkingLos) => set({ checkingLos }),

  toggleGisLayer: (id) =>
    set((s) => {
      const cur = s.gisLayers[id];
      if (!cur) return {};
      return { gisLayers: { ...s.gisLayers, [id]: { ...cur, visible: !cur.visible } } };
    }),

  setGisLayerOpacity: (id, opacity) =>
    set((s) => {
      const cur = s.gisLayers[id];
      if (!cur) return {};
      const clamped = Math.min(1, Math.max(0, opacity));
      return { gisLayers: { ...s.gisLayers, [id]: { ...cur, opacity: clamped } } };
    }),

  toggleGisPanel: (open) =>
    set((s) => ({ gisPanelOpen: open ?? !s.gisPanelOpen })),

  setSearchResult: (searchResult) => set({ searchResult }),
  setMovingSiteId: (movingSiteId) => set({ movingSiteId, tool: movingSiteId ? 'site-move' : 'select' }),

  flashNotice: (msg) => {
    set({ mapNotice: msg });
    if (noticeTimer) clearTimeout(noticeTimer);
    noticeTimer = setTimeout(() => set({ mapNotice: null }), 2600);
  },

  addProfilePoint: (lat, lng) => {
    // Two-click pattern (mirrors the measure tool): a 3rd click starts a fresh
    // line. Auto-fetches once both endpoints are set.
    const pts = get().profilePts;
    const next: [number, number][] =
      pts.length >= 2 ? [[lat, lng]] : [...pts, [lat, lng]];
    set({ profilePts: next, profileError: null, ...(next.length < 2 ? { profileData: null } : {}) });
    if (next.length === 2) void get().runProfile();
  },

  clearProfile: () =>
    set({ profilePts: [], profileData: null, profileLoading: false, profileError: null }),

  setProfileParams: (patch) => {
    set({
      profileTxH: patch.txH ?? get().profileTxH,
      profileRxH: patch.rxH ?? get().profileRxH,
      profileFreq: patch.freq ?? get().profileFreq,
    });
    if (get().profilePts.length === 2) void get().runProfile();
  },

  runProfile: async () => {
    const { profilePts, profileTxH, profileRxH, profileFreq } = get();
    if (profilePts.length !== 2) return;
    const [aLat, aLng] = profilePts[0]!;
    const [bLat, bLng] = profilePts[1]!;
    set({ profileLoading: true, profileError: null });
    try {
      // ponytail: reuse the backend DEM + LoS/Fresnel endpoint (the pluggable,
      // SSRF-safe provider abstraction) instead of a frontend elevation provider
      // layer. Swap the provider server-side in app/services/elevation.py.
      const res = await wirelessApi.losCheck({
        a_lat: aLat, a_lon: aLng, b_lat: bLat, b_lon: bLng,
        frequency_ghz: profileFreq, tx_height_m: profileTxH, rx_height_m: profileRxH,
        samples: 48,
      });
      const points = (res.profile?.points ?? []).map((p) => ({
        distance_m: p.distance_m,
        elevation_m: p.elevation_m,
      }));
      set({
        profileData: {
          points,
          totalDistanceM: res.profile?.total_distance_m ?? res.distance_m,
          losClear: res.los_clear,
          fresnelClear: res.fresnel_clear,
          worstObstructionM: res.worst_obstruction_m,
          minClearanceRatio: res.min_clearance_ratio,
          terrainSource: res.profile?.terrain_source ?? 'dem',
        },
        profileLoading: false,
      });
    } catch (err) {
      const msg =
        (err as { status?: number })?.status === 503
          ? 'Elevation provider unavailable — try again shortly.'
          : (err as { message?: string })?.message ?? 'Failed to fetch elevation profile.';
      set({ profileLoading: false, profileError: msg, profileData: null });
    }
  },

  updateLinkLos: (linkId, los, obstructionDb) =>
    set((s) => {
      const link = s.links.get(linkId);
      if (!link) return {};
      const links = new Map(s.links);
      const from = s.devices.get(link.fromId);
      const rainDb = from
        ? (() => {
            // re-import dynamically to avoid circular dep at top level
            const { rainRate } = s;
            if (rainRate <= 0) return 0;
            const k = from.frequency <= 3 ? 0.0001071 : from.frequency <= 6 ? 0.0091 : 0.3171;
            const alpha = from.frequency <= 3 ? 1.6009 : from.frequency <= 6 ? 1.217 : 0.8545;
            return k * Math.pow(rainRate, alpha) * (link.distance / 1000);
          })()
        : 0;

      const newRssi = from
        ? calcRssi(from.txPower, link.distance, from.frequency) - rainDb - obstructionDb
        : link.rssi;

      links.set(linkId, { ...link, los, obstructionDb, rainDb, rssi: newRssi });
      return { links };
    }),

  rebuildLinks: () => {
    const { devices, rainRate } = get();
    const list = Array.from(devices.values());
    const links = new Map<string, MapLink>();
    const aps = list.filter((d) => d.kind === 'ap' || d.kind === 'tower');

    for (const dev of list) {
      if (dev.kind === 'ap') continue;

      let bestAp: MapDevice | null = null;
      let bestDist = Infinity;

      for (const ap of aps) {
        if (ap.id === dev.id) continue;
        const dist = haversineM(dev.lat, dev.lng, ap.lat, ap.lng);
        if (dist < bestDist && dist <= ap.range) {
          bestDist = dist;
          bestAp = ap;
        }
      }

      if (bestAp) {
        const linkId = [bestAp.id, dev.id].sort().join('--');
        if (!links.has(linkId)) {
          const existingLink = get().links.get(linkId);
          const rssi = calcRssi(bestAp.txPower, bestDist, bestAp.frequency);

          // Rain attenuation
          const k = bestAp.frequency <= 3 ? 0.0001071 : bestAp.frequency <= 6 ? 0.0091 : 0.3171;
          const alpha = bestAp.frequency <= 3 ? 1.6009 : bestAp.frequency <= 6 ? 1.217 : 0.8545;
          const rainDb = rainRate > 0 ? k * Math.pow(rainRate, alpha) * (bestDist / 1000) : 0;

          links.set(linkId, {
            id: linkId,
            fromId: bestAp.id,
            toId: dev.id,
            distance: Math.round(bestDist),
            rssi: Math.round((rssi - rainDb) * 10) / 10,
            los: existingLink?.los ?? 'unknown',
            rainDb: Math.round(rainDb * 10) / 10,
            obstructionDb: existingLink?.obstructionDb ?? 0,
            fresnelM: Math.round(fresnelAtMid(bestDist, bestAp.frequency) * 10) / 10,
          });
        }
      }
    }

    set({ links });
  },

  triggerLosCheck: async () => {
    const { devices, links, checkingLos } = get();
    if (checkingLos) return; // debounce

    const linkList = Array.from(links.values());
    if (linkList.length === 0) return;

    set({ checkingLos: true });

    try {
      // Dynamic import to avoid loading the heavy elevation module until needed.
      const { computeSignal } = await import('@/services/signalSim');
      const { rainRate } = get();

      await Promise.all(
        linkList.map(async (link) => {
          const from = devices.get(link.fromId);
          const to = devices.get(link.toId);
          if (!from || !to) return;

          try {
            const result = await computeSignal({
              txPower: from.txPower,
              distanceM: link.distance,
              freqGhz: from.frequency,
              rainRateMmHr: rainRate,
              lat1: from.lat, lng1: from.lng, altAgl1: from.antennaHeight,
              lat2: to.lat, lng2: to.lng, altAgl2: to.antennaHeight,
            });

            get().updateLinkLos(link.id, result.los, result.obstructionDb);
          } catch {
            // Per-link error: mark as unknown, don't crash.
            get().updateLinkLos(link.id, 'unknown', 0);
          }
        }),
      );
    } finally {
      set({ checkingLos: false });
    }
  },
}));
