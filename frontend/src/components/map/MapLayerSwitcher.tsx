/**
 * MapLayerSwitcher — basemap selector (Satellite / Street), top-right.
 * Mirrors the UISP Design Center layer toggle, which docks basemap controls
 * top-right instead of colliding with the left-side navigation rail
 * (QA D8 — docs/design/16-UISP-PARITY-PLAN.md). Backed by `mapStore.mapLayer`
 * and the free, key-less providers in `config/mapTiles.ts`.
 *
 * QA-visual #5 (2026-09-12): cut from 5 choices (Satellite/Street/Hybrid/
 * Dark/Topo) to just these 2 — Hybrid/Dark/Topo are UI-picker choices only;
 * their underlying terrain/hillshade/contour DATA stays available to the
 * engine and to the GIS Layers panel (config/gisLayers.ts), which never
 * shared these tile configs in the first place.
 *
 * Sits above MapCounterChips' slot in the top-right stack — see the
 * top-3/top-11/top-28/top-24 rhythm comment in MapView.tsx.
 *
 * QA-visual #6 (2026-09-12): raised from top-16 to top-11 — Surya asked for
 * it higher still; MapCounterChips above tops out around y=40px (single
 * row of 4 small chips starting at top-3/12px), so top-11/44px keeps a
 * few px of clearance without the two ever touching.
 */
import { Satellite, Map as MapIcon } from 'lucide-react';
import { useMapStore } from '@/store/mapStore';
import type { MapTileKey } from '@/config/mapTiles';
import { cn } from '@/lib/cn';
import { zc } from '@/theme/z';

const LAYERS: { key: MapTileKey; label: string; icon: typeof Satellite }[] = [
  { key: 'satellite', label: 'Satellite', icon: Satellite },
  { key: 'street', label: 'Street', icon: MapIcon },
];

export interface MapLayerSwitcherProps {
  /** Basemap tile-load state, fed by MapView (owns the MapLibre instance).
   *  Surfaces slow/failed tile providers instead of a silent black map —
   *  QA screencast bug where a style change looked "stuck" with nothing
   *  telling the user whether it was still loading or broken. */
  tileStatus?: 'loading' | 'ready' | 'error';
}

export function MapLayerSwitcher({ tileStatus = 'ready' }: MapLayerSwitcherProps) {
  const mapLayer = useMapStore((s) => s.mapLayer);
  const setMapLayer = useMapStore((s) => s.setMapLayer);

  return (
    <div className={cn('pointer-events-auto absolute right-4 top-11 flex flex-col items-end gap-1', zc.workspace)}>
      <div
        className="glass-strong flex gap-1 rounded-xl border border-fg/15 p-1 shadow-glass-lg"
        role="group"
        aria-label="Map layer"
      >
        {LAYERS.map(({ key, label, icon: Icon }) => {
          const active = mapLayer === key;
          return (
            <button
              key={key}
              onClick={() => setMapLayer(key)}
              aria-pressed={active}
              className={cn(
                'flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs font-medium transition-colors',
                active ? 'bg-accent/25 text-accent' : 'text-fg/55 hover:bg-fg/10 hover:text-fg',
              )}
            >
              <Icon className="h-3.5 w-3.5" />
              {label}
            </button>
          );
        })}
      </div>
      {tileStatus !== 'ready' && (
        <span
          role="status"
          className={cn(
            'glass-strong rounded-md border px-2 py-0.5 text-[11px] font-medium',
            tileStatus === 'error'
              ? 'border-danger/30 text-danger'
              : 'border-fg/15 text-fg/55',
          )}
        >
          {tileStatus === 'error' ? 'Tiles unavailable' : 'Loading tiles…'}
        </span>
      )}
    </div>
  );
}
