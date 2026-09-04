/**
 * Sesi LOD tuning (2026-09-02) — locks in the draw-call budget for a dense
 * plant scenario (2 x 42U racks, 78 devices, mixed switch/server/patch-
 * panel/ODF/duct — docs/qa/draw-call-inventory-2026-09-01.md's own scenario
 * definition) so the RJ45/LC port-detail instancing this slice added can't
 * silently regress back to one mesh per port.
 *
 * Pure structural count, not a render: jsdom has no WebGL
 * (webglAvailable() is always false there), so this counts drawable objects
 * in the built scene graph via root.traverse() — the same objects a real
 * renderer.info.render.calls would submit one draw call each for at the
 * near-LOD state (every fineDetail object visible; visibility doesn't
 * remove an object from the graph, so the raw count already reflects the
 * worst case). Cross-checked against a real headless-Chromium
 * renderer.info reading during this slice's own verification (not
 * committed — see docs/qa/2026-09-02-lod.md, local-only): 1984 draw calls
 * for the exact scenario measured there vs ~2051 objects for this test's
 * own (slightly different link topology) fixture — same order, both well
 * under budget; the two aren't required to match exactly.
 */
import { describe, expect, it, beforeAll } from 'vitest';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import * as THREE from 'three';
import { buildScene, disposeScene, type BuildOptions, type DeviceDef, type LinkDef } from './rack3d';
import { loadBootAssets } from './bootAssets';

async function nodeFetch(url: string): Promise<ArrayBuffer> {
  const file = path.resolve(__dirname, '../../../public', url.replace(/^\//, ''));
  const buf = await readFile(file);
  return buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength);
}

// production loads the rj45/lc boot + sfp/qsfp cage .glb assets over fetch
// before the first real scene build (App-level loadBootAssets() call) — do
// the same here so this budget reflects the real steady-state scene, not
// the pre-asset-load procedural fallback (which is its own, separately
// tested, transient state — see rack3d.cageInstancing.test.ts /
// rack3d.instancing.test.ts).
beforeAll(async () => {
  await loadBootAssets(nodeFetch);
});

function dev(id: string, u: number, h: number, kind: DeviceDef['kind'], portGroups?: { type: NonNullable<DeviceDef['ptype']>; count: number }[]): DeviceDef {
  const ports = portGroups?.reduce((s, g) => s + g.count, 0) ?? 0;
  return { id, u, h, kind, brand: 'Test', model: 'TST-1', accent: 0x847e75, chassis: 0x1c1c1a, ports, portGroups };
}
function link(a: [string, number], b: [string, number], m = 'cat6a'): LinkDef {
  return { a, b, m, live: true };
}

// 78 devices total / 2 x 42U racks (39/rack), mixed like the inventory
// doc's own scenario: switches (7x 48+4port, 7x 24+4port), patch panels
// (24x rj45), ODFs (24x lc), duct panels (no front ports), servers (2U,
// 4x rj45). U budget per rack: 7+7+10+6+6+6(3x2U) = 42U exactly.
function denseRackDevices(prefix: string): DeviceDef[] {
  const out: DeviceDef[] = [];
  let ru = 1;
  for (let i = 0; i < 7; i++) { out.push(dev(`${prefix}-sw48-${i}`, ru, 1, 'switch', [{ type: 'rj45', count: 48 }, { type: 'sfp28', count: 4 }])); ru += 1; }
  for (let i = 0; i < 7; i++) { out.push(dev(`${prefix}-sw24-${i}`, ru, 1, 'switch', [{ type: 'rj45', count: 24 }, { type: 'sfp28', count: 4 }])); ru += 1; }
  for (let i = 0; i < 10; i++) { out.push(dev(`${prefix}-patch-${i}`, ru, 1, 'patch', [{ type: 'rj45', count: 24 }])); ru += 1; }
  for (let i = 0; i < 6; i++) { out.push(dev(`${prefix}-odf-${i}`, ru, 1, 'odf', [{ type: 'lc', count: 24 }])); ru += 1; }
  for (let i = 0; i < 6; i++) { out.push(dev(`${prefix}-duct-${i}`, ru, 1, 'duct')); ru += 1; }
  for (let i = 0; i < 3; i++) { out.push(dev(`${prefix}-srv-${i}`, ru, 2, 'server', [{ type: 'rj45', count: 4 }])); ru += 2; }
  return out;
}

function denseScenario(): BuildOptions {
  const rackA = denseRackDevices('a');
  const rackB = denseRackDevices('b');
  const links: LinkDef[] = [];
  for (const list of [rackA, rackB]) {
    for (let i = 0; i < list.length - 1; i++) links.push(link([list[i]!.id, 0], [list[i + 1]!.id, 1]));
  }
  return {
    racks: [
      { key: 'rack-a', enclosure: 'apc', devices: rackA, ruHeight: 42 },
      { key: 'rack-b', enclosure: 'apc', devices: rackB, ruHeight: 42 },
    ],
    links,
  };
}

function countDrawable(root: THREE.Object3D): number {
  let n = 0;
  root.traverse((o) => {
    if ((o as THREE.Mesh).isMesh || (o as THREE.InstancedMesh).isInstancedMesh
      || (o as THREE.Sprite).isSprite || (o as THREE.Line).isLine) n++;
  });
  return n;
}

describe('draw-call budget (Sesi LOD tuning, 78-device dense scenario)', () => {
  it('stays under the 3000 near-LOD draw-call budget', () => {
    const built = buildScene(denseScenario());
    try {
      expect(countDrawable(built.root)).toBeLessThan(3000);
    } finally {
      disposeScene(built);
    }
  });

  it('RJ45/LC port decoration collapses to one InstancedMesh per part, not one mesh per port', () => {
    const built = buildScene(denseScenario());
    try {
      for (const name of ['rj45-cage-instanced', 'rj45-notch-instanced', 'keystone-instanced', 'port-led-instanced', 'lc-bore-instanced', 'lc-adapter-instanced']) {
        const im = built.root.getObjectByName(name) as THREE.InstancedMesh | undefined;
        expect(im, `${name} should exist`).toBeTruthy();
        expect(im!.count).toBeGreaterThan(1); // actually collapsing many ports, not a coincidental single-port case
      }
      // portMap must still resolve every instance back to a real dev/port —
      // click-to-patch picking depends on this (Rack3DElevationPanel).
      const keystoneIm = built.root.getObjectByName('keystone-instanced') as THREE.InstancedMesh;
      const map = keystoneIm.userData.portMap as { dev: string; port: number }[];
      expect(map.length).toBe(keystoneIm.count);
      expect(map.every((p) => typeof p.dev === 'string' && typeof p.port === 'number')).toBe(true);
    } finally {
      disposeScene(built);
    }
  });
});
