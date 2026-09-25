/**
 * TopBar — compact (~56px) global bar (design §3.1). Left: brand + project +
 * saved state. Center: the command bar (opens the ⌘K palette). Right: the
 * simulation transport (Run), lab-mode switch, auto-address, presence,
 * connection, updates (bell), theme, settings, clock and the user menu.
 *
 * This is the AppShell's top chrome; module navigation lives in NavigationRail,
 * not here. It reuses the existing SimulationBar / ModeSwitch / PresenceBar /
 * UpdatesButton so no simulation or collaboration behaviour is duplicated.
 */
import { useEffect, useRef, useState } from 'react';
import { Check, LogOut, Moon, Search, Sun, Wand2, Wifi, WifiOff } from 'lucide-react';
import type { ConnState } from '@/api/ws';
import { useUiStore } from '@/store/uiStore';
import { useAuthStore } from '@/store/authStore';
import { useTopologyStore } from '@/store/topologyStore';
import { zc } from '@/theme/z';
import { SimulationBar } from '@/components/SimulationBar';
import { ModeSwitch } from '@/components/ModeSwitch';
import { UpdatesButton } from '@/components/shell/UpdatesButton';
import { PresenceBar } from '@/components/shell/PresenceBar';
import { HScrollToolbar } from '@/components/shell/HScrollToolbar';
import { GROUPS, isGroupActive, activateMember, type RailMember } from '@/components/shell/NavigationRail';
import { useWindowChrome, WindowButtons } from '@/components/shell/NativeTitleBar';
import { cn } from '@/lib/cn';

/** Stops a mousedown from bubbling to the header's native-chrome drag
 * handler — the "no-drag on interactive elements" half of the drag contract
 * (see NativeTitleBar.tsx file header). No-op outside the native shell. */
const noDrag = (e: React.MouseEvent) => e.stopPropagation();

interface TopBarProps {
  projectName: string;
  conn: ConnState;
}

/** Current product mark, shared with the installed app and favicon. */
function NetGeoMark() {
  return (
    <img src="/netgeo.svg" alt="" className="h-7 w-7 shrink-0 rounded-lg" />
  );
}

/**
 * SubNavStrip — reaches the non-primary members of the active rail group
 * (design decision NG-SHELL-01: the rail collapsed 13 items into 5 groups;
 * this is the cheapest surface to keep every one of them reachable — a
 * small segmented control in the one header that's already mounted for
 * every workspace, vs. a new anchored-popover primitive on the rail).
 * Hidden entirely when the active group has only one member (Projects).
 */
function SubNavStrip() {
  const viewMode = useUiStore((s) => s.viewMode);
  const activeModal = useUiStore((s) => s.activeModal);

  const group = GROUPS.find((g) => isGroupActive(g, viewMode));
  if (!group || group.members.length < 2) return null;

  const isMemberActive = (m: RailMember): boolean => ('view' in m ? viewMode === m.view : activeModal === 'scenarios');

  return (
    <div
      role="tablist"
      aria-label={`${group.label} sections`}
      className="flex shrink-0 items-center gap-0.5 rounded-lg border border-fg/10 bg-fg/5 p-0.5"
    >
      {group.members.map((m) => {
        const active = isMemberActive(m);
        const Icon = m.icon;
        return (
          <button
            key={m.key}
            role="tab"
            aria-selected={active}
            aria-label={m.label}
            onClick={() => activateMember(m)}
            title={m.label}
            className={cn(
              'flex items-center gap-1.5 whitespace-nowrap rounded-md px-2.5 py-1.5 text-xs font-medium transition-colors',
              active ? 'bg-accent/20 text-accent' : 'text-fg/55 hover:bg-fg/8 hover:text-fg/90',
            )}
          >
            <Icon className="h-3.5 w-3.5 shrink-0" />
            {/* Icon-only below 1024px (leader review 2026-09-20: labels were
                wrapping onto 2 lines at 1280/1440 because this span had no
                whitespace-nowrap and the tablist could shrink below its
                content width) — the accessible name survives via
                aria-label/title above regardless of whether this renders. */}
            <span className="hidden whitespace-nowrap 2xl:inline">{m.label}</span>
          </button>
        );
      })}
    </div>
  );
}

export function TopBar({ projectName, conn }: TopBarProps) {
  const chrome = useWindowChrome();
  const theme = useUiStore((s) => s.theme);
  const toggleTheme = useUiStore((s) => s.toggleTheme);
  const openModal = useUiStore((s) => s.openModal);
  const closeModal = useUiStore((s) => s.closeModal);
  // Shared with the command palette and UpdatesButton (uiStore.activeModal) so
  // opening one closes the others, and Escape-to-close is the global handler
  // in useShortcuts — no per-popover Escape listener needed.
  const userMenuOpen = useUiStore((s) => s.activeModal === 'userMenu');
  const projectId = useUiStore((s) => s.projectId);
  const dirty = useTopologyStore((s) => s.dirty);
  const username = useAuthStore((s) => s.username);
  const logout = useAuthStore((s) => s.logout);
  const [clock, setClock] = useState(() => new Date());
  const userMenuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const t = setInterval(() => setClock(new Date()), 30_000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    if (!userMenuOpen) return;
    const handler = (e: MouseEvent) => {
      if (userMenuRef.current && !userMenuRef.current.contains(e.target as Node)) closeModal();
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [userMenuOpen, closeModal]);

  const online = conn === 'open';

  return (
    <header
      // In the native shell this row IS the window's top edge (Surya QA
      // 2026-09-20: window buttons used to live in their own 36px strip
      // ABOVE this header — a visible seam). onMouseDown/onDoubleClick make
      // the header itself a drag/double-click-to-maximize surface; the two
      // content blocks below opt out via `noDrag` (stopPropagation) so
      // clicking any real control never starts a window move, same
      // convention NativeTitleBar's own buttons always used.
      className={cn(
        'glass-strong flex h-14 shrink-0 items-center gap-3 border-b border-fg/10 px-3 text-[13px] text-fg/85',
        chrome.isNative && 'select-none',
      )}
      onMouseDown={chrome.isNative ? chrome.onMove : undefined}
      onDoubleClick={chrome.isNative ? chrome.toggleMaximize : undefined}
    >
      {chrome.isNative && chrome.layout.side === 'left' && (
        <WindowButtons api={chrome.api} layout={chrome.layout} isMaximized={chrome.isMaximized} toggleMaximize={chrome.toggleMaximize} />
      )}

      {/* Brand — the one thing that stays pinned even when the rest of the
          bar scrolls (HScrollToolbar below); everything else is either
          per-project or a control, this is the app's own identity. Left
          undecorated by `noDrag`: it's the header's main empty-space drag
          handle in the native shell. */}
      <div className="flex shrink-0 items-center gap-2 font-semibold">
        <NetGeoMark />
        <span className="hidden font-display text-sm tracking-tight sm:inline">NetGeo</span>
        <span className="text-fg/25">/</span>
        <span className="max-w-20 truncate text-xs font-normal text-fg/70 2xl:max-w-[160px]">{projectName}</span>
        <span className={cn('hidden items-center gap-1 text-[11px] 2xl:inline-flex', dirty ? 'text-warning' : 'text-success')} title={dirty ? 'Unsaved local changes' : 'All changes saved'}>
          {dirty ? <span className="h-1.5 w-1.5 rounded-full bg-warning" /> : <Check className="h-3.5 w-3.5" />}
          {dirty ? 'Unsaved' : 'Saved'}
        </span>
      </div>

      {/* Everything else: never wraps or clips (Surya 2026-09-18 — WPS-
          ribbon pattern). At comfortable widths this never visibly scrolls;
          below it, it scrolls instead of the controls disappearing off the
          right edge. */}
      <HScrollToolbar className="min-w-0 flex-1 gap-2" onMouseDown={chrome.isNative ? noDrag : undefined}>
        <SubNavStrip />
      </HScrollToolbar>

      <div className="flex min-w-0 shrink-0 items-center gap-1.5 whitespace-nowrap" onMouseDown={chrome.isNative ? noDrag : undefined}>

        {/* The most elastic item in the row (leader review 2026-09-20): was
            a fixed w-72/shrink-0, so every OTHER item had to fit around it
            unchanged — the tabs took the overflow instead, wrapping. Now
            shrinks first, down to a legible floor, before anything else
            gives up width; xl+ gets its old full size back. */}
        <button
          onClick={() => openModal('command')}
          className="flex h-8 w-8 items-center justify-center rounded-lg border border-fg/10 bg-fg/5 text-left text-xs text-fg/40 transition-colors hover:border-fg/20 hover:bg-fg/8 xl:w-28 xl:justify-start xl:gap-2 xl:px-3 2xl:w-64"
          aria-label="Open command palette"
        >
          <Search className="h-3.5 w-3.5 shrink-0" />
          <span className="hidden flex-1 truncate xl:inline">Search…</span>
          <kbd className="hidden shrink-0 rounded border border-fg/15 px-1.5 py-0.5 font-mono text-[10px] 2xl:inline">⌘K</kbd>
        </button>

        <SimulationBar />
        <ModeSwitch />

        <div className="flex shrink-0 items-center gap-1.5">
          <button
            onClick={() => projectId && openModal('addressingWizard')}
            disabled={!projectId}
            aria-label="Open the auto-addressing wizard"
            title="Auto-address: preview and apply a dual-stack IP plan"
            className={cn(
              'grid h-8 w-8 place-items-center rounded-md transition-colors',
              'text-fg/60 hover:bg-fg/10 hover:text-fg disabled:opacity-40',
            )}
          >
            <Wand2 className="h-4 w-4" />
          </button>

          <PresenceBar />

          <div
            className={cn('flex items-center gap-1 rounded-md px-1.5 py-0.5 text-xs', online ? 'text-success' : 'text-warning')}
            title={`Realtime channel: ${conn}`}
          >
            {online ? <Wifi className="h-3.5 w-3.5" /> : <WifiOff className="h-3.5 w-3.5" />}
            <span className="hidden lg:inline">{conn}</span>
          </div>

          <button
            onClick={toggleTheme}
            aria-label="Toggle theme"
            className="grid h-8 w-8 place-items-center rounded-md text-fg/60 hover:bg-fg/10 hover:text-fg"
          >
            {theme === 'dark' ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
          </button>

          {/* Lowest-value item in the cluster (Surya QA 2026-09-20 collapse
              order): goes first, at xl, well before the status chip's text
              (lg) — so between 1100–1280 the clock is what gives way. */}
          <time className="hidden tabular-nums text-fg/50 xl:inline">
            {clock.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
          </time>
        </div>
      </div>

      {/* Pinned, not scrolled: both drop their own popover below the header,
          which an `overflow-x-auto` ancestor (HScrollToolbar) would clip
          vertically too (an overflow-x other than visible forces the
          computed overflow-y to auto as well — CSS2.1 §11.1.1) — same
          reason the brand stays pinned on the other end. */}
      <div className="flex shrink-0 items-center gap-1.5" onMouseDown={chrome.isNative ? noDrag : undefined}>
        <UpdatesButton />

        <div className="relative" ref={userMenuRef}>
          <button
            onClick={() => (userMenuOpen ? closeModal() : openModal('userMenu'))}
            aria-label="User menu"
            className="grid h-8 w-8 place-items-center rounded-full bg-accent/25 text-xs font-semibold text-accent transition-colors hover:bg-accent/40"
          >
            {username?.[0]?.toUpperCase() ?? '?'}
          </button>

          {userMenuOpen && (
            <div className={cn('glass-strong absolute right-0 top-10 min-w-[160px] overflow-hidden rounded-lg border border-fg/15 shadow-glass-lg animate-fade-in', zc.popover)}>
              <div className="border-b border-fg/10 px-3 py-2">
                <p className="text-xs font-medium text-fg/80">{username}</p>
                <p className="text-[10px] text-fg/40">Local account</p>
              </div>
              <button
                onClick={() => {
                  closeModal();
                  logout();
                }}
                className="flex w-full items-center gap-2 px-3 py-2 text-xs text-danger/80 hover:bg-danger/10 hover:text-danger"
              >
                <LogOut className="h-3.5 w-3.5" /> Sign out
              </button>
            </div>
          )}
        </div>
      </div>

      {chrome.isNative && chrome.layout.side === 'right' && (
        <WindowButtons api={chrome.api} layout={chrome.layout} isMaximized={chrome.isMaximized} toggleMaximize={chrome.toggleMaximize} />
      )}
    </header>
  );
}
