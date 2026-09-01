/**
 * NG-PH3D 3e (Surya): a link with exactly one endpoint in the shown racks
 * (a different site, or a wireless radio with no rack of its own) must be
 * KEPT by adaptTopology, not silently dropped — rack3d.ts routes the
 * in-scene half up into the tray and stops there (externalCableCurve).
 * Direct proof at the plantAdapter level, no buildScene/THREE involved.
 */
import { describe, expect, it } from 'vitest';
import type { Cable, Interface, LinkModel, NodeModel, Rack, Topology } from '@/api/types';
import { adaptTopology } from './plantAdapter';

function iface(id: string, nodeId: string): Interface {
  return {
    id, node_id: nodeId, name: 'eth0', type: 'eth', ip: [], mac: '00:00:00:00:00:00',
    speed: 1000, mtu: 1500, peer_link_id: null, admin_enabled: true, poe_enabled: false,
  };
}
function node(id: string, rackId: string | null, ruStart: number | null, ifaceId: string): NodeModel {
  return {
    id, project_id: 'p1', name: id, kind: 'switch', nos: 'forgeos', mode: 'sim', x: 0, y: 0,
    interfaces: [iface(ifaceId, id)], config_ref: null, status: 'stopped',
    rack_id: rackId, ru_start: ruStart, ru_span: 1, device_type_id: null,
  };
}
function rack(id: string): Rack {
  return { id, project_id: 'p1', site_id: null, name: id, ru_height: 42, enclosure_profile: null };
}

describe('adaptTopology keeps links with only one endpoint in the shown racks', () => {
  it('the off-scene node\'s rack is excluded from rackIds — the link survives, not dropped', () => {
    const shown = node('n-shown', 'r1', 1, 'if-shown');
    const offsite = node('n-offsite', 'r2', 1, 'if-offsite'); // real rack, just not in rackIds
    const link: LinkModel = {
      id: 'l1', project_id: 'p1', a_iface: 'if-shown', b_iface: 'if-offsite',
      type: 'copper', bandwidth: 1000, delay: 0, loss: 0, mtu: 1500, status: 'up',
    };
    const cable: Cable = { id: 'c1', project_id: 'p1', link_id: 'l1', media: 'cat6a', length_m: 1, label: '' };
    const topology: Topology = {
      nodes: [shown, offsite], links: [link], racks: [rack('r1'), rack('r2')], cables: [cable],
    };
    // only r1 shown — r2 (and n-offsite) is off-scene, same as a different site
    const adapted = adaptTopology(topology, ['r1'])!;
    expect(adapted.links).toHaveLength(1);
    expect(adapted.links[0]!.a).toEqual(['n-shown', 0]);
    // the off-scene endpoint keeps its node id — rack3d only ever reads a[0]/b[0]
    // to decide presence, never renders b's own ordinal
    expect(adapted.links[0]!.b[0]).toBe('n-offsite');
    expect(adapted.cableIds).toEqual(['c1']);
  });

  it('a node with no rack at all (wireless radio) also keeps its link', () => {
    const shown = node('n-shown', 'r1', 1, 'if-shown');
    const radio = node('n-radio', null, null, 'if-radio');
    const link: LinkModel = {
      id: 'l1', project_id: 'p1', a_iface: 'if-shown', b_iface: 'if-radio',
      type: 'wireless', bandwidth: 100, delay: 0, loss: 0, mtu: 1500, status: 'up',
    };
    const cable: Cable = { id: 'c1', project_id: 'p1', link_id: 'l1', media: 'cat6a', length_m: 1, label: '' };
    const topology: Topology = {
      nodes: [shown, radio], links: [link], racks: [rack('r1')], cables: [cable],
    };
    const adapted = adaptTopology(topology, ['r1'])!;
    expect(adapted.links).toHaveLength(1);
    expect(adapted.links[0]!.b[0]).toBe('n-radio');
  });

  it('a link where NEITHER endpoint is shown is still dropped', () => {
    const a = node('n-a', 'r2', 1, 'if-a');
    const b = node('n-b', 'r2', 2, 'if-b');
    const link: LinkModel = {
      id: 'l1', project_id: 'p1', a_iface: 'if-a', b_iface: 'if-b',
      type: 'copper', bandwidth: 1000, delay: 0, loss: 0, mtu: 1500, status: 'up',
    };
    const cable: Cable = { id: 'c1', project_id: 'p1', link_id: 'l1', media: 'cat6a', length_m: 1, label: '' };
    const topology: Topology = {
      nodes: [a, b], links: [link], racks: [rack('r1'), rack('r2')], cables: [cable],
    };
    const adapted = adaptTopology(topology, ['r1'])!;
    expect(adapted.links).toHaveLength(0);
  });
});
