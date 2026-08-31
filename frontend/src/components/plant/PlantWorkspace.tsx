/**
 * PlantWorkspace — the Physical Plant full-bleed view (design 12-UI §2.2,
 * design §11). Hosts the 3D rack elevation (permintaan Surya: physical plant
 * is 3D-only now — the old 2D elevation panel + view switcher are gone).
 */
import { Rack3DElevationPanel } from './Rack3DElevationPanel';

export function PlantWorkspace() {
  return (
    <div className="absolute inset-0">
      <Rack3DElevationPanel />
    </div>
  );
}
