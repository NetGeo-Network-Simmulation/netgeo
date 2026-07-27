/**
 * BottomDrawer — the one shared bottom drawer (design 12-UI §2.1, design §14).
 * Replaces the old N floating Console/Diagnostics/Ledger/Config windows with a
 * single collapsible tray: tabs Console · Diagnostics · Event Ledger · Config,
 * each body the existing panel rendered 1:1. Node-scoped tabs (Console, Config)
 * follow the current topology selection.
 *
 * Full-width over the workspace, anchored above the StatusBar. Resizable by the
 * top drag handle; height persists for the session (uiStore). AppShell mounts it
 * only in topology/map — other workspaces own their bottom edge.
 */
import { useCallback, useRef } from 'react';
import { Activity, FileCode2, ListVideo, TerminalSquare, X, type LucideIcon } from 'lucide-react';
import { useUiStore, type DrawerTab } from '@/store/uiStore';
import { ConsolePanel } from '@/components/ConsolePanel';
import { DiagnosticsPanel } from '@/components/DiagnosticsPanel';
import { EventLedgerPanel } from '@/components/EventLedgerPanel';
import { ConfigViewer } from '@/components/ConfigViewer';
import { cn } from '@/lib/cn';
import { zc } from '@/theme/z';

const TABS: { id: DrawerTab; label: string; icon: LucideIcon }[] = [
  { id: 'console', label: 'Console', icon: TerminalSquare },
  { id: 'diagnostics', label: 'Diagnostics', icon: Activity },
  { id: 'ledger', label: 'Event Ledger', icon: ListVideo },
  { id: 'config', label: 'Config', icon: FileCode2 },
];

export function BottomDrawer() {
  const open = useUiStore((s) => s.drawerOpen);
  const tab = useUiStore((s) => s.drawerTab);
  const height = useUiStore((s) => s.drawerHeight);
  const setDrawerOpen = useUiStore((s) => s.setDrawerOpen);
  const setDrawerTab = useUiStore((s) => s.setDrawerTab);
  const setDrawerHeight = useUiStore((s) => s.setDrawerHeight);
  const dragRef = useRef<number | null>(null);

  // Drag the top edge to resize — pointer capture keeps it smooth over the canvas.
  const onHandleDown = useCallback(
    (e: React.PointerEvent) => {
      (e.target as HTMLElement).setPointerCapture(e.pointerId);
      dragRef.current = e.clientY;
    },
    [],
  );
  const onHandleMove = useCallback(
    (e: React.PointerEvent) => {
      if (dragRef.current === null) return;
      const dy = dragRef.current - e.clientY;
      dragRef.current = e.clientY;
      setDrawerHeight(useUiStore.getState().drawerHeight + dy);
    },
    [setDrawerHeight],
  );
  const onHandleUp = useCallback(() => {
    dragRef.current = null;
  }, []);

  if (!open) return null;

  return (
    <section
      aria-label="Diagnostics drawer"
      className={cn(
        'glass-strong pointer-events-auto absolute inset-x-0 bottom-0 flex flex-col border-t border-fg/15 shadow-glass-lg',
        zc.drawer,
      )}
      style={{ height }}
    >
      {/* resize handle */}
      <div
        onPointerDown={onHandleDown}
        onPointerMove={onHandleMove}
        onPointerUp={onHandleUp}
        onPointerCancel={onHandleUp}
        className="group absolute inset-x-0 -top-1 h-2 cursor-ns-resize"
        role="separator"
        aria-orientation="horizontal"
        aria-label="Resize drawer"
      >
        <span className="mx-auto mt-0.5 block h-0.5 w-10 rounded-full bg-fg/20 group-hover:bg-fg/40" />
      </div>

      {/* tab strip */}
      <div className="flex shrink-0 items-center gap-1 border-b border-fg/10 px-2 py-1.5">
        {TABS.map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            onClick={() => setDrawerTab(id)}
            aria-pressed={tab === id}
            className={cn(
              'flex items-center gap-1.5 rounded-md px-2.5 py-1 text-xs transition-colors',
              tab === id ? 'bg-accent/20 text-accent' : 'text-fg/55 hover:bg-fg/8 hover:text-fg/85',
            )}
          >
            <Icon className="h-3.5 w-3.5" />
            <span className="hidden sm:inline">{label}</span>
          </button>
        ))}
        <div className="flex-1" />
        <button
          onClick={() => setDrawerOpen(false)}
          aria-label="Close drawer"
          className="grid h-6 w-6 place-items-center rounded text-fg/50 hover:bg-fg/10 hover:text-fg"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      {/* body — one panel per tab, rendered 1:1 */}
      <div className="min-h-0 flex-1 overflow-hidden">
        {tab === 'console' && <ConsolePanel />}
        {tab === 'diagnostics' && <DiagnosticsPanel />}
        {tab === 'ledger' && <EventLedgerPanel />}
        {tab === 'config' && <ConfigViewer />}
      </div>
    </section>
  );
}
