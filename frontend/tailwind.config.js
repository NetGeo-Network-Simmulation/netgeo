/**
 * NetGeo — design system Tailwind config.
 * Identity: professional GIS workstation × modern IDE × creative workspace.
 * Two color layers:
 *   - Static brand palette (accent/success/warning/danger/info/node) for
 *     domain components (canvas, badges).
 *   - Theme-aware aliases (surface/panel/fg/primary/…) bound to CSS variables
 *     written by theme/tokens.ts, so Light/Dark/High-Contrast re-skin instantly.
 * @type {import('tailwindcss').Config}
 */
export default {
  darkMode: 'class',
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      fontFamily: {
        // Bundled variable fonts (see main.tsx) with system fallbacks so text
        // stays legible before the woff2 loads. `display` = Geist for headings.
        sans: ['Inter Variable', 'Inter', '-apple-system', 'BlinkMacSystemFont', 'Segoe UI', 'sans-serif'],
        display: ['Geist Variable', 'Inter Variable', '-apple-system', 'BlinkMacSystemFont', 'sans-serif'],
        mono: ['JetBrains Mono Variable', 'JetBrains Mono', 'SF Mono', 'ui-monospace', 'Menlo', 'monospace'],
      },
      colors: {
        // --- Theme-aware semantic aliases (CSS-variable backed) ---------------
        surface: 'var(--ng-surface)',
        'surface-2': 'var(--ng-surface-2)',
        panel: 'var(--ng-panel)',
        'panel-2': 'var(--ng-panel-2)',
        hairline: 'var(--ng-border)',
        'hairline-strong': 'var(--ng-border-strong)',
        // Alpha-capable foreground: `text-fg/NN`, `border-fg/NN`, `bg-fg/NN`
        // replace the old hardcoded `*-white/NN`. White in dark (pixel-identical),
        // ink in light, white in high-contrast — one class, all three themes.
        fg: 'rgb(var(--ng-fg-rgb) / <alpha-value>)',
        'fg-muted': 'var(--ng-fg-muted)',
        'fg-subtle': 'var(--ng-fg-subtle)',
        // Recessed wells/fields: `bg-recess/NN` replaces the old `bg-black/NN`.
        // Black in dark (pixel-identical), slate tint in light.
        recess: 'rgb(var(--ng-recess-rgb) / <alpha-value>)',
        primary: { DEFAULT: 'var(--ng-primary)', fg: 'var(--ng-accent-fg)' },
        secondary: { DEFAULT: 'var(--ng-secondary)', fg: 'var(--ng-accent-fg)' },

        // --- Anthropic clay accent (theme-aware, CSS-variable backed) --------
        // Coral terracotta in both themes: deep #A84B2A in light, #D97757 in dark.
        // Channel form so `bg-accent/NN` alpha modifiers still resolve. All
        // ~45 accent consumers re-skin per theme with no per-file changes.
        accent: {
          DEFAULT: 'rgb(var(--ng-accent-rgb) / <alpha-value>)',
          soft: 'var(--ng-accent-soft)', // hover / soft fill (brand coral)
          // On-accent foreground: ink in dark, white in light. Pair
          // `bg-accent text-accent-fg`.
          fg: 'var(--ng-accent-fg)',
        },
        success: { DEFAULT: '#27C28B', dark: '#15805C' },
        warning: { DEFAULT: '#F5A623', dark: '#9E6300' },
        danger: { DEFAULT: '#FF4D4F', dark: '#A01012' },
        // DEFAULT kept in sync with theme/tokens.ts semantic.info (dark-mode
        // value; brightened 2026-09-11 for WCAG AA text contrast, see there).
        info: { DEFAULT: '#38BBC7', dark: '#0E7C88' },
        ink: {
          DEFAULT: '#101626',
          soft: '#566076',
          muted: '#8A93A6',
        },
        // Printed-document surface (Reports Center preview iframe): a report
        // page is paper, always white — deliberately NOT theme-aware, even in
        // dark mode. Named so it reads as intent, not a stray raw bg-white.
        paper: '#FFFFFF',
        // Node-kind palette (also exported as CSS vars in theme/tokens.ts).
        node: {
          router: '#2F6BFF',
          switch: '#27C28B',
          host: '#8A93A6',
          ap: '#7C5CFC',
          cpe: '#007AFF',  // map-deploy CPE (wireless client)
          olt: '#F5A623',
          firewall: '#FF4D4F',
          server: '#27B5C2',
        },
      },
      // Crisp technical-tool corners (design.md §2.1: 8-10px controls,
      // 10-14px panels; Stitch lg=8 / xl=12). Tightened from the softer
      // 18/24 so cards (rounded-lg) sit at 8px and panels (rounded-xl) at 12px.
      borderRadius: {
        sm: '4px',
        md: '6px',
        lg: '8px',
        xl: '12px',
      },
      backdropBlur: {
        12: '12px',
        20: '20px',
        30: '30px',
      },
      boxShadow: {
        glass: '0 8px 32px rgba(0,0,0,0.18)',
        'glass-lg': '0 20px 60px rgba(0,0,0,0.28)',
        soft: '0 2px 10px rgba(0,0,0,0.08)',
        window: '0 24px 70px rgba(0,0,0,0.32), 0 2px 8px rgba(0,0,0,0.14)',
        dock: '0 12px 40px rgba(0,0,0,0.28)',
      },
      transitionDuration: {
        fast: '120ms',
        std: '180ms',
        slow: '240ms',
      },
      keyframes: {
        'fade-in': {
          from: { opacity: '0', transform: 'translateY(6px)' },
          to: { opacity: '1', transform: 'translateY(0)' },
        },
        'scale-in': {
          from: { opacity: '0', transform: 'scale(0.96)' },
          to: { opacity: '1', transform: 'scale(1)' },
        },
        'slide-up': {
          from: { opacity: '0', transform: 'translateY(12px)' },
          to: { opacity: '1', transform: 'translateY(0)' },
        },
        // Live-simulation heartbeat for the transport play/pause button
        // (Stitch sim-active-pulse). Amber ring pulse, no layout shift.
        'sim-pulse': {
          '0%, 100%': { boxShadow: '0 0 0 0 rgba(245,166,35,0.55)' },
          '50%': { boxShadow: '0 0 0 6px rgba(245,166,35,0)' },
        },
      },
      animation: {
        'fade-in': 'fade-in 180ms ease-out',
        'scale-in': 'scale-in 180ms ease-out',
        'slide-up': 'slide-up 200ms ease-out',
        'sim-pulse': 'sim-pulse 1.4s ease-out infinite',
      },
    },
  },
  plugins: [],
};
