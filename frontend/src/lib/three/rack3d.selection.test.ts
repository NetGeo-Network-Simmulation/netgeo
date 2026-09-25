import { expect, it } from 'vitest';
import * as THREE from 'three';
import { applySelection, buildScene, disposeScene, type BuildOptions, type DeviceDef } from './rack3d';

const device = (id: string, u: number): DeviceDef => ({
  id, u, h: 1, kind: 'switch', brand: 'Test', model: 'T', accent: 0x555555,
  chassis: 0x222222, ports: 2, ptype: 'rj45',
});

it('dims only unrelated cables of the same media regardless of link order or an added rack', () => {
  const base: BuildOptions = {
    racks: [{ key: 'one', enclosure: 'apc', ruHeight: 42, devices: [device('a', 1), device('b', 3), device('c', 5)] }],
    links: [
      { a: ['a', 0], b: ['b', 0], m: 'cat6a', live: true },
      { a: ['b', 1], b: ['c', 0], m: 'cat6a', live: true },
    ],
  };
  for (const reverse of [false, true]) {
    const opts = { ...base, links: reverse ? [...base.links].reverse() : base.links,
      racks: reverse ? [...base.racks, { key: 'two', enclosure: 'apc', ruHeight: 42, devices: [] }] : base.racks } as BuildOptions;
    const built = buildScene(opts);
    try {
      const cables = built.registry.cables.filter((c) => c.meta.devs.length === 2);
      expect(cables).toHaveLength(2);
      applySelection(built.registry, 'a');
      const touching = cables.find((c) => c.meta.devs.includes('a'))!;
      const other = cables.find((c) => !c.meta.devs.includes('a'))!;
      expect(touching.mesh.material).not.toBe(other.mesh.material);
      expect((touching.mesh.material as THREE.MeshStandardMaterial).opacity).toBe(1);
      expect((other.mesh.material as THREE.MeshStandardMaterial).opacity).toBe(0.12);
      applySelection(built.registry, null);
      expect(touching.mesh.material).toBe(other.mesh.material);
    } finally {
      disposeScene(built);
    }
  }
});
