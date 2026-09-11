/**
 * shell.test.ts — guards the left-inset contract (slice/ui-layout-
 * consistency, Surya QA 2026-09-07: /plant and /topology content wasn't
 * touching the viewport's left edge). Pure arithmetic on the exported
 * Tailwind class strings — no DOM/browser needed, matching this repo's
 * existing test style (vitest.config.ts: "No React/component tests live
 * here yet").
 */
import { describe, expect, it } from 'vitest';
import { CHROME_INSET, CHROME_INSET_PL, RAIL_INSET } from './shell';

function px(cls: string): number {
  const m = cls.match(/\[(-?\d+)px\]/);
  if (!m) throw new Error(`not a left-[NNNpx]/pl-[NNNpx] class: ${cls}`);
  return Number(m[1]);
}

describe('shell inset contract', () => {
  it('CHROME_INSET (position) and CHROME_INSET_PL (padding) reserve the identical pixel gap', () => {
    // A bled workspace mixes floating chrome (CHROME_INSET, `left-[…]`) and
    // in-flow chrome (CHROME_INSET_PL, `pl-[…]`) on the same row. If the two
    // ever drifted apart, floating and in-flow controls would misalign.
    expect(px(CHROME_INSET_PL)).toBe(px(CHROME_INSET));
  });

  it('CHROME_INSET is RAIL_INSET re-based on x=0 — same 16px (left-4) margin past the rail', () => {
    // RAIL_INSET (120px) offsets a non-bled workspace so it starts clear of
    // the rail (right edge x=100) with the rail's own left-4 margin to
    // spare. CHROME_INSET (136px) is that same clearance, just measured
    // from x=0 instead of x=120 for a bled workspace's chrome — the two
    // MUST differ by exactly that 16px margin, not by coincidence.
    expect(px(CHROME_INSET) - px(RAIL_INSET)).toBe(16);
  });

  it('no exported inset is zero or negative', () => {
    // A zero/negative inset would let chrome render UNDER the rail instead
    // of merely near it — the exact regression this contract exists to
    // prevent.
    for (const cls of [RAIL_INSET, CHROME_INSET, CHROME_INSET_PL]) {
      expect(px(cls)).toBeGreaterThan(0);
    }
  });
});
