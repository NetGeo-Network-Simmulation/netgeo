/**
 * MapLayerSwitcher — basemap selector (Satellite / Street / Hybrid), top-right.
 * Mirrors the UISP Design Center layer toggle, which docks basemap controls
 * top-right instead of colliding with the left-side navigation rail
 * (QA D8 — docs/design/16-UISP-PARITY-PLAN.md). Backed by `mapStore.mapLayer`
 * and the free, key-less providers in `config/mapTiles.ts`.
 *
 * Sits above MapCounterChips' slot in the top-right stack — see the
 * top-3/top-16/top-28/top-40 rhythm comment in MapView.tsx.
 */
import { Satellite, Map as MapIcon, Layers, Moon, Mountain } from 'lucide-react';
import { useMapStore } from '@/store/mapStore';
import type { MapTileKey } from '@/config/mapTiles';
import { cn } from '@/lib/cn';
import { zc } from '@/theme/z';

const LAYERS: { key: MapTileKey; label: string; icon: typeof Satellite }[] = [
  { key: 'satellite', label: 'Satellite', icon: Satellite },
  { key: 'street', label: 'Street', icon: MapIcon },
  { key: 'hybrid', label: 'Hybrid', icon: Layers },
  { key: 'dark', label: 'Dark', icon: Moon },
  { key: 'topo', label: 'Topo', icon: Mountain },
];

export function MapLayerSwitcher() {
  const mapLayer = useMapStore((s) => s.mapLayer);
  const setMapLayer = useMapStore((s) => s.setMapLayer);

  return (
    <div className={cn('pointer-events-auto absolute right-4 top-16', zc.workspace)}>
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
    </div>
  );
}
