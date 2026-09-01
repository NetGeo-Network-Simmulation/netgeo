/**
 * NG-PH3D P41 — buildScene's own half of the 0/N enclosure contract: given
 * a `racks` row of length N, exactly N real rack enclosures are built —
 * never a phantom extra bay. This used to include an always-present
 * decorative CPI cabinet every fixture had to account for; Slice G removed
 * it (a second rack the user never added, visible next to whatever they
 * actually built — see rack3d.ts buildScene's assembly section).
 */
import { describe, expect, it } from 'vitest';
import { buildScene, disposeScene, type BuildOptions } from './rack3d';

function bay(key: string): BuildOptions['racks'][number] {
  return { key, enclosure: 'apc', devices: [] };
}

describe('buildScene bay count (NG-PH3D P41)', () => {
  it('one real rack -> exactly one enclosure in the registry, never a ghost second bay', () => {
    const built = buildScene({ racks: [bay('r1')], links: [] });
    try {
      expect(Object.keys(built.registry.racks)).toEqual(['r1']);
    } finally {
      disposeScene(built);
    }
  });

  it('N real racks -> exactly N real enclosures, left to right in the given order', () => {
    const built = buildScene({ racks: [bay('r1'), bay('r2'), bay('r3'), bay('r4')], links: [] });
    try {
      const keys = Object.keys(built.registry.racks);
      expect(keys).toEqual(['r1', 'r2', 'r3', 'r4']);
      const xs = keys.map((k) => built.registry.racks[k]!.x);
      expect(xs).toEqual([...xs].sort((a, b) => a - b)); // left-to-right, no overlap
      for (let i = 1; i < xs.length; i++) expect(xs[i]!).toBeGreaterThan(xs[i - 1]!);
    } finally {
      disposeScene(built);
    }
  });

  it('a 2-bay row is centred exactly the same as the old fixed A/B formula (equal widths: ±(w+gap)/2)', () => {
    const built = buildScene({ racks: [bay('A'), bay('B')], links: [] });
    try {
      const w = 0.6, gap = 0.1; // RACK_SPECS.apc.w = 600mm
      expect(built.registry.racks.A!.x).toBeCloseTo(-(w + gap) / 2, 6);
      expect(built.registry.racks.B!.x).toBeCloseTo((w + gap) / 2, 6);
    } finally {
      disposeScene(built);
    }
  });
});
