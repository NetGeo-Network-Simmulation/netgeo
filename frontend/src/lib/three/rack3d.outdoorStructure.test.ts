/**
 * Slice 6: the actual mesh wiring behind outdoor placement — the
 * 'outdoor-nema' cabinet swap in buildRack(), and the `structure`/
 * `outdoorNodes` BuildOptions. Loads the real committed .glb bytes (same
 * pattern as rack3d.drawCallBudget.test.ts) so this exercises the true
 * geometry-cached path, not just "didn't crash".
 */
import { describe, expect, it, beforeAll } from 'vitest';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { buildScene, type DeviceDef } from './rack3d';
import { loadBootAssets } from './bootAssets';

async function nodeFetch(url: string): Promise<ArrayBuffer> {
  const file = path.resolve(__dirname, '../../../public', url.replace(/^\//, ''));
  const buf = await readFile(file);
  return buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength);
}

beforeAll(async () => {
  await loadBootAssets(nodeFetch);
});

function dev(id: string): DeviceDef {
  return { id, u: 1, h: 1, kind: 'switch', brand: 'Test', model: 'TST-1', accent: 0x847e75, chassis: 0x1c1c1a, ports: 0 };
}

describe('outdoor-nema cabinet swap', () => {
  it('renders the real cabinet-outdoor.glb shell instead of the generic frame', () => {
    const built = buildScene({
      racks: [{ key: 'r1', enclosure: 'outdoor-nema', devices: [dev('d1')], ruHeight: 42 }],
      links: [],
    });
    const rackGroup = built.registry.racks['r1']!.group;
    expect(rackGroup.getObjectByName('cabinet-outdoor-shell')).toBeDefined();
    // the generic procedural build names a merged frame mesh 'rack-frame-
    // merged' — must NOT be present once the GLB shell took over.
    expect(rackGroup.getObjectByName('rack-frame-merged')).toBeUndefined();
    // real vendor dims (600x800x2000mm) still recorded for device RU math.
    expect(built.registry.racks['r1']).toMatchObject({ w: 0.6, d: 0.8, h: 2 });
  });

  it('still builds the procedural frame for a normal enclosure profile', () => {
    const built = buildScene({
      racks: [{ key: 'r1', enclosure: 'apc', devices: [dev('d1')], ruHeight: 42 }],
      links: [],
    });
    const rackGroup = built.registry.racks['r1']!.group;
    expect(rackGroup.getObjectByName('rack-frame-merged')).toBeDefined();
    expect(rackGroup.getObjectByName('cabinet-outdoor-shell')).toBeUndefined();
  });
});

describe('site structure + outdoor node markers', () => {
  it('scales the monopole mesh by the site height_agl_m', () => {
    const built = buildScene({
      racks: [{ key: 'r1', enclosure: 'apc', devices: [], ruHeight: 42 }],
      links: [],
      structure: { family: 'tower-monopole', scale: 24 },
    });
    const mesh = built.root.getObjectByName('site-structure');
    expect(mesh).toBeDefined();
    expect(mesh!.scale.x).toBe(24);
    expect(mesh!.scale.y).toBe(24);
    expect(mesh!.scale.z).toBe(24);
  });

  it('places an outdoor node marker at its mount height', () => {
    const built = buildScene({
      racks: [{ key: 'r1', enclosure: 'apc', devices: [], ruHeight: 42 }],
      links: [],
      outdoorNodes: [{ id: 'n1', name: 'rru1', y: 12.5 }],
    });
    const marker = built.root.getObjectByName('outdoor-node-n1');
    expect(marker).toBeDefined();
    expect(marker!.position.y).toBe(12.5);
  });

  it('draws nothing extra when structure/outdoorNodes are omitted (existing scenes unaffected)', () => {
    const built = buildScene({
      racks: [{ key: 'r1', enclosure: 'apc', devices: [], ruHeight: 42 }],
      links: [],
    });
    expect(built.root.getObjectByName('site-structure')).toBeUndefined();
  });
});
