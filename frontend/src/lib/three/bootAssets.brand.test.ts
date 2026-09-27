import { describe, expect, it } from 'vitest';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import * as THREE from 'three';
import { getVisualChassis, loadBootAssets, loadBrandAssets } from './bootAssets';

const fetched: string[] = [];
async function nodeFetch(url: string): Promise<ArrayBuffer> {
  fetched.push(url);
  const file = path.resolve(__dirname, '../../../public', url.replace(/^\//, ''));
  const buf = await readFile(file);
  return buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength);
}

describe('optional device brand assets', () => {
  it('loads core parts without fetching any brand device, then only the requested SKU', async () => {
    await loadBootAssets(nodeFetch);
    expect(fetched.some((url) => url.includes('/brands/') || url.includes('/chassis-'))).toBe(false);
    await loadBrandAssets(['chassis-mikrotik-rb5009-k79'], nodeFetch);
    expect(fetched.filter((url) => url.includes('/brands/'))).toEqual([
      '/3d/brands/mikrotik/rb5009ug-s-in-k79.glb',
    ]);
    const scene = getVisualChassis('chassis-mikrotik-rb5009-k79')!;
    expect(scene).toBeDefined();
    const size = new THREE.Box3().setFromObject(scene).getSize(new THREE.Vector3());
    expect(size.x).toBeCloseTo(0.4826, 3);
    expect(size.y).toBeCloseTo(0.04445, 3);
    // The unit itself is 125 mm deep; raised front sockets make the complete
    // visual assembly slightly deeper than the published chassis envelope.
    const caseMesh = scene.getObjectByName('rb5009-anodized-chassis') as THREE.Mesh;
    const caseSize = new THREE.Box3().setFromObject(caseMesh).getSize(new THREE.Vector3());
    expect(caseSize.x).toBeCloseTo(0.220, 3);
    expect(caseSize.z).toBeCloseTo(0.125, 3);
    const bodyEnvelope = new THREE.Box3().setFromObject(caseMesh);
    for (let i = 0; i < 21; i++) {
      bodyEnvelope.union(new THREE.Box3().setFromObject(scene.getObjectByName(`heatsink-top-${i}`)!));
    }
    expect(bodyEnvelope.getSize(new THREE.Vector3()).y).toBeCloseTo(0.022, 3);
    const names: string[] = [];
    const materials = new Set<string>();
    scene.traverse((obj) => {
      if (obj instanceof THREE.Mesh) {
        names.push(obj.name);
        for (const mat of Array.isArray(obj.material) ? obj.material : [obj.material]) materials.add(mat.name);
      }
    });
    expect(names.filter((name) => /^ether\d+-.*-mouth$/.test(name))).toHaveLength(8);
    expect(names).toContain('sfpplus-1-mouth');
    expect(names).toContain('usb-a-3-mouth');
    expect(names).toContain('dc-input-mouth');
    expect(names).toContain('side-2pin-terminal');
    expect(names).toContain('k79-left-ear');
    expect(names).toContain('k79-right-ear');
    expect(names.filter((name) => name.startsWith('heatsink-top-'))).toHaveLength(21);
    const ear = scene.getObjectByName('k79-left-ear') as THREE.Mesh;
    const ray = new THREE.Raycaster(new THREE.Vector3(-0.229, 0.015, 0.1),
      new THREE.Vector3(0, 0, -1));
    expect(ray.intersectObject(ear).length).toBe(0); // punched rack slot
    ray.set(new THREE.Vector3(-0.200, 0, 0.1), new THREE.Vector3(0, 0, -1));
    expect(ray.intersectObject(ear).length).toBeGreaterThan(0);
    expect(materials.size).toBeGreaterThanOrEqual(5);
  });
  it('keeps optional brand failures out of the critical scene load', async () => {
    let attempts = 0;
    const missing = async () => { attempts++; throw new Error('404'); };
    await expect(loadBrandAssets(['chassis-mikrotik-crs328'], missing)).resolves.toBeUndefined();
    await loadBrandAssets(['chassis-mikrotik-crs328'], missing);
    expect(attempts).toBe(1);
  });
});
