/**
 * NavigationRail — floating device-rail, the app's primary navigation
 * (design §3.2; visual approved in docs/design/stitch-html/clay/
 * shell-device-rail/). 13 destinations collapse into 5 groups + Settings;
 * every destination the rail used to expose 1:1 is still reachable — the
 * non-primary members of a group show up in TopBar's contextual sub-nav
 * strip (see GROUPS export) when that group is active, so nothing here was
 * deleted, only regrouped.
 *
 * Groups (fixed IA, do not redesign): Projects · Design(topology/plant/
 * config) · Map(map/rf/fiber) · Simulate(twin/edu/scenarios/diagnostics) ·
 * Operate(problems/reports). Settings stays a separate bottom button.
 */
import {
  FolderKanban,
  Network,
  Map as MapIcon,
  Boxes,
  RadioTower,
  Cable,
  Server,
  FileCode2,
  Siren,
  FileBarChart2,
  FlaskConical,
  GraduationCap,
  Activity,
  Settings2,
  type LucideIcon,
} from 'lucide-react';
import { useUiStore, type ViewMode } from '@/store/uiStore';
import { cn } from '@/lib/cn';
import { zc } from '@/theme/z';

export type RailMember =
  | { key: string; label: string; icon: LucideIcon; view: ViewMode }
  | { key: string; label: string; icon: LucideIcon; action: 'scenarios' | 'diagnostics' };

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
    members: [
      { key: 'map', label: 'Map', icon: MapIcon, view: 'map' },
      { key: 'rf', label: 'RF Planning', icon: RadioTower, view: 'rf' },
      { key: 'fiber', label: 'Fiber / FTTH', icon: Cable, view: 'fiber' },
    ],
  },
  {
    key: 'simulate',
    label: 'Simulate',
    icon: Boxes,
    members: [
      { key: 'twin', label: 'Digital Twin', icon: Boxes, view: 'twin' },
      { key: 'edu', label: 'Education Lab', icon: GraduationCap, view: 'edu' },
      { key: 'labs', label: 'Labs', icon: FlaskConical, action: 'scenarios' },
      { key: 'diag', label: 'Diagnostics', icon: Activity, action: 'diagnostics' },
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
  if (member.action === 'scenarios') {
    ui.openModal('scenarios');
  } else {
    // Diagnostics is a drawer tab (topology/map only) — hop to topology if the
    // current workspace can't host the drawer, then open it.
    if (ui.viewMode !== 'topology' && ui.viewMode !== 'map') ui.setViewMode('topology');
    ui.openDrawer('diagnostics');
  }
}

const SETTINGS = { key: 'settings', label: 'Settings', icon: Settings2 };

export function NavigationRail() {
  const viewMode = useUiStore((s) => s.viewMode);
  const activeModal = useUiStore((s) => s.activeModal);

  return (
    <nav
      aria-label="Primary"
      className={cn(
        'rail-chassis pointer-events-auto absolute left-6 top-6 flex w-[76px] flex-col items-center gap-1 overflow-hidden rounded-xl border py-4',
        zc.workspace,
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
