/**
 * NavigationRail — floating device-rail, the app's primary navigation
 * (design §3.2; visual approved in docs/design/stitch-html/clay/
 * shell-device-rail/). 11 destinations collapse into 5 groups + Settings;
 * every non-primary member of a group shows up in TopBar's contextual
 * sub-nav strip (see GROUPS export) when that group is active.
 *
 * RF Planning and Fiber/FTTH are deliberately NOT members here (S8 NAV-02):
 * the map is the single surface the rail/sub-nav exposes, and those two
 * workspaces open contextually instead — auto-open on device deploy
 * (mapDeploy.ts), the command palette (CommandPalette.tsx), or their /rf
 * and /fiber deep links (uiStore.ts VIEW_PATHS, unaffected by this list).
 *
 * Diagnostics is likewise NOT a member (Surya decision, 2026-09-11): it's a
 * panel of the topology workspace, not a navigation destination. Reach it
 * from StatusBar's drawer toggle or the command palette's "Open Diagnostics".
 *
 * Groups (fixed IA, do not redesign): Projects · Design(topology/plant/
 * config) · Map(map) · Simulate(twin/edu/scenarios) ·
 * Operate(problems/reports). Settings stays a separate bottom button.
 */
import {
  FolderKanban,
  Network,
  Map as MapIcon,
  Boxes,
  Server,
  FileCode2,
  Siren,
  FileBarChart2,
  FlaskConical,
  GraduationCap,
  Settings2,
  type LucideIcon,
} from 'lucide-react';
import { useUiStore, type ViewMode } from '@/store/uiStore';
import { cn } from '@/lib/cn';
import { zc } from '@/theme/z';

export type RailMember =
  | { key: string; label: string; icon: LucideIcon; view: ViewMode }
  | { key: string; label: string; icon: LucideIcon; action: 'scenarios' };

export interface RailGroup {
  key: string;
  label: string;
  icon: LucideIcon;
  /** First member is the group's primary — what a rail click navigates to. */
  members: [RailMember, ...RailMember[]];
}

/** Exported so TopBar's contextual sub-nav strip shares this one data model
 *  instead of a second, drifting copy of the IA. */
export const GROUPS: RailGroup[] = [
  {
    key: 'projects',
    label: 'Projects',
    icon: FolderKanban,
    members: [{ key: 'projects', label: 'Projects', icon: FolderKanban, view: 'projects' }],
  },
  {
    key: 'design',
    label: 'Design',
    icon: Network,
    members: [
      { key: 'topology', label: 'Topology', icon: Network, view: 'topology' },
      { key: 'plant', label: 'Physical Plant', icon: Server, view: 'plant' },
      { key: 'config', label: 'Config Center', icon: FileCode2, view: 'config' },
    ],
  },
  {
    key: 'map',
    label: 'Map',
    icon: MapIcon,
    members: [{ key: 'map', label: 'Map', icon: MapIcon, view: 'map' }],
  },
  {
    key: 'simulate',
    label: 'Simulate',
    icon: Boxes,
    members: [
      { key: 'twin', label: 'Digital Twin', icon: Boxes, view: 'twin' },
      { key: 'edu', label: 'Education Lab', icon: GraduationCap, view: 'edu' },
      { key: 'labs', label: 'Labs', icon: FlaskConical, action: 'scenarios' },
    ],
  },
  {
    key: 'operate',
    label: 'Operate',
    icon: Siren,
    members: [
      { key: 'problems', label: 'Problem Center', icon: Siren, view: 'problems' },
      { key: 'reports', label: 'Reports Center', icon: FileBarChart2, view: 'reports' },
    ],
  },
];

/** True if `viewMode` belongs to one of this group's view-typed members. */
export function isGroupActive(group: RailGroup, viewMode: ViewMode): boolean {
  return group.members.some((m) => 'view' in m && m.view === viewMode);
}

/** Navigate to (or trigger) a rail member — shared by the rail and TopBar's
 *  sub-nav strip so the two surfaces can never disagree on what a click does. */
export function activateMember(member: RailMember): void {
  const ui = useUiStore.getState();
  if ('view' in member) {
    ui.setViewMode(member.view);
    return;
  }
  ui.openModal('scenarios');
}

const SETTINGS = { key: 'settings', label: 'Settings', icon: Settings2 };

export function NavigationRail() {
  const viewMode = useUiStore((s) => s.viewMode);
  const activeModal = useUiStore((s) => s.activeModal);

  return (
    // Auto-hide dock (Surya QA 2026-09-11: "dash to dock tanpa animasi" —
    // hidden by default so every workspace gets the full x=0 width, no
    // reserved-space tax anywhere; see AppShell.tsx). Reveals on hover of
    // the hot-zone strip below OR the instant any rail button receives
    // keyboard focus (`group-focus-within`). No easing/duration utility
    // anywhere in this subtree — the opacity/pointer-events flip is a
    // plain class swap, so it's an instant snap, matching the explicit "no
    // animation" request; don't "fix" that by adding easing.
    <div className="group/dock pointer-events-none absolute inset-y-0 left-0 w-[100px]">
      {/* Hot-zone: 24px strip flush with the rail's own left-6 offset (no
          dead gap between "hoverable" and "visible"). Mouse-only trigger —
          keyboard users reveal the rail by tabbing straight into it (see
          `nav` below): its buttons stay in tab order and keyboard-activated
          (Enter/Space) even while invisible, because `pointer-events-none`
          only blocks mouse hit-testing, never focus or synthetic keyboard
          activation. That's also why hiding the rail this way is safe for
          the canvas underneath: a real click at this position falls through
          to the workspace below instead of hitting an invisible button. */}
      <div className="pointer-events-auto absolute inset-y-0 left-0 w-6" aria-hidden />

      <nav
        aria-label="Primary"
        className={cn(
          'rail-chassis pointer-events-none absolute left-6 top-1/2 flex w-[76px] -translate-y-1/2 flex-col items-center gap-1 overflow-hidden rounded-xl border py-4 opacity-0',
          'group-hover/dock:pointer-events-auto group-hover/dock:opacity-100',
          'group-focus-within/dock:pointer-events-auto group-focus-within/dock:opacity-100',
          zc.popover,
        )}
      >
      {/* Metal-grain overlay — decorative, procedural (no raster asset). */}
      <div className="rail-grain pointer-events-none absolute inset-0" aria-hidden />

      {/* Screws */}
      {(['top-2 left-2', 'top-2 right-2', 'bottom-2 left-2', 'bottom-2 right-2'] as const).map((pos) => (
        <span
          key={pos}
          className={cn('absolute h-1.5 w-1.5 rounded-full bg-fg/15 shadow-[inset_0_1px_1px_rgba(0,0,0,0.5)]', pos)}
          aria-hidden
        />
      ))}

      {/* Vents */}
      <div className="relative z-10 mb-5 flex w-8 flex-col gap-1" aria-hidden>
        {Array.from({ length: 5 }).map((_, i) => (
          <div key={i} className="h-0.5 rounded-full bg-recess/80 shadow-[0_1px_0_rgba(255,255,255,0.1)]" />
        ))}
      </div>

      <div className="relative z-10 flex w-full flex-col gap-3 px-2">
        {GROUPS.map((group) => (
          <RailButton
            key={group.key}
            icon={group.icon}
            label={group.label}
            active={isGroupActive(group, viewMode)}
            onClick={() => activateMember(group.members[0])}
          />
        ))}
      </div>

      {/* Divider groove */}
      <div className="relative z-10 my-4 h-px w-[80%] bg-recess/70 shadow-[0_1px_0_rgba(255,255,255,0.05)]" aria-hidden />

      <div className="relative z-10 w-full px-2">
        <RailButton
          icon={SETTINGS.icon}
          label={SETTINGS.label}
          active={activeModal === 'settings'}
          onClick={() => useUiStore.getState().openModal('settings')}
        />
      </div>

      {/* Engraved nameplate */}
      <div className="relative z-10 mt-3 flex flex-col items-center text-fg-subtle opacity-70">
        <span className="text-[9px] font-bold tracking-widest">NETGEO</span>
        <span className="font-mono text-[8px]">NG-5X</span>
      </div>
      </nav>
    </div>
  );
}

function RailButton({
  icon: Icon,
  label,
  active,
  onClick,
}: {
  icon: LucideIcon;
  label: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      aria-label={label}
      aria-current={active ? 'page' : undefined}
      title={label}
      className={cn(
        'rail-socket group relative grid h-[52px] w-full place-items-center rounded-md transition-colors',
        active
          ? 'border border-accent/20 text-accent shadow-[inset_0_0_15px_rgb(var(--ng-accent-rgb)_/_0.15)]'
          : 'text-fg-muted hover:text-fg hover:shadow-[inset_0_0_12px_rgb(var(--ng-fg-rgb)_/_0.1)]',
      )}
    >
      {active && (
        <>
          <span
            className="absolute -right-1 top-1/2 h-1.5 w-1.5 -translate-y-1/2 rounded-full bg-accent shadow-[0_0_6px_rgb(var(--ng-accent-rgb)_/_0.7)]"
            aria-hidden
          />
          <span className="absolute inset-y-1 left-0 w-0.5 rounded-r-sm bg-accent" aria-hidden />
        </>
      )}
      <Icon className="h-5 w-5" />
    </button>
  );
}
