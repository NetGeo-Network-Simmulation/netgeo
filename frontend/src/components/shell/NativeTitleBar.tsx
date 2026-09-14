/**
 * NativeTitleBar — the app's own window chrome, shown ONLY inside the
 * frameless native window (packaging/launcher.py's `_try_webview`, Surya
 * 2026-09-14: "tetap frameless+title bar sendiri biar bisa full rounded").
 * In a plain browser tab (distribution variants #4/#5) this renders nothing
 * at all — `window.pywebview` only exists inside the pywebview runtime, so
 * the browser gets zero extra DOM and zero layout shift.
 *
 * pywebview 6.2.1 has no working drag/resize for a frameless window on
 * Wayland (see packaging/launcher.py's `_WindowBridge` docstring — its own
 * move()/easy_drag are no-ops or too broad), so this bar talks to the
 * `_WindowBridge` js_api instead of the CSS `pywebview-drag-region` class:
 * mousedown on the title strip calls `begin_move()`, and four invisible
 * edge/corner strips call `begin_resize(edge)`.
 */
import { useEffect, useState } from 'react';
import { Minus, Square, X } from 'lucide-react';
import { cn } from '@/lib/cn';
import { useIsNativeShell, useIsMaximized } from '@/hooks/useNativeShell';

type ResizeEdge = 'n' | 's' | 'e' | 'w' | 'ne' | 'nw' | 'se' | 'sw';
type ButtonKind = 'close' | 'minimize' | 'maximize';
interface ButtonLayout {
  side: 'left' | 'right';
  order: ButtonKind[];
}
const DEFAULT_BUTTON_LAYOUT: ButtonLayout = { side: 'right', order: ['minimize', 'maximize', 'close'] };

interface NetGeoWindowApi {
  minimize(): void;
  toggle_maximize(): void;
  close(): void;
  begin_move(): void;
  begin_resize(edge: ResizeEdge): void;
  button_layout(): Promise<ButtonLayout>;
}

declare global {
  interface Window {
    pywebview?: { api: NetGeoWindowApi; platform: string; token: string };
  }
}

/** 24×24 "mirrored node" mark — brand set 8a, `netgeo-icon.svg` — inlined so
 * it inherits `currentColor` instead of shipping a second asset request. */
function BrandGlyph() {
  return (
    <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor" fillRule="evenodd" aria-hidden>
      <path d="M8.8 8.2 A3.2 3.2 0 1 0 15.2 8.2 A3.2 3.2 0 1 0 8.8 8.2 Z M8.8 15.8 A3.2 3.2 0 1 0 15.2 15.8 A3.2 3.2 0 1 0 8.8 15.8 Z M4.6 3.4 A1.8 1.8 0 1 0 8.2 3.4 A1.8 1.8 0 1 0 4.6 3.4 Z M15.8 3.4 A1.8 1.8 0 1 0 19.4 3.4 A1.8 1.8 0 1 0 15.8 3.4 Z M4.6 20.6 A1.8 1.8 0 1 0 8.2 20.6 A1.8 1.8 0 1 0 4.6 20.6 Z M15.8 20.6 A1.8 1.8 0 1 0 19.4 20.6 A1.8 1.8 0 1 0 15.8 20.6 Z M2.6 10.9 H7.2 V13.1 H2.6 Z M16.8 10.9 H21.4 V13.1 H16.8 Z" />
    </svg>
  );
}

/** Invisible strips along the four edges/corners — the only way to resize a
 * frameless pywebview window (see file header). Rendered above everything,
 * including modals, since resizing is a window-level action. */
function ResizeHandles({ api }: { api: NetGeoWindowApi }) {
  const grab = (edge: ResizeEdge) => (e: React.MouseEvent) => {
    if (e.button !== 0) return;
    api.begin_resize(edge);
  };
  const edge = 'fixed z-[999]';
  return (
    <>
      <div className={cn(edge, 'inset-x-0 top-0 h-1 cursor-n-resize')} onMouseDown={grab('n')} />
      <div className={cn(edge, 'inset-x-0 bottom-0 h-1 cursor-s-resize')} onMouseDown={grab('s')} />
      <div className={cn(edge, 'inset-y-0 left-0 w-1 cursor-w-resize')} onMouseDown={grab('w')} />
      <div className={cn(edge, 'inset-y-0 right-0 w-1 cursor-e-resize')} onMouseDown={grab('e')} />
      <div className={cn(edge, 'left-0 top-0 h-2.5 w-2.5 cursor-nw-resize')} onMouseDown={grab('nw')} />
      <div className={cn(edge, 'right-0 top-0 h-2.5 w-2.5 cursor-ne-resize')} onMouseDown={grab('ne')} />
      <div className={cn(edge, 'bottom-0 left-0 h-2.5 w-2.5 cursor-sw-resize')} onMouseDown={grab('sw')} />
      <div className={cn(edge, 'bottom-0 right-0 h-2.5 w-2.5 cursor-se-resize')} onMouseDown={grab('se')} />
    </>
  );
}

export const NATIVE_TITLE_BAR_HEIGHT = 36;

export function NativeTitleBar() {
  const isNative = useIsNativeShell();
  const [isMaximized, setIsMaximized] = useIsMaximized();
  // Fetched once from packaging/launcher.py's button_layout() bridge method
  // (reads GNOME's button-layout gsetting) — starts at the same right-side
  // default the buttons always had, so there's no flash for non-GNOME
  // desktops or before the async call resolves.
  const [layout, setLayout] = useState<ButtonLayout>(DEFAULT_BUTTON_LAYOUT);

  useEffect(() => {
    if (!isNative || !window.pywebview) return;
    window.pywebview.api
      .button_layout()
      .then(setLayout)
      .catch(() => {}); // ponytail: keep the default on any failure
  }, [isNative]);

  if (!isNative || !window.pywebview) return null;
  const api = window.pywebview.api;

  const onMove = (e: React.MouseEvent) => {
    if (e.button !== 0 || e.detail > 1) return; // left-click only; double-click falls through to toggleMaximize
    api.begin_move();
  };
  const toggleMaximize = () => {
    api.toggle_maximize();
    setIsMaximized((v) => !v);
  };

  const buttonProps: Record<ButtonKind, { label: string; icon: React.ReactNode; onClick: () => void; className: string }> = {
    minimize: {
      label: 'Minimize',
      icon: <Minus size={14} />,
      onClick: () => api.minimize(),
      className: 'hover:bg-fg/10',
    },
    maximize: {
      label: isMaximized ? 'Restore' : 'Maximize',
      icon: <Square size={11} />,
      onClick: toggleMaximize,
      className: 'hover:bg-fg/10',
    },
    close: {
      label: 'Close',
      icon: <X size={14} />,
      onClick: () => api.close(),
      className: 'hover:bg-danger/15 hover:text-danger',
    },
  };

  const buttons = (
    <div className="flex h-full items-stretch" onMouseDown={(e) => e.stopPropagation()}>
      {layout.order.map((kind) => {
        const b = buttonProps[kind];
        return (
          <button
            key={kind}
            type="button"
            aria-label={b.label}
            onClick={b.onClick}
            className={cn('flex w-11 items-center justify-center', b.className)}
          >
            {b.icon}
          </button>
        );
      })}
    </div>
  );

  return (
    <>
      <ResizeHandles api={api} />
      <div
        className="relative z-[999] flex h-9 shrink-0 select-none items-center bg-panel text-fg-muted"
        style={{ height: NATIVE_TITLE_BAR_HEIGHT }}
        onMouseDown={onMove}
        onDoubleClick={toggleMaximize}
      >
        {layout.side === 'left' && buttons}
        <div className="flex items-center gap-1.5 pl-3 text-xs font-medium text-fg-subtle">
          <BrandGlyph />
          <span>NetGeo</span>
        </div>
        <div className="flex-1" />
        {layout.side === 'right' && buttons}
      </div>
    </>
  );
}
