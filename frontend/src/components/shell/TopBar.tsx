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
import { GROUPS, isGroupActive, activateMember, type RailMember } from '@/components/shell/NavigationRail';
import { cn } from '@/lib/cn';

interface TopBarProps {
  projectName: string;
  conn: ConnState;
}

/** NetGeo topology mark (logo.png): a hub node linked to three satellites,
 * in the primary accent. Inline SVG so it inherits theme + scales crisply. */
function NetGeoMark() {
  return (
    <span className="grid h-7 w-7 shrink-0 place-items-center rounded-lg bg-accent/15 ring-1 ring-inset ring-accent/30">
      <svg viewBox="0 0 24 24" className="h-[18px] w-[18px] text-accent" fill="none" aria-hidden>
        <g stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" opacity="0.9">
          <line x1="12" y1="12" x2="12" y2="4.5" />
          <line x1="12" y1="12" x2="5.5" y2="17.5" />
          <line x1="12" y1="12" x2="18.5" y2="17.5" />
        </g>
        <g fill="currentColor">
          <circle cx="12" cy="4.5" r="2.1" />
          <circle cx="5.5" cy="17.5" r="2.1" />
          <circle cx="18.5" cy="17.5" r="2.1" />
        </g>
        <circle cx="12" cy="12" r="2.8" fill="currentColor" />
      </svg>
    </span>
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
  const drawerOpen = useUiStore((s) => s.drawerOpen);
  const drawerTab = useUiStore((s) => s.drawerTab);

  const group = GROUPS.find((g) => isGroupActive(g, viewMode));
  if (!group || group.members.length < 2) return null;

  const isMemberActive = (m: RailMember): boolean => {
    if ('view' in m) return viewMode === m.view;
    if (m.action === 'scenarios') return activeModal === 'scenarios';
    return drawerOpen && drawerTab === 'diagnostics';
  };

  return (
    <div
      role="tablist"
      aria-label={`${group.label} sections`}
      className="flex items-center gap-0.5 rounded-lg border border-fg/10 bg-fg/5 p-0.5"
    >
      {group.members.map((m) => {
        const active = isMemberActive(m);
        const Icon = m.icon;
        return (
          <button
            key={m.key}
            role="tab"
            aria-selected={active}
            onClick={() => activateMember(m)}
            title={m.label}
            className={cn(
              'flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs font-medium transition-colors',
              active ? 'bg-accent/20 text-accent' : 'text-fg/55 hover:bg-fg/8 hover:text-fg/90',
            )}
          >
            <Icon className="h-3.5 w-3.5" />
            <span className="hidden sm:inline">{m.label}</span>
          </button>
        );
      })}
    </div>
  );
}

export function TopBar({ projectName, conn }: TopBarProps) {
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
    <header className="glass-strong flex h-14 shrink-0 items-center gap-3 border-b border-fg/10 px-3 text-[13px] text-fg/85">
      {/* Brand + project + saved state */}
      <div className="flex items-center gap-2 font-semibold">
        <NetGeoMark />
        <span className="hidden font-display text-sm tracking-tight sm:inline">NetGeo</span>
      </div>
      <span className="text-fg/25">/</span>
      <span className="max-w-[160px] truncate text-fg/70">{projectName}</span>
      <span
        className={cn(
          'hidden items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] md:inline-flex',
          dirty ? 'text-warning' : 'text-success',
        )}
        title={dirty ? 'Unsaved local changes' : 'All changes saved'}
      >
        {dirty ? <span className="h-1.5 w-1.5 rounded-full bg-warning" /> : <Check className="h-3.5 w-3.5" />}
        {dirty ? 'Unsaved' : 'Saved'}
      </span>

      <SubNavStrip />

      {/* Command bar (center) */}
      <div className="mx-2 flex flex-1 justify-center">
        <button
          onClick={() => openModal('command')}
          className="flex w-full max-w-xl items-center gap-2 rounded-lg border border-fg/10 bg-fg/5 px-3 py-2 text-left text-xs text-fg/40 transition-colors hover:border-fg/20 hover:bg-fg/8"
          aria-label="Open command palette"
        >
          <Search className="h-3.5 w-3.5 shrink-0" />
          <span className="flex-1 truncate">Search devices, IPs, or run a command…</span>
          <kbd className="hidden shrink-0 rounded border border-fg/15 px-1.5 py-0.5 font-mono text-[10px] sm:inline">⌘K</kbd>
        </button>
      </div>

      {/* Right cluster */}
      <SimulationBar />
      <ModeSwitch />

      <div className="flex items-center gap-1.5">
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

        <UpdatesButton />

        <button
          onClick={toggleTheme}
          aria-label="Toggle theme"
          className="grid h-8 w-8 place-items-center rounded-md text-fg/60 hover:bg-fg/10 hover:text-fg"
        >
          {theme === 'dark' ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
        </button>

        <time className="hidden tabular-nums text-fg/50 lg:inline">
          {clock.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
        </time>

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
    </header>
  );
}
