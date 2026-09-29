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
import { buildScene, disposeScene, resolveCableProfile, type BuildOptions, type DeviceDef, type LinkDef } from './rack3d';
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
    racks: [
      { key: 'A', enclosure: 'apc', devices: [
        dev('a-patch', 1, 1, 'patch', 24, 'rj45'),
        dev('a-sw1', 2, 1, 'switch', 24, 'rj45'),
        dev('a-odf', 5, 1, 'odf', 24, 'lc'),
        dev('a-sw2', 20, 1, 'switch', 12, 'sfp28'),
      ] },
      { key: 'B', enclosure: 'apc', devices: [] },
    ],
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
    expect(afterCount.names).toContain('boot-instanced-sfp-lc');
    expect(afterCount.names).not.toContain('rj45-plug');
    expect(afterCount.names).not.toContain('lc-connector');
    // Power remains on its explicit IEC fallback, never an LC connector.
    expect(afterCount.names).toContain('iec-connector');

    // instancing must reduce draw-call-contributing objects, not add to them
    expect(afterCount.meshes).toBeLessThan(beforeCount.meshes);
    console.log('mesh count before assets loaded:', beforeCount.meshes);
    console.log('mesh count after assets loaded:', afterCount.meshes);
    disposeScene(after);
  });

  it('selects SFP/QSFP DAC families per endpoint and points each module inward', async () => {
    await loadBootAssets(nodeFetch);
    const built = buildScene({
      racks: [{ key: 'A', enclosure: 'apc', devices: [
        dev('sfp', 1, 1, 'switch', 1, 'sfp28'),
        dev('qsfp', 3, 1, 'switch', 1, 'qsfp28'),
      ] }],
      links: [link(['sfp', 0], ['qsfp', 0], 'dac')],
    });
    try {
      const sfp = built.root.getObjectByName('boot-instanced-sfp-dac') as THREE.InstancedMesh;
      const qsfp = built.root.getObjectByName('boot-instanced-qsfp-dac') as THREE.InstancedMesh;
      expect(sfp.count).toBe(1);
      expect(qsfp.count).toBe(1);
      const cable = built.registry.cables.find((entry) => entry.mediaKey === 'dac')!;
      expect(cable.profileKey).toBe('fs-q28-pc01');
      expect(cable.radiusM).toBe(0.0033);
      expect(cable.minBendM).toBe(0.033);
      expect(cable.faceplateClearanceM).toBe(0.07245);
      const localInsertion = new THREE.Vector3(0, 1, 0);
      const readAxis = (mesh: THREE.InstancedMesh) => {
        const matrix = new THREE.Matrix4();
        mesh.getMatrixAt(0, matrix);
        const q = new THREE.Quaternion();
        matrix.decompose(new THREE.Vector3(), q, new THREE.Vector3());
        return localInsertion.clone().applyQuaternion(q).normalize();
      };
      expect(readAxis(sfp).dot(cable.curve.getTangentAt(0.004).normalize().negate())).toBeGreaterThan(0.999);
      expect(readAxis(qsfp).dot(cable.curve.getTangentAt(0.996).normalize())).toBeGreaterThan(0.999);
      const relief = built.root.getObjectByName('boot-instanced-strain-relief') as THREE.InstancedMesh;
      const reliefMatrix = new THREE.Matrix4();
      relief.getMatrixAt(0, reliefMatrix);
      const reliefScale = new THREE.Vector3();
      reliefMatrix.decompose(new THREE.Vector3(), new THREE.Quaternion(), reliefScale);
      expect(reliefScale.x).toBeCloseTo(cable.radiusM, 5);
      expect(reliefScale.z).toBeCloseTo(cable.radiusM, 5);
      const pullLoop = built.root.getObjectByName('boot-instanced-qsfp-pull-loop') as THREE.InstancedMesh;
      expect(pullLoop.count).toBe(1);
      expect(built.registry.fineDetail).toContain(pullLoop);
      expect(built.root.getObjectByName('iec-connector')).toBeTruthy();
    } finally { disposeScene(built); }
  });

  it('keeps DAC/AOC stock profiles distinct and never maps MPO to LC', async () => {
    expect(resolveCableProfile('dac', ['sfp28'], 1)).toMatchObject({
      key: 'fs-sfp10-ac01', radiusM: 0.003, minBendM: 0.03,
    });
    expect(resolveCableProfile('dac', ['qsfp28'], 1)).toMatchObject({
      key: 'fs-q28-pc01', radiusM: 0.0033, minBendM: 0.033, faceplateClearanceM: 0.07245,
    });
    expect(resolveCableProfile('dac', ['qsfp28'], 5)).toMatchObject({
      key: 'fs-q28-pc05', radiusM: 0.0042, minBendM: 0.042, faceplateClearanceM: 0.08325,
    });
    expect(resolveCableProfile('aoc', ['qsfp28'], 5)).toMatchObject({
      key: 'cisco-qsfp100-aoc', radiusM: 0.00175, minBendM: 0.03,
    });

    const built = buildScene({
      racks: [{ key: 'A', enclosure: 'apc', devices: [
        dev('q1', 1, 1, 'switch', 1, 'qsfp28'),
        dev('q2', 3, 1, 'switch', 1, 'qsfp28'),
        dev('odf', 5, 1, 'odf', 1, 'lc'),
        dev('sfp', 7, 1, 'switch', 1, 'sfp28'),
        dev('copper', 9, 1, 'switch', 1, 'rj45'),
      ] }],
      links: [
        link(['q1', 0], ['q2', 0], 'mpo'),
        link(['q2', 0], ['odf', 0], 'os2'),
        link(['sfp', 0], ['copper', 0], 'cat6a'),
      ],
    });
    try {
      expect((built.root.getObjectByName('boot-instanced-qsfp-mpo') as THREE.InstancedMesh).count).toBe(2);
      expect((built.root.getObjectByName('boot-instanced-qsfp-lc') as THREE.InstancedMesh).count).toBe(1);
      expect((built.root.getObjectByName('boot-instanced-sfp-rj45') as THREE.InstancedMesh).count).toBe(1);
      const lc = built.root.getObjectByName('boot-instanced-lc') as THREE.InstancedMesh;
      expect(lc.count).toBe(1); // only the real LC-panel endpoint
    } finally { disposeScene(built); }
  });

  it('applies visible vendor material variants without duplicating MSA geometry', async () => {
    const cisco = { ...dev('cisco', 1, 1, 'switch', 1, 'qsfp28'), brand: 'Cisco' };
    const fs = { ...dev('fs', 3, 1, 'switch', 1, 'qsfp28'), brand: 'FS' };
    const built = buildScene({
      racks: [{ key: 'A', enclosure: 'apc', devices: [cisco, fs] }],
      links: [link(['cisco', 0], ['fs', 0], 'dac')],
    });
    try {
      const heads = built.root.getObjectByName('boot-instanced-qsfp-dac') as THREE.InstancedMesh;
      expect(heads.count).toBe(2);
      const a = new THREE.Color(); const b = new THREE.Color();
      heads.getColorAt(0, a); heads.getColorAt(1, b);
      expect(a.getHex()).not.toBe(b.getHex());
    } finally { disposeScene(built); }
  });
});
