/**
 * Loader for the Blender-authored assets (tools/blender/build_assets.py ->
 * frontend/public/3d/*.glb): cable-end boots (rj45/lc), device-faceplate
 * port cages (sfp/qsfp/rj11), the outdoor NEMA cabinet, and generic tower
 * structures (monopole/lattice — outdoor placement track, Slice 5 + 7).
 * Kept out of rack3d.ts so buildScene() itself never touches the network/
 * filesystem — it stays a pure, synchronous scene builder the rest of the
 * app (and every existing test) can keep calling the way it already does.
 * A host component loads these once and rebuilds the scene after they
 * resolve; buildScene() falls back to its old procedural shape for any
 * family not yet cached (first paint, or a test that never calls
 * loadBootAssets()). The cabinet/tower families are loader-only as of
 * Slice 5/7 — not yet rendered into the scene (that's Slice 6).
 */
import * as THREE from 'three';
import { GLTFLoader, type GLTF } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { mergeGeometries } from 'three/examples/jsm/utils/BufferGeometryUtils.js';

export type BootFamily = 'rj45' | 'lc';
export type CageFamily = 'cage-sfp' | 'cage-qsfp' | 'cage-rj11';
export type EnclosureFamily = 'cabinet-outdoor';
export type StructureFamily = 'tower-monopole' | 'tower-lattice4' | 'tower-lattice3';
export type AssetFamily = BootFamily | CageFamily | EnclosureFamily | StructureFamily;

const URLS: Record<AssetFamily, string> = {
  rj45: '/3d/boot-rj45.glb',
  lc: '/3d/boot-lc.glb',
  'cage-sfp': '/3d/cage-sfp.glb',
  'cage-qsfp': '/3d/cage-qsfp.glb',
  'cage-rj11': '/3d/cage-rj11.glb',
  'cabinet-outdoor': '/3d/cabinet-outdoor.glb',
  'tower-monopole': '/3d/tower-monopole.glb',
  'tower-lattice4': '/3d/tower-lattice4.glb',
  'tower-lattice3': '/3d/tower-lattice3.glb',
};

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

/** Fetches and caches every connector/cage geometry. Idempotent — safe to
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
