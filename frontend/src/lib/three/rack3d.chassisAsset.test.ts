import { beforeAll, describe, expect, it } from 'vitest';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import * as THREE from 'three';
import type { DeviceType as CatalogEntry } from '@/api/client';
import { resolveDeviceType } from '@/components/rack/deviceTypes';
import { chassisFamilyForDeviceSlug, loadBrandAssets } from './bootAssets';
import { buildScene, disposeScene, type DeviceDef } from './rack3d';

async function nodeFetch(url: string): Promise<ArrayBuffer> {
  const file = path.resolve(__dirname, '../../../public', url.replace(/^\//, ''));
  const buf = await readFile(file);
  return buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength);
}

beforeAll(() => loadBrandAssets([
  'chassis-mikrotik-ccr2004', 'chassis-mikrotik-crs317',
  'chassis-mikrotik-rb5009-k79', 'chassis-cisco-c9300-48p-nm8x',
], nodeFetch, new Set(['chassis-mikrotik-rb5009-k79'])));

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
  it('resolves the C9300 pack alias to the corrected 488mm/NM-8X catalog entry', () => {
    const pack = {
      id: 'switches:cisco-c9300-48p-access', name: 'Cisco Catalyst 9300-48P',
      category: 'switches', description: '', builtin: true, vendor: 'Cisco',
      ports: [{ count: 48, type: 'eth' }, { count: 8, type: 'sfp' }],
      physical: { ru: 1, form_factor: '1U-stackable' },
    } satisfies CatalogEntry;
    const resolved = resolveDeviceType('ios', 'switch', [], pack);
    expect(resolved.slug).toBe('cisco-c9300-48p');
    expect(resolved.chassisMm).toEqual({ widthMm: 445, depthMm: 488 });
    expect(resolved.front.portZones.flatMap((zone) => zone.ports)
      .find((port) => port.type === 'sfp+')?.count).toBe(8);
  });
  it('resolves the RB5009 pack as a 220 mm body with a separate K-79 asset', () => {
    const pack = {
      id: 'routers:mikrotik-rb5009ug-s-in',
      name: 'MikroTik RB5009UG+S+IN', category: 'router',
      description: '', builtin: true, vendor: 'MikroTik',
      ports: [
        { count: 1, type: 'sfp' }, { count: 1, type: 'eth' },
        { count: 7, type: 'eth' },
      ],
      physical: { ru: 1, form_factor: 'K-79 1U adapter' },
    } satisfies CatalogEntry;
    const resolved = resolveDeviceType('routeros', 'router', [], pack);
    expect(resolved.chassisMm).toEqual({ widthMm: 220, depthMm: 125 });
    expect(chassisFamilyForDeviceSlug(resolved.slug)).toBe('chassis-mikrotik-rb5009-k79');
    expect(resolved.front.portZones.flatMap((zone) => zone.ports).reduce((n, p) => n + p.count, 0)).toBe(9);
  });
  it('uses the full-color RB5009 and K-79 with nine cableable anchors and no second faceplate', () => {
    const built = build({
      ...base, id: 'rb', model: 'RB5009UG+S+IN',
      chassisAsset: 'chassis-mikrotik-rb5009-k79',
      bodyWidthM: 0.220, bodyDepthM: 0.125,
      ports: 9,
      portGroups: [{ type: 'sfp28', count: 1 }, { type: 'rj45', count: 8 }],
    });
    try {
      const chassis = built.root.getObjectByName('chassis')!;
      const size = new THREE.Box3().setFromObject(chassis).getSize(new THREE.Vector3());
      expect(size.x).toBeCloseTo(0.4826, 3);
      expect(size.y).toBeCloseTo(0.04445, 3);
      expect(Object.keys(built.registry.devices.rb!.portRefs)).toHaveLength(9);
      expect(built.root.getObjectByName('front-plate')).toBeFalsy();
      expect(built.root.getObjectByName('rack-ear')).toBeFalsy();
      const hits = built.root.getObjectByName('port-hit-instanced') as THREE.InstancedMesh;
      expect(hits.count).toBe(9);
      expect(hits.userData.portMap[0]).toEqual({ dev: 'rb', port: 0 });
      expect(chassis.getObjectByName('ether1-2p5g-mouth')?.userData.dev).toBe('rb');
    } finally { disposeScene(built); }
  });
  it.each([['desktop', 'mounted'], ['mounted', 'desktop']])(
    'keeps K79 on the mounted instance when %s is built before %s', (first, second) => {
      const devices = [first, second].map((kind, index): DeviceDef => ({
        ...base, id: kind, u: index * 2 + 1, model: 'RB5009UG+S+IN',
        chassisAsset: 'chassis-mikrotik-rb5009-k79',
        bodyWidthM: 0.220, bodyDepthM: 0.125, ports: 9,
        rackMounted: kind === 'mounted',
      }));
      const built = buildScene({
        racks: [{ key: 'r1', enclosure: 'apc', ruHeight: 42, devices }], links: [],
      });
      try {
        const desktop = built.registry.devices.desktop!.group.getObjectByName('chassis')!;
        const mounted = built.registry.devices.mounted!.group.getObjectByName('chassis')!;
        expect(desktop.getObjectByName('k79-left-ear')).toBeUndefined();
        expect(mounted.getObjectByName('k79-left-ear')).toBeDefined();
        expect(new THREE.Box3().setFromObject(desktop).getSize(new THREE.Vector3()).x).toBeCloseTo(0.220, 3);
        expect(new THREE.Box3().setFromObject(mounted).getSize(new THREE.Vector3()).x).toBeCloseTo(0.4826, 3);
      } finally { disposeScene(built); }
    },
  );
  it('retains curated dimensions/layout when an exact SKU arrives from the pack API', () => {
    const pack = {
      id: 'onu:actiontec-xg-99m', name: 'Actiontec XG-99M', category: 'onu',
      description: '', builtin: true, vendor: 'Actiontec Electronics',
      ports: [{ count: 2, type: 'eth' }],
      physical: { ru: 1, form_factor: 'desktop-or-wall-mount' },
    } satisfies CatalogEntry;
    const resolved = resolveDeviceType('forgeos', 'host', [], pack);
    expect(resolved.slug).toBe('actiontec-xg-99m');
    expect(resolved.chassisMm).toEqual({ widthMm: 208, depthMm: 150 });
  });

  it('maps the namespaced router catalog id to the curated CCR2004 chassis', () => {
    const pack = {
      id: 'routers:mikrotik-ccr2004-1g-12s-2xs',
      name: 'MikroTik CCR2004-1G-12S+2XS', category: 'router',
      description: '', builtin: true, vendor: 'MikroTik',
      ports: [
        { count: 1, type: 'eth' },
        { count: 12, type: 'sfp' },
        { count: 2, type: 'sfp28' },
      ],
      physical: { ru: 1, form_factor: '1U-rackmount' },
    } satisfies CatalogEntry;
    const resolved = resolveDeviceType('routeros', 'router', [], pack);
    expect(resolved.slug).toBe('mikrotik-ccr2004-1g-12s-2xs');
    expect(resolved.chassisMm).toEqual({ widthMm: 443, depthMm: 224 });
    expect(chassisFamilyForDeviceSlug(resolved.slug)).toBe('chassis-mikrotik-ccr2004');
  });

  it('uses the cached CCR2004 GLB while keeping live catalog ports and anchors', () => {
    const built = build({
      ...base,
      model: 'CCR2004-1G-12S+2XS',
      chassisAsset: 'chassis-mikrotik-ccr2004',
      portGroups: [
        { type: 'rj45', count: 2 },
        { type: 'sfp28', count: 12 },
        { type: 'sfp28', count: 2 },
      ],
      ports: 16,
    });
    try {
      const chassis = built.root.getObjectByName('chassis') as THREE.Mesh;
      const size = new THREE.Vector3();
      new THREE.Box3().setFromObject(chassis).getSize(size);
      expect(size.x).toBeCloseTo(0.443, 3);
      expect(size.y).toBeCloseTo(0.044, 3);
      expect(size.z).toBeCloseTo(0.224, 3);
      expect(Object.keys(built.registry.devices.sw1!.portRefs)).toHaveLength(16);
      expect(built.root.getObjectByName('console-port')).toBeFalsy();
    } finally {
      disposeScene(built);
    }
  });

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

  it('uses the full C9300-48P/NM-8X visual with 56 ordered cable anchors', () => {
    const built = build({
      ...base, id: 'c9300', brand: 'Cisco', model: 'Catalyst 9300-48P',
      chassisAsset: 'chassis-cisco-c9300-48p-nm8x',
      bodyWidthM: 0.445, bodyDepthM: 0.488, ports: 56,
      portGroups: [{ type: 'rj45', count: 48 }, { type: 'sfp28', count: 8 }],
    });
    try {
      const chassis = built.root.getObjectByName('chassis')!;
      const size = new THREE.Box3().setFromObject(chassis).getSize(new THREE.Vector3());
      expect(size.x).toBeCloseTo(0.4826, 3);
      expect(size.z).toBeCloseTo(0.488, 4);
      expect(Object.keys(built.registry.devices.c9300!.portRefs)).toHaveLength(56);
      expect(built.registry.devices.c9300!.portRefs[0]!.y).toBeCloseTo(0.0082, 4);
      expect(built.registry.devices.c9300!.portRefs[47]!.y).toBeCloseTo(-0.0082, 4);
      expect(built.registry.devices.c9300!.portRefs[48]!.x).toBeCloseTo(0.158, 4);
      expect(built.root.getObjectByName('front-plate')).toBeFalsy();
      expect(built.root.getObjectByName('rack-ear')).toBeFalsy();
      const hits = built.root.getObjectByName('port-hit-instanced') as THREE.InstancedMesh;
      expect(hits.count).toBe(56);
      expect(hits.userData.portMap).toHaveLength(56);
      expect(hits.userData.portMap[55]).toEqual({ dev: 'c9300', port: 55 });
      const individualHits: THREE.Object3D[] = [];
      built.root.traverse((obj) => { if (/^port-hit-\d+$/.test(obj.name)) individualHits.push(obj); });
      expect(individualHits).toHaveLength(0);
    } finally { disposeScene(built); }
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

  it('keeps a verified desktop ONU narrow and does not add 19-inch rack ears', () => {
    const built = build({
      ...base,
      id: 'ont1',
      brand: 'Actiontec',
      model: 'XG-99M',
      bodyWidthM: 0.208,
      bodyDepthM: 0.15,
      rackMounted: false,
      portGroups: [
        { type: 'pon', count: 1 },
        { type: 'rj45', count: 2 },
        { type: 'fxs', count: 2 },
      ],
      ports: 5,
    });
    try {
      const plate = built.root.getObjectByName('front-plate') as THREE.Mesh;
      const size = new THREE.Vector3();
      new THREE.Box3().setFromObject(plate).getSize(size);
      expect(size.x).toBeCloseTo(0.202, 3);
      expect(built.root.getObjectByName('rack-ear')).toBeFalsy();
      for (const anchor of Object.values(built.registry.devices.ont1!.portRefs)) {
        expect(Math.abs(anchor.x)).toBeLessThan(0.104);
      }
    } finally {
      disposeScene(built);
    }
  });
});
