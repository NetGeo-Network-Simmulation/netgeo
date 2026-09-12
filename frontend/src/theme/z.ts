/**
 * z.ts — the single source of truth for stacking order (design 12-UI §2.4).
 *
 * Every non-Leaflet overlay in the app picks its layer from here instead of a
 * hardcoded `z-[NNN]`. Leaflet's own panes (tile/overlay/marker/tooltip/popup)
 * and the map's in-container loading badges keep Leaflet's internal z-context —
 * they are untouched by this scale.
 *
 * Two shapes for the same seven layers:
 *   - `zc.*`  Tailwind class string, for `className={cn(zc.workspace, …)}`.
 *   - `Z.*`   numeric value, for the rare `style={{ zIndex: Z.workspace }}`.
 * The `z-[NNN]` literals below are the ONLY ones in src (Tailwind JIT scans this
 * file, so referencing `zc.workspace` elsewhere still compiles the utility).
 */
export const Z = {
  canvas: 0, // canvases (topology / map surface)
  workspace: 100, // workspace chrome: toolbars, side panels, trays, legends
  drawer: 200, // BottomDrawer
  dock: 300, // SimulationDock transport
  popover: 400, // TopBar dropdowns, UpdatesButton
  modal: 500, // ModalScrim + every modal (command, picker, settings, …)
  toast: 600, // transient notice / flash chips
} as const;

export const zc = {
  canvas: 'z-0',
  workspace: 'z-[100]',
  drawer: 'z-[200]',
  dock: 'z-[300]',
  popover: 'z-[400]',
  modal: 'z-[500]',
  toast: 'z-[600]',
} as const;
