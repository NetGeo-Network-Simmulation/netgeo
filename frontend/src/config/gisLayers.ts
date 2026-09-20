/**
 * GIS layer registry (NetGeo/05_MAP_ENGINE §GIS Layers).
 *
 * The Map Engine is specified as a multi-layer geospatial canvas with a
 * *provider abstraction* so operators can swap data sources per licence/need.
 * This module is the declarative source of truth for the layer tree the
 * GisLayerPanel renders and the MapView consumes.
 *
 * Layer kinds:
 *  - 'tile'    — a raster/vector tile overlay we can render today (key-less,
 *                free provider). Carries `tileUrl` + attribution.
 *  - 'feature' — a vector feature layer fed by an async query (e.g. Overpass
 *                towers). Rendered by a bespoke React layer, gated on `visible`.
 *  - 'planned' — reserved in the spec; shown disabled (progressive disclosure)
 *                until a data provider/plugin is wired. Keeps the tree honest.
 *
 * Adding a provider later = flip `kind` and supply `tileUrl`/loader; the panel
 * and store need no changes.
 */

export type GisLayerGroup =
  | 'core'
  | 'transportation'
  | 'utilities'
  | 'terrain'
  | 'environment'
  | 'population'
  | 'weather';

export type GisLayerKind = 'tile' | 'feature' | 'planned';

export interface GisLayerDef {
  id: string;
  group: GisLayerGroup;
  label: string;
  kind: GisLayerKind;
  defaultVisible: boolean;
  defaultOpacity: number; // 0..1
  /** Tile template for `kind: 'tile'` (provider abstraction). */
  tileUrl?: string;
  attribution?: string;
  subdomains?: string;
  maxZoom?: number;
  /** Hint for feature layers that only load when zoomed in (e.g. Overpass). */
  minZoom?: number;
  description?: string;
  /** True for layers that fetch from a public internet API (Overpass, Esri
   *  online tiles, …) — silently fails (and leaks the request) on an offline
   *  install. Surfaced in GisLayerPanel; does not gate rendering itself. */
  requiresInternet?: boolean;
}

export interface GisGroupDef {
  group: GisLayerGroup;
  label: string;
  /** Collapsed by default in the panel to keep the tree compact. */
  collapsed: boolean;
}

/** Display order + labels for the layer-tree groups. */
export const GIS_GROUPS: GisGroupDef[] = [
  { group: 'core', label: 'Administrative', collapsed: true },
  { group: 'transportation', label: 'Transportation', collapsed: true },
  { group: 'utilities', label: 'Utilities & Telecom', collapsed: false },
  { group: 'terrain', label: 'Terrain', collapsed: false },
  { group: 'environment', label: 'Environment', collapsed: true },
  { group: 'population', label: 'Population', collapsed: true },
  { group: 'weather', label: 'Weather', collapsed: true },
];

const ESRI_HILLSHADE =
  'https://server.arcgisonline.com/ArcGIS/rest/services/Elevation/World_Hillshade/MapServer/tile/{z}/{y}/{x}';
const ESRI_ATTR = 'Tiles &copy; Esri';
const OTM_URL = 'https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png';
const OTM_ATTR =
  'Map data: &copy; OpenStreetMap contributors, SRTM | &copy; OpenTopoMap (CC-BY-SA)';
const OSM_URL = 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png';
const OSM_ATTR = '&copy; OpenStreetMap contributors';

/**
 * Full layer catalogue. Order within a group is preserved by the panel.
 * Only a curated subset is wired to live key-less providers today; the rest
 * are spec'd placeholders surfaced as disabled rows.
 */
export const GIS_LAYERS: GisLayerDef[] = [
  // --- Administrative (Core) ------------------------------------------------
  { id: 'core-country', group: 'core', label: 'Country', kind: 'planned', defaultVisible: false, defaultOpacity: 1 },
  { id: 'core-province', group: 'core', label: 'Province / State', kind: 'planned', defaultVisible: false, defaultOpacity: 1 },
  { id: 'core-city', group: 'core', label: 'City', kind: 'planned', defaultVisible: false, defaultOpacity: 1 },
  { id: 'core-district', group: 'core', label: 'District', kind: 'planned', defaultVisible: false, defaultOpacity: 1 },
  { id: 'core-village', group: 'core', label: 'Village', kind: 'planned', defaultVisible: false, defaultOpacity: 1 },

  // --- Transportation -------------------------------------------------------
  {
    id: 'trans-road',
    group: 'transportation',
    label: 'Roads & Highways',
    kind: 'tile',
    defaultVisible: false,
    defaultOpacity: 0.55,
    tileUrl: OSM_URL,
    attribution: OSM_ATTR,
    subdomains: 'abc',
    maxZoom: 19,
    description: 'OpenStreetMap road network overlay.',
  },
  { id: 'trans-railway', group: 'transportation', label: 'Railway', kind: 'planned', defaultVisible: false, defaultOpacity: 1 },
  { id: 'trans-airport', group: 'transportation', label: 'Airport', kind: 'planned', defaultVisible: false, defaultOpacity: 1 },
  { id: 'trans-seaport', group: 'transportation', label: 'Seaport', kind: 'planned', defaultVisible: false, defaultOpacity: 1 },

  // --- Utilities & Telecom --------------------------------------------------
  {
    id: 'util-tower',
    group: 'utilities',
    label: 'Telecom Towers (OSM)',
    kind: 'feature',
    // OFFLINE-MAP-4 fix: was `true` — on an offline install this queried
    // overpass-api.de by default and silently failed, leaking a request the
    // user never asked for. Off by default everywhere (not just when the
    // offline basemap is active): the store's initial gisLayers state is
    // built synchronously at module load (mapStore.ts `initialGisLayers`),
    // before the async /api/maps/status offline check resolves, so there's
    // no offline flag available at that point to key off. A global
    // default-off is the simpler, honest fix — still one click to enable.
    defaultVisible: false,
    defaultOpacity: 1,
    minZoom: 12,
    description: 'Live BTS/mast/microwave towers from the Overpass API.',
    requiresInternet: true,
  },
  {
    id: 'rf-coverage',
    group: 'utilities',
    label: 'RF Coverage (best-server)',
    kind: 'feature',
    defaultVisible: false,
    defaultOpacity: 0.55,
    description:
      'Predicted best-server RSSI raster over the viewport, computed from placed AP/Tower radios (backend RF engine).',
  },
  { id: 'util-fiber', group: 'utilities', label: 'Fiber Route', kind: 'planned', defaultVisible: false, defaultOpacity: 1 },
  { id: 'util-power', group: 'utilities', label: 'Power Line', kind: 'planned', defaultVisible: false, defaultOpacity: 1 },
  { id: 'util-datacenter', group: 'utilities', label: 'Data Center', kind: 'planned', defaultVisible: false, defaultOpacity: 1 },
  { id: 'util-ixp', group: 'utilities', label: 'Internet Exchange', kind: 'planned', defaultVisible: false, defaultOpacity: 1 },

  // --- Terrain --------------------------------------------------------------
  {
    id: 'terrain-hillshade',
    group: 'terrain',
    label: 'Hillshade',
    kind: 'tile',
    defaultVisible: false,
    defaultOpacity: 0.5,
    tileUrl: ESRI_HILLSHADE,
    attribution: ESRI_ATTR,
    maxZoom: 16,
    description: 'Esri World Hillshade — relief shading for LOS context.',
  },
  {
    id: 'terrain-contour',
    group: 'terrain',
    label: 'Contour / DEM',
    kind: 'tile',
    defaultVisible: false,
    defaultOpacity: 0.6,
    tileUrl: OTM_URL,
    attribution: OTM_ATTR,
    subdomains: 'abc',
    maxZoom: 17,
    description: 'OpenTopoMap contour lines (SRTM-derived).',
  },
  { id: 'terrain-slope', group: 'terrain', label: 'Slope', kind: 'planned', defaultVisible: false, defaultOpacity: 1 },
  { id: 'terrain-aspect', group: 'terrain', label: 'Aspect', kind: 'planned', defaultVisible: false, defaultOpacity: 1 },

  // --- Environment ----------------------------------------------------------
  { id: 'env-forest', group: 'environment', label: 'Forest', kind: 'planned', defaultVisible: false, defaultOpacity: 1 },
  { id: 'env-water', group: 'environment', label: 'Water', kind: 'planned', defaultVisible: false, defaultOpacity: 1 },
  { id: 'env-urban', group: 'environment', label: 'Urban', kind: 'planned', defaultVisible: false, defaultOpacity: 1 },
  { id: 'env-protected', group: 'environment', label: 'Protected Area', kind: 'planned', defaultVisible: false, defaultOpacity: 1 },

  // --- Population ------------------------------------------------------------
  {
    id: 'pop-density',
    group: 'population',
    label: 'Density',
    kind: 'feature',
    defaultVisible: false,
    defaultOpacity: 1,
    minZoom: 16,
    description: 'Building-density heat (OSM footprints binned per ~110 m cell).',
    requiresInternet: true,
  },
  {
    id: 'pop-buildings',
    group: 'population',
    label: 'Building Distribution',
    kind: 'feature',
    defaultVisible: false,
    defaultOpacity: 1,
    minZoom: 16,
    description: 'Live OSM building footprints from the Overpass API.',
    requiresInternet: true,
  },

  // --- Weather --------------------------------------------------------------
  { id: 'weather-rain', group: 'weather', label: 'Rain', kind: 'planned', defaultVisible: false, defaultOpacity: 1, description: 'Requires a weather tile provider/API key (plugin).' },
  { id: 'weather-cloud', group: 'weather', label: 'Cloud', kind: 'planned', defaultVisible: false, defaultOpacity: 1 },
  { id: 'weather-wind', group: 'weather', label: 'Wind', kind: 'planned', defaultVisible: false, defaultOpacity: 1 },
];

export type GisLayerId = (typeof GIS_LAYERS)[number]['id'];

/** Index for O(1) lookup by id. */
export const GIS_LAYER_BY_ID: Record<string, GisLayerDef> = Object.fromEntries(
  GIS_LAYERS.map((l) => [l.id, l]),
);
