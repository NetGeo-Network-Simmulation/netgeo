/**
 * GisLayerPanel — the multi-layer GIS tree (NetGeo/05_MAP_ENGINE §GIS Layers).
 *
 * A dockable, collapsible layer manager (ArcGIS Pro / QGIS analogue) listing
 * every spec'd layer grouped by category. Each renderable layer has a
 * visibility checkbox and an opacity slider; spec'd-but-unwired layers are
 * shown disabled (progressive disclosure) so the roadmap stays visible without
 * pretending to render data.
 *
 * State lives in mapStore (`gisLayers`); MapView reads it to mount tile/feature
 * overlays. Toggled open via the Layers button in MapView.
 */
import { useState } from 'react';
import { Layers, ChevronRight, X, Lock } from 'lucide-react';
import { useMapStore } from '@/store/mapStore';
import { GIS_GROUPS, GIS_LAYERS, type GisLayerGroup } from '@/config/gisLayers';
import { cn } from '@/lib/cn';

export function GisLayerPanel() {
  const open = useMapStore((s) => s.gisPanelOpen);
  const togglePanel = useMapStore((s) => s.toggleGisPanel);
  const gisLayers = useMapStore((s) => s.gisLayers);
  const toggleLayer = useMapStore((s) => s.toggleGisLayer);
  const setOpacity = useMapStore((s) => s.setGisLayerOpacity);

  const [collapsed, setCollapsed] = useState<Record<string, boolean>>(() =>
    Object.fromEntries(GIS_GROUPS.map((g) => [g.group, g.collapsed])),
  );

  if (!open) return null;

  const layersFor = (group: GisLayerGroup) => GIS_LAYERS.filter((l) => l.group === group);

  return (
    <aside
      role="region"
      aria-label="GIS layers"
      className="pointer-events-auto w-72 animate-fade-in"
    >
      <div className="glass-strong flex max-h-[70vh] flex-col overflow-hidden rounded-xl border border-fg/15 shadow-glass-lg">
        {/* Header */}
        <div className="flex items-center gap-2 border-b border-fg/10 px-3 py-2">
          <Layers className="h-4 w-4 text-accent" />
          <h2 className="text-xs font-semibold uppercase tracking-wider text-fg/70">
            GIS Layers
          </h2>
          <button
            onClick={() => togglePanel(false)}
            aria-label="Close layer panel"
            className="ml-auto grid h-6 w-6 place-items-center rounded text-fg/50 hover:bg-fg/10 hover:text-fg"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </div>

        {/* Layer tree */}
        <div className="ng-scroll overflow-y-auto px-1.5 py-1.5">
          {GIS_GROUPS.map(({ group, label }) => {
            const layers = layersFor(group);
            const isCollapsed = collapsed[group];
            const visibleCount = layers.filter((l) => gisLayers[l.id]?.visible).length;
            return (
              <div key={group} className="mb-1">
                <button
                  onClick={() => setCollapsed((c) => ({ ...c, [group]: !c[group] }))}
                  aria-expanded={!isCollapsed}
                  className="flex w-full items-center gap-1.5 rounded px-1.5 py-1 text-left text-[11px] font-semibold text-fg/60 hover:bg-fg/5 hover:text-fg/85"
                >
                  <ChevronRight
                    className={cn(
                      'h-3.5 w-3.5 transition-transform',
                      !isCollapsed && 'rotate-90',
                    )}
                  />
                  <span className="uppercase tracking-wide">{label}</span>
                  {visibleCount > 0 && (
                    <span className="ml-auto rounded-full bg-accent/25 px-1.5 text-[9px] font-medium text-accent">
                      {visibleCount}
                    </span>
                  )}
                </button>

                {!isCollapsed && (
                  <ul className="ml-2 space-y-0.5 border-l border-fg/8 pl-2">
                    {layers.map((layer) => {
                      const state = gisLayers[layer.id];
                      const planned = layer.kind === 'planned';
                      const checked = state?.visible ?? false;
                      return (
                        <li key={layer.id} className="py-0.5">
                          <label
                            className={cn(
                              'flex items-center gap-2 rounded px-1 py-0.5 text-[11px]',
                              planned
                                ? 'cursor-not-allowed text-fg/30'
                                : 'cursor-pointer text-fg/75 hover:bg-fg/5',
                            )}
                            title={layer.description ?? layer.label}
                          >
                            <input
                              type="checkbox"
                              checked={checked}
                              disabled={planned}
                              onChange={() => toggleLayer(layer.id)}
                              className="h-3 w-3 accent-accent"
                              aria-label={layer.label}
                            />
                            <span className="flex-1 truncate">{layer.label}</span>
                            {planned && (
                              <span
                                className="flex items-center gap-0.5 text-[8px] uppercase text-fg/25"
                                title="Spec'd — provider not yet wired"
                              >
                                <Lock className="h-2.5 w-2.5" /> soon
                              </span>
                            )}
                          </label>

                          {/* Opacity slider for visible tile layers + the RF
                              coverage raster (both are translucent overlays). */}
                          {checked && (layer.kind === 'tile' || layer.id === 'rf-coverage') && state && (
                            <div className="ml-5 mt-0.5 flex items-center gap-1.5">
                              <input
                                type="range"
                                min={0}
                                max={100}
                                value={Math.round(state.opacity * 100)}
                                onChange={(e) =>
                                  setOpacity(layer.id, Number(e.target.value) / 100)
                                }
                                aria-label={`${layer.label} opacity`}
                                className="h-1 flex-1 accent-accent"
                              />
                              <span className="w-7 text-right font-mono text-[9px] text-fg/40">
                                {Math.round(state.opacity * 100)}%
                              </span>
                            </div>
                          )}
                        </li>
                      );
                    })}
                  </ul>
                )}
              </div>
            );
          })}
        </div>

        <p className="border-t border-fg/10 px-3 py-1.5 text-[9px] text-fg/30">
          Layers stack above the basemap. Disabled rows await a data provider.
        </p>
      </div>
    </aside>
  );
}
