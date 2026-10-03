import { describe, expect, it, vi } from 'vitest';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import * as THREE from 'three';
import { getBootGeometry, getVisualChassis, invalidatePackAssetRegistry, loadBootAssets, loadBrandAssets } from './bootAssets';
import { devicePacksApi, http } from '@/api/client';
import { buildScene, disposeScene } from './rack3d';

const fetched: string[] = [];
async function nodeFetch(url: string): Promise<ArrayBuffer> {
  fetched.push(url);
  const file = path.resolve(__dirname, '../../../public', url.replace(/^\//, ''));
  const buf = await readFile(file);
  return buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength);
}

describe('optional device brand assets', () => {
  it('loads an arbitrary installed family lazily and falls back when disabled', async () => {
    const body = '/api/device-packs/sample/assets/body.glb';
    const list = vi.spyOn(devicePacksApi, 'list').mockResolvedValue([{
      id: 'sample', name: 'Sample', version: '1.0.0', categories: [], device_count: 1,
      enabled_by_default: false, source_note: '', enabled: true,
      assets: { 'sample:novel-switch': { body } },
    }]);
    invalidatePackAssetRegistry();
    const reads: string[] = [];
    await loadBrandAssets(['sample:novel-switch'], async (url) => {
      reads.push(url);
      const bytes = await readFile(path.resolve(__dirname, '../../../public/3d/brands/cisco/c9300-48p-nm8x.glb'));
      return bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength);
    });
    expect(reads).toEqual([body]);
    expect(getBootGeometry('sample:novel-switch')).toBeDefined();
    const scene = buildScene({ racks: [{ key: 'r1', enclosure: 'apc', ruHeight: 42, devices: [{
      id: 'novel', u: 1, h: 1, kind: 'switch', brand: 'Fixture', model: 'Novel',
      accent: 0x444444, chassis: 0x222222, ports: 1, chassisAsset: 'sample:novel-switch',
    }] }], links: [] });
    expect((scene.root.getObjectByName('chassis') as THREE.Mesh).geometry).toBe(getBootGeometry('sample:novel-switch'));
    disposeScene(scene);
    list.mockResolvedValue([]);
    invalidatePackAssetRegistry();
    expect(getBootGeometry('sample:novel-switch')).toBeUndefined();
    await loadBrandAssets(['sample:novel-switch'], async () => { throw new Error('should not fetch disabled pack'); });
    expect(getBootGeometry('sample:novel-switch')).toBeUndefined();
    list.mockRestore();
  });
  it('fetches installed GLB bytes through the authenticated API client', async () => {
    const list = vi.spyOn(devicePacksApi, 'list').mockResolvedValue([{
      id: 'sample', name: 'Sample', version: '1.0.0', categories: [], device_count: 1,
      enabled_by_default: false, source_note: '', enabled: true,
      assets: { 'sample:second-switch': { body: '/api/device-packs/sample/assets/second.glb' } },
    }]);
    const bytes = await readFile(path.resolve(__dirname, '../../../public/3d/brands/cisco/c9300-48p-nm8x.glb'));
    const get = vi.spyOn(http, 'get').mockResolvedValue({ data: bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) });
    invalidatePackAssetRegistry();
    await loadBrandAssets(['sample:second-switch']);
    expect(get).toHaveBeenCalledWith('/device-packs/sample/assets/second.glb', { responseType: 'arraybuffer' });
    expect(getBootGeometry('sample:second-switch')).toBeDefined();
    get.mockRestore();
    list.mockRestore();
    invalidatePackAssetRegistry();
  });
  it('resolves the Cisco GLB from the enabled pack manifest on demand', async () => {
    const manifest = JSON.parse(await readFile(path.resolve(__dirname, '../../../../network/devices/packs/switches/manifest.json'), 'utf8'));
    const list = vi.spyOn(devicePacksApi, 'list').mockResolvedValue([{ ...manifest, enabled: true }]);
    invalidatePackAssetRegistry();
    await loadBrandAssets(['chassis-cisco-c9300-48p-nm8x'], nodeFetch);
    expect(fetched).toContain(manifest.assets['chassis-cisco-c9300-48p-nm8x'].body);
    expect(getVisualChassis('chassis-cisco-c9300-48p-nm8x')).toBeDefined();
    list.mockRestore();
    invalidatePackAssetRegistry();
  });
  it('loads core parts without fetching any brand device, then only the requested SKU', async () => {
    fetched.length = 0;
    await loadBootAssets(nodeFetch);
    expect(fetched.some((url) => url.includes('/brands/') || url.includes('/chassis-'))).toBe(false);
    await loadBrandAssets(['chassis-mikrotik-rb5009-k79'], nodeFetch);
    expect(fetched.filter((url) => url.includes('/brands/'))).toEqual([
      '/3d/brands/mikrotik/rb5009ug-s-in.glb',
    ]);
    const bodyOnly = getVisualChassis('chassis-mikrotik-rb5009-k79')!;
    expect(new THREE.Box3().setFromObject(bodyOnly).getSize(new THREE.Vector3()).x).toBeCloseTo(0.220, 3);
    await loadBrandAssets(
      ['chassis-mikrotik-rb5009-k79'], nodeFetch,
      new Set(['chassis-mikrotik-rb5009-k79']),
    );
    expect(fetched.filter((url) => url.includes('/brands/'))).toEqual([
      '/3d/brands/mikrotik/rb5009ug-s-in.glb',
      '/3d/brands/mikrotik/k79-rackmount.glb',
    ]);
    const scene = getVisualChassis('chassis-mikrotik-rb5009-k79')!;
    expect(scene).toBeDefined();
    const desktopAfterMount = getVisualChassis('chassis-mikrotik-rb5009-k79', false)!;
    expect(desktopAfterMount.getObjectByName('k79-left-ear')).toBeUndefined();
    expect(new THREE.Box3().setFromObject(desktopAfterMount).getSize(new THREE.Vector3()).x).toBeCloseTo(0.220, 3);
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
