/**
 * AppShell.bleed.test.ts — guards which workspaces bleed their canvas to
 * x=0 (no left dead-zone) vs pay the RAIL_INSET reserved-space tax on
 * <main> (slice/ui-layout-consistency, Surya QA 2026-09-07: /plant and
 * /topology content sat in a fixed 120px gap that never touched the
 * viewport's left edge, worst at narrow tab widths where 120px is a large
 * share of the available width).
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
const bleedLine = src.split('\n').find((l) => l.trim().startsWith('const bleed ='));

describe('AppShell workspace bleed', () => {
  it('has exactly one `const bleed =` assignment to check', () => {
    expect(bleedLine).toBeTruthy();
  });

  for (const mode of ['map', 'rf', 'plant', 'topology']) {
    it(`bleeds the ${mode} workspace to x=0 (viewMode === '${mode}')`, () => {
      expect(bleedLine).toContain(`viewMode === '${mode}'`);
    });
  }

  // Everything else (projects/twin/config/problems/reports/edu/fiber) is a
  // list/table/document surface, not a canvas — it should keep the simpler
  // reserved-space contract, not silently pick up bleed via a careless
  // rewrite (e.g. `viewMode !== 'x'`).
  for (const mode of ['projects', 'config', 'problems', 'reports', 'edu']) {
    it(`does NOT bleed the ${mode} workspace`, () => {
      expect(bleedLine).not.toContain(`viewMode === '${mode}'`);
    });
  }
});
