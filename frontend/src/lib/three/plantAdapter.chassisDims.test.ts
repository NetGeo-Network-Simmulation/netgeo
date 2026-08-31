/**
 * NG-PH3D 3b-2: proves the §8.1-sourced per-SKU chassis override actually
 * reaches DeviceDef, and that a curated model whose dimensions are
 * UNVERIFIED (arista-7050cx3-32s) falls back to the generic default instead
 * — carrying the same "approximate shape" marker `def.generic` drives
 * elsewhere (faceTexture()'s dashed hachure border).
 */
import { describe, expect, it } from 'vitest';
import type { NodeModel, Rack, Topology } from '@/api/types';
import { adaptTopology } from './plantAdapter';

function node(id: string, nos: NodeModel['nos'], ruStart: number): NodeModel {
  return {
    id,
    project_id: 'p1',
    name: id,
    kind: 'switch',
    nos,
    mode: 'sim',
    x: 0,
    y: 0,
    interfaces: [],
    config_ref: null,
    status: 'stopped',
    rack_id: 'r1',
    ru_start: ruStart,
    ru_span: 1,
    device_type_id: null,
  };
}

const rack: Rack = { id: 'r1', project_id: 'p1', site_id: null, name: 'Rack1', ru_height: 42, enclosure_profile: null };

describe('plantAdapter — per-SKU chassis dimensions (§8.1)', () => {
  it('a model with verified chassisMm (MikroTik CRS317) gets real body dims and no approximate marker', () => {
    const topology: Topology = { nodes: [node('n1', 'routeros', 1)], links: [], racks: [rack] };
    const adapted = adaptTopology(topology, ['r1'])!;
    const dev = adapted.racks[0]!.devices.find((d) => d.id === 'n1')!;
    expect(dev.bodyWidthM).toBeCloseTo(0.443, 5);
    expect(dev.bodyDepthM).toBeCloseTo(0.224, 5);
    expect(dev.generic).toBe(false);
  });

  it('a curated model with UNVERIFIED dims (Arista 7050CX3-32S) falls back to the generic default and is marked approximate', () => {
    const topology: Topology = { nodes: [node('n2', 'eos', 1)], links: [], racks: [rack] };
    const adapted = adaptTopology(topology, ['r1'])!;
    const dev = adapted.racks[0]!.devices.find((d) => d.id === 'n2')!;
    expect(dev.bodyWidthM).toBeUndefined();
    expect(dev.bodyDepthM).toBeUndefined();
    expect(dev.generic).toBe(true);
  });

  it('two verified models with different §8.1 numbers produce different body dims (CRS317 vs QFX5120)', () => {
    const topology: Topology = {
      nodes: [node('n1', 'routeros', 1), node('n4', 'junos', 2)],
      links: [],
      racks: [rack],
    };
    const adapted = adaptTopology(topology, ['r1'])!;
    const crs317 = adapted.racks[0]!.devices.find((d) => d.id === 'n1')!;
    const qfx = adapted.racks[0]!.devices.find((d) => d.id === 'n4')!;
    expect(crs317.bodyWidthM).not.toBeCloseTo(qfx.bodyWidthM!, 3);
    expect(crs317.bodyDepthM).not.toBeCloseTo(qfx.bodyDepthM!, 2);
  });
});
