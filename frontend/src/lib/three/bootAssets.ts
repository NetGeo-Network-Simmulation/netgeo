/**
 * Loader for the Blender-authored assets (tools/blender/build_assets.py ->
 * frontend/public/3d/*.glb): cable-end boots (rj45/lc), device-faceplate
 * port cages (sfp/qsfp/rj11), the outdoor NEMA cabinet, and generic tower
 * structures (monopole/lattice — outdoor placement track, Slice 5 + 7),
 * plus verified, Blender-authored MikroTik CCR2004/CRS317/CRS328 chassis
 * envelopes with low-poly ventilation detail.
 * Kept out of rack3d.ts so buildScene() itself never touches the network/
 * filesystem — it stays a pure, synchronous scene builder the rest of the
 * app (and every existing test) can keep calling the way it already does.
 * A host component loads these once and rebuilds the scene after they
 * resolve; buildScene() falls back to its old procedural shape for any
 * family not yet cached (first paint, or a test that never calls
 * loadBootAssets()). The cabinet/tower families (Slice 5/7) have no
 * procedural fallback of their own — outdoor-nema racks keep the generic
 * rack frame and towers simply don't render until cached (Slice 6).
 */
import * as THREE from 'three';
import { GLTFLoader, type GLTF } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { mergeGeometries } from 'three/examples/jsm/utils/BufferGeometryUtils.js';

export type BootFamily = 'rj45' | 'lc';
export type CageFamily = 'cage-sfp' | 'cage-qsfp' | 'cage-rj11';
export type EnclosureFamily = 'cabinet-outdoor';
export type StructureFamily = 'tower-monopole' | 'tower-lattice4' | 'tower-lattice3';
export type ChassisFamily =
  | 'chassis-mikrotik-ccr2004'
  | 'chassis-mikrotik-crs317'
  | 'chassis-mikrotik-crs328';
export type AssetFamily = BootFamily | CageFamily | EnclosureFamily | StructureFamily | ChassisFamily;

const URLS: Record<AssetFamily, string> = {
  rj45: '/3d/boot-rj45.glb',
  lc: '/3d/boot-lc.glb',
  'cage-sfp': '/3d/cage-sfp.glb',
  'cage-qsfp': '/3d/cage-qsfp.glb',
  'cage-rj11': '/3d/cage-rj11.glb',
  'chassis-mikrotik-ccr2004': '/3d/chassis-mikrotik-ccr2004.glb',
  'chassis-mikrotik-crs317': '/3d/chassis-mikrotik-crs317.glb',
  'chassis-mikrotik-crs328': '/3d/chassis-mikrotik-crs328.glb',
  'cabinet-outdoor': '/3d/cabinet-outdoor.glb',
  'tower-monopole': '/3d/tower-monopole.glb',
  'tower-lattice4': '/3d/tower-lattice4.glb',
  'tower-lattice3': '/3d/tower-lattice3.glb',
};

const CHASSIS_BY_DEVICE_SLUG: Record<string, ChassisFamily> = {
  'mikrotik-ccr2004-1g-12s-2xs': 'chassis-mikrotik-ccr2004',
  'mikrotik-crs317-1g-16splus-rm': 'chassis-mikrotik-crs317',
  'mikrotik-crs328-24p-4splus-rm': 'chassis-mikrotik-crs328',
};

export function chassisFamilyForDeviceSlug(slug: string): ChassisFamily | undefined {
  return CHASSIS_BY_DEVICE_SLUG[slug];
}

const cache: Partial<Record<AssetFamily, THREE.BufferGeometry>> = {};
let inflight: Promise<void> | null = null;

/** Every mesh in the loaded glTF, world-baked and merged into one geometry
 *  (Blender's `join()` already leaves a single mesh, but this stays correct
 *  if a future asset ships more than one primitive). */
function mergedGeometry(gltf: GLTF): THREE.BufferGeometry {
  const geos: THREE.BufferGeometry[] = [];
  gltf.scene.updateMatrixWorld(true);
  gltf.scene.traverse((o) => {
    if (o instanceof THREE.Mesh) geos.push(o.geometry.clone().applyMatrix4(o.matrixWorld));
  });
  return geos.length === 1 ? geos[0]! : mergeGeometries(geos, false);
}

/** Fetches and caches every Blender-authored geometry. Idempotent — safe to
 *  call from every mount. `fetchArrayBuffer` is injectable so tests can read
 *  the committed .glb bytes straight off disk instead of hitting the
 *  network. */
export function loadBootAssets(
  fetchArrayBuffer: (url: string) => Promise<ArrayBuffer> = (u) => fetch(u).then((r) => r.arrayBuffer()),
): Promise<void> {
  if (inflight) return inflight;
  const loader = new GLTFLoader();
  inflight = (async () => {
    for (const fam of Object.keys(URLS) as AssetFamily[]) {
      const buf = await fetchArrayBuffer(URLS[fam]);
      const gltf = await loader.parseAsync(buf, '');
      cache[fam] = mergedGeometry(gltf);
    }
  })();
  return inflight;
}

export function getBootGeometry(fam: AssetFamily): THREE.BufferGeometry | undefined {
  return cache[fam];
}
