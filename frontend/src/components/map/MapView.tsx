/**
 * MapView — network design view (UISP Design Center style), on a MapLibre GL
 * globe basemap (docs/design/21-GLOBE-MAP-MIGRATION.md, Stage 1–3).
 *
 * Stage 1 swapped the render engine for the basemap + GIS raster overlays.
 * Stage 2 brought the vector overlays back — device markers + coverage rings,
 * link polylines, topology node/link markers, device/tower popups, OSM
 * towers/buildings, geocode fly-to — as MapLibre GeoJSON sources + style
 * layers per the migration doc's explicit rejection of a drop-in Leaflet shim.
 * Stage 3 (this pass) finishes the pixel/canvas-anchored features the doc
 * flags as the real precision risk (§5): click-to-place (device/measure/
 * profile/deploy, `e.point`/`unproject` instead of Leaflet's
 * `e.containerPoint`/`e.latlng`), the RF coverage raster (`image` source
 * instead of `L.imageOverlay`), and the RF PtP beam (GeoJSON, ported back
 * from the deleted RfBeamLayer.tsx).
 *
 * Still deferred to Stage 4 (see the debt block at the bottom of this file):
 * muting tile saturation per basemap/theme — cosmetic, not required for
 * feature parity.
 */
import { createContext, useContext, useEffect, useMemo, useRef, useState } from 'react';
import 'maplibre-gl/dist/maplibre-gl.css';
import {
  MapLibreMap,
  NavigationControl,
  Marker as MglMarker,
  Popup,
  type GeoJSONSource,
  type ImageSource,
  type MapLayerMouseEvent,
  type MapMouseEvent,
  type LngLat,
} from 'maplibre-gl';
import {
  useMapStore,
  rainRateLabel,
  linkColor,
  haversineM,
  rssiRampCss,
  coverageGrid,
  type GisLayerState,
  type MapDevice,
  type MapLink,
  type MapDeviceKind,
} from '@/store/mapStore';
import { useTopologyStore } from '@/store/topologyStore';
import { useRfStore } from '@/store/rfStore';
import { marginStatus, STATUS_COLOR, fmtKm } from '@/components/rf/rfLogic';
import type { NodeModel, LinkModel } from '@/api/types';
import { rfApi, type CoverageSite } from '@/api/client';
import {
  fetchOsmTowers,
  towerLabel,
  towerKind,
  fetchOsmBuildings,
  densityColors,
  type OsmTower,
  type OsmBuilding,
} from '@/services/osmService';
import { MAP_TILES, type TileLayerConfig, type MapTileKey } from '@/config/mapTiles';
import { GIS_LAYERS } from '@/config/gisLayers';
import { MapToolbar } from './MapToolbar';
import { MapDevicePanel } from './MapDevicePanel';
import { MapOnboardingModal } from './MapOnboardingModal';
import { MapLayerSwitcher } from './MapLayerSwitcher';
import { MapCounterChips } from './MapCounterChips';
import { MapSearch } from './MapSearch';
import { GisLayerPanel } from './GisLayerPanel';
import { ElevationProfilePanel } from './ElevationProfilePanel';
import { MapDeployMenu } from './MapDeployMenu';
import { DeviceLibraryModal } from './DeviceLibraryModal';
import { Layers as LayersIcon, AlertTriangle } from 'lucide-react';
import { useUiStore } from '@/store/uiStore';
import { cn } from '@/lib/cn';
import { zc } from '@/theme/z';

/* -------------------------------------------------------------------------- */
/* Map instance context — lets the vector-layer components below reach the    */
/* single MapLibre instance GlobeBasemap owns, without a Leaflet-style        */
/* per-marker React tree (design doc §4: no drop-in shim).                    */
/* -------------------------------------------------------------------------- */
const MapCtx = createContext<MapLibreMap | null>(null);
function useGlobeMap(): MapLibreMap | null {
  return useContext(MapCtx);
}

/* -------------------------------------------------------------------------- */
/* Basemap — MapLibre GL globe projection, layer-aware raster sources          */
/* Replaces the old MapContainer/BaseTiles/GisOverlayTiles/ZoomControl/        */
/* MapResizeWatcher stack. Full raster-layer rebuild on every basemap/GIS      */
/* toggle change: at most ~5 layers, so this is simpler and cheap enough to    */
/* beat hand-rolled diffing (ponytail).                                       */
/* -------------------------------------------------------------------------- */
const BASE_SOURCE_ID = 'ng-base';
const BASE_OVERLAY_SOURCE_ID = 'ng-base-overlay';
const GIS_SOURCE_PREFIX = 'ng-gis-';

/** Expand a Leaflet-style `{s}` subdomain placeholder into concrete tile URLs
 *  — MapLibre's raster source takes a flat URL array instead of a template. */
function tileUrls(url: string, subdomains?: string): string[] {
  if (!subdomains) return [url];
  return subdomains.split('').map((s) => url.replace('{s}', s));
}

function rasterSource(cfg: {
  url: string;
  subdomains?: string;
  maxZoom?: number;
  attribution?: string;
}) {
  return {
    type: 'raster' as const,
    tiles: tileUrls(cfg.url, cfg.subdomains),
    tileSize: 256,
    maxzoom: cfg.maxZoom ?? 19,
    // Compliance (gate #3): MapLibre's built-in AttributionControl reads this
    // field off every currently-used source and merges the strings — Esri/OSM/
    // CARTO/OpenTopo credit was silently dropped since Stage 1 because this
    // field was never forwarded. GlobeBasemap never passes
    // `attributionControl: false`, so the default control (bottom-right,
    // compact) is already mounted and just needed sources to read from.
    attribution: cfg.attribution,
  };
}

/** First non-raster (vector) layer currently in the style, if any. Used as the
 *  `beforeId` anchor so conditionally-mounted vector layers (OSM towers/
 *  buildings) always re-insert at the floor of the vector stack no matter
 *  when they're toggled on, instead of landing on top of whatever else has
 *  been added since their last mount. */
function firstVectorLayerId(map: MapLibreMap): string | undefined {
  for (const layer of map.getStyle().layers ?? []) {
    if (
      layer.id === BASE_SOURCE_ID ||
      layer.id === BASE_OVERLAY_SOURCE_ID ||
      layer.id.startsWith(GIS_SOURCE_PREFIX)
    ) {
      continue;
    }
    return layer.id;
  }
  return undefined;
}

function syncRasterLayers(
  map: MapLibreMap,
  mapLayer: MapTileKey,
  gisLayers: Record<string, GisLayerState>,
) {
  const style = map.getStyle();
  for (const layer of style.layers ?? []) {
    if (layer.id === BASE_SOURCE_ID || layer.id === BASE_OVERLAY_SOURCE_ID || layer.id.startsWith(GIS_SOURCE_PREFIX)) {
      map.removeLayer(layer.id);
    }
  }
  for (const id of Object.keys(style.sources ?? {})) {
    if (id === BASE_SOURCE_ID || id === BASE_OVERLAY_SOURCE_ID || id.startsWith(GIS_SOURCE_PREFIX)) {
      map.removeSource(id);
    }
  }

  // Re-inserted raster layers must land below any surviving vector overlay
  // (device/link/topology/OSM layers) so a basemap/GIS toggle never paints
  // over them. Computed once, after the old raster layers are gone, so this
  // is genuinely "the current vector floor" — undefined until Stage 2's
  // layers exist, matching the original append-on-top behavior.
  const beforeId = map.getStyle().layers?.[0]?.id;

  const cfg: TileLayerConfig = MAP_TILES[mapLayer];
  map.addSource(BASE_SOURCE_ID, rasterSource({ ...cfg, attribution: cfg.attribution }));
  map.addLayer({ id: BASE_SOURCE_ID, type: 'raster', source: BASE_SOURCE_ID }, beforeId);

  if (cfg.overlay) {
    map.addSource(
      BASE_OVERLAY_SOURCE_ID,
      rasterSource({ url: cfg.overlay.url, maxZoom: cfg.maxZoom, attribution: cfg.overlay.attribution }),
    );
    map.addLayer(
      {
        id: BASE_OVERLAY_SOURCE_ID,
        type: 'raster',
        source: BASE_OVERLAY_SOURCE_ID,
        paint: { 'raster-opacity': cfg.overlay.opacity ?? 1 },
      },
      beforeId,
    );
  }

  for (const layer of GIS_LAYERS) {
    if (layer.kind !== 'tile' || !layer.tileUrl) continue;
    const state = gisLayers[layer.id];
    if (!state?.visible) continue;
    const id = GIS_SOURCE_PREFIX + layer.id;
    map.addSource(
      id,
      rasterSource({ url: layer.tileUrl, subdomains: layer.subdomains, maxZoom: layer.maxZoom, attribution: layer.attribution }),
    );
    map.addLayer({ id, type: 'raster', source: id, paint: { 'raster-opacity': state.opacity } }, beforeId);
  }
}

function GlobeBasemap({ onMapChange }: { onMapChange: (map: MapLibreMap | null) => void }) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<MapLibreMap | null>(null);
  const mapLayer = useMapStore((s) => s.mapLayer);
  const gisLayers = useMapStore((s) => s.gisLayers);

  // Mount once — initial center/zoom only; the geocode search fly-to
  // (SearchResultLayer below) is the only thing that recenters after mount.
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const { mapCenter, mapZoom } = useMapStore.getState();
    const map = new MapLibreMap({
      container: el,
      style: { version: 8, sources: {}, layers: [] },
      center: [mapCenter[1], mapCenter[0]],
      zoom: mapZoom,
    });
    map.addControl(new NavigationControl({ showCompass: false }), 'bottom-right');
    map.once('load', () => {
      map.setProjection({ type: 'globe' });
      const s = useMapStore.getState();
      syncRasterLayers(map, s.mapLayer, s.gisLayers);
      onMapChange(map);
    });
    mapRef.current = map;

    // Fix tile blank-on-layout-shift by resizing on container size change
    // (mirrors the removed Leaflet invalidateSize watcher, same 150ms debounce
    // — drawer animation is ~180ms, so this fires after settle).
    let timer: ReturnType<typeof setTimeout> | null = null;
    const ro = new ResizeObserver(() => {
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => map.resize(), 150);
    });
    ro.observe(el);

    return () => {
      ro.disconnect();
      if (timer) clearTimeout(timer);
      onMapChange(null);
      map.remove();
      mapRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    if (map && map.isStyleLoaded()) syncRasterLayers(map, mapLayer, gisLayers);
  }, [mapLayer, gisLayers]);

  return (
    <div
      ref={containerRef}
      className="isolate h-full w-full"
      style={{ background: 'var(--ng-surface, #0d1117)' }}
    />
  );
}

/* -------------------------------------------------------------------------- */
/* Shared vector-layer plumbing — every Stage 2 overlay below is one GeoJSON  */
/* source + a handful of style layers, added/removed through this tiny        */
/* helper set instead of Leaflet-shaped per-marker React components.          */
/* -------------------------------------------------------------------------- */
const EMPTY_FC: GeoJSON.FeatureCollection = { type: 'FeatureCollection', features: [] };

interface LayerDef {
  id: string;
  type: 'fill' | 'line' | 'circle';
  source: string;
  paint: Record<string, unknown>;
  filter?: unknown[];
}

function ensureSource(map: MapLibreMap, id: string) {
  if (!map.getSource(id)) map.addSource(id, { type: 'geojson', data: EMPTY_FC });
}

// ponytail: MapLibre's style-spec types brand paint expressions very strictly
// (exact tuple literals); a runtime-valid `['get', 'color']` expression often
// doesn't structurally satisfy them without fighting generics. One localized
// cast here beats `as unknown as AddLayerObject` repeated at every call site.
function ensureLayer(map: MapLibreMap, layer: LayerDef, beforeId?: string) {
  if (!map.getLayer(layer.id)) {
    map.addLayer(
      {
        id: layer.id,
        type: layer.type,
        source: layer.source,
        paint: layer.paint,
        ...(layer.filter ? { filter: layer.filter } : {}),
      } as Parameters<MapLibreMap['addLayer']>[0],
      beforeId,
    );
  }
}

function teardown(map: MapLibreMap, sourceId: string, layerIds: string[]) {
  for (const id of layerIds) if (map.getLayer(id)) map.removeLayer(id);
  if (map.getSource(sourceId)) map.removeSource(sourceId);
}

function setSourceData(map: MapLibreMap, sourceId: string, fc: GeoJSON.FeatureCollection) {
  (map.getSource(sourceId) as GeoJSONSource | undefined)?.setData(fc);
}

function setCursor(map: MapLibreMap, cursor: string) {
  map.getCanvas().style.cursor = cursor;
}

function openPopup(map: MapLibreMap, lngLat: LngLat, html: string) {
  new Popup({ closeButton: true, maxWidth: '260px' }).setLngLat(lngLat).setHTML(html).addTo(map);
}

function escapeHtml(s: string): string {
  const map: Record<string, string> = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
  return s.replace(/[&<>"']/g, (c) => map[c]!);
}

/** Geodesic destination point (spherical, same R as mapStore's haversineM so
 *  ring geometry and distance math agree). */
function destPoint(lat: number, lng: number, bearingDeg: number, distM: number): [number, number] {
  const R = 6_378_137;
  const brng = (bearingDeg * Math.PI) / 180;
  const lat1 = (lat * Math.PI) / 180;
  const lng1 = (lng * Math.PI) / 180;
  const dR = distM / R;
  const lat2 = Math.asin(Math.sin(lat1) * Math.cos(dR) + Math.cos(lat1) * Math.sin(dR) * Math.cos(brng));
  const lng2 =
    lng1 +
    Math.atan2(Math.sin(brng) * Math.sin(dR) * Math.cos(lat1), Math.cos(dR) - Math.sin(lat1) * Math.sin(lat2));
  return [(lat2 * 180) / Math.PI, (((lng2 * 180) / Math.PI + 540) % 360) - 180];
}

/** Polygon ring approximating a geodesic circle of `radiusM` around
 *  (lat,lng), in GeoJSON [lng, lat] coordinate order. */
function circleRingCoords(lat: number, lng: number, radiusM: number, steps = 48): [number, number][] {
  const ring: [number, number][] = [];
  for (let i = 0; i <= steps; i++) {
    const [plat, plng] = destPoint(lat, lng, (360 * i) / steps, radiusM);
    ring.push([plng, plat]);
  }
  return ring;
}

interface LabelItem {
  id: string;
  lat: number;
  lng: number;
  text: string;
  color: string;
}

/** DOM-marker label pool shared by every "permanent tooltip" in this file.
 *  ponytail: MapLibre GL symbol layers need a self-hosted glyph/PBF server to
 *  render text — none exists here, and standing one up would be new
 *  self-hosted infra, out of Stage 2 scope, and works against the migration
 *  doc's own "no API key / self-hostable / air-gap friendly" criterion (§2).
 *  Leaflet's "permanent tooltip" was already a DOM element, not canvas text —
 *  this is the same technique, pooled/imperative instead of one React
 *  component per marker. */
function useLabelMarkers(map: MapLibreMap | null, items: LabelItem[]) {
  const poolRef = useRef<Map<string, MglMarker>>(new Map());

  useEffect(() => {
    if (!map) return;
    const pool = poolRef.current;
    const seen = new Set<string>();
    for (const item of items) {
      seen.add(item.id);
      let marker = pool.get(item.id);
      if (!marker) {
        const el = document.createElement('div');
        el.className = 'ng-map-label';
        el.style.pointerEvents = 'none';
        el.style.fontWeight = '700';
        el.style.fontSize = '10px';
        marker = new MglMarker({ element: el, anchor: 'bottom', offset: [0, -14] })
          .setLngLat([item.lng, item.lat])
          .addTo(map);
        pool.set(item.id, marker);
      } else {
        marker.setLngLat([item.lng, item.lat]);
      }
      const el = marker.getElement();
      if (el.textContent !== item.text) el.textContent = item.text;
      el.style.color = item.color;
    }
    for (const [id, marker] of pool) {
      if (!seen.has(id)) {
        marker.remove();
        pool.delete(id);
      }
    }
  }, [map, items]);

  // Cleanup only on true unmount — deliberately separate from the sync effect
  // above so a `map` identity check never nukes and rebuilds the whole pool.
  useEffect(
    () => () => {
      for (const marker of poolRef.current.values()) marker.remove();
      poolRef.current.clear();
    },
    [],
  );
}

/* -------------------------------------------------------------------------- */
/* Legacy mapStore devices — coverage rings + dots + labels + popup            */
/* -------------------------------------------------------------------------- */
const COVERAGE_RINGS = [
  { pct: 0.25, color: '#34C759', opacity: 0.2 }, // strong core
  { pct: 0.5, color: '#A3E635', opacity: 0.13 },
  { pct: 0.75, color: '#FFCC00', opacity: 0.09 },
  { pct: 1.0, color: '#FF453A', opacity: 0.05 }, // weak edge
] as const;

const KIND_COLOR: Record<MapDeviceKind, string> = {
  ap: '#5856D6',
  cpe: '#007AFF',
  tower: '#FF9F0A',
};

const DEV_RING_SRC = 'ng-dev-rings';
const DEV_RING_FILL = 'ng-dev-rings-fill';
const DEV_RING_LINE = 'ng-dev-rings-line';
const DEV_POINT_SRC = 'ng-dev-points';
const DEV_POINT_LAYER = 'ng-dev-points-circle';

function devicesRingsFC(devices: MapDevice[]): GeoJSON.FeatureCollection {
  const features: GeoJSON.Feature[] = [];
  for (const d of devices) {
    if (d.kind === 'cpe') continue;
    for (const ring of COVERAGE_RINGS) {
      features.push({
        type: 'Feature',
        geometry: { type: 'Polygon', coordinates: [circleRingCoords(d.lat, d.lng, d.range * ring.pct)] },
        properties: { color: ring.color, opacity: ring.opacity },
      });
    }
  }
  return { type: 'FeatureCollection', features };
}

function devicesPointsFC(devices: MapDevice[], selectedId: string | null): GeoJSON.FeatureCollection {
  return {
    type: 'FeatureCollection',
    features: devices.map((d) => ({
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [d.lng, d.lat] },
      properties: {
        id: d.id,
        selected: d.id === selectedId,
        color: KIND_COLOR[d.kind],
        name: d.name,
        kind: d.kind,
        txPower: d.txPower,
        frequency: d.frequency,
        antennaHeight: d.antennaHeight,
        range: d.range,
        ip: d.ip,
        lat: d.lat,
        lng: d.lng,
      },
    })),
  };
}

function deviceInfoHtml(p: Record<string, unknown>): string {
  const color = String(p.color);
  const ip = p.ip ? `<p class="ng-popup-ip">${escapeHtml(String(p.ip))}</p>` : '';
  return `<div class="ng-popup-body">
    <p class="ng-popup-title" style="color:${color}">${escapeHtml(String(p.name))}</p>
    <p class="ng-popup-sub">${escapeHtml(String(p.kind)).toUpperCase()} &middot; ${p.frequency} GHz &middot; ${p.txPower} dBm TX</p>
    <p class="ng-popup-sub2">Antenna: ${p.antennaHeight} m AGL &middot; Range: ${p.range} m</p>
    <p class="ng-popup-coord">${Number(p.lat).toFixed(6)}, ${Number(p.lng).toFixed(6)}</p>
    ${ip}
  </div>`;
}

function DeviceLayer() {
  const map = useGlobeMap();
  const devices = useMapStore((s) => s.deviceList());
  const selectedId = useMapStore((s) => s.selectedDeviceId);
  const selectDevice = useMapStore((s) => s.selectDevice);

  useEffect(() => {
    if (!map) return;
    ensureSource(map, DEV_RING_SRC);
    ensureLayer(map, {
      id: DEV_RING_FILL,
      type: 'fill',
      source: DEV_RING_SRC,
      paint: { 'fill-color': ['get', 'color'], 'fill-opacity': ['get', 'opacity'] },
    });
    ensureLayer(map, {
      id: DEV_RING_LINE,
      type: 'line',
      source: DEV_RING_SRC,
      paint: { 'line-color': ['get', 'color'], 'line-opacity': ['get', 'opacity'] },
    });
    ensureSource(map, DEV_POINT_SRC);
    ensureLayer(map, {
      id: DEV_POINT_LAYER,
      type: 'circle',
      source: DEV_POINT_SRC,
      paint: {
        'circle-radius': ['case', ['get', 'selected'], 14, 10],
        'circle-color': ['get', 'color'],
        'circle-opacity': 0.92,
        'circle-stroke-color': ['case', ['get', 'selected'], '#ffffff', ['get', 'color']],
        'circle-stroke-width': ['case', ['get', 'selected'], 3, 2],
      },
    });

    const onClick = (e: MapLayerMouseEvent) => {
      const f = e.features?.[0];
      if (!f?.properties) return;
      selectDevice(String(f.properties.id));
      openPopup(map, e.lngLat, deviceInfoHtml(f.properties));
    };
    const enter = () => setCursor(map, 'pointer');
    const leave = () => setCursor(map, '');
    map.on('click', DEV_POINT_LAYER, onClick);
    map.on('mouseenter', DEV_POINT_LAYER, enter);
    map.on('mouseleave', DEV_POINT_LAYER, leave);

    return () => {
      map.off('click', DEV_POINT_LAYER, onClick);
      map.off('mouseenter', DEV_POINT_LAYER, enter);
      map.off('mouseleave', DEV_POINT_LAYER, leave);
      teardown(map, DEV_RING_SRC, [DEV_RING_FILL, DEV_RING_LINE]);
      teardown(map, DEV_POINT_SRC, [DEV_POINT_LAYER]);
    };
  }, [map, selectDevice]);

  useEffect(() => {
    if (!map) return;
    setSourceData(map, DEV_RING_SRC, devicesRingsFC(devices));
    setSourceData(map, DEV_POINT_SRC, devicesPointsFC(devices, selectedId));
  }, [map, devices, selectedId]);

  const labels = useMemo<LabelItem[]>(
    () => devices.map((d) => ({ id: d.id, lat: d.lat, lng: d.lng, text: d.name, color: KIND_COLOR[d.kind] })),
    [devices],
  );
  useLabelMarkers(map, labels);

  return null;
}

/* -------------------------------------------------------------------------- */
/* Legacy mapStore links — LOS-aware polylines + popup                         */
/* -------------------------------------------------------------------------- */
const LINK_SRC = 'ng-links';
const LINK_SOLID = 'ng-links-solid';
const LINK_BLOCKED = 'ng-links-blocked';
const LINK_PARTIAL = 'ng-links-partial';
const LINK_LAYER_IDS = [LINK_SOLID, LINK_BLOCKED, LINK_PARTIAL];

function legacyLinksFC(links: MapLink[], devById: Map<string, MapDevice>): GeoJSON.FeatureCollection {
  const features: GeoJSON.Feature[] = [];
  for (const link of links) {
    const from = devById.get(link.fromId);
    const to = devById.get(link.toId);
    if (!from || !to) continue;
    const dash = link.los === 'blocked' ? 'blocked' : link.los === 'partial' ? 'partial' : 'solid';
    const losLabel =
      link.los === 'clear'
        ? 'Line of sight: Clear'
        : link.los === 'partial'
          ? 'Partial Fresnel obstruction'
          : link.los === 'blocked'
            ? 'LOS blocked'
            : 'LOS unknown (checking…)';
    const losColor =
      link.los === 'clear' ? '#34C759' : link.los === 'partial' ? '#FFCC00' : link.los === 'blocked' ? '#FF453A' : '#8E8E93';
    features.push({
      type: 'Feature',
      geometry: {
        type: 'LineString',
        coordinates: [
          [from.lng, from.lat],
          [to.lng, to.lat],
        ],
      },
      properties: {
        color: linkColor(link),
        dash,
        width: link.los === 'blocked' ? 2 : 2.5,
        opacity: link.los === 'blocked' ? 0.7 : 0.9,
        effectiveRssi: link.rssi - link.obstructionDb,
        distance: link.distance,
        rssi: link.rssi,
        rainDb: link.rainDb,
        obstructionDb: link.obstructionDb,
        fresnelM: link.fresnelM,
        losLabel,
        losColor,
        fromName: from.name,
        toName: to.name,
      },
    });
  }
  return { type: 'FeatureCollection', features };
}

function linkInfoHtml(p: Record<string, unknown>): string {
  const rain = Number(p.rainDb) > 0 ? `<p>Rain fade: &minus;${Number(p.rainDb).toFixed(1)} dB</p>` : '';
  const obstr =
    Number(p.obstructionDb) > 0 ? `<p>Terrain loss: &minus;${Number(p.obstructionDb).toFixed(1)} dB</p>` : '';
  return `<div class="ng-popup-body">
    <p class="ng-popup-title" style="color:${p.color}">RSSI: ${Number(p.effectiveRssi).toFixed(1)} dBm</p>
    <div class="ng-popup-sub">
      <p>Distance: ${Number(p.distance).toLocaleString()} m</p>
      <p>FSPL RSSI: ${Number(p.rssi).toFixed(1)} dBm</p>
      ${rain}${obstr}
      <p>Fresnel r&#8321;: ${p.fresnelM} m</p>
    </div>
    <p class="ng-popup-los" style="color:${p.losColor}">${escapeHtml(String(p.losLabel))}</p>
    <p class="ng-popup-coord">${escapeHtml(String(p.fromName))} &rarr; ${escapeHtml(String(p.toName))}</p>
  </div>`;
}

function LinkLayer() {
  const map = useGlobeMap();
  const links = useMapStore((s) => s.linkList());
  const devById = useMapStore((s) => s.devices);

  useEffect(() => {
    if (!map) return;
    ensureSource(map, LINK_SRC);
    const basePaint = {
      'line-color': ['get', 'color'],
      'line-width': ['get', 'width'],
      'line-opacity': ['get', 'opacity'],
    };
    ensureLayer(map, { id: LINK_SOLID, type: 'line', source: LINK_SRC, paint: basePaint, filter: ['==', ['get', 'dash'], 'solid'] });
    ensureLayer(map, {
      id: LINK_BLOCKED,
      type: 'line',
      source: LINK_SRC,
      paint: { ...basePaint, 'line-dasharray': [1.4, 1] },
      filter: ['==', ['get', 'dash'], 'blocked'],
    });
    ensureLayer(map, {
      id: LINK_PARTIAL,
      type: 'line',
      source: LINK_SRC,
      paint: { ...basePaint, 'line-dasharray': [2.4, 0.8] },
      filter: ['==', ['get', 'dash'], 'partial'],
    });

    const onClick = (e: MapLayerMouseEvent) => {
      const f = e.features?.[0];
      if (!f?.properties) return;
      openPopup(map, e.lngLat, linkInfoHtml(f.properties));
    };
    const enter = () => setCursor(map, 'pointer');
    const leave = () => setCursor(map, '');
    for (const id of LINK_LAYER_IDS) {
      map.on('click', id, onClick);
      map.on('mouseenter', id, enter);
      map.on('mouseleave', id, leave);
    }

    return () => {
      for (const id of LINK_LAYER_IDS) {
        map.off('click', id, onClick);
        map.off('mouseenter', id, enter);
        map.off('mouseleave', id, leave);
      }
      teardown(map, LINK_SRC, LINK_LAYER_IDS);
    };
  }, [map]);

  useEffect(() => {
    if (!map) return;
    setSourceData(map, LINK_SRC, legacyLinksFC(links, devById));
  }, [map, links, devById]);

  return null;
}

/* -------------------------------------------------------------------------- */
/* Topology nodes/links — real backend graph with lat/lon                      */
/* -------------------------------------------------------------------------- */
const NODE_KIND_COLOR: Record<string, string> = {
  router: '#2F6BFF',
  switch: '#27C28B',
  host: '#8A93A6',
  ap: '#7C5CFC',
  cpe: '#007AFF',
  olt: '#F5A623',
  firewall: '#FF4D4F',
  server: '#27B5C2',
  cloud: '#6B7280',
};

const NODE_KIND_LABEL: Record<string, string> = {
  router: 'RTR', switch: 'SW', host: 'HOST',
  ap: 'AP', cpe: 'CPE', olt: 'OLT',
  firewall: 'FW', server: 'SRV', cloud: 'CLD',
};

function nodeForEndpoint(ref: string, nodeById: Map<string, NodeModel>): NodeModel | undefined {
  const direct = nodeById.get(ref);
  if (direct) return direct;
  for (const n of nodeById.values()) {
    if (n.interfaces.some((i) => i.id === ref)) return n;
  }
  return undefined;
}

function topoNodesFC(nodes: NodeModel[], selectedId: string | null): GeoJSON.FeatureCollection {
  return {
    type: 'FeatureCollection',
    features: nodes.map((n) => ({
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [n.lon as number, n.lat as number] },
      properties: {
        id: n.id,
        selected: n.id === selectedId,
        color: NODE_KIND_COLOR[n.kind] ?? '#8A93A6',
        name: n.name,
        kind: n.kind,
        status: n.status,
        lat: n.lat,
        lon: n.lon,
      },
    })),
  };
}

function topoNodeInfoHtml(p: Record<string, unknown>): string {
  return `<div class="ng-popup-body">
    <p class="ng-popup-title" style="color:${p.color}">${escapeHtml(String(p.name))}</p>
    <p class="ng-popup-sub">${escapeHtml(String(p.kind)).toUpperCase()} &middot; ${escapeHtml(String(p.status))}</p>
    <p class="ng-popup-coord">${Number(p.lat).toFixed(6)}, ${Number(p.lon).toFixed(6)}</p>
  </div>`;
}

function topoLinksFC(links: LinkModel[], nodeById: Map<string, NodeModel>): GeoJSON.FeatureCollection {
  const features: GeoJSON.Feature[] = [];
  for (const link of links) {
    const a = nodeForEndpoint(link.a_iface, nodeById);
    const b = nodeForEndpoint(link.b_iface, nodeById);
    if (!a || !b || a.lat == null || a.lon == null || b.lat == null || b.lon == null) continue;
    const wireless = link.type === 'wireless';
    features.push({
      type: 'Feature',
      geometry: {
        type: 'LineString',
        coordinates: [
          [a.lon, a.lat],
          [b.lon, b.lat],
        ],
      },
      properties: {
        id: link.id,
        wireless,
        color: wireless ? '#7C5CFC' : '#27B5C2',
        midLng: (a.lon + b.lon) / 2,
        midLat: (a.lat + b.lat) / 2,
        label: `${a.name} ↔ ${b.name} · ${link.type}`,
      },
    });
  }
  return { type: 'FeatureCollection', features };
}

const TNODE_SRC = 'ng-topo-nodes';
const TNODE_LAYER = 'ng-topo-nodes-circle';

function TopologyNodeLayer() {
  const map = useGlobeMap();
  const nodes = useTopologyStore((s) => s.nodeList());
  const selectedId = useTopologyStore((s) => s.selectedNodeId);
  const select = useTopologyStore((s) => s.select);
  const geoNodes = useMemo(() => nodes.filter((n) => n.lat != null && n.lon != null), [nodes]);

  useEffect(() => {
    if (!map) return;
    ensureSource(map, TNODE_SRC);
    ensureLayer(map, {
      id: TNODE_LAYER,
      type: 'circle',
      source: TNODE_SRC,
      paint: {
        'circle-radius': ['case', ['get', 'selected'], 12, 8],
        'circle-color': ['get', 'color'],
        'circle-opacity': 0.9,
        'circle-stroke-color': ['case', ['get', 'selected'], '#ffffff', ['get', 'color']],
        'circle-stroke-width': ['case', ['get', 'selected'], 3, 2],
      },
    });

    const onClick = (e: MapLayerMouseEvent) => {
      const f = e.features?.[0];
      if (!f?.properties) return;
      select({ nodeId: String(f.properties.id) });
      openPopup(map, e.lngLat, topoNodeInfoHtml(f.properties));
    };
    const enter = () => setCursor(map, 'pointer');
    const leave = () => setCursor(map, '');
    map.on('click', TNODE_LAYER, onClick);
    map.on('mouseenter', TNODE_LAYER, enter);
    map.on('mouseleave', TNODE_LAYER, leave);

    return () => {
      map.off('click', TNODE_LAYER, onClick);
      map.off('mouseenter', TNODE_LAYER, enter);
      map.off('mouseleave', TNODE_LAYER, leave);
      teardown(map, TNODE_SRC, [TNODE_LAYER]);
    };
  }, [map, select]);

  useEffect(() => {
    if (!map) return;
    setSourceData(map, TNODE_SRC, topoNodesFC(geoNodes, selectedId));
  }, [map, geoNodes, selectedId]);

  const labels = useMemo<LabelItem[]>(
    () =>
      geoNodes.map((n) => ({
        id: n.id,
        lat: n.lat as number,
        lng: n.lon as number,
        text: `[${NODE_KIND_LABEL[n.kind] ?? n.kind.toUpperCase()}] ${n.name}`,
        color: NODE_KIND_COLOR[n.kind] ?? '#8A93A6',
      })),
    [geoNodes],
  );
  useLabelMarkers(map, labels);

  return null;
}

const TLINK_SRC = 'ng-topo-links';
const TLINK_CABLED = 'ng-topo-links-cabled';
const TLINK_WIRELESS = 'ng-topo-links-wireless';

function TopologyLinkLayer() {
  const map = useGlobeMap();
  const links = useTopologyStore((s) => s.linkList());
  const nodeById = useTopologyStore((s) => s.nodes);

  useEffect(() => {
    if (!map) return;
    ensureSource(map, TLINK_SRC);
    ensureLayer(map, {
      id: TLINK_CABLED,
      type: 'line',
      source: TLINK_SRC,
      paint: { 'line-color': ['get', 'color'], 'line-width': 2, 'line-opacity': 0.85 },
      filter: ['==', ['get', 'wireless'], false],
    });
    ensureLayer(map, {
      id: TLINK_WIRELESS,
      type: 'line',
      source: TLINK_SRC,
      paint: { 'line-color': ['get', 'color'], 'line-width': 2, 'line-opacity': 0.85, 'line-dasharray': [1.5, 1] },
      filter: ['==', ['get', 'wireless'], true],
    });
    return () => teardown(map, TLINK_SRC, [TLINK_CABLED, TLINK_WIRELESS]);
  }, [map]);

  const fc = useMemo(() => topoLinksFC(links, nodeById), [links, nodeById]);
  useEffect(() => {
    if (map) setSourceData(map, TLINK_SRC, fc);
  }, [map, fc]);

  const labels = useMemo<LabelItem[]>(
    () =>
      fc.features.map((f) => {
        const p = f.properties as { id: string; midLat: number; midLng: number; label: string };
        return { id: `tlink-${p.id}`, lat: p.midLat, lng: p.midLng, text: p.label, color: '#C9D1E0' };
      }),
    [fc],
  );
  useLabelMarkers(map, labels);

  return null;
}

/* -------------------------------------------------------------------------- */
/* OSM towers/buildings — reference-only overlays fed by Overpass              */
/* -------------------------------------------------------------------------- */
const OSM_KIND_COLOR: Record<string, string> = {
  bts: '#FF6B00',
  mast: '#E040FB',
  microwave: '#00E5FF',
  broadcast: '#FFD600',
};

const OSM_KIND_LABEL: Record<string, string> = {
  bts: 'BTS',
  mast: 'MAST',
  microwave: 'MW',
  broadcast: 'TX',
};

const TOWER_SRC = 'ng-osm-towers';
const TOWER_LAYER = 'ng-osm-towers-circle';

function towersFC(towers: OsmTower[]): GeoJSON.FeatureCollection {
  return {
    type: 'FeatureCollection',
    features: towers.map((t) => {
      const kind = towerKind(t);
      return {
        type: 'Feature',
        geometry: { type: 'Point', coordinates: [t.lng, t.lat] },
        properties: {
          id: t.id,
          color: OSM_KIND_COLOR[kind] ?? '#FF6B00',
          label: OSM_KIND_LABEL[kind] ?? 'BTS',
          name: towerLabel(t),
          operator: t.tags.operator ?? '',
          height: t.tags.height ?? '',
          lat: t.lat,
          lng: t.lng,
        },
      };
    }),
  };
}

function osmTowerInfoHtml(p: Record<string, unknown>): string {
  const operator = p.operator ? `<p class="ng-popup-sub2">Operator: ${escapeHtml(String(p.operator))}</p>` : '';
  const height = p.height ? `<p class="ng-popup-sub2">Height: ${escapeHtml(String(p.height))} m</p>` : '';
  return `<div class="ng-popup-body">
    <p class="ng-popup-title" style="color:${p.color}">${escapeHtml(String(p.name))}</p>
    <p class="ng-popup-sub">OSM ID: ${p.id} &middot; Type: ${escapeHtml(String(p.label))}</p>
    ${operator}${height}
    <p class="ng-popup-coord">${Number(p.lat).toFixed(6)}, ${Number(p.lng).toFixed(6)}</p>
    <p class="ng-popup-attrib">Source: OpenStreetMap contributors</p>
  </div>`;
}

function OsmTowerLayer() {
  const map = useGlobeMap();
  const [towers, setTowers] = useState<OsmTower[]>([]);
  const [loading, setLoading] = useState(false);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const safetyRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const reqSeqRef = useRef(0);

  useEffect(() => {
    if (!map) return;
    ensureSource(map, TOWER_SRC);
    ensureLayer(
      map,
      {
        id: TOWER_LAYER,
        type: 'circle',
        source: TOWER_SRC,
        paint: {
          'circle-radius': 6,
          'circle-opacity': 0,
          'circle-stroke-color': ['get', 'color'],
          'circle-stroke-width': 1.5,
          'circle-stroke-opacity': 0.55,
        },
      },
      firstVectorLayerId(map),
    );
    const onClick = (e: MapLayerMouseEvent) => {
      const f = e.features?.[0];
      if (!f?.properties) return;
      openPopup(map, e.lngLat, osmTowerInfoHtml(f.properties));
    };
    const enter = () => setCursor(map, 'pointer');
    const leave = () => setCursor(map, '');
    map.on('click', TOWER_LAYER, onClick);
    map.on('mouseenter', TOWER_LAYER, enter);
    map.on('mouseleave', TOWER_LAYER, leave);

    return () => {
      map.off('click', TOWER_LAYER, onClick);
      map.off('mouseenter', TOWER_LAYER, enter);
      map.off('mouseleave', TOWER_LAYER, leave);
      teardown(map, TOWER_SRC, [TOWER_LAYER]);
    };
  }, [map]);

  useEffect(() => {
    if (!map) return;
    let mounted = true;
    const clearSafety = () => {
      if (safetyRef.current) {
        clearTimeout(safetyRef.current);
        safetyRef.current = null;
      }
    };
    const load = () => {
      if (map.getZoom() < 12) {
        setTowers([]);
        setLoading(false);
        clearSafety();
        return;
      }
      if (debounceRef.current) clearTimeout(debounceRef.current);
      debounceRef.current = setTimeout(() => {
        const b = map.getBounds();
        const seq = ++reqSeqRef.current;
        setLoading(true);
        clearSafety();
        safetyRef.current = setTimeout(() => {
          if (mounted) setLoading(false);
        }, 27_000);

        fetchOsmTowers(b.getSouth(), b.getWest(), b.getNorth(), b.getEast())
          .then((result) => {
            if (!mounted || seq !== reqSeqRef.current) return;
            setTowers(result);
          })
          .catch(() => {
            if (!mounted || seq !== reqSeqRef.current) return;
            setTowers([]);
          })
          .finally(() => {
            if (!mounted || seq !== reqSeqRef.current) return;
            clearSafety();
            setLoading(false);
          });
      }, 600);
    };

    map.on('moveend', load);
    map.on('zoomend', load);
    load();

    return () => {
      mounted = false;
      map.off('moveend', load);
      map.off('zoomend', load);
      if (debounceRef.current) clearTimeout(debounceRef.current);
      clearSafety();
    };
  }, [map]);

  useEffect(() => {
    if (!map) return;
    setSourceData(map, TOWER_SRC, towersFC(towers));
  }, [map, towers]);

  const labels = useMemo<LabelItem[]>(
    () =>
      towers.map((t) => {
        const kind = towerKind(t);
        return {
          id: String(t.id),
          lat: t.lat,
          lng: t.lng,
          text: `[${OSM_KIND_LABEL[kind] ?? 'BTS'}] ${towerLabel(t)}`,
          color: OSM_KIND_COLOR[kind] ?? '#FF6B00',
        };
      }),
    [towers],
  );
  useLabelMarkers(map, labels);

  return loading ? (
    <div
      className={cn('pointer-events-none absolute left-[60px] top-2 rounded-md px-2 py-0.5 text-[10px]', zc.workspace)}
      style={{ background: 'rgba(0,0,0,0.65)', color: '#FF6B00' }}
    >
      Loading OSM towers…
    </div>
  ) : null;
}

const BLDG_SRC = 'ng-osm-buildings';
const BLDG_LAYER = 'ng-osm-buildings-fill';

function OsmBuildingsLayer({ densityMode }: { densityMode: boolean }) {
  const map = useGlobeMap();
  const [buildings, setBuildings] = useState<OsmBuilding[]>([]);
  const [loading, setLoading] = useState(false);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const safetyRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const reqSeqRef = useRef(0);

  useEffect(() => {
    if (!map) return;
    ensureSource(map, BLDG_SRC);
    ensureLayer(
      map,
      {
        id: BLDG_LAYER,
        type: 'fill',
        source: BLDG_SRC,
        paint: { 'fill-color': ['get', 'color'], 'fill-opacity': ['get', 'fillOpacity'] },
      },
      firstVectorLayerId(map),
    );
    return () => teardown(map, BLDG_SRC, [BLDG_LAYER]);
  }, [map]);

  useEffect(() => {
    if (!map) return;
    let mounted = true;
    const clearSafety = () => {
      if (safetyRef.current) {
        clearTimeout(safetyRef.current);
        safetyRef.current = null;
      }
    };
    const load = () => {
      if (map.getZoom() < 16) {
        setBuildings([]);
        setLoading(false);
        clearSafety();
        return;
      }
      if (debounceRef.current) clearTimeout(debounceRef.current);
      debounceRef.current = setTimeout(() => {
        const b = map.getBounds();
        const seq = ++reqSeqRef.current;
        setLoading(true);
        clearSafety();
        safetyRef.current = setTimeout(() => {
          if (mounted) setLoading(false);
        }, 27_000);

        fetchOsmBuildings(b.getSouth(), b.getWest(), b.getNorth(), b.getEast())
          .then((result) => {
            if (!mounted || seq !== reqSeqRef.current) return;
            setBuildings(result);
          })
          .catch(() => {
            if (!mounted || seq !== reqSeqRef.current) return;
            setBuildings([]);
          })
          .finally(() => {
            if (!mounted || seq !== reqSeqRef.current) return;
            clearSafety();
            setLoading(false);
          });
      }, 600);
    };

    map.on('moveend', load);
    map.on('zoomend', load);
    load();

    return () => {
      mounted = false;
      map.off('moveend', load);
      map.off('zoomend', load);
      if (debounceRef.current) clearTimeout(debounceRef.current);
      clearSafety();
    };
  }, [map]);

  useEffect(() => {
    if (!map) return;
    const colors = densityMode ? densityColors(buildings.map((b) => b.center)) : null;
    const fc: GeoJSON.FeatureCollection = {
      type: 'FeatureCollection',
      features: buildings.map((b, i) => ({
        type: 'Feature',
        geometry: { type: 'Polygon', coordinates: [b.ring.map(([lat, lng]) => [lng, lat])] },
        properties: {
          color: (colors ? colors[i] : null) ?? '#4C9AFF',
          fillOpacity: densityMode ? 0.55 : 0.2,
        },
      })),
    };
    setSourceData(map, BLDG_SRC, fc);
  }, [map, buildings, densityMode]);

  return loading ? (
    <div
      className={cn('pointer-events-none absolute left-[60px] top-2 rounded-md px-2 py-0.5 text-[10px]', zc.workspace)}
      style={{ background: 'rgba(0,0,0,0.65)', color: '#4C9AFF' }}
    >
      {densityMode ? 'Loading density…' : 'Loading buildings…'}
    </div>
  ) : null;
}

/* -------------------------------------------------------------------------- */
/* Geocoding search result — flyTo + temporary marker                          */
/* -------------------------------------------------------------------------- */
const SEARCH_SRC = 'ng-search-result';
const SEARCH_LAYER = 'ng-search-result-circle';

function SearchResultLayer() {
  const map = useGlobeMap();
  const result = useMapStore((s) => s.searchResult);

  useEffect(() => {
    if (!map) return;
    ensureSource(map, SEARCH_SRC);
    ensureLayer(map, {
      id: SEARCH_LAYER,
      type: 'circle',
      source: SEARCH_SRC,
      paint: {
        'circle-radius': 9,
        'circle-color': '#FF9F0A',
        'circle-opacity': 0.35,
        'circle-stroke-color': '#FF9F0A',
        'circle-stroke-width': 3,
      },
    });
    return () => teardown(map, SEARCH_SRC, [SEARCH_LAYER]);
  }, [map]);

  useEffect(() => {
    if (!map) return;
    const fc: GeoJSON.FeatureCollection = result
      ? {
          type: 'FeatureCollection',
          features: [
            {
              type: 'Feature',
              geometry: { type: 'Point', coordinates: [result.lng, result.lat] },
              properties: {},
            },
          ],
        }
      : EMPTY_FC;
    setSourceData(map, SEARCH_SRC, fc);
    if (result) map.flyTo({ center: [result.lng, result.lat], zoom: 16, duration: 1200 });
  }, [map, result]);

  const labels = useMemo<LabelItem[]>(
    () => (result ? [{ id: 'search-result', lat: result.lat, lng: result.lng, text: result.label, color: '#FF9F0A' }] : []),
    [result],
  );
  useLabelMarkers(map, labels);

  return null;
}

/* -------------------------------------------------------------------------- */
/* RF coverage raster — Stage 3, best-server RSSI overlay (design doc §4)      */
/* Ports the removed `L.imageOverlay` + custom `rfCoverage` pane straight to a */
/* MapLibre `image` source + `raster` layer — same canvas painting, same       */
/* debounce/stale-guard/safety-timeout as the OSM layers above, same beforeId  */
/* anchor (firstVectorLayerId) as OSM towers/buildings so the raster always    */
/* re-inserts above the basemap but below every vector overlay, matching the   */
/* old pane's z-index=350 (tilePane=200 < 350 < overlayPane=400).              */
/* -------------------------------------------------------------------------- */
const COVERAGE_SRC = 'ng-rf-coverage';
const COVERAGE_LAYER = 'ng-rf-coverage-raster';

function RfCoverageLayer() {
  const map = useGlobeMap();
  const deviceMap = useMapStore((s) => s.devices);
  const opacity = useMapStore((s) => s.gisLayers['rf-coverage']?.opacity ?? 0.55);
  const [status, setStatus] = useState<'idle' | 'loading' | 'ok' | 'empty' | 'error'>('idle');
  const [siteCount, setSiteCount] = useState(0);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const safetyRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const reqSeqRef = useRef(0);

  const sites = useMemo<CoverageSite[]>(
    () =>
      Array.from(deviceMap.values())
        .filter((d) => d.kind === 'ap' || d.kind === 'tower')
        .map((d) => ({
          lat: d.lat,
          lon: d.lng,
          height_m: d.antennaHeight,
          tx_power_dbm: d.txPower,
          freq_mhz: d.frequency * 1000, // GHz → MHz
        })),
    [deviceMap],
  );

  useEffect(() => {
    if (!map) return;
    let mounted = true;
    const clearSafety = () => {
      if (safetyRef.current) {
        clearTimeout(safetyRef.current);
        safetyRef.current = null;
      }
    };
    const removeOverlay = () => {
      if (map.getLayer(COVERAGE_LAYER)) map.removeLayer(COVERAGE_LAYER);
      if (map.getSource(COVERAGE_SRC)) map.removeSource(COVERAGE_SRC);
    };

    const load = () => {
      setSiteCount(sites.length);
      if (sites.length === 0) {
        removeOverlay();
        setStatus('empty');
        clearSafety();
        return;
      }
      if (debounceRef.current) clearTimeout(debounceRef.current);
      debounceRef.current = setTimeout(() => {
        const b = map.getBounds();
        const south = b.getSouth();
        const west = b.getWest();
        const north = b.getNorth();
        const east = b.getEast();
        const midLat = (south + north) / 2;
        const midLon = (west + east) / 2;
        const widthM = haversineM(midLat, west, midLat, east);
        const heightM = haversineM(south, midLon, north, midLon);
        const { rows, cols } = coverageGrid(widthM / Math.max(heightM, 1e-6));

        const seq = ++reqSeqRef.current;
        setStatus('loading');
        clearSafety();
        safetyRef.current = setTimeout(() => {
          if (mounted && seq === reqSeqRef.current) setStatus('error');
        }, 12_000);

        rfApi
          .coverage({
            sites,
            technology: 'wifi_5ghz',
            model_id: 'fspl',
            rows,
            cols,
            min_lat: south,
            min_lon: west,
            max_lat: north,
            max_lon: east,
            rx_height_m: 1.5,
          })
          .then((res) => {
            if (!mounted || seq !== reqSeqRef.current) return;
            // Paint one pixel per cell; flip vertically (backend row 0 = south,
            // canvas y=0 = north) so the image aligns to its bounds below.
            const canvas = document.createElement('canvas');
            canvas.width = res.cols;
            canvas.height = res.rows;
            const ctx = canvas.getContext('2d');
            if (!ctx) return;
            for (let y = 0; y < res.rows; y++) {
              const srcRow = res.values[res.rows - 1 - y];
              for (let x = 0; x < res.cols; x++) {
                const dbm = srcRow?.[x] ?? -120;
                if (dbm < -95) continue;
                ctx.fillStyle = rssiRampCss(dbm);
                ctx.fillRect(x, y, 1, 1);
              }
            }
            const url = canvas.toDataURL();
            // MapLibre image-source coordinates: TL, TR, BR, BL, clockwise,
            // [lng, lat] each — confirmed against maplibre-gl.d.ts's own
            // documented example. North=top of the flipped canvas above.
            const coords: [[number, number], [number, number], [number, number], [number, number]] = [
              [res.bounds.min_lon, res.bounds.max_lat],
              [res.bounds.max_lon, res.bounds.max_lat],
              [res.bounds.max_lon, res.bounds.min_lat],
              [res.bounds.min_lon, res.bounds.min_lat],
            ];
            const existing = map.getSource(COVERAGE_SRC) as ImageSource | undefined;
            if (existing) {
              existing.updateImage({ url, coordinates: coords });
            } else {
              map.addSource(COVERAGE_SRC, { type: 'image', url, coordinates: coords });
              map.addLayer(
                { id: COVERAGE_LAYER, type: 'raster', source: COVERAGE_SRC, paint: { 'raster-opacity': opacity } },
                firstVectorLayerId(map),
              );
            }
            setStatus('ok');
          })
          .catch(() => {
            if (!mounted || seq !== reqSeqRef.current) return;
            removeOverlay();
            setStatus('error');
          })
          .finally(() => {
            if (!mounted || seq !== reqSeqRef.current) return;
            clearSafety();
          });
      }, 600);
    };

    map.on('moveend', load);
    map.on('zoomend', load);
    load();

    return () => {
      mounted = false;
      map.off('moveend', load);
      map.off('zoomend', load);
      if (debounceRef.current) clearTimeout(debounceRef.current);
      clearSafety();
      removeOverlay();
    };
    // opacity intentionally excluded — handled by the setPaintProperty effect below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map, sites]);

  // Opacity is a cheap live update — never trigger a recompute for it.
  useEffect(() => {
    if (map?.getLayer(COVERAGE_LAYER)) map.setPaintProperty(COVERAGE_LAYER, 'raster-opacity', opacity);
  }, [map, opacity]);

  return (
    <div className={cn('pointer-events-none absolute bottom-10 left-4', zc.workspace)}>
      <div className="rounded-xl border border-fg/15 bg-recess/60 px-3 py-2 shadow-glass backdrop-blur">
        <p className="mb-1.5 flex items-center gap-1.5 text-[9px] font-semibold uppercase tracking-wider text-fg/40">
          RF Coverage
          {status === 'loading' && (
            <span className="inline-block h-2 w-2 animate-spin rounded-full border-2 border-fg/25 border-t-fg/70" />
          )}
        </p>

        {status === 'empty' ? (
          <p className="max-w-[150px] text-[10px] leading-snug text-fg/45">
            Place an AP or Tower to compute best-server coverage.
          </p>
        ) : status === 'error' ? (
          <p className="max-w-[150px] text-[10px] leading-snug text-danger/80">
            Coverage compute failed — pan or retry.
          </p>
        ) : (
          <>
            <div className="flex items-center gap-2">
              <span className="text-[10px] text-fg/60">−95</span>
              <span
                className="h-2.5 w-24 rounded-full"
                style={{
                  background: 'linear-gradient(90deg, #FF453A 0%, #FFCC00 40%, #A3E635 70%, #34C759 100%)',
                }}
              />
              <span className="text-[10px] text-fg/60">−55</span>
            </div>
            <p className="mt-1 text-[9px] text-fg/35">
              dBm · best-server · {siteCount} site{siteCount === 1 ? '' : 's'}
            </p>
          </>
        )}
      </div>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* RF PtP beam — the beam line between the two chosen RF-workspace endpoints. */
/* RfBeamLayer.tsx was deleted with the Leaflet render layer (Stage 1); this   */
/* ports it back as a GeoJSON source pair instead of reviving the file, since  */
/* it needs the same MapCtx this file already owns. Two line layers (solid/   */
/* dashed) reuse the LinkLayer convention above — MapLibre's line-dasharray    */
/* paint property isn't data-driven, so a boolean `dashed` property is a      */
/* `filter`, not a paint expression, exactly like LINK_SOLID/LINK_BLOCKED.     */
/* -------------------------------------------------------------------------- */
const BEAM_LINE_SRC = 'ng-rf-beam-line';
const BEAM_LINE_SOLID = 'ng-rf-beam-line-solid';
const BEAM_LINE_DASHED = 'ng-rf-beam-line-dashed';
const BEAM_LINE_IDS = [BEAM_LINE_SOLID, BEAM_LINE_DASHED];
const BEAM_RING_SRC = 'ng-rf-beam-rings';
const BEAM_RING_LAYER = 'ng-rf-beam-rings-circle';

function RfBeamLayer() {
  const map = useGlobeMap();
  const aId = useRfStore((s) => s.aId);
  const bId = useRfStore((s) => s.bId);
  const result = useRfStore((s) => s.result);
  const freqGhz = useRfStore((s) => s.freqGhz);
  const devices = useMapStore((s) => s.devices);
  const a = aId ? devices.get(aId) : undefined;
  const b = bId ? devices.get(bId) : undefined;
  const color = result ? STATUS_COLOR[marginStatus(result.fade_margin_db)] : '#5C8AFF';

  useEffect(() => {
    if (!map) return;
    ensureSource(map, BEAM_LINE_SRC);
    const basePaint = { 'line-color': ['get', 'color'], 'line-width': 3, 'line-opacity': 0.95 };
    ensureLayer(map, {
      id: BEAM_LINE_SOLID,
      type: 'line',
      source: BEAM_LINE_SRC,
      paint: basePaint,
      filter: ['==', ['get', 'dashed'], false],
    });
    ensureLayer(map, {
      id: BEAM_LINE_DASHED,
      type: 'line',
      source: BEAM_LINE_SRC,
      paint: { ...basePaint, 'line-dasharray': [1.2, 1.6] },
      filter: ['==', ['get', 'dashed'], true],
    });
    ensureSource(map, BEAM_RING_SRC);
    ensureLayer(map, {
      id: BEAM_RING_LAYER,
      type: 'circle',
      source: BEAM_RING_SRC,
      paint: {
        'circle-radius': 12,
        'circle-color': ['get', 'color'],
        'circle-opacity': 0.12,
        'circle-stroke-color': ['get', 'color'],
        'circle-stroke-width': 2.5,
      },
    });
    return () => {
      teardown(map, BEAM_LINE_SRC, BEAM_LINE_IDS);
      teardown(map, BEAM_RING_SRC, [BEAM_RING_LAYER]);
    };
  }, [map]);

  useEffect(() => {
    if (!map) return;
    const rings: GeoJSON.Feature[] = [];
    if (a && b) {
      rings.push(
        { type: 'Feature', geometry: { type: 'Point', coordinates: [a.lng, a.lat] }, properties: { color } },
        { type: 'Feature', geometry: { type: 'Point', coordinates: [b.lng, b.lat] }, properties: { color } },
      );
    } else {
      const one = a ?? b;
      if (one) {
        rings.push({
          type: 'Feature',
          geometry: { type: 'Point', coordinates: [one.lng, one.lat] },
          properties: { color: '#5C8AFF' },
        });
      }
    }
    setSourceData(map, BEAM_RING_SRC, { type: 'FeatureCollection', features: rings });

    const lineFc: GeoJSON.FeatureCollection =
      a && b
        ? {
            type: 'FeatureCollection',
            features: [
              {
                type: 'Feature',
                geometry: {
                  type: 'LineString',
                  coordinates: [
                    [a.lng, a.lat],
                    [b.lng, b.lat],
                  ],
                },
                properties: { color, dashed: !result },
              },
            ],
          }
        : EMPTY_FC;
    setSourceData(map, BEAM_LINE_SRC, lineFc);
  }, [map, a, b, color, result]);

  const distM = a && b ? (result?.distance_m ?? haversineM(a.lat, a.lng, b.lat, b.lng)) : 0;
  const labels = useMemo<LabelItem[]>(() => {
    if (!a || !b) return [];
    return [
      {
        id: 'rf-beam-chip',
        lat: (a.lat + b.lat) / 2,
        lng: (a.lng + b.lng) / 2,
        text: `${freqGhz} GHz · ${fmtKm(distM)}`,
        color,
      },
    ];
  }, [a, b, freqGhz, distM, color]);
  useLabelMarkers(map, labels);

  return null;
}

/* -------------------------------------------------------------------------- */
/* Elevation-profile line — endpoints + connecting line while the tool is used */
/* Pure render, driven by mapStore's profilePts (unchanged store contract);    */
/* only the render target moved off Leaflet onto a GeoJSON source.           */
/* -------------------------------------------------------------------------- */
const PROFILE_SRC = 'ng-profile-line';
const PROFILE_LAYER = 'ng-profile-line-layer';

function ProfileLine() {
  const map = useGlobeMap();
  const pts = useMapStore((s) => s.profilePts);

  useEffect(() => {
    if (!map) return;
    ensureSource(map, PROFILE_SRC);
    ensureLayer(map, {
      id: PROFILE_LAYER,
      type: 'line',
      source: PROFILE_SRC,
      paint: { 'line-color': '#A0785A', 'line-width': 2.5, 'line-opacity': 0.9, 'line-dasharray': [2, 6] },
    });
    return () => teardown(map, PROFILE_SRC, [PROFILE_LAYER]);
  }, [map]);

  useEffect(() => {
    if (!map) return;
    const fc: GeoJSON.FeatureCollection =
      pts.length === 2
        ? {
            type: 'FeatureCollection',
            features: [
              {
                type: 'Feature',
                geometry: {
                  type: 'LineString',
                  coordinates: pts.map(([lat, lng]) => [lng, lat]),
                },
                properties: {},
              },
            ],
          }
        : EMPTY_FC;
    setSourceData(map, PROFILE_SRC, fc);
  }, [map, pts]);

  const labels = useMemo<LabelItem[]>(
    () => pts.map(([lat, lng], i) => ({ id: `profile-${i}`, lat, lng, text: i === 0 ? 'TX' : 'RX', color: '#C79A73' })),
    [pts],
  );
  useLabelMarkers(map, labels);

  return null;
}

/* -------------------------------------------------------------------------- */
/* Click-to-place: device placement, measure tool, deploy anchor, and the      */
/* "any click dismisses the search pin" behavior. Rewritten from the removed  */
/* MapEventHandler (Leaflet's useMapEvents hook) per the design doc's Stage 3 */
/* precision-risk callout: `e.latlng` → `e.lngLat`, `e.containerPoint` →      */
/* `e.point`. Both are read by name (`.lat`/`.lng`, `.x`/`.y`) everywhere      */
/* below, never as a positional tuple, so there's no [lat,lng] vs [lng,lat]   */
/* swap risk in this file — mapStore's haversineM/addDevice/addProfilePoint   */
/* signatures (lat, lng) are unchanged and called the same way as before.     */
/*                                                                            */
/* Old Leaflet markers only stopped click propagation to the container for   */
/* three layers — device dots, topology nodes, the search-result pin (see    */
/* main:MapView.tsx DeviceMarker/TopologyNodeMarker/SearchResultLayer). Links */
/* and OSM towers never did, so a click on either still fell through to the   */
/* place/deselect/dismiss logic below — preserved here via an explicit       */
/* queryRenderedFeatures guard instead of relying on MapLibre listener        */
/* registration order (which, unlike DOM bubbling, has no stopPropagation).   */
/* -------------------------------------------------------------------------- */
const KIND_LABEL: Record<MapDeviceKind, string> = { ap: 'AP', cpe: 'CPE', tower: 'TWR' };

const MEASURE_SRC = 'ng-measure-line';
const MEASURE_LAYER = 'ng-measure-line-layer';

function stoppedPropagationLayerIds(map: MapLibreMap): string[] {
  return [DEV_POINT_LAYER, TNODE_LAYER, SEARCH_LAYER].filter((id) => map.getLayer(id));
}

export interface DeployAnchor {
  px: { x: number; y: number };
  lat: number;
  lon: number;
}

function MapClickHandler({ onDeployClick }: { onDeployClick: (anchor: DeployAnchor) => void }) {
  const map = useGlobeMap();
  const tool = useMapStore((s) => s.tool);
  const [measure, setMeasure] = useState<{ start: [number, number]; end: [number, number] | null } | null>(null);

  // Leaving the measure tool clears its line.
  useEffect(() => {
    if (tool !== 'measure') setMeasure(null);
  }, [tool]);

  useEffect(() => {
    if (!map) return;

    const onClick = (e: MapMouseEvent) => {
      const hitIds = stoppedPropagationLayerIds(map);
      if (hitIds.length > 0 && map.queryRenderedFeatures(e.point, { layers: hitIds }).length > 0) return;

      const lat = e.lngLat.lat;
      const lng = e.lngLat.lng;
      const s = useMapStore.getState();

      if (s.searchResult) s.setSearchResult(null);

      if (s.tool === 'select') {
        s.selectDevice(null);
        return;
      }

      if (s.tool === 'profile') {
        s.addProfilePoint(lat, lng);
        return;
      }

      if (s.tool === 'measure') {
        setMeasure((m) => (!m || m.end ? { start: [lat, lng], end: null } : { start: m.start, end: [lat, lng] }));
        return;
      }

      // Deploy tool: show the popover at the click pixel position.
      if (s.tool === 'deploy') {
        onDeployClick({ px: { x: e.point.x, y: e.point.y }, lat, lon: lng });
        return;
      }

      const kind = s.tool as MapDeviceKind;
      const deviceList = s.deviceList();

      // Reject stacking a device on top of an existing one (< 5 m): coincident
      // AP/tower/CPE markers hide each other and skew coverage/link math.
      const tooClose = deviceList.find((d) => haversineM(d.lat, d.lng, lat, lng) < 5);
      if (tooClose) {
        s.flashNotice(`Too close to ${tooClose.name} (< 5 m) — zoom in or pick another spot.`);
        return;
      }

      const count = deviceList.filter((d) => d.kind === kind).length + 1;
      s.addDevice({
        name: `${KIND_LABEL[kind]}-${count}`,
        kind,
        lat,
        lng,
        txPower: kind === 'tower' ? 27 : 20,
        frequency: 5,
        range: kind === 'tower' ? 2000 : 500,
        antennaHeight: kind === 'tower' ? 30 : 6, // metres AGL
        ip: '',
      });
      // Multi-point flow (UISP-style): after an AP goes down, the natural next
      // step is placing its CPE clients — switch so the hint guides the user.
      if (kind === 'ap') s.setTool('cpe');
    };

    map.on('click', onClick);
    return () => {
      map.off('click', onClick);
    };
  }, [map, onDeployClick]);

  // In-map measure result: line + glass distance label (replaces the old
  // alert()). Only while the measure tool is active with both endpoints.
  useEffect(() => {
    if (!map) return;
    ensureSource(map, MEASURE_SRC);
    ensureLayer(map, {
      id: MEASURE_LAYER,
      type: 'line',
      source: MEASURE_SRC,
      paint: { 'line-color': '#2DD4BF', 'line-width': 2.5, 'line-opacity': 0.9, 'line-dasharray': [4, 6] },
    });
    return () => teardown(map, MEASURE_SRC, [MEASURE_LAYER]);
  }, [map]);

  const showMeasure = tool === 'measure' && !!measure?.end;
  useEffect(() => {
    if (!map) return;
    const fc: GeoJSON.FeatureCollection =
      showMeasure && measure?.end
        ? {
            type: 'FeatureCollection',
            features: [
              {
                type: 'Feature',
                geometry: {
                  type: 'LineString',
                  coordinates: [
                    [measure.start[1], measure.start[0]],
                    [measure.end[1], measure.end[0]],
                  ],
                },
                properties: {},
              },
            ],
          }
        : EMPTY_FC;
    setSourceData(map, MEASURE_SRC, fc);
  }, [map, showMeasure, measure]);

  const measureDist =
    showMeasure && measure?.end ? haversineM(measure.start[0], measure.start[1], measure.end[0], measure.end[1]) : 0;
  const measureLabels = useMemo<LabelItem[]>(() => {
    if (!showMeasure || !measure?.end) return [];
    const midLat = (measure.start[0] + measure.end[0]) / 2;
    const midLng = (measure.start[1] + measure.end[1]) / 2;
    return [
      {
        id: 'measure-dist',
        lat: midLat,
        lng: midLng,
        text: `${Math.round(measureDist).toLocaleString()} m · ${(measureDist / 1000).toFixed(2)} km`,
        color: '#2DD4BF',
      },
    ];
  }, [showMeasure, measure, measureDist]);
  useLabelMarkers(map, measureLabels);

  return null;
}

/* -------------------------------------------------------------------------- */
/* Signal legend                                                               */
/* -------------------------------------------------------------------------- */
function SignalLegend() {
  const rainRate = useMapStore((s) => s.rainRate);
  const checkingLos = useMapStore((s) => s.checkingLos);
  const triggerLosCheck = useMapStore((s) => s.triggerLosCheck);

  return (
    <div className={cn('pointer-events-auto absolute bottom-10 right-4 space-y-2', zc.workspace)}>
      {/* LOS check button */}
      <button
        onClick={() => void triggerLosCheck()}
        disabled={checkingLos}
        className="glass-strong flex w-full items-center justify-center gap-1.5 rounded-lg border border-fg/15 px-3 py-1.5 text-xs text-fg/80 transition-colors hover:border-accent/40 hover:text-accent disabled:cursor-not-allowed disabled:opacity-50"
      >
        {checkingLos ? (
          <>
            <span className="inline-block h-2.5 w-2.5 animate-spin rounded-full border-2 border-fg/30 border-t-fg/80" />
            Checking LOS…
          </>
        ) : (
          'Check Line of Sight'
        )}
      </button>

      {/* Signal legend */}
      <div className="glass-strong rounded-xl border border-fg/15 px-3 py-2 shadow-glass">
        <p className="mb-1.5 text-[9px] font-semibold uppercase tracking-wider text-fg/60">
          Signal Quality
        </p>
        <div className="flex flex-col gap-0.5">
          {[
            { label: 'Strong', color: '#34C759', range: '> −55 dBm' },
            { label: 'Good',   color: '#A3E635', range: '−55 to −70' },
            { label: 'Fair',   color: '#FFCC00', range: '−70 to −80' },
            { label: 'Weak',   color: '#FF453A', range: '< −80 dBm' },
          ].map(({ label, color, range }) => (
            <div key={label} className="flex items-center gap-2">
              <span className="h-2 w-5 rounded-sm" style={{ background: color }} />
              <span className="text-[10px] text-fg/80">{label}</span>
              <span className="ml-auto text-[9px] text-fg/55">{range}</span>
            </div>
          ))}
        </div>

        <div className="my-1.5 border-t border-fg/10" />

        <p className="mb-1 text-[9px] font-semibold uppercase tracking-wider text-fg/60">
          LOS Status
        </p>
        <div className="flex flex-col gap-0.5">
          {[
            { label: 'Clear', dash: 'solid', color: '#34C759' },
            { label: 'Partial', dash: 'dashed', color: '#FFCC00' },
            { label: 'Blocked', dash: 'dotted', color: '#FF453A' },
          ].map(({ label, dash, color }) => (
            <div key={label} className="flex items-center gap-2">
              <span
                className="h-0.5 w-5"
                style={{
                  background: color,
                  borderTop: `2px ${dash} ${color}`,
                  height: 0,
                  display: 'block',
                }}
              />
              <span className="text-[10px] text-fg/80">{label}</span>
            </div>
          ))}
        </div>

        {/* Weather indicator */}
        {rainRate > 0 && (
          <>
            <div className="my-1.5 border-t border-fg/10" />
            <div className="flex items-center gap-1.5">
              <span className="text-xs">🌧</span>
              <span className="text-[10px] text-info">
                {rainRateLabel(rainRate)} ({rainRate} mm/hr)
              </span>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Cursor hint strip (bottom center)                                           */
/* -------------------------------------------------------------------------- */
const TOOL_HINTS: Record<string, string> = {
  select: 'Click a device to select it • Delete key removes selected device',
  deploy: 'Click the map to deploy a real device — choose wireless or cabled',
  ap: 'Click the map to place an Access Point (legacy, local-only)',
  cpe: 'Click the map to place a CPE client (legacy, local-only)',
  tower: 'Click the map to place a Tower (legacy, local-only)',
  measure: 'Click two points to measure distance',
  profile: 'Click two points to draw a terrain elevation profile',
};

function ToolHint() {
  const tool = useMapStore((s) => s.tool);
  return (
    <div className={cn('pointer-events-none absolute bottom-6 left-1/2 -translate-x-1/2', zc.workspace)}>
      <div className="rounded-full border border-fg/15 bg-recess/55 px-4 py-1.5 text-xs text-fg/55 shadow-glass backdrop-blur">
        {TOOL_HINTS[tool] ?? ''}
      </div>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Transient notice (top-center) — non-blocking placement warnings etc.        */
/* -------------------------------------------------------------------------- */
function MapNotice() {
  const notice = useMapStore((s) => s.mapNotice);
  if (!notice) return null;
  return (
    <div className={cn('pointer-events-none absolute left-1/2 top-16 -translate-x-1/2', zc.toast)}>
      <div className="flex items-center gap-2 rounded-full border border-warning/40 bg-recess/80 px-4 py-1.5 text-xs text-fg/85 shadow-glass backdrop-blur animate-fade-in">
        <AlertTriangle className="h-3.5 w-3.5 shrink-0 text-warning" />
        {notice}
      </div>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Weather bar (top-center, only when rain > 0)                               */
/* -------------------------------------------------------------------------- */
function WeatherBar() {
  const rainRate = useMapStore((s) => s.rainRate);
  if (rainRate === 0) return null;
  return (
    <div className={cn('pointer-events-none absolute left-1/2 top-4 -translate-x-1/2', zc.workspace)}>
      <div className="flex items-center gap-2 rounded-full border border-blue-400/30 bg-blue-900/60 px-4 py-1.5 text-xs text-blue-200 backdrop-blur">
        <span>🌧</span>
        <span>
          {rainRateLabel(rainRate)} — {rainRate} mm/hr · Rain fade active
        </span>
      </div>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* GIS layer panel toggle (top-right, above the gradient legend)               */
/* -------------------------------------------------------------------------- */
function GisLayerToggle() {
  const open = useMapStore((s) => s.gisPanelOpen);
  const togglePanel = useMapStore((s) => s.toggleGisPanel);
  return (
    <button
      onClick={() => togglePanel()}
      aria-label="Toggle GIS layers"
      aria-pressed={open}
      title="GIS layers"
      className={cn(
        'pointer-events-auto absolute right-4 top-40 grid h-9 w-9 place-items-center rounded-lg border border-fg/15 shadow-glass backdrop-blur transition-colors',
        zc.workspace,
        open ? 'bg-accent/25 text-accent' : 'bg-recess/55 text-fg/70 hover:text-fg',
      )}
    >
      <LayersIcon className="h-4 w-4" />
    </button>
  );
}

/* -------------------------------------------------------------------------- */
/* UISP-style Weak↔Strong gradient legend (top-right)                          */
/* -------------------------------------------------------------------------- */
function GradientLegend() {
  return (
    <div className={cn('pointer-events-none absolute right-4 top-28', zc.workspace)}>
      <div className="glass-strong rounded-xl border border-fg/15 px-3 py-2 shadow-glass">
        <p className="mb-1.5 text-[9px] font-semibold uppercase tracking-wider text-fg/60">
          Signal Strength
        </p>
        <div className="flex items-center gap-2">
          <span className="text-[10px] text-fg/60">Weak</span>
          <span
            className="h-2.5 w-28 rounded-full"
            style={{
              background:
                'linear-gradient(90deg, #FF453A 0%, #FFCC00 40%, #A3E635 70%, #34C759 100%)',
            }}
          />
          <span className="text-[10px] text-fg/60">Strong</span>
        </div>
      </div>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Main MapView                                                                */
/* -------------------------------------------------------------------------- */
export function MapView({ rfMode = false }: { rfMode?: boolean } = {}) {
  const [glMap, setGlMap] = useState<MapLibreMap | null>(null);
  const [deployAnchor, setDeployAnchor] = useState<DeployAnchor | null>(null);
  const showOnboarding = useMapStore((s) => s.showOnboarding);
  const activeModal = useUiStore((s) => s.activeModal);
  const openModal = useUiStore((s) => s.openModal);
  const coverageVisible = useMapStore((s) => s.gisLayers['rf-coverage']?.visible ?? false);
  const towersVisible = useMapStore((s) => s.gisLayers['util-tower']?.visible ?? false);
  const buildingsVisible = useMapStore((s) => s.gisLayers['pop-buildings']?.visible ?? false);
  const densityVisible = useMapStore((s) => s.gisLayers['pop-density']?.visible ?? false);

  // First visit to the standalone map claims the shared modal slot for the
  // quickstart (never in RF, which owns its own chrome). Exclusive by construction.
  useEffect(() => {
    if (rfMode || !showOnboarding) return;
    if (useUiStore.getState().activeModal === null) openModal('mapOnboarding');
  }, [rfMode, showOnboarding, openModal]);

  return (
    <div className="relative h-full w-full overflow-hidden">
      <MapCtx.Provider value={glMap}>
        <GlobeBasemap onMapChange={setGlMap} />

        {/* Vector overlays — GeoJSON sources + style layers (Stage 2). Mount
            order sets z-order: OSM reference layers anchor to the vector floor
            (firstVectorLayerId) so they always stay below real project data,
            regardless of toggle timing; everything else stacks bottom-to-top
            in this order, matching the pre-migration Leaflet z-order. */}
        {towersVisible && <OsmTowerLayer />}
        {(buildingsVisible || densityVisible) && <OsmBuildingsLayer densityMode={densityVisible} />}
        {coverageVisible && <RfCoverageLayer />}
        <SearchResultLayer />
        <ProfileLine />
        <TopologyLinkLayer />
        <TopologyNodeLayer />
        <LinkLayer />
        <DeviceLayer />
        {/* RF workspace: the PtP beam between the two chosen endpoints — always
            on top, matching the old JSX order (rendered last in MapContainer). */}
        {rfMode && <RfBeamLayer />}

        {/* Stage 3: click-to-place (device/measure/profile/deploy) + search-pin
            dismiss. Owns no visible chrome of its own besides the measure line. */}
        <MapClickHandler onDeployClick={setDeployAnchor} />

        {/* Overlay UI */}
        <MapSearch />
        <MapToolbar />
        {/* RF mode owns the right dock + bottom bar, so suppress the generic map
            chrome that would collide (device panel, signal legend, tool hint,
            center-bottom elevation panel). */}
        {!rfMode && <MapDevicePanel />}
        {!rfMode && <SignalLegend />}
        {/* Signal-strength gradient only describes the RF coverage raster — show it
            only when that layer is on, so it doesn't float over the top bar/popovers. */}
        {coverageVisible && <GradientLegend />}
        <GisLayerToggle />
        <GisLayerPanel />
        {!rfMode && <ToolHint />}
        <MapNotice />
        <WeatherBar />
        {/* Top-right stack (QA D8/P3) — each item reserves a fixed slot whether
            or not it's currently visible, so nothing shifts when a conditional
            neighbor toggles: top-3 chips, top-16 basemap switcher, top-28
            gradient legend, top-40 GIS toggle, top-52 GIS panel. */}
        <MapCounterChips />
        <MapLayerSwitcher />
        {!rfMode && <ElevationProfilePanel />}

        {/* Deploy popover — positioned in absolute px coords over the map. */}
        {deployAnchor && (
          <MapDeployMenu
            px={deployAnchor.px}
            lat={deployAnchor.lat}
            lon={deployAnchor.lon}
            onClose={() => setDeployAnchor(null)}
          />
        )}

        {/* First-run + device library — share the single exclusive modal slot. */}
        {activeModal === 'mapOnboarding' && <MapOnboardingModal />}
        {activeModal === 'deviceLibrary' && <DeviceLibraryModal />}
      </MapCtx.Provider>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Stage 4 debt — deliberately not in this Stage 3 pass (design doc §4):       */
/*  - Mute tile saturation per basemap/theme (old per-tile CSS filter on the   */
/*    removed DOM tile layer → MapLibre `raster-saturation` paint). Cosmetic,  */
/*    explicitly deferred to Stage 4 polish; the migration doc never scoped   */
/*    it into Stage 3.                                                        */
/* Everything else from the original debt list is now live: device markers +  */
/* coverage rings, link polylines, topology node/link markers, device/OSM     */
/* popups, OSM towers/buildings, geocode fly-to, click-to-place (device/       */
/* measure/profile/deploy) + search-pin dismiss, RF coverage raster (image    */
/* source), RF PtP beam (GeoJSON, ported back from the deleted RfBeamLayer).  */
/* -------------------------------------------------------------------------- */
