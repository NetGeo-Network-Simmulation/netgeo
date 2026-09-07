/**
 * shell.ts — the single source of truth for the floating rail's geometry
 * (design 12-UI §2, v1.2.56 map-bleed slice; broadened slice/ui-layout-
 * consistency to every full-bleed workspace, not just map/rf; corrected
 * slice/ui-edge-fit after Surya's 2026-09-07 re-QA found the broadening had
 * over-applied the fix — see below).
 *
 * `NavigationRail` floats at `left-6 top-1/2 -translate-y-1/2 w-[76px]`
 * (design §3.2, not duplicated here since only chrome that actually
 * overlaps it needs to react): its right edge sits at x=100, and being
 * vertically CENTERED, it only occupies a band around the middle of the
 * viewport height (roughly ±260px around the vertical center — well short
 * of the top or bottom edge on any realistic window height). Two things
 * key off its geometry:
 *   - The BottomDrawer/SimulationDock layer still reserves `left-[120px]` on
 *     `<main>` (see AppShell's reserved-space contract comment) so it clears
 *     the rail with room to spare.
 *   - A bled workspace's own left-anchored chrome bleeds its canvas to x=0,
 *     which would otherwise slide left-anchored chrome 120px left, under
 *     the rail — but ONLY chrome that is actually positioned inside the
 *     rail's vertical band (the map's vertically-centered tool dock, e.g.
 *     MapToolbar, plus the rest of that same left tool column by design
 *     convention) needs `CHROME_INSET` (position) or `CHROME_INSET_PL`
 *     (padding, for in-flow bars whose own border/background must still
 *     touch x=0) to stay clear of it — both `left-[136px]`, the same
 *     `left-4` (16px) offset chrome always had, just re-based on x=0
 *     instead of x=120.
 *   - Chrome pinned to a FIXED top or bottom edge (a toolbar row that's the
 *     first child of a flex column, a status bar that's the last, a
 *     bottom-anchored floating dock like TopologyToolbar) is NOT in the
 *     rail's vertical band and must NOT use either export — doing so only
 *     wastes 136px of width for no visual reason (the exact regression
 *     Surya reported 2026-09-07: the /plant toolbar row still looked
 *     indented from the left edge after the x=0 canvas bleed landed,
 *     because the row itself kept paying this padding). Such bars should
 *     use a plain, symmetric `px-3`/`left-4`/`pl-3` instead.
 * The `left-[NNNpx]`/`pl-[NNNpx]` literals below are the ONLY ones in src for
 * these offsets (Tailwind JIT scans this file, so referencing the exports
 * elsewhere still compiles the utility — see theme/z.ts for the same idiom).
 */
export const RAIL_INSET = 'left-[120px]';
/** Use ONLY for floating chrome that is actually positioned inside the
 *  rail's vertical band (roughly the middle ±260px of the viewport height —
 *  e.g. MapToolbar's `top-1/2 -translate-y-1/2` dock) or for chrome that
 *  shares a deliberate aligned column with such a control (MapSearch,
 *  RfWorkspace's OSM pill, the map's RF-coverage legend — all part of the
 *  same left tool column as MapToolbar). A bar pinned to a fixed top/bottom
 *  edge is never in the rail's band and must not use this. */
export const CHROME_INSET = 'left-[136px]';
/** Padding-based twin of `CHROME_INSET`: same 136px clearance from the rail,
 *  but as inner padding so an in-flow row's own border/background can still
 *  span the full width and touch x=0. Same "must actually be in the rail's
 *  vertical band" restriction as `CHROME_INSET` applies — a fixed top/bottom
 *  bar (toolbar row, status bar) should use plain `px-3`/`pl-3` instead. */
export const CHROME_INSET_PL = 'pl-[136px]';
