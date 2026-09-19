/**
 * Map tile layer configuration.
 *
 * All providers listed here are free and require no API key for standard
 * usage. Attribution strings must be preserved per each provider's ToS.
 *
 * Usage (MapLibre GL, see components/map/MapView.tsx `rasterSource`):
 *   import { MAP_TILES } from '@/config/mapTiles';
 *   map.addSource('base', { type: 'raster', tiles: [...], tileSize: 256 });
 */
import { API_BASE } from '@/api/client';

export interface TileLayerConfig {
  url: string;
  attribution: string;
  maxZoom?: number;
  subdomains?: string;
  /** Secondary overlay layer (labels, roads) to stack on top */
  overlay?: {
    url: string;
    attribution: string;
    opacity?: number;
  };
}

export const MAP_TILES = {
  /**
   * Esri World Imagery — high-resolution satellite/aerial imagery.
   * No API key required. Suitable for ISP field design (UISP-style).
   */
  satellite: {
    url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
    attribution:
      'Tiles &copy; Esri &mdash; Source: Esri, i-cubed, USDA, USGS, AEX, GeoEye, Getmapping, Aerogrid, IGN, IGP, UPR-EGP, and the GIS User Community',
    maxZoom: 19,
  },

  /**
   * OpenStreetMap Standard — street map with roads, buildings, POIs.
   * Tile usage policy: https://operations.osmfoundation.org/policies/tiles/
   */
  street: {
    url: 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
    attribution: '&copy; OpenStreetMap contributors',
    maxZoom: 19,
    subdomains: 'abc',
  },
} as const satisfies Record<string, TileLayerConfig>;

export type MapTileKey = keyof typeof MAP_TILES;

/** Default tile layer to use on first load. */
export const DEFAULT_TILE: MapTileKey = 'street';

/**
 * QA-visual #5 (2026-09-12): the basemap picker was cut from 5 choices down
 * to these 2 — `hybrid`/`dark`/`topo` are gone from MAP_TILES above. Their
 * underlying data (hillshade, contour/DEM) is untouched: GisLayerPanel's
 * terrain group reads its own tile configs straight from config/gisLayers.ts,
 * which never referenced this file.
 *
 * Nothing in the app currently persists `mapLayer` (no project field, no
 * localStorage — grepped clean), so a stale 'hybrid'/'dark'/'topo' can't
 * actually reach the store today. This is the safety net anyway, in case a
 * future save/load path resurrects the field: `normalizeMapLayer` maps each
 * retired key to its closest surviving basemap instead of an undefined
 * `MAP_TILES[key]` lookup going through to a blank map.
 */
const LEGACY_TILE_FALLBACK: Record<string, MapTileKey> = {
  hybrid: 'satellite', // same Esri World Imagery URL as `satellite`, exact match
  dark: 'street',      // closest surviving line-drawn (non-imagery) style
  topo: 'street',      // closest surviving line-drawn (non-imagery) style
};

/** Coerce any string to a valid `MapTileKey`, remapping retired basemap keys
 *  instead of letting an unknown one fall through to a blank map. */
export function normalizeMapLayer(key: string): MapTileKey {
  if (key in MAP_TILES) return key as MapTileKey;
  return LEGACY_TILE_FALLBACK[key] ?? DEFAULT_TILE;
}

/* -------------------------------------------------------------------------- */
/* OFFLINE-MAP-2 — local MBTiles basemap (native full-offline distribution,   */
/* memory netgeo-distribusi-lima-bentuk variant #1). Backend (OFFLINE-MAP-1)  */
/* reports whether an operator-installed region file exists via              */
/* GET /api/maps/status; when it does, it becomes THE basemap tile source —  */
/* not a third switcher entry. A device carries at most one installed region */
/* file, so both Satellite/Street buttons resolve to the same local pixels   */
/* while offline (Surya: "offline adalah sumber tile, bukan jenis peta").    */
/* -------------------------------------------------------------------------- */

/** Shape of GET /api/maps/status (backend/app/services/offline_maps.py). */
export interface OfflineMapStatus {
  available: boolean;
  region: string | null;
  attribution: string | null;
  min_zoom?: number | null;
  max_zoom?: number | null;
  /** "pbf" = vector MBTiles (OFFLINE-MAP-4); anything else/absent = raster. */
  format?: string | null;
}

/** The offline tile endpoint sits behind the same bearer-token auth as every
 *  other API route (unlike the external Esri/OSM providers above, which need
 *  no header) — MapView's `transformRequest` matches requests against this
 *  prefix to attach the Authorization header MapLibre's own fetch wouldn't
 *  otherwise send. */
export const OFFLINE_TILE_PREFIX = `${API_BASE}/maps/tiles/`;

/** Decide which tiles actually back the basemap: the local file when the
 *  operator installed one, else the online provider for `mapLayer` — exactly
 *  today's behavior, unchanged (the safety net). Pure function so the
 *  decision is unit-testable without a MapLibre/DOM instance. */
export function resolveBaseTile(
  mapLayer: MapTileKey,
  offline: OfflineMapStatus | null | undefined,
): TileLayerConfig & { offline: boolean; vector: boolean } {
  const cfg: TileLayerConfig = MAP_TILES[mapLayer];
  if (offline?.available) {
    return {
      url: `${OFFLINE_TILE_PREFIX}{z}/{x}/{y}`,
      attribution: offline.attribution || 'Offline map data',
      maxZoom: offline.max_zoom ?? cfg.maxZoom,
      offline: true,
      vector: offline.format === 'pbf',
    };
  }
  return {
    url: cfg.url,
    subdomains: cfg.subdomains,
    maxZoom: cfg.maxZoom,
    attribution: cfg.attribution,
    offline: false,
    vector: false,
  };
}

/* -------------------------------------------------------------------------- */
/* OFFLINE-MAP-4 — vector basemap (format=pbf offline MBTiles, e.g. a         */
/* Planetiler/OpenMapTiles build — see memory                                 */
/* mbtiles-vector-spike-2026-09-19). MapView adds one MapLibre 'vector'       */
/* source + these style layers instead of a single raster layer when         */
/* `resolveBaseTile(...).vector` is true.                                     */
/*                                                                            */
/* Fill/line only — deliberately no `symbol`/text layers. MapLibre needs a    */
/* glyph server ({fontstack}/{range}.pbf tree) to render vector-tile text,    */
/* and a true-offline install has none bundled; calling a public glyph CDN   */
/* would silently break the "zero external requests while offline" contract. */
/* ponytail: offline vector maps render unlabeled until glyphs are bundled    */
/* locally — add a local glyph set + `symbol` layers (place/transportation_   */
/* name/poi/housenumber, per OpenMapTiles' own style) if that proves          */
/* confusing in QA. Sprites are not needed: nothing here uses `icon-image`.   */
/* -------------------------------------------------------------------------- */

export interface VectorBaseLayer {
  /** MapLibre layer id (prefixed by the caller) and OpenMapTiles source-layer
   *  name (they match 1:1 here, kept separate in case that ever changes). */
  id: string;
  sourceLayer: string;
  type: 'fill' | 'line';
  paint: Record<string, unknown>;
  minzoom?: number;
}

/** Approximate, theme-matched colors for an OpenMapTiles-schema basemap —
 *  independent of the brand token ramp in theme/tokens.ts (that ramp is for
 *  app chrome, not cartography), picked to read as "map" in each theme the
 *  way the online satellite/street tiles already do. */
const VECTOR_COLORS = {
  dark: {
    water: '#16232E',
    landcover: '#1E2420',
    landuse: '#242320',
    park: '#20301F',
    building: '#332F2A',
    road: '#4A453D',
    boundary: '#5C574E',
  },
  light: {
    water: '#AAD3DF',
    landcover: '#E4E8DA',
    landuse: '#EFEBE0',
    park: '#CFE3C8',
    building: '#DCD5C6',
    road: '#FFFFFF',
    boundary: '#B7AE9C',
  },
} as const;

/** OpenMapTiles layer stack, bottom to top: water < landcover < landuse <
 *  park < building < transportation (roads) < boundary. */
export function vectorBaseLayers(theme: 'light' | 'dark'): VectorBaseLayer[] {
  const c = VECTOR_COLORS[theme];
  return [
    { id: 'water', sourceLayer: 'water', type: 'fill', paint: { 'fill-color': c.water } },
    { id: 'landcover', sourceLayer: 'landcover', type: 'fill', paint: { 'fill-color': c.landcover, 'fill-opacity': 0.7 } },
    { id: 'landuse', sourceLayer: 'landuse', type: 'fill', paint: { 'fill-color': c.landuse, 'fill-opacity': 0.5 } },
    { id: 'park', sourceLayer: 'park', type: 'fill', paint: { 'fill-color': c.park, 'fill-opacity': 0.6 } },
    { id: 'building', sourceLayer: 'building', type: 'fill', paint: { 'fill-color': c.building }, minzoom: 13 },
    {
      id: 'transportation',
      sourceLayer: 'transportation',
      type: 'line',
      paint: {
        'line-color': c.road,
        'line-width': ['interpolate', ['linear'], ['zoom'], 5, 0.4, 12, 1.2, 18, 6],
      },
    },
    {
      id: 'boundary',
      sourceLayer: 'boundary',
      type: 'line',
      paint: { 'line-color': c.boundary, 'line-width': 1, 'line-dasharray': [2, 1.5] },
    },
  ];
}
