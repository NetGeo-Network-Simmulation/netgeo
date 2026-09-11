/**
 * NavigationRail.dock.test.ts — guards the auto-hide dock contract (slice/
 * ui-dock-autohide, Surya QA 2026-09-11: "buat seperti dash to dock tanpa
 * perlu animasi cukup agar bisa buka tutup sendiri").
 *
 * No React renderer lives in this repo yet (vitest.config.ts: "No React/
 * component tests live here yet"), so this checks source text directly,
 * matching the existing convention (theme/z.ts, the deleted
 * chromeInset.usage.test.ts) — brittle to a literal rewrite, but a
 * regression that re-adds a transition/animation or drops the keyboard
 * reveal path should fail loudly here.
 */
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';

const src = readFileSync(path.resolve(__dirname, 'NavigationRail.tsx'), 'utf8');

describe('NavigationRail auto-hide dock', () => {
  it('never uses a transition/animation class — the reveal/hide is instant, by explicit request', () => {
    expect(src).not.toMatch(/\btransition(?!-colors)|animate-/);
  });

  it('reveals on keyboard focus (group-focus-within), not just mouse hover', () => {
    expect(src).toContain('group-focus-within/dock:opacity-100');
  });

  it('reveals on hover of the hot-zone (group-hover), the mouse path', () => {
    expect(src).toContain('group-hover/dock:opacity-100');
  });
});
