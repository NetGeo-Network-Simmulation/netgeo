import { beforeAll, describe, expect, it } from 'vitest';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import * as THREE from 'three';
import { loadBootAssets } from './bootAssets';
import { buildScene, disposeScene, type DeviceDef } from './rack3d';

async function nodeFetch(url: string): Promise<ArrayBuffer> {
  const file = path.resolve(__dirname, '../../../public', url.replace(/^\//, ''));
  const buf = await readFile(file);
  return buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength);
}

beforeAll(() => loadBootAssets(nodeFetch));

function build(device: DeviceDef) {
  return buildScene({
    racks: [{ key: 'r1', enclosure: 'apc', ruHeight: 42, devices: [device] }],
    links: [],
  });
}

const base: DeviceDef = {
  id: 'sw1', u: 1, h: 1, kind: 'switch', brand: 'MikroTik',
  model: 'CRS317-1G-16S+RM', accent: 0xe4002b, chassis: 0x202022,
  ports: 0, bodyWidthM: 0.443, bodyDepthM: 0.224,
};

describe('rack3d Blender chassis envelope', () => {
  it('uses the cached CRS317 GLB without moving the catalog-driven faceplate', () => {
    const built = build({ ...base, chassisAsset: 'chassis-mikrotik-crs317' });
    try {
      const chassis = built.root.getObjectByName('chassis') as THREE.Mesh;
      const size = new THREE.Vector3();
      new THREE.Box3().setFromObject(chassis).getSize(size);
      expect(size.x).toBeCloseTo(0.443, 3);
      expect(size.y).toBeCloseTo(0.044, 3);
      expect(size.z).toBeCloseTo(0.224, 3);
      expect(built.root.getObjectByName('front-plate')).toBeTruthy();
    } finally {
      disposeScene(built);
    }
  });

  it('keeps the existing procedural body when no SKU asset is mapped', () => {
    const built = build(base);
    try {
      const chassis = built.root.getObjectByName('chassis') as THREE.Mesh;
      const size = new THREE.Vector3();
      new THREE.Box3().setFromObject(chassis).getSize(size);
      expect(size.x).toBeCloseTo(0.443, 3);
      expect(size.y).toBeCloseTo(0.04295, 3);
      expect(size.z).toBeCloseTo(0.224, 3);
    } finally {
      disposeScene(built);
    }
  });
});
