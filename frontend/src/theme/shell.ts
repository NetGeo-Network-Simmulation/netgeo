/**
 * shell.ts — the single source of truth for the floating rail's geometry
 * (design 12-UI §2, v1.2.56 map-bleed slice; broadened slice/ui-layout-
 * consistency to every full-bleed workspace, not just map/rf).
 *
 * `NavigationRail` floats at `left-6 w-[76px]` (design §3.2, not duplicated
 * here since only AppShell/workspace chrome need to react to it), so its
 * right edge sits at x=100. Two things key off that:
 *   - The BottomDrawer/SimulationDock layer still reserves `left-[120px]` on
 *     `<main>` (see AppShell's reserved-space contract comment) so it clears
 *     the rail with room to spare.
 *   - Every workspace's own canvas/background bleeds to x=0 instead (so
 *     panning/the full width is never obstructed), which would otherwise
 *     slide left-anchored chrome (toolbar, search box, inspector, …) 120px
 *     left, right under the rail. That chrome instead uses `CHROME_INSET`
 *     (position) or `CHROME_INSET_PL` (padding, for in-flow bars whose own
 *     border/background must still touch x=0) — both `left-[136px]`, the
 *     same `left-4` (16px) offset chrome always had, just re-based on x=0
 *     instead of x=120 — so it stays visually put.
 * The `left-[NNNpx]`/`pl-[NNNpx]` literals below are the ONLY ones in src for
 * these offsets (Tailwind JIT scans this file, so referencing the exports
 * elsewhere still compiles the utility — see theme/z.ts for the same idiom).
 */
export const RAIL_INSET = 'left-[120px]';
export const CHROME_INSET = 'left-[136px]';
/** Padding-based twin of `CHROME_INSET`: same 136px clearance from the rail,
 *  but as inner padding so an in-flow row's own border/background can still
 *  span the full width and touch x=0 (used by full-bleed workspaces' toolbar
 *  and status rows, e.g. Rack3DElevationPanel). */
export const CHROME_INSET_PL = 'pl-[136px]';
