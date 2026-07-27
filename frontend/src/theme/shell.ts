/**
 * shell.ts — the single source of truth for the floating rail's geometry
 * (design 12-UI §2, v1.2.56 map-bleed slice).
 *
 * `NavigationRail` floats at `left-6 w-[76px]` (design §3.2, not duplicated
 * here since only AppShell/map chrome need to react to it), so its right
 * edge sits at x=100. Two things key off that:
 *   - Every non-map workspace still reserves `left-[120px]` on `<main>`
 *     (see AppShell's reserved-space contract comment) so its content clears
 *     the rail with room to spare.
 *   - Map/RF workspaces bleed their canvas to x=0 instead (so panning is
 *     never obstructed), which would otherwise slide their left-anchored
 *     chrome (toolbar, search box, …) 120px left, right under the rail. That
 *     chrome moves to `left-[136px]` — the same `left-4` (16px) offset it
 *     always had, just re-based on x=0 instead of x=120 — so it stays put.
 * The `left-[NNNpx]` literals below are the ONLY ones in src for these two
 * offsets (Tailwind JIT scans this file, so referencing the exports
 * elsewhere still compiles the utility — see theme/z.ts for the same idiom).
 */
export const RAIL_INSET = 'left-[120px]';
export const MAP_CHROME_INSET = 'left-[136px]';
