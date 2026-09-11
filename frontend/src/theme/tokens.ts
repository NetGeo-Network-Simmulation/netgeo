/**
 * NetGeo design tokens — single source of truth for the design system.
 *
 * NetGeo's visual identity blends a professional GIS workstation (ArcGIS Pro),
 * a modern IDE (VS Code), and a flexible creative workspace (Figma/Blender).
 * Tokens are exposed three ways so every layer stays in sync:
 *   1. Tailwind (static palette)            → tailwind.config.js
 *   2. TypeScript (canvas / inline styles)  → this module
 *   3. CSS custom properties on <html>      → applyTheme(), drives theming
 *
 * Themes (03_UI_UX_GUIDELINES §Theme): Light · Dark.
 */
import type { NodeKind } from '@/api/types';

export type ThemeMode = 'light' | 'dark';

/**
 * Semantic color tokens (03_UI_UX_GUIDELINES §Color Tokens).
 * Primary/Secondary/Success/Warning/Danger/Info form the accent system; the
 * neutral ramp + surface tokens are theme-dependent (see THEMES below).
 */
export const semantic = {
  // Full Clay Anthropic across both themes (user pick 2026-07-13): coral
  // terracotta primary + warm teal secondary. Per-theme values live in THEMES
  // below; these defaults mirror the dark (warm charcoal) theme.
  primary: '#D97757', // Anthropic coral — AA as text on charcoal (5.3:1) AND carries ink on fills (5.9:1)
  secondary: '#3BAFBF', // warm teal — AI / planning, legible on charcoal (6.4:1)
  success: '#27C28B',
  warning: '#F5A623',
  danger: '#FF4D4F',
  // Brightened from #27B5C2 (audit 2026-09-11: text-info on its own bg-info/20
  // chip fill measured 4.42:1 on dark charcoal, just under WCAG AA 4.5:1 for
  // normal text — see tailwind.config.js `info.DEFAULT`, kept in sync).
  info: '#38BBC7',
} as const;

/** Node-kind accent colors — mirror tailwind `colors.node` + used by canvas. */
export const nodeColors: Record<NodeKind, string> = {
  router: '#2F6BFF',
  switch: '#27C28B',
  host: '#8A93A6',
  ap: '#7C5CFC',
  cpe: '#007AFF',  // wireless client (map deploy)
  olt: '#F5A623',
  firewall: '#FF4D4F',
  server: '#27B5C2',
  cloud: '#3BC9DB',
};

/** Link-status colors for edges (up/down/admin-down/unknown). */
export const linkStatusColors = {
  up: '#27C28B',
  down: '#FF4D4F',
  admin_down: '#F5A623',
  // Physically errored (NG-PH-03): cable over its rated max length. Amber-red
  // to read as a fault distinct from an operator-driven admin_down.
  errored: '#FF7A45',
  unknown: '#8A93A6',
} as const;

/**
 * Per-theme CSS custom properties. Every shell surface reads these (via the
 * Tailwind aliases `surface`, `panel`, `fg`, …) so switching the theme re-skins
 * the whole chrome with zero per-component branching.
 */
type ThemeVars = Record<string, string>;

const THEMES: Record<ThemeMode, ThemeVars> = {
  dark: {
    // Full Clay Anthropic dark (user pick 2026-07-13): warm charcoal ink, NOT
    // navy/blue. Three layers read as layers — ground (darkest) < surface <
    // elevated. Warm charcoal keeps the coral accent feeling native, not glued on.
    '--ng-bg-0': '#0F0F0E', // warm ink ground (darkest layer, gradient outer)
    '--ng-bg-1': '#141413', // app background (gradient center highlight)
    '--ng-surface': 'rgba(31,30,29,0.72)', // #1F1E1D glass surface
    '--ng-surface-2': 'rgba(38,37,36,0.72)', // #262524 elevated glass
    '--ng-panel': '#1F1E1D', // docked panels — warm charcoal
    '--ng-panel-2': '#262524', // elevated panel
    '--ng-border': 'rgba(250,249,245,0.12)',
    '--ng-border-strong': 'rgba(250,249,245,0.20)',
    '--ng-fg': '#FAF9F5', // ivory (17.5:1 on app bg)
    '--ng-fg-muted': '#A8A49C', // warm grey (6.7:1 on surface)
    '--ng-fg-subtle': '#847E75', // faint warm grey (4.6:1 on app bg)
    // Alpha-capable channels for the theme-aware `fg`/`recess` Tailwind aliases
    // (replace hardcoded text-white/border-white/bg-white and bg-black wells).
    // Dark uses ivory/warm-ink so `text-fg/NN` stays warm; light inverts to ink.
    '--ng-fg-rgb': '250 249 245', // ivory
    '--ng-recess-rgb': '15 15 14', // warm near-black recessed wells
    '--ng-glass-bg': 'rgba(31,30,29,0.62)',
    '--ng-glass-border': 'rgba(250,249,245,0.10)',
    '--ng-elevate': '0 16px 50px rgba(0,0,0,0.55)', // soft warm-charcoal drop
    // Accent = Anthropic coral #D97757. One token serves both roles: reads as
    // text/link on charcoal (5.3:1 on surface, AA) AND carries warm ink on the
    // coral fill (5.9:1). Channels power `bg-accent/NN` alpha modifiers.
    '--ng-accent-rgb': '217 119 87', // #D97757
    '--ng-accent-soft': '#E08B66', // lighter coral — hover fill
    '--ng-accent-fg': '#141413', // warm ink on the coral fill
    '--ng-primary': semantic.primary,
    '--ng-secondary': semantic.secondary,
    '--ng-success': semantic.success,
    '--ng-warning': semantic.warning,
    '--ng-danger': semantic.danger,
    '--ng-info': semantic.info,
  },
  light: {
    // Full Clay Anthropic (user pick 2026-07-13): warm ivory/cream surfaces,
    // ink text, terracotta accent. Three layers read as layers — cream ground
    // (canvas) < ivory panels < white elevated surfaces.
    '--ng-bg-0': '#EFEDE6', // warm cream canvas ground (darkest layer)
    '--ng-bg-1': '#F3F1EA', // radial-gradient partner, a step lighter
    '--ng-surface': 'rgba(255,255,255,0.92)', // elevated glass — white
    '--ng-surface-2': 'rgba(250,249,245,0.94)',
    '--ng-panel': '#FAF9F5', // ivory docked panels
    '--ng-panel-2': '#F0EEE6', // recessed panel
    '--ng-border': 'rgba(20,20,19,0.12)',
    '--ng-border-strong': 'rgba(20,20,19,0.22)',
    '--ng-fg': '#141413', // Anthropic ink
    '--ng-fg-muted': '#6B6862', // warm grey
    '--ng-fg-subtle': '#928E85',
    '--ng-fg-rgb': '20 20 19', // ink — legible foreground/hairline on clay surfaces
    '--ng-recess-rgb': '41 37 36', // warm brown-black recessed wells
    '--ng-glass-bg': 'rgba(255,255,255,0.82)',
    '--ng-glass-border': 'rgba(20,20,19,0.12)',
    '--ng-elevate': '0 12px 34px rgba(80,60,45,0.14)', // warm claymorphism shadow
    // Deep terracotta (not the bright #D97757 fill) so it doubles as text/link
    // on ivory (5.4:1) and carries white on fills (5.7:1). Brand coral #D97757
    // lives in --ng-accent-soft for decorative/hover fills.
    '--ng-accent-rgb': '168 75 42', // #A84B2A
    '--ng-accent-soft': '#D97757', // Anthropic brand coral — hover / soft fills (pair with ink)
    '--ng-accent-fg': '#FFFFFF', // white on the deep terracotta fill
    '--ng-primary': '#A84B2A',
    '--ng-secondary': '#0E7C88', // teal — AI / planning
    '--ng-success': '#15805C',
    '--ng-warning': '#B45309',
    '--ng-danger': '#C0392B',
    '--ng-info': '#0E7C88',
  },
};

/**
 * Apply a theme mode: toggles Tailwind `dark`/`hc` classes and writes the CSS
 * variable set onto <html>. Idempotent — safe to call on every mount/change.
 */
export function applyTheme(mode: ThemeMode): void {
  const root = document.documentElement;
  root.classList.toggle('dark', mode === 'dark');
  root.dataset.theme = mode;

  const vars = THEMES[mode];
  for (const [key, value] of Object.entries(vars)) {
    root.style.setProperty(key, value);
  }
  root.style.setProperty('color-scheme', mode === 'light' ? 'light' : 'dark');
}
