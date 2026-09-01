/**
 * Slice F bug 1 — frustumSpan() (pulled out of Rack3DElevationPanel's
 * spanFor(), see its own comment) must always compute a half-extent that
 * keeps the whole rack — floor to mesh-top + tray headroom — inside the
 * orthographic frustum. The pre-fix formula halved the top alone and
 * ignored where the camera's look-at height (`cy`) actually sits, so it
 * undershot and clipped the rack's own head even before any outer zoom
 * factor was applied.
 */
import { describe, expect, it } from 'vitest';
import { frustumSpan } from './plantAdapter';
import { U } from './rack3d';

/** A frustum centred on cy with the given half-extent must cover
 *  [floor, meshTop] — the whole rack — with no gap at either edge. */
function fits(railTopM: number, span: number) {
  const meshTop = railTopM + 0.3;
  const cy = railTopM * 0.46;
  const floorY = -0.08;
  return cy - span <= floorY + 1e-9 && cy + span >= meshTop - 1e-9;
}

describe('frustumSpan (Slice F bug 1)', () => {
  it('12U rack: fits floor-to-mesh-top, unzoomed, no row width', () => {
    const railTopM = 12 * U;
    const span = frustumSpan({ railTopM, rowWidthM: 0, aspect: 16 / 9, zoomed: false });
    expect(fits(railTopM, span)).toBe(true);
  });

  it('42U rack: fits floor-to-mesh-top, unzoomed, no row width', () => {
    const railTopM = 42 * U;
    const span = frustumSpan({ railTopM, rowWidthM: 0, aspect: 16 / 9, zoomed: false });
    expect(fits(railTopM, span)).toBe(true);
  });

  it('a wide N-bay row: width becomes the binding constraint but height still fits', () => {
    const railTopM = 12 * U; // short racks, wide row (e.g. many 12U bays side by side)
    const rowWidthM = 6; // far wider than three 600mm bays + gaps
    const aspect = 16 / 9;
    const span = frustumSpan({ railTopM, rowWidthM, aspect, zoomed: false });
    const widthSpan = (rowWidthM + 1.2) / 2 / aspect;
    expect(span).toBeCloseTo(widthSpan, 9); // width is the larger term here
    expect(fits(railTopM, span)).toBe(true); // never sacrifices height coverage for it
  });

  it('mixed heights (caller passes the tallest shown bay): a 42U neighbour still frames a fit', () => {
    const railTopM = 42 * U; // caller already reduced heights.map(...) to its max
    const span = frustumSpan({ railTopM, rowWidthM: 1.3, aspect: 16 / 9, zoomed: false });
    expect(fits(railTopM, span)).toBe(true);
  });

  it('zoomed work-mode ignores row width entirely, even when one is supplied', () => {
    const railTopM = 42 * U;
    const span = frustumSpan({ railTopM, rowWidthM: 50, aspect: 16 / 9, zoomed: true });
    const spanNoWidth = frustumSpan({ railTopM, rowWidthM: 0, aspect: 16 / 9, zoomed: true });
    expect(span).toBe(spanNoWidth);
  });

  it('narrow/short viewport (1024x640) still fits a 42U rack', () => {
    const railTopM = 42 * U;
    const span = frustumSpan({ railTopM, rowWidthM: 0.6, aspect: 1024 / 640, zoomed: false });
    expect(fits(railTopM, span)).toBe(true);
  });
});
