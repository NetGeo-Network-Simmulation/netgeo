/**
 * Slice C — drag-to-move in the 3D rack view. `resolveDropTarget`,
 * `canPlaceDevice`, and `dropDecision` are the entire decision surface for a
 * drop: together they prove an occupied/oversized slot never reaches
 * `moveDevice.mutate` (Surya: "ditolak dengan jelas sebelum request
 * dikirim") and that a released-but-unresolved or no-op drag sends nothing
 * ("pembatalan = nol perubahan terkirim").
 */
import { describe, expect, it } from 'vitest';
import { canPlaceDevice, dropDecision, resolveDropTarget } from './plantAdapter';
import { U, type DeviceDef, type RackBay } from './rack3d';

function dev(id: string, u: number, h = 1): DeviceDef {
  return { id, u, h, kind: 'switch', brand: 'B', model: 'M', accent: 0, ports: 0 };
}
function bay(key: string, devices: DeviceDef[]): RackBay {
  return { key, enclosure: 'apc', devices };
}

describe('resolveDropTarget', () => {
  it('picks the nearest rack by x-distance', () => {
    const racks = [{ key: 'a', x: 0 }, { key: 'b', x: 1 }];
    expect(resolveDropTarget(racks, { x: 0.9, y: 0.055 })?.rackKey).toBe('b');
    expect(resolveDropTarget(racks, { x: 0.1, y: 0.055 })?.rackKey).toBe('a');
  });

  it('returns null past the tolerance from every rack', () => {
    const racks = [{ key: 'a', x: 0 }];
    expect(resolveDropTarget(racks, { x: 10, y: 0.055 })).toBeNull();
  });

  it('returns null with no racks at all', () => {
    expect(resolveDropTarget([], { x: 0, y: 0.055 })).toBeNull();
  });

  it('derives U1 at the rail base, and higher U for a higher hit', () => {
    const racks = [{ key: 'a', x: 0 }];
    expect(resolveDropTarget(racks, { x: 0, y: 0.055 })?.ru).toBe(1);
    // U5's centre is 4 rack-units up from the base.
    expect(resolveDropTarget(racks, { x: 0, y: 0.055 + 4 * U })?.ru).toBe(5);
  });
});

describe('canPlaceDevice', () => {
  const bays = [bay('a', [dev('existing', 5, 2)])];

  it('rejects below U1', () => {
    expect(canPlaceDevice(bays, 'a', 0, 1, 42)).toBe(false);
  });

  it('rejects past the rack height', () => {
    expect(canPlaceDevice(bays, 'a', 42, 2, 42)).toBe(false);
  });

  it('rejects an overlapping slot', () => {
    expect(canPlaceDevice(bays, 'a', 6, 1, 42)).toBe(false); // inside existing's U5-6
    expect(canPlaceDevice(bays, 'a', 4, 2, 42)).toBe(false); // straddles U5
  });

  it('accepts a free slot', () => {
    expect(canPlaceDevice(bays, 'a', 10, 1, 42)).toBe(true);
    expect(canPlaceDevice(bays, 'a', 1, 4, 42)).toBe(true);
  });

  it('excludes the dragged device from its own overlap check', () => {
    expect(canPlaceDevice(bays, 'a', 5, 2, 42, 'existing')).toBe(true);
  });

  it('rejects an unknown rack', () => {
    expect(canPlaceDevice(bays, 'missing', 1, 1, 42)).toBe(false);
  });
});

describe('dropDecision', () => {
  const bays = [bay('a', [dev('dragged', 5, 1), dev('other', 10, 1)]), bay('b', [])];
  const ruHeights = { a: 42, b: 20 };
  const origin = { rackKey: 'a', ru: 5 };

  it('sends nothing when the drag was released off every rack', () => {
    expect(dropDecision(null, 1, ruHeights, bays, origin, 'dragged')).toEqual({ commit: false });
  });

  it('sends nothing for a no-op drop back on the same slot', () => {
    expect(dropDecision({ rackKey: 'a', ru: 5 }, 1, ruHeights, bays, origin, 'dragged')).toEqual({ commit: false });
  });

  it('sends nothing for an occupied slot', () => {
    expect(dropDecision({ rackKey: 'a', ru: 10 }, 1, ruHeights, bays, origin, 'dragged')).toEqual({ commit: false });
  });

  it('sends nothing for a slot that does not fit (2U into a 1U gap between neighbours)', () => {
    expect(dropDecision({ rackKey: 'a', ru: 9 }, 2, ruHeights, bays, origin, 'dragged')).toEqual({ commit: false });
  });

  it('sends nothing for a rack this panel has no RU height for', () => {
    expect(dropDecision({ rackKey: 'ghost', ru: 1 }, 1, ruHeights, bays, origin, 'dragged')).toEqual({ commit: false });
  });

  it('commits a same-rack RU move', () => {
    expect(dropDecision({ rackKey: 'a', ru: 20 }, 1, ruHeights, bays, origin, 'dragged'))
      .toEqual({ commit: true, rackId: 'a', ruStart: 20 });
  });

  it('commits a cross-rack move within the same scene', () => {
    expect(dropDecision({ rackKey: 'b', ru: 3 }, 1, ruHeights, bays, origin, 'dragged'))
      .toEqual({ commit: true, rackId: 'b', ruStart: 3 });
  });
});
