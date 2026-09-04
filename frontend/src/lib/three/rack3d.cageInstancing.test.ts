/**
 * NG-PH3D 3b — proves the SFP/QSFP cage instancing actually fires once the
 * Blender-authored assets are loaded (same shape as rack3d.instancing.test.ts
 * for the 3a rj45/lc boots): building the same scene before/after
 * loadBootAssets() must swap the per-port procedural box+lip meshes for two
 * shared InstancedMesh draw calls, carrying a portMap the click-to-patch
 * raycaster can resolve back to a real dev/port pair. Also proves the LOD
 * mechanism (applyLod) actually toggles visibility on the fine-detail
 * objects it collects, in both directions.
 */
import { describe, expect, it } from 'vitest';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import * as THREE from 'three';
import { applyLod, buildScene, disposeScene, LOD_FAR_SPAN_M, type BuildOptions, type DeviceDef, type LinkDef } from './rack3d';
import { loadBootAssets } from './bootAssets';

async function nodeFetch(url: string): Promise<ArrayBuffer> {
  const file = path.resolve(__dirname, '../../../public', url.replace(/^\//, ''));
  const buf = await readFile(file);
  return buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength);
}

function dev(id: string, u: number, portGroups: { type: 'rj45' | 'sfp28' | 'qsfp28'; count: number }[]): DeviceDef {
  const ports = portGroups.reduce((s, g) => s + g.count, 0);
  return { id, u, h: 1, kind: 'switch', brand: 'Test', model: 'TST-1', accent: 0x847e75, chassis: 0x1c1c1a, ports, portGroups };
}
function link(a: [string, number], b: [string, number], m = 'cat6a'): LinkDef {
  return { a, b, m, live: true };
}

// Mirrors the task brief's own worst-case example: a switch with 48 RJ45 +
// 8 SFP/QSFP uplinks — the case instancing exists to keep off the draw
// budget.
function sceneOpts(): BuildOptions {
  return {
    racks: [
      { key: 'A', enclosure: 'apc', devices: [
        dev('a-sw1', 1, [{ type: 'rj45', count: 48 }, { type: 'sfp28', count: 4 }, { type: 'qsfp28', count: 4 }]),
        dev('a-sw2', 2, [{ type: 'sfp28', count: 2 }]),
      ] },
      { key: 'B', enclosure: 'apc', devices: [] },
    ],
    links: [link(['a-sw1', 0], ['a-sw2', 0], 'dac')],
  };
}

function meshNames(root: THREE.Object3D): string[] {
  const names: string[] = [];
  root.traverse((o) => {
    if (o instanceof THREE.InstancedMesh || o instanceof THREE.Mesh) names.push(o.name);
  });
  return names;
}

describe('SFP/QSFP cage instancing (NG-PH3D 3b)', () => {
  it('falls back to procedural box+lip cages before assets load, then instances after', async () => {
    const before = buildScene(sceneOpts());
    const beforeNames = meshNames(before.root);
    expect(beforeNames.some((n) => n.startsWith('cage-sfp-instanced'))).toBe(false);
    expect(beforeNames.some((n) => n.startsWith('cage-qsfp-instanced'))).toBe(false);
    expect(beforeNames.some((n) => n.startsWith('port-sfp28-'))).toBe(true);
    expect(beforeNames.some((n) => n.startsWith('port-qsfp28-'))).toBe(true);
    expect(beforeNames).toContain('sfp-cage-lip');
    const beforeCount = beforeNames.length;
    disposeScene(before);

    await loadBootAssets(nodeFetch);

    const after = buildScene(sceneOpts());
    const afterNames = meshNames(after.root);
    expect(afterNames).toContain('cage-sfp-instanced');
    expect(afterNames).toContain('cage-qsfp-instanced');
    expect(afterNames.some((n) => n.startsWith('port-sfp28-'))).toBe(false);
    expect(afterNames.some((n) => n.startsWith('port-qsfp28-'))).toBe(false);
    expect(afterNames).not.toContain('sfp-cage-lip');
    // rj45 ports are instanced too (Sesi LOD tuning, unconditionally — not
    // gated on boot-asset load like the sfp/qsfp cages above), so no more
    // per-port 'port-rj45-*' meshes either.
    expect(afterNames.some((n) => n.startsWith('port-rj45-'))).toBe(false);
    expect(afterNames).toContain('rj45-cage-instanced');

    // instancing must reduce draw-call-contributing objects (6 sfp+qsfp
    // ports collapse from 2 meshes each to 2 InstancedMesh total), not add
    expect(afterNames.length).toBeLessThan(beforeCount);

    // portMap: the InstancedMesh carries per-instance dev/port so the
    // click-to-patch raycaster can resolve a hit's instanceId
    const sfpIm = after.root.getObjectByName('cage-sfp-instanced') as THREE.InstancedMesh;
    const qsfpIm = after.root.getObjectByName('cage-qsfp-instanced') as THREE.InstancedMesh;
    expect(sfpIm.count).toBe(6); // 4 (a-sw1) + 2 (a-sw2)
    expect(qsfpIm.count).toBe(4);
    const portMap = sfpIm.userData.portMap as { dev: string; port: number }[];
    expect(portMap).toHaveLength(6);
    expect(portMap.every((p) => p.dev === 'a-sw1' || p.dev === 'a-sw2')).toBe(true);
    disposeScene(after);
  });

  it('applyLod hides per-port fine detail past LOD_FAR_SPAN_M and restores it below', async () => {
    await loadBootAssets(nodeFetch);
    const built = buildScene(sceneOpts());
    expect(built.registry.fineDetail.length).toBeGreaterThan(0);

    applyLod(built.registry, LOD_FAR_SPAN_M + 1);
    expect(built.registry.fineDetail.every((o) => o.visible === false)).toBe(true);
    // chassis/faceplate stay visible regardless — LOD only touches fineDetail
    const chassis = built.registry.devices['a-sw1']!.group.getObjectByName('chassis')!;
    expect(chassis.visible).toBe(true);

    applyLod(built.registry, LOD_FAR_SPAN_M - 0.5);
    expect(built.registry.fineDetail.every((o) => o.visible === true)).toBe(true);
    disposeScene(built);
  });
});
