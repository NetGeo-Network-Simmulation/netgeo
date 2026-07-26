/**
 * MapView — network design view (UISP Design Center style), now on a MapLibre
 * GL globe basemap (docs/design/21-GLOBE-MAP-MIGRATION.md, Stage 1).
 *
 * Stage 1 swaps only the render engine for the basemap + GIS raster overlays.
 * Everything vector (device markers, coverage rings, links, RF beam, OSM
 * towers/buildings, RF coverage raster, click-to-place, measure/profile tools,
 * search fly-to) was Leaflet-shaped (`react-leaflet` markers/popups/events) and
 * has been removed rather than shimmed — it comes back in Stage 2 as MapLibre
 * GeoJSON sources + style layers. See the bottom of this file for the exact
 * debt list.
 *
 * What still works:
 *  - Satellite/Street/Hybrid/Dark/Topo tile picker (MapLayerSwitcher)
 *  - GIS overlay tiles (Roads, Hillshade, Contour — the `kind: 'tile'` layers)
 *  - Globe projection, pan/zoom, zoom control
 *  - Every absolute-positioned chrome panel that doesn't touch the map
 *    surface itself (toolbar, device panel, search box, GIS layer panel,
 *    legends, onboarding/device-library modals)
 */
import { useEffect, useRef } from 'react';
import { MapLibreMap, NavigationControl } from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import {
  useMapStore,
  rainRateLabel,
  type GisLayerState,
} from '@/store/mapStore';
import { MAP_TILES, type TileLayerConfig, type MapTileKey } from '@/config/mapTiles';
import { GIS_LAYERS } from '@/config/gisLayers';
import { MapToolbar } from './MapToolbar';
import { MapDevicePanel } from './MapDevicePanel';
import { MapOnboardingModal } from './MapOnboardingModal';
import { MapLayerSwitcher } from './MapLayerSwitcher';
import { MapSearch } from './MapSearch';
import { GisLayerPanel } from './GisLayerPanel';
import { ElevationProfilePanel } from './ElevationProfilePanel';
import { DeviceLibraryModal } from './DeviceLibraryModal';
import { Layers as LayersIcon, AlertTriangle } from 'lucide-react';
import { useUiStore } from '@/store/uiStore';
import { cn } from '@/lib/cn';
import { zc } from '@/theme/z';

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
}) {
  return {
    type: 'raster' as const,
    tiles: tileUrls(cfg.url, cfg.subdomains),
    tileSize: 256,
    maxzoom: cfg.maxZoom ?? 19,
  };
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

  const cfg: TileLayerConfig = MAP_TILES[mapLayer];
  map.addSource(BASE_SOURCE_ID, rasterSource(cfg));
  map.addLayer({ id: BASE_SOURCE_ID, type: 'raster', source: BASE_SOURCE_ID });

  if (cfg.overlay) {
    map.addSource(BASE_OVERLAY_SOURCE_ID, rasterSource({ url: cfg.overlay.url, maxZoom: cfg.maxZoom }));
    map.addLayer({
      id: BASE_OVERLAY_SOURCE_ID,
      type: 'raster',
      source: BASE_OVERLAY_SOURCE_ID,
      paint: { 'raster-opacity': cfg.overlay.opacity ?? 1 },
    });
  }

  for (const layer of GIS_LAYERS) {
    if (layer.kind !== 'tile' || !layer.tileUrl) continue;
    const state = gisLayers[layer.id];
    if (!state?.visible) continue;
    const id = GIS_SOURCE_PREFIX + layer.id;
    map.addSource(id, rasterSource({ url: layer.tileUrl, subdomains: layer.subdomains, maxZoom: layer.maxZoom }));
    map.addLayer({ id, type: 'raster', source: id, paint: { 'raster-opacity': state.opacity } });
  }
}

function GlobeBasemap() {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<MapLibreMap | null>(null);
  const mapLayer = useMapStore((s) => s.mapLayer);
  const gisLayers = useMapStore((s) => s.gisLayers);

  // Mount once — initial center/zoom only; nothing currently drives a live
  // recenter after mount (the old search fly-to lived in the removed
  // SearchResultLayer, see debt list at the bottom of this file).
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
      map.remove();
      mapRef.current = null;
    };
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
        'pointer-events-auto absolute right-4 top-16 grid h-9 w-9 place-items-center rounded-lg border border-fg/15 shadow-glass backdrop-blur transition-colors',
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
    <div className={cn('pointer-events-none absolute right-4 top-3', zc.workspace)}>
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
  const showOnboarding = useMapStore((s) => s.showOnboarding);
  const activeModal = useUiStore((s) => s.activeModal);
  const openModal = useUiStore((s) => s.openModal);
  const coverageVisible = useMapStore((s) => s.gisLayers['rf-coverage']?.visible ?? false);

  // First visit to the standalone map claims the shared modal slot for the
  // quickstart (never in RF, which owns its own chrome). Exclusive by construction.
  useEffect(() => {
    if (rfMode || !showOnboarding) return;
    if (useUiStore.getState().activeModal === null) openModal('mapOnboarding');
  }, [rfMode, showOnboarding, openModal]);

  return (
    <div className="relative h-full w-full overflow-hidden">
      <GlobeBasemap />

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
      <MapLayerSwitcher />
      {!rfMode && <ElevationProfilePanel />}

      {/* First-run + device library — share the single exclusive modal slot. */}
      {activeModal === 'mapOnboarding' && <MapOnboardingModal />}
      {activeModal === 'deviceLibrary' && <DeviceLibraryModal />}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Stage 2 debt — removed in this Stage 1 swap, not shimmed (design doc §4):   */
/*  - Device markers + coverage rings, link polylines, RF PtP beam            */
/*    (RfBeamLayer.tsx deleted), topology node/link markers                   */
/*  - Click-to-place tool (device/measure/profile/deploy), incl. the          */
/*    MapDeployMenu popover trigger (deployAnchor)                            */
/*  - OSM towers/buildings overlay (Overpass-fed feature layers)              */
/*  - RF coverage raster (best-server RSSI canvas → L.imageOverlay)           */
/*  - Geocode fly-to on search result select                                  */
/* All of the above are pure-math/state in mapStore.ts/signalSim.ts/          */
/* elevation.ts, untouched — Stage 2 rewrites just their render as GeoJSON    */
/* sources + style layers on this same MapLibre map instance.                 */
/* -------------------------------------------------------------------------- */
