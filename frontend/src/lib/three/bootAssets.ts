/**
 * Loader for the Blender-authored assets (tools/blender/build_assets.py ->
 * frontend/public/3d/*.glb): cable-end boots/transceivers (RJ45, LC duplex,
 * SFP LC/DAC/AOC, QSFP DAC/AOC), device-faceplate
 * port cages (sfp/qsfp/rj11), the outdoor NEMA cabinet, and generic tower
 * structures (monopole/lattice — outdoor placement track, Slice 5 + 7),
 * plus verified, Blender-authored MikroTik and Cisco chassis packs.
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

export type BootFamily =
  | 'rj45' | 'lc' | 'mpo'
  | 'sfp-lc' | 'sfp-dac' | 'sfp-rj45'
  | 'qsfp-dac' | 'qsfp-lc' | 'qsfp-mpo'
  | 'strain-relief' | 'qsfp-pull-loop';
export type CageFamily = 'cage-sfp' | 'cage-qsfp' | 'cage-rj11';
export type EnclosureFamily = 'cabinet-outdoor';
export type StructureFamily = 'tower-monopole' | 'tower-lattice4' | 'tower-lattice3';
export type ChassisFamily =
  | 'chassis-mikrotik-ccr2004'
  | 'chassis-mikrotik-crs317'
  | 'chassis-mikrotik-crs328'
  | 'chassis-mikrotik-rb5009-k79'
  | 'chassis-cisco-c9300-48p-nm8x';
export type AssetFamily = BootFamily | CageFamily | EnclosureFamily | StructureFamily | ChassisFamily;

const URLS: Record<Exclude<AssetFamily, ChassisFamily>, string> = {
  rj45: '/3d/boot-rj45.glb',
  lc: '/3d/boot-lc.glb',
  // No sourced standalone MPO envelope exists in the verified matrix yet.
  // Generic MPO uses rack3d's explicit inferred placeholder and is therefore
  // intentionally omitted from the Blender asset table.
  mpo: '',
  'sfp-lc': '/3d/connector-sfp-lc.glb',
  'sfp-dac': '/3d/connector-sfp-dac.glb',
  'sfp-rj45': '/3d/connector-sfp-rj45.glb',
  'qsfp-dac': '/3d/connector-qsfp-dac.glb',
  'qsfp-lc': '/3d/connector-qsfp-lc.glb',
  'qsfp-mpo': '/3d/connector-qsfp-mpo.glb',
  'strain-relief': '/3d/connector-strain-relief.glb',
  'qsfp-pull-loop': '/3d/connector-qsfp-pull-loop.glb',
  'cage-sfp': '/3d/cage-sfp.glb',
  'cage-qsfp': '/3d/cage-qsfp.glb',
  'cage-rj11': '/3d/cage-rj11.glb',
  'cabinet-outdoor': '/3d/cabinet-outdoor.glb',
  'tower-monopole': '/3d/tower-monopole.glb',
  'tower-lattice4': '/3d/tower-lattice4.glb',
  'tower-lattice3': '/3d/tower-lattice3.glb',
};

// Brand packs are optional. A Plant mount fetches the shared connector and
// cabinet assets above; only a visible SKU requests its brand GLB below.
const BRAND_URLS: Record<ChassisFamily, { body: string; rackmountAccessory?: string }> = {
  'chassis-mikrotik-ccr2004': { body: '/3d/chassis-mikrotik-ccr2004.glb' },
  'chassis-mikrotik-crs317': { body: '/3d/chassis-mikrotik-crs317.glb' },
  'chassis-mikrotik-crs328': { body: '/3d/chassis-mikrotik-crs328.glb' },
  'chassis-mikrotik-rb5009-k79': {
    body: '/3d/brands/mikrotik/rb5009ug-s-in.glb',
    rackmountAccessory: '/3d/brands/mikrotik/k79-rackmount.glb',
  },
  'chassis-cisco-c9300-48p-nm8x': { body: '/3d/brands/cisco/c9300-48p-nm8x.glb' },
};

const CHASSIS_BY_DEVICE_SLUG: Record<string, ChassisFamily> = {
  'mikrotik-ccr2004-1g-12s-2xs': 'chassis-mikrotik-ccr2004',
  'mikrotik-crs317-1g-16splus-rm': 'chassis-mikrotik-crs317',
  'mikrotik-crs328-24p-4splus-rm': 'chassis-mikrotik-crs328',
  'mikrotik-rb5009ug-s-in': 'chassis-mikrotik-rb5009-k79',
  'cisco-c9300-48p-access': 'chassis-cisco-c9300-48p-nm8x',
  'cisco-c9300-48p': 'chassis-cisco-c9300-48p-nm8x',
};

export function chassisFamilyForDeviceSlug(slug: string): ChassisFamily | undefined {
  return CHASSIS_BY_DEVICE_SLUG[slug];
}

const cache: Partial<Record<AssetFamily, THREE.BufferGeometry>> = {};
const visualCache: Partial<Record<ChassisFamily, THREE.Group>> = {};
const brandInflight: Partial<Record<ChassisFamily, Promise<void>>> = {};
const rackmountAccessoryLoaded = new Set<ChassisFamily>();
const unavailableBrands = new Set<ChassisFamily>();
const inflight: Partial<Record<Exclude<AssetFamily, ChassisFamily>, Promise<void>>> = {};

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
  return loadAssetFamilies((Object.keys(URLS) as Exclude<AssetFamily, ChassisFamily>[])
    .filter((family) => URLS[family]), fetchArrayBuffer);
}

/** Load only the connector/cage/structure families required by one scene.
 * Requests are cached independently, so a copper-only rack never downloads
 * optical heads, towers, outdoor cabinets, or another brand package. */
export function loadAssetFamilies(
  families: Iterable<Exclude<AssetFamily, ChassisFamily>>,
  fetchArrayBuffer: (url: string) => Promise<ArrayBuffer> = async (url) => {
    const response = await fetch(url);
    if (!response.ok) throw new Error(`${url}: HTTP ${response.status}`);
    return response.arrayBuffer();
  },
): Promise<void> {
  const loader = new GLTFLoader();
  return Promise.all([...new Set(families)].map((fam) => {
    const url = URLS[fam];
    if (!url || cache[fam]) return Promise.resolve();
    if (!inflight[fam]) inflight[fam] = (async () => {
      const gltf = await loader.parseAsync(await fetchArrayBuffer(url), '');
      cache[fam] = mergedGeometry(gltf);
    })();
    return inflight[fam];
  })).then(() => undefined);
}

export function loadBrandAssets(
  families: Iterable<ChassisFamily>,
  fetchArrayBuffer: (url: string) => Promise<ArrayBuffer> = async (url) => {
    const response = await fetch(url);
    if (!response.ok) throw new Error(`${url}: HTTP ${response.status}`);
    return response.arrayBuffer();
  },
  rackmountAccessories: ReadonlySet<ChassisFamily> = new Set(),
): Promise<void> {
  return Promise.all([...new Set(families)].map((fam) => {
    const source = BRAND_URLS[fam];
    const needsAccessory = !!source.rackmountAccessory && rackmountAccessories.has(fam);
    if ((cache[fam] || visualCache[fam]) && (!needsAccessory || rackmountAccessoryLoaded.has(fam))) return Promise.resolve();
    if (unavailableBrands.has(fam)) return Promise.resolve();
    if (!brandInflight[fam]) {
      brandInflight[fam] = (async () => {
        const appendAccessory = needsAccessory && !!visualCache[fam] && !!source.rackmountAccessory;
        const urls = appendAccessory
          ? [source.rackmountAccessory!]
          : [source.body, ...(needsAccessory && source.rackmountAccessory ? [source.rackmountAccessory] : [])];
        const gltfs = await Promise.all(urls.map(async (url) => (
          new GLTFLoader().parseAsync(await fetchArrayBuffer(url), '')
        )));
        if (fam === 'chassis-mikrotik-rb5009-k79' || fam === 'chassis-cisco-c9300-48p-nm8x') {
          if (appendAccessory) {
            const packageGroup = new THREE.Group();
            packageGroup.name = fam + '-packages';
            packageGroup.add(visualCache[fam]!, gltfs[0]!.scene);
            visualCache[fam] = packageGroup;
          } else if (gltfs.length === 1) visualCache[fam] = gltfs[0]!.scene;
          else {
            const packageGroup = new THREE.Group();
            packageGroup.name = fam + '-packages';
            for (const gltf of gltfs) packageGroup.add(gltf.scene);
            visualCache[fam] = packageGroup;
          }
          if (needsAccessory) rackmountAccessoryLoaded.add(fam);
        } else cache[fam] = mergedGeometry(gltfs[0]!);
      })().catch((error) => {
        // Optional brand art never blocks the procedural chassis and ports.
        unavailableBrands.add(fam);
        console.warn(`Optional 3D asset unavailable: ${fam}`, error);
      }).finally(() => {
        brandInflight[fam] = undefined;
      });
    }
    return brandInflight[fam];
  })).then(() => undefined);
}

export function getVisualChassis(fam: ChassisFamily): THREE.Group | undefined {
  return visualCache[fam];
}

export function getBootGeometry(fam: AssetFamily): THREE.BufferGeometry | undefined {
  return cache[fam];
}
