/**
 * shell.ts — the single source of truth for the floating rail's geometry
 * (design 12-UI §2, v1.2.56 map-bleed slice; broadened slice/ui-layout-
 * consistency to every full-bleed workspace, not just map/rf; corrected
 * slice/ui-edge-fit after Surya's 2026-09-07 re-QA found the broadening had
 * over-applied the fix — see below; corrected AGAIN 2026-09-18 after a
 * leader review of docs/qa/shots/native-controls-2026-09-18/03 found the
 * claim below false at 680×640 — see RAIL_TOP_CLEAR/RAIL_BOTTOM_CLEAR).
 *
 * `NavigationRail` floats at `left-6 w-[76px]` (design §3.2, not duplicated
 * here since only chrome that actually overlaps it needs to react): its
 * right edge sits at x=100. It used to be simply `top-1/2 -translate-y-1/2`
 * (content-height, viewport-centered) on the claim that being vertically
 * centered kept it "well short of the top or bottom edge on any realistic
 * window height" — FALSE at small window heights: the rail's own natural
 * content is ~527px tall (measured live, Playwright element-rect, not the
 * ~558px this file used to claim), so on any window short enough that the
 * available band drops below that, the centered-but-unbounded rail grows
 * past Topology's top chips/search row (bottom edge 80.5px below the
 * workspace top, measured) and its bottom toolbar dock (top edge 58px above
 * the workspace bottom, measured) — exactly the 2026-09-18 QA bug. The rail
 * now self-bounds to `RAIL_TOP_CLEAR`/`RAIL_BOTTOM_CLEAR` (NavigationRail.tsx)
 * instead of trusting "it's centered, so it's fine": within that band it
 * still centers itself (unchanged visual for any normal window size), but
 * it can no longer grow into either bar, and degrades its own content
 * (hide decoration, then scroll the icon list) rather than overlap anything
 * when the band itself is shorter than its content. Two more things key off
 * its geometry:
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
 *  shares a deliberate aligned column with such a control (RfWorkspace's
 *  OSM pill, the map's RF-coverage legend — part of the same left tool
 *  column as MapToolbar). A bar pinned to a fixed top/bottom edge is never
 *  in the rail's band and must not use this. */
export const CHROME_INSET = 'left-[136px]';
/** Padding-based twin of `CHROME_INSET`: same 136px clearance from the rail,
 *  but as inner padding so an in-flow row's own border/background can still
 *  span the full width and touch x=0. Same "must actually be in the rail's
 *  vertical band" restriction as `CHROME_INSET` applies — a fixed top/bottom
 *  bar (toolbar row, status bar) should use plain `px-3`/`pl-3` instead. */
export const CHROME_INSET_PL = 'pl-[136px]';

/**
 * Vertical band NavigationRail confines itself to, measured from the
 * workspace's own top/bottom edges (the `<main>` wrapper AppShell gives the
 * rail and workspace — NOT the viewport, TopBar/StatusBar already sit
 * outside it). Real numbers (Playwright element-rect, a device placed +
 * inspector open, 2026-09-18 — the same method the old `558px`/`680×640`
 * claim skipped, and this time across every workspace the rail actually
 * appears in, not just Topology — Physical Plant's own toolbar turned out
 * taller than Topology's, so a Topology-only measurement would have under-
 * sized this the same way the bug it replaces did):
 *   - Topology's top chips-row + search-field panel: bottom edge 80.5px
 *     below the workspace top, constant across every width tried down to
 *     680px (the chips row never wraps in that range).
 *   - Physical Plant's own two-row toolbar (Rack3DElevationPanel — site/
 *     rack picker row + Animasi/Cable Mode/Tambah perangkat row): bottom
 *     edge 116px below the workspace top — the taller of the two, and the
 *     one RAIL_TOP_CLEAR is actually sized from.
 *   - Topology's bottom Add/Select/Link/Group/Delete dock: top edge 58px
 *     above the workspace bottom, constant regardless of window width
 *     (only the dock's OWN width changes as its labels hide below the
 *     `md:`/`sm:` breakpoints — its height never does). Physical Plant's own
 *     bottom status strip sits well inside this already.
 * Map and Config Center don't drive either number: Map's left tool column
 * already uses `CHROME_INSET` (safe at any rail height), and Config
 * Center's sidebar content pads past the rail with `pl-[116px]` (safe at
 * any rail height too) — see their own files.
 * Each clearance below is that measured reach plus one `gap-4` (16px) of
 * breathing room, rounded to a clean number — not the exact minimum, so a
 * sub-pixel layout change elsewhere doesn't reopen the collision. Twin/Edu
 * reuse Topology's chrome PLUS their own extra top bar (TwinStepper/
 * EduModeBar) — possibly taller than even Plant's, NOT verified against
 * this constant (out of this slice's required-workspace list; the rail's
 * own degrade-then-scroll behavior still keeps it from overlapping there,
 * it just enters that degraded state sooner than on the workspaces this
 * was actually measured against).
 */
export const RAIL_TOP_CLEAR = 'top-[136px]';
export const RAIL_BOTTOM_CLEAR = 'bottom-[74px]';
