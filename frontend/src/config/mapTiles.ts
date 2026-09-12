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
