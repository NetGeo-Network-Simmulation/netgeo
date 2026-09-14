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

type ResizeEdge = 'n' | 's' | 'e' | 'w' | 'ne' | 'nw' | 'se' | 'sw';

interface NetGeoWindowApi {
  minimize(): void;
  toggle_maximize(): void;
  close(): void;
  begin_move(): void;
  begin_resize(edge: ResizeEdge): void;
}

declare global {
  interface Window {
    pywebview?: { api: NetGeoWindowApi; platform: string; token: string };
  }
}

/** True only inside the pywebview native window — never in a plain browser
 * tab. pywebview injects `window.pywebview` before page scripts run, but a
 * `pywebviewready` listener covers the (documented) case where it lands
 * slightly later than React's first paint. */
function useIsNativeShell(): boolean {
  const [isNative, setIsNative] = useState(() => typeof window !== 'undefined' && 'pywebview' in window);
  useEffect(() => {
    if (isNative) return;
    const onReady = () => setIsNative(true);
    window.addEventListener('pywebviewready', onReady);
    return () => window.removeEventListener('pywebviewready', onReady);
  }, [isNative]);
  return isNative;
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
  const [isMaximized, setIsMaximized] = useState(false);

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

  return (
    <>
      <ResizeHandles api={api} />
      <div
        className="relative z-[999] flex h-9 shrink-0 select-none items-center bg-panel text-fg-muted"
        style={{ height: NATIVE_TITLE_BAR_HEIGHT }}
        onMouseDown={onMove}
        onDoubleClick={toggleMaximize}
      >
        <div className="flex items-center gap-1.5 pl-3 text-xs font-medium text-fg-subtle">
          <BrandGlyph />
          <span>NetGeo</span>
        </div>
        <div className="flex-1" />
        <div className="flex h-full items-stretch" onMouseDown={(e) => e.stopPropagation()}>
          <button
            type="button"
            aria-label="Minimize"
            onClick={() => api.minimize()}
            className="flex w-11 items-center justify-center hover:bg-fg/10"
          >
            <Minus size={14} />
          </button>
          <button
            type="button"
            aria-label={isMaximized ? 'Restore' : 'Maximize'}
            onClick={toggleMaximize}
            className="flex w-11 items-center justify-center hover:bg-fg/10"
          >
            <Square size={11} />
          </button>
          <button
            type="button"
            aria-label="Close"
            onClick={() => api.close()}
            className="flex w-11 items-center justify-center hover:bg-danger/15 hover:text-danger"
          >
            <X size={14} />
          </button>
        </div>
      </div>
    </>
  );
}
