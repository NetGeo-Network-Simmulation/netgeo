/**
 * UI store — global, app-wide UI state that isn't graph or window geometry:
 * theme mode, simulation state, and the currently-open project id.
 * Persists theme to localStorage; everything else is session state.
 */
import { create } from 'zustand';
import { applyTheme, type ThemeMode } from '@/theme/tokens';
import type { SimState } from '@/api/types';

const THEME_KEY = 'netgeo.theme';

export type ViewMode = 'projects' | 'topology' | 'map' | 'twin' | 'rf' | 'fiber' | 'edu' | 'plant' | 'config' | 'problems' | 'reports';

/** Bottom-drawer tabs (design 12-UI §2.1) — each body is an existing panel. */
export type DrawerTab = 'console' | 'diagnostics' | 'ledger' | 'config';

/** The single modal slot (design 12-UI §2.3). Exactly one open at a time.
 *  'updates' and 'userMenu' are TopBar corner popovers, not ModalScrim dialogs —
 *  they render inline in their own component, but share this slot so they,
 *  the command palette, and every other modal stay mutually exclusive for free
 *  (single-slot eviction + the existing global Escape handler in useShortcuts). */
export type ModalId =
  | 'command'
  | 'settings'
  | 'scenarios'
  | 'devicePicker'
  | 'importConfig'
  | 'deviceLibrary'
  | 'fiberDetail'
  | 'onboarding'
  | 'mapOnboarding'
  | 'addressingWizard'
  | 'iconLibrary'
  | 'updates'
  | 'userMenu';

/** The workspace views that own a URL path (all of them). ViewMode === path slug,
 *  so the map is the identity — but listing them keeps the parse total + typed. */
const VIEW_PATHS: readonly ViewMode[] = [
  'projects', 'topology', 'map', 'twin', 'rf', 'fiber', 'edu', 'plant', 'config', 'problems', 'reports',
];

/** Read the current view from location.pathname; unknown/`/` → 'topology'. */
function viewFromPath(): ViewMode {
  const slug = window.location.pathname.replace(/^\/+|\/+$/g, '');
  return (VIEW_PATHS as readonly string[]).includes(slug) ? (slug as ViewMode) : 'topology';
}

/** Reflect the active view into the URL without a navigation (F5 restores it). */
function syncPath(view: ViewMode) {
  const next = `/${view}`;
  if (window.location.pathname !== next) window.history.replaceState(null, '', next);
}

function initialTheme(): ThemeMode {
  const saved = localStorage.getItem(THEME_KEY);
  if (saved === 'light') return 'light';
  // 'high-contrast' was removed in v1 (deferred to v2); migrate to 'dark'.
  return 'dark';
}

interface UiState {
  theme: ThemeMode;
  simState: SimState;
  simSpeed: number;
  /** Latest engine virtual-clock time (seconds), from sim.tick telemetry. */
  simTime: number;
  /** Latest engine metrics (delivered/dropped/...), from sim.tick telemetry. */
  simMetrics: Record<string, number> | null;
  projectId: string | null;
  viewMode: ViewMode;

  /** Bottom drawer (design 12-UI §2.1) — renders only in topology/map. Height
   *  is session-persistent (in-memory) and resizable. */
  drawerOpen: boolean;
  drawerTab: DrawerTab;
  drawerHeight: number;

  /** The one open modal, or null. Exclusive by construction (design 12-UI §2.3). */
  activeModal: ModalId | null;

  /** Pending "select + center this node in topology" request (BUG-08). Held in
   *  the store (not a window event) so it survives the workspace switch when
   *  TopologyCanvas isn't mounted yet; the canvas consumes and clears it. */
  focusNodeId: string | null;

  setTheme: (mode: ThemeMode) => void;
  /** Toggle Dark ↔ Light. */
  toggleTheme: () => void;
  setSimState: (s: SimState) => void;
  setSimSpeed: (n: number) => void;
  /** Apply authoritative telemetry from a /ws/topology sim.tick event. */
  applySimTick: (
    t: number,
    metrics?: Record<string, number>,
    state?: SimState | 'completed' | 'stopped' | 'error',
  ) => void;
  setProject: (id: string | null) => void;
  setViewMode: (mode: ViewMode) => void;

  /** Open the drawer to a tab (or toggle it closed if that tab is already up). */
  openDrawer: (tab: DrawerTab) => void;
  setDrawerOpen: (open: boolean) => void;
  setDrawerTab: (tab: DrawerTab) => void;
  setDrawerHeight: (h: number) => void;

  openModal: (id: ModalId) => void;
  closeModal: () => void;

  setFocusNode: (id: string | null) => void;
}

export const useUiStore = create<UiState>((set, get) => ({
  theme: initialTheme(),
  simState: 'idle',
  simSpeed: 1,
  simTime: 0,
  simMetrics: null,
  projectId: null,
  // Restore the workspace from the URL on load so F5 on /problems (etc.) stays
  // put instead of snapping back to topology (BUG-07). LoginPage is gated on auth
  // upstream, so this never interferes with the login redirect.
  viewMode: viewFromPath(),
  drawerOpen: false,
  drawerTab: 'console',
  drawerHeight: 320,
  activeModal: null,
  focusNodeId: null,

  setTheme: (mode) => {
    applyTheme(mode);
    localStorage.setItem(THEME_KEY, mode);
    set({ theme: mode });
  },
  toggleTheme: () => {
    get().setTheme(get().theme === 'dark' ? 'light' : 'dark');
  },
  setSimState: (simState) => set({ simState }),
  setSimSpeed: (simSpeed) => set({ simSpeed }),
  applySimTick: (t, metrics, state) =>
    set((s) => {
      // Terminal engine states snap the transport bar back to idle; running and
      // paused are authoritative and override the optimistic local state.
      let next = s.simState;
      if (state === 'completed' || state === 'stopped' || state === 'error') next = 'idle';
      else if (state === 'running' || state === 'paused') next = state;
      return { simTime: t, simMetrics: metrics ?? s.simMetrics, simState: next };
    }),
  setProject: (projectId) => set({ projectId }),
  setViewMode: (viewMode) => {
    syncPath(viewMode);
    set((s) => {
      // 'updates' and 'userMenu' are corner popovers, not blocking dialog
      // modals (design 12-UI §2.3) — there's no full-screen scrim, so a rail
      // click can still reach the rail while one is open, leaving it stranded
      // over the new workspace (D6). Dialog modals (settings, command, …)
      // never hit this: their scrim blocks the rail, so viewMode can't change
      // under them in the first place.
      const closesOnNav: ModalId[] = ['updates', 'userMenu'];
      const activeModal = s.activeModal && closesOnNav.includes(s.activeModal) ? null : s.activeModal;
      return { viewMode, activeModal };
    });
  },

  openDrawer: (tab) =>
    set((s) => (s.drawerOpen && s.drawerTab === tab ? { drawerOpen: false } : { drawerOpen: true, drawerTab: tab })),
  setDrawerOpen: (drawerOpen) => set({ drawerOpen }),
  setDrawerTab: (drawerTab) => set({ drawerTab, drawerOpen: true }),
  setDrawerHeight: (drawerHeight) => set({ drawerHeight: Math.max(160, Math.min(720, drawerHeight)) }),

  // Single slot → opening any modal evicts the previous one; two can never stack.
  openModal: (activeModal) => set({ activeModal }),
  closeModal: () => set({ activeModal: null }),

  setFocusNode: (focusNodeId) => set({ focusNodeId }),
}));
