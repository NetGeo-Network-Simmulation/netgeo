/**
 * PlantWorkspace — the Physical Plant full-bleed view (design 12-UI §2.2,
 * design §11). Hosts two renderings of the same plant: the QA'd 2D rack
 * elevation (backend-bound: sites, racks, device placement, over-length cable
 * warnings) and the new 2.5D rackmount view ported from the design handoff.
 *
 * ponytail: a toggle, not a replacement — the 2D panel is the one wired to the
 * API today; the 3D view still runs on the handoff's sample fit-out. Drop the
 * toggle once 3D reads the real project data.
 */
import { useState } from 'react';
import { Box, Rows3 } from 'lucide-react';
import { RackElevationPanel } from '@/components/RackElevationPanel';
import { Rack3DElevationPanel } from './Rack3DElevationPanel';

export function PlantWorkspace() {
  const [view, setView] = useState<'2d' | '3d'>('2d');

  return (
    <div className="absolute inset-0">
      <div className="absolute right-3 top-3 z-10 flex overflow-hidden rounded-md border border-fg/10 bg-surface/80 backdrop-blur">
        {([
          ['2d', 'Elevasi', Rows3],
          ['3d', '2.5D', Box],
        ] as const).map(([key, label, Icon]) => (
          <button
            key={key}
            type="button"
            onClick={() => setView(key)}
            className={`flex items-center gap-1.5 px-2.5 py-1.5 text-xs transition ${
              view === key ? 'bg-accent/20 text-accent' : 'text-recess hover:bg-fg/5 hover:text-fg'
            }`}
          >
            <Icon className="size-3.5" />
            {label}
          </button>
        ))}
      </div>
      {view === '2d' ? <RackElevationPanel /> : <Rack3DElevationPanel />}
    </div>
  );
}
