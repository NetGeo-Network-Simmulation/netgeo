/**
 * NG-PH3D 3a — proves the rj45/lc boot instancing actually fires once the
 * Blender-authored assets are loaded (not just that bootAssets.ts can parse
 * a .glb in isolation): building the same scene before/after
 * loadBootAssets() must swap the per-cable procedural body+boot meshes for
 * a couple of shared InstancedMesh draw calls, and the total mesh count
 * must go DOWN, not up.
 */
import { describe, expect, it } from 'vitest';
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

function dev(id: string, u: number, h: number, kind: DeviceDef['kind'], ports = 0, ptype: DeviceDef['ptype'] = undefined): DeviceDef {
  return { id, u, h, kind, brand: 'Test', model: 'TST-1', accent: 0x847e75, chassis: 0x1c1c1a, ports, ptype };
}
function link(a: [string, number], b: [string, number], m = 'cat6a', live = true): LinkDef {
  return { a, b, m, live };
}

function sceneOpts(): BuildOptions {
  return {
    rackA: 'apc',
    rackB: 'apc',
    fitout: {
      A: [
        dev('a-patch', 1, 1, 'patch', 24, 'rj45'),
        dev('a-sw1', 2, 1, 'switch', 24, 'rj45'),
        dev('a-odf', 5, 1, 'odf', 24, 'lc'),
        dev('a-sw2', 20, 1, 'switch', 12, 'sfp28'),
      ],
      B: [],
    },
    links: [
      link(['a-patch', 0], ['a-sw1', 0], 'cat6a'),
      link(['a-patch', 1], ['a-sw1', 1], 'cat6a'),
      link(['a-sw1', 5], ['a-odf', 0], 'os2'),
      link(['a-odf', 1], ['a-sw2', 0], 'om3'),
    ],
  };
}

function countMeshes(root: THREE.Object3D) {
  let meshes = 0;
  const names: string[] = [];
  root.traverse((o) => {
    if (o instanceof THREE.InstancedMesh || o instanceof THREE.Mesh) {
      meshes++;
      names.push(o.name);
    }
  });
  return { meshes, names };
}

describe('rj45/lc boot instancing (NG-PH3D 3a)', () => {
  it('falls back to procedural boots before assets load, then instances after', async () => {
    const before = buildScene(sceneOpts());
    const beforeCount = countMeshes(before.root);
    // fallback path: no InstancedMesh, and the old per-cable names exist
    expect(beforeCount.names.some((n) => n.startsWith('boot-instanced-'))).toBe(false);
    expect(beforeCount.names).toContain('rj45-plug');
    expect(beforeCount.names).toContain('lc-connector');
    disposeScene(before);

    await loadBootAssets(nodeFetch);

    const after = buildScene(sceneOpts());
    const afterCount = countMeshes(after.root);
    expect(afterCount.names).toContain('boot-instanced-rj45');
    expect(afterCount.names).toContain('boot-instanced-lc');
    expect(afterCount.names).not.toContain('rj45-plug');
    expect(afterCount.names).not.toContain('lc-connector');
    // 'strain-relief' legitimately still appears for the power cords every
    // switch/server gets (fam 'iec' — DAC/AOC/power boots are out of this
    // session's verified-dimension scope, untouched procedural fallback).

    // instancing must reduce draw-call-contributing objects, not add to them
    expect(afterCount.meshes).toBeLessThan(beforeCount.meshes);
    console.log('mesh count before assets loaded:', beforeCount.meshes);
    console.log('mesh count after assets loaded:', afterCount.meshes);
    disposeScene(after);
  });
});
