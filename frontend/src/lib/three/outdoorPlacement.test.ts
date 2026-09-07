/**
 * Slice 6: pure logic behind the outdoor-placement scene inputs — GLB
 * family per `structure_type`, height scaling + its documented fallback,
 * the rooftop+camouflage "no mesh" case, and node mount elevation. No
 * `three`/browser needed; the actual mesh wiring is covered by
 * rack3d.outdoorStructure.test.ts.
 */
import { describe, expect, it } from 'vitest';
import {
  FALLBACK_STRUCTURE_HEIGHT_M,
  isHiddenStructure,
  mountElevationM,
  structureScale,
  structureSpecFor,
} from './outdoorPlacement';

describe('structureScale', () => {
  it('uses the real height when given', () => {
    expect(structureScale(45)).toBe(45);
  });
  it('falls back to the documented placeholder when null/undefined/0/negative', () => {
    expect(structureScale(null)).toBe(FALLBACK_STRUCTURE_HEIGHT_M);
    expect(structureScale(undefined)).toBe(FALLBACK_STRUCTURE_HEIGHT_M);
    expect(structureScale(0)).toBe(FALLBACK_STRUCTURE_HEIGHT_M);
    expect(structureScale(-5)).toBe(FALLBACK_STRUCTURE_HEIGHT_M);
  });
});

describe('isHiddenStructure', () => {
  it('is true only for rooftop + camouflage true', () => {
    expect(isHiddenStructure('rooftop', true)).toBe(true);
  });
  it('is false for every other combination', () => {
    expect(isHiddenStructure('rooftop', false)).toBe(false);
    expect(isHiddenStructure('rooftop', null)).toBe(false);
    expect(isHiddenStructure('ground', true)).toBe(false);
    expect(isHiddenStructure(null, true)).toBe(false);
    expect(isHiddenStructure(undefined, undefined)).toBe(false);
  });
});

describe('structureSpecFor', () => {
  it('maps monopole -> tower-monopole at real height', () => {
    expect(structureSpecFor({ structure_type: 'monopole', height_agl_m: 24 })).toEqual({
      family: 'tower-monopole',
      scale: 24,
    });
  });
  it('maps self-supporting-lattice -> tower-lattice4', () => {
    expect(structureSpecFor({ structure_type: 'self-supporting-lattice', height_agl_m: 60 })).toEqual({
      family: 'tower-lattice4',
      scale: 60,
    });
  });
  it('maps guyed-mast -> tower-monopole (reused, no lattice/guy-wire asset)', () => {
    expect(structureSpecFor({ structure_type: 'guyed-mast', height_agl_m: 40 })).toEqual({
      family: 'tower-monopole',
      scale: 40,
    });
  });
  it('falls back to the placeholder height when height_agl_m is missing', () => {
    expect(structureSpecFor({ structure_type: 'monopole', height_agl_m: null })).toEqual({
      family: 'tower-monopole',
      scale: FALLBACK_STRUCTURE_HEIGHT_M,
    });
  });
  it('is null when the site has no structure_type recorded', () => {
    expect(structureSpecFor({ structure_type: null })).toBeNull();
  });
  it('is null for a disguised rooftop installation (no mesh of its own)', () => {
    expect(
      structureSpecFor({ structure_type: 'monopole', mount_location: 'rooftop', camouflage: true, height_agl_m: 10 }),
    ).toBeNull();
  });
  it('still renders a rooftop structure that is not camouflaged', () => {
    expect(
      structureSpecFor({ structure_type: 'monopole', mount_location: 'rooftop', camouflage: false, height_agl_m: 10 }),
    ).toEqual({ family: 'tower-monopole', scale: 10 });
  });
});

describe('mountElevationM', () => {
  it('uses the recorded height', () => {
    expect(mountElevationM(12.5)).toBe(12.5);
  });
  it('falls back to ground level (0) when missing', () => {
    expect(mountElevationM(null)).toBe(0);
    expect(mountElevationM(undefined)).toBe(0);
  });
});
