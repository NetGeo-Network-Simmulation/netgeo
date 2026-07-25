/**
 * RfWorkspace — the RF Planning full-bleed view (NG-RF-03, PtP slice).
 *
 * Reuses MapView (in `rfMode`) as the map surface — device placement, tiles, and
 * coverage all keep working — and layers the RF chrome over it: the Link Analysis
 * dock (right) and the endpoint/parameter bar (bottom). Endpoints are picked
 * either from the bottom-bar selectors or by clicking two placed AP/Tower sites
 * on the map (their existing selection drives `pickEndpoint`).
 */
import { useEffect } from 'react';
import { MapView } from '@/components/map/MapView';
import { useMapStore } from '@/store/mapStore';
import { useRfStore } from '@/store/rfStore';
import { useUiStore } from '@/store/uiStore';
import { zc } from '@/theme/z';
import { RfAnalysisPanel } from './RfAnalysisPanel';
import { RfLinkBar } from './RfLinkBar';

export function RfWorkspace() {
  const loadModels = useRfStore((s) => s.loadModels);
  const loadRadios = useRfStore((s) => s.loadRadios);
  const loadStudies = useRfStore((s) => s.loadStudies);
  const pickEndpoint = useRfStore((s) => s.pickEndpoint);
  const selectedId = useMapStore((s) => s.selectedDeviceId);
  const towersVisible = useMapStore((s) => s.gisLayers['util-tower']?.visible ?? false);
  const projectId = useUiStore((s) => s.projectId);

  // Load the propagation-model registry once. The tool is NOT forced to select
  // here (design 12-UI §3.1) — the link bar's "Place AP/tower" actions drive it,
  // and a click on an existing AP/tower still picks it as an endpoint below.
  useEffect(() => {
    void loadModels();
    void loadRadios();
  }, [loadModels, loadRadios]);

  // Load saved PtMP/product-select studies for the mode selects (NG-RF cross-
  // cutting persistence) once a project is open.
  useEffect(() => {
    if (projectId) void loadStudies();
  }, [projectId, loadStudies]);

  // A map click that selects an AP/Tower assigns it to the next endpoint slot.
  useEffect(() => {
    if (selectedId) pickEndpoint(selectedId);
  }, [selectedId, pickEndpoint]);

  return (
    <>
      <MapView rfMode />
      <RfAnalysisPanel />
      <RfLinkBar />
      {towersVisible && (
        <div className={`pointer-events-none absolute left-4 top-16 ${zc.workspace}`}>
          <span className="glass-strong flex items-center gap-1.5 rounded-full border border-fg/15 px-2.5 py-1 text-[11px] text-fg/60 shadow-glass">
            <span className="h-2 w-2 rounded-full border border-dashed border-fg/50" aria-hidden />
            OSM reference — not selectable
          </span>
        </div>
      )}
    </>
  );
}
