/**
 * AppShell.bleed.test.ts — guards which workspaces bleed their own surface to
 * x=0 (no left dead-zone) vs pay the RAIL_INSET reserved-space tax on
 * <main> (slice/ui-layout-consistency, Surya QA 2026-09-07: /plant and
 * /topology content sat in a fixed 120px gap that never touched the
 * viewport's left edge; broadened slice/ui-edge-fit, 2026-09-12: the same
 * dead, off-color band was still visible on Projects/Config/Problems/
 * Reports/Twin/Edu, so every workspace but /fiber now bleeds — the
 * list/table ones pad their own leftmost content column instead, see each
 * workspace's `pl-[116px]`).
 *
 * No React renderer lives in this repo yet (vitest.config.ts: "No React/
 * component tests live here yet"), so this checks the source text directly
 * instead of rendering AppShell — brittle to a literal rewrite of the
 * `bleed` expression, but that expression IS the fix, so a rewrite should
 * fail loudly here rather than silently reintroduce the reported gap.
 */
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';

const src = readFileSync(path.resolve(__dirname, 'AppShell.tsx'), 'utf8');
const bleedStart = src.indexOf('const bleed =');
const bleedEnd = src.indexOf(';', bleedStart);
const bleedExpr = bleedStart >= 0 && bleedEnd >= 0 ? src.slice(bleedStart, bleedEnd) : '';

describe('AppShell workspace bleed', () => {
  it('has a `const bleed =` assignment to check', () => {
    expect(bleedExpr).toBeTruthy();
  });

  for (const mode of [
    'map',
    'rf',
    'plant',
    'topology',
    'twin',
    'edu',
    'projects',
    'config',
    'problems',
    'reports',
  ]) {
    it(`bleeds the ${mode} workspace to x=0 (viewMode === '${mode}')`, () => {
      expect(bleedExpr).toContain(`viewMode === '${mode}'`);
    });
  }

  // /fiber is the one workspace not covered by this slice — it should keep
  // the simpler reserved-space contract, not silently pick up bleed via a
  // careless rewrite (e.g. `viewMode !== 'x'`).
  it('does NOT bleed the fiber workspace', () => {
    expect(bleedExpr).not.toContain(`viewMode === 'fiber'`);
  });
});
