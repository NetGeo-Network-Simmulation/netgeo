/**
 * chromeInset.usage.test.ts — guards WHICH bars use `CHROME_INSET`/
 * `CHROME_INSET_PL` (slice/ui-edge-fit, Surya re-QA 2026-09-07: the /plant
 * toolbar row still looked indented from the left edge, and its right side
 * got cut off, after slice/ui-layout-consistency's x=0 canvas bleed landed
 * — root cause was this padding/offset being applied to bars pinned to a
 * fixed top/bottom edge, which the vertically-centered rail never overlaps;
 * see theme/shell.ts for the full contract).
 *
 * No React renderer lives in this repo yet (vitest.config.ts: "No React/
 * component tests live here yet"), so this checks source text directly —
 * brittle to a literal rewrite, but a careless re-introduction of the inset
 * on a fixed-edge bar (or a careless removal from a genuinely rail-band
 * control) should fail loudly here.
 */
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';

const componentsDir = path.resolve(__dirname, '../components');
const read = (rel: string) => readFileSync(path.join(componentsDir, rel), 'utf8');

describe('bars pinned to a fixed top/bottom edge must NOT use the rail inset', () => {
  // Rack3DElevationPanel: toolbar (top-pinned) + every stacked banner row +
  // the status bar (bottom-pinned) — none of them sit in the rail's
  // vertically-centered band.
  it('Rack3DElevationPanel no longer imports or uses CHROME_INSET_PL', () => {
    const src = read('plant/Rack3DElevationPanel.tsx');
    expect(src).not.toContain('CHROME_INSET_PL');
    expect(src).not.toContain('CHROME_INSET');
  });

  it('UnrackedDevicesPanel (embedded in the same top-pinned stack) no longer uses it either', () => {
    const src = read('plant/UnrackedDevicesPanel.tsx');
    expect(src).not.toContain('CHROME_INSET_PL');
    expect(src).not.toContain('CHROME_INSET');
  });

  it('TopologyToolbar (bottom-pinned floating dock) no longer imports CHROME_INSET', () => {
    const src = read('topology/TopologyToolbar.tsx');
    expect(src).not.toMatch(/from '@\/theme\/shell'/);
  });
});

describe('chrome actually inside the rail\'s vertical band keeps the inset', () => {
  // MapToolbar floats at top-1/2 -translate-y-1/2 — dead center, the one
  // control genuinely behind the rail's band. RfWorkspace shares its left
  // tool column by deliberate design, not because it individually overlaps
  // the rail, so it keeps the same offset for visual alignment. (MapSearch
  // was the other member of this group; QA-visual #1, 2026-09-12, removed
  // it in favor of the CommandPalette's location search.)
  it('MapToolbar (vertically centered) still uses CHROME_INSET', () => {
    expect(read('map/MapToolbar.tsx')).toContain('CHROME_INSET');
  });

  it('RfWorkspace (shares the map tool column) still uses CHROME_INSET', () => {
    expect(read('rf/RfWorkspace.tsx')).toContain('CHROME_INSET');
  });
});
