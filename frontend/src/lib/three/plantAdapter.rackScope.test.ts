/**
 * NG-PH3D P41 — the slice's core contract: the scene shows *exactly* the
 * racks that exist for the currently viewed site, no more, no fewer, and
 * nothing at all when there are zero. `racksForSite` is what the panel
 * calls to turn "which site is selected" into the rack-id row `adaptTopology`
 * takes; `adaptTopology` is what turns that row into `BuildOptions.racks`
 * (the only thing `buildScene` reads a rack count from) — proving both
 * together proves the panel can't draw a rack that doesn't exist, or skip
 * one that does.
 */
import { describe, expect, it } from 'vitest';
import type { NodeModel, Rack, Topology } from '@/api/types';
import { adaptTopology, racksForSite } from './plantAdapter';

function rack(id: string, siteId: string | null, name = id): Rack {
  return { id, project_id: 'p1', site_id: siteId, name, ru_height: 42, enclosure_profile: null };
}

describe('racksForSite (site-scoped rack row)', () => {
  it('a site with zero racks resolves to zero ids', () => {
    const racks = [rack('r1', 'site-a'), rack('r2', 'site-a')];
    expect(racksForSite(racks, 'site-b')).toEqual([]);
  });

  it('one rack in the site resolves to exactly that one id', () => {
    const racks = [rack('r1', 'site-a'), rack('r2', 'site-b')];
    expect(racksForSite(racks, 'site-a')).toEqual(['r1']);
  });

  it('N racks in the site resolve to exactly those N ids, in topology order — a rack from another site never leaks in', () => {
    const racks = [rack('r1', 'site-a'), rack('r2', 'site-b'), rack('r3', 'site-a'), rack('r4', 'site-a')];
    expect(racksForSite(racks, 'site-a')).toEqual(['r1', 'r3', 'r4']);
  });

  it('null site_id racks group into the "(no site)" bucket, separate from any real site', () => {
    const racks = [rack('r1', null), rack('r2', 'site-a')];
    expect(racksForSite(racks, null)).toEqual(['r1']);
    expect(racksForSite(racks, 'site-a')).toEqual(['r2']);
  });
});

describe('adaptTopology rack count (NG-PH3D P41 — 0/N enclosure contract)', () => {
  function topologyWith(racks: Rack[]): Topology {
    const nodes: NodeModel[] = [];
    return { nodes, links: [], racks };
  }

  it('zero real racks resolved -> adaptTopology returns null (buildScene must not be called at all)', () => {
    const topology = topologyWith([rack('r1', 'site-a')]);
    expect(adaptTopology(topology, [])).toBeNull();
    expect(adaptTopology(topology, racksForSite(topology.racks!, 'site-b')))
      .toBeNull(); // no rack in site-b
  });

  it('one real rack -> BuildOptions.racks has exactly one bay', () => {
    const topology = topologyWith([rack('r1', 'site-a')]);
    const adapted = adaptTopology(topology, racksForSite(topology.racks!, 'site-a'))!;
    expect(adapted.racks.map((b) => b.key)).toEqual(['r1']);
  });

  it('N real racks -> BuildOptions.racks has exactly N bays, in order — adding a rack grows it by exactly one', () => {
    const topology = topologyWith([rack('r1', 'site-a'), rack('r2', 'site-a'), rack('r3', 'site-a')]);
    const ids = racksForSite(topology.racks!, 'site-a');
    expect(adaptTopology(topology, ids)!.racks.map((b) => b.key)).toEqual(['r1', 'r2', 'r3']);
    // "adding one more rack" == the same call with one more id in the row
    const grown = topologyWith([...topology.racks!, rack('r4', 'site-a')]);
    expect(adaptTopology(grown, racksForSite(grown.racks!, 'site-a'))!.racks.map((b) => b.key))
      .toEqual(['r1', 'r2', 'r3', 'r4']);
  });

  it('switching site drops the previous site\'s racks entirely, not just adds the new ones', () => {
    const topology = topologyWith([rack('r1', 'site-a'), rack('r2', 'site-a'), rack('r3', 'site-b')]);
    const siteA = adaptTopology(topology, racksForSite(topology.racks!, 'site-a'))!;
    expect(siteA.racks.map((b) => b.key)).toEqual(['r1', 'r2']);
    const siteB = adaptTopology(topology, racksForSite(topology.racks!, 'site-b'))!;
    expect(siteB.racks.map((b) => b.key)).toEqual(['r3']); // no r1/r2 leaking across the switch
  });
});
