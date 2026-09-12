/**
 * AppShell — workspace frame (design §3, §27; rebuild 12-UI §2).
 * Composition: TopBar (56px) · NavigationRail (64px) · workspace · StatusBar.
 * The only cross-mode surfaces are TopBar, rail, StatusBar, ModalLayer and
 * toasts; the BottomDrawer is gated to topology/map and SimulationDock to
 * topology + a running sim. The legacy floating-window shell is gone: secondary
 * tools live in the shared BottomDrawer, and Settings/Scenarios are modals.
 */
import { lazy, Suspense } from 'react';
import { Loader2 } from 'lucide-react';
import type { ConnState } from '@/api/ws';
import { useUiStore } from '@/store/uiStore';
import { useLabStore } from '@/store/labStore';
import { useShortcuts } from '@/hooks/useShortcuts';
import { cn } from '@/lib/cn';
import { RAIL_INSET } from '@/theme/shell';
import { TopBar } from './TopBar';
import { NavigationRail } from './NavigationRail';
import { StatusBar } from './StatusBar';
import { BottomDrawer } from './BottomDrawer';
import { ModalLayer } from './ModalLayer';
import { TopologyCanvas } from '@/components/canvas/TopologyCanvas';
import { TopologyToolbar } from '@/components/topology/TopologyToolbar';
import { ContextInspector } from '@/components/topology/ContextInspector';
import { DevicePicker } from '@/components/topology/DevicePicker';
import { CommandPalette } from '@/components/CommandPalette';
import { SimulationDock } from '@/components/SimulationDock';
import { TwinWorkspace } from '@/components/twin/TwinWorkspace';
import { FiberWorkspace } from '@/components/fiber/FiberWorkspace';
import { PlantWorkspace } from '@/components/plant/PlantWorkspace';

// Education Lab is a self-contained workspace (author editor + student runner);
// lazy so its bundle stays out of the initial load until the module is opened.
const EduWorkspace = lazy(() =>
  import('@/components/edu/EduWorkspace').then((m) => ({ default: m.EduWorkspace })),
);

// Map + RF workspaces both pull in maplibre-gl (WebGL globe engine, ~200kB+
// gzipped) — lazy so that cost is only paid when a map view actually opens,
// not on every route including login (gate: entry chunk must not carry it).
// RfWorkspace statically imports MapView itself; splitting both here means
// Rollup puts maplibre-gl in their shared async chunk, never the entry one.
const MapView = lazy(() =>
  import('@/components/map/MapView').then((m) => ({ default: m.MapView })),
);
const RfWorkspace = lazy(() =>
  import('@/components/rf/RfWorkspace').then((m) => ({ default: m.RfWorkspace })),
);

// Projects Portal — card grid of every project. Lazy: it's an entry surface,
// not part of the topology-first initial view.
const ProjectsWorkspace = lazy(() =>
  import('@/components/projects/ProjectsWorkspace').then((m) => ({ default: m.ProjectsWorkspace })),
);

// Config Center — device config running/diff/export workspace. Lazy: it pulls a
// diff/export slice of the config API only when the operator opens it.
const ConfigWorkspace = lazy(() =>
  import('@/components/config/ConfigWorkspace').then((m) => ({ default: m.ConfigWorkspace })),
);

// Problem Center — network-health findings derived client-side from the topology
// snapshot. Lazy: it's a diagnostic surface opened on demand, not the first view.
const ProblemsWorkspace = lazy(() =>
  import('@/components/problems/ProblemsWorkspace').then((m) => ({ default: m.ProblemsWorkspace })),
);

// Reports Center — BOM + project report documentation. Lazy: it pulls the
// report/BOM slice of the API only when the operator opens it.
const ReportsWorkspace = lazy(() =>
  import('@/components/reports/ReportsWorkspace').then((m) => ({ default: m.ReportsWorkspace })),
);

export function AppShell({ projectName, conn }: { projectName: string; conn: ConnState }) {
  const viewMode = useUiStore((s) => s.viewMode);
  const simMode = useLabStore((s) => s.mode) === 'simulation';
  const drawerHosted = viewMode === 'topology' || viewMode === 'map';
  // Every workspace's own surface bleeds to the viewport's left edge instead
  // of paying a fixed 120px reserved-space tax that left a dead, off-color
  // band there (design feedback 2026-07-27 for map/rf; broadened
  // slice/ui-layout-consistency 2026-09-07 for plant/topology; broadened
  // again slice/ui-edge-fit 2026-09-12 after Surya's screenshots showed the
  // same band on Projects/Config/Problems/Reports/Twin/Edu — the wrapper
  // below was still gating those six on the old RAIL_INSET branch). Map/RF
  // tiles are infinitely pannable and topology/plant/twin/edu all render the
  // same pannable TopologyCanvas, so nothing real is lost under the rail
  // chassis there; Projects/Config/Problems/Reports are list/table-shaped —
  // their own components pad their leftmost in-flow column instead (see
  // each workspace's `pl-[116px]`, a 16px gutter past the rail's x=100
  // right edge) so real content never renders under the floating rail.
  // The rail is vertically centered (NavigationRail.tsx), so only chrome
  // actually sitting in its vertical band needs `CHROME_INSET`/
  // `CHROME_INSET_PL` (theme/shell.ts); a fixed top/bottom bar (plant's
  // toolbar/status rows, topology's bottom-left dock) sits outside that
  // band and stays flush left instead (slice/ui-edge-fit, 2026-09-07 re-QA:
  // the broadened fix above had applied the inset to those bars too, which
  // is the dead-gap-on-the-left regression Surya then reported a second
  // time).
  const bleed =
    viewMode === 'map' ||
    viewMode === 'rf' ||
    viewMode === 'plant' ||
    viewMode === 'topology' ||
    viewMode === 'twin' ||
    viewMode === 'edu' ||
    viewMode === 'projects' ||
    viewMode === 'config' ||
    viewMode === 'problems' ||
    viewMode === 'reports';
  useShortcuts();

  return (
    <div className="flex h-screen w-screen flex-col overflow-hidden">
      <TopBar projectName={projectName} conn={conn} />

      {/* relative: anchors the floating device-rail (design 12-UI shell-device-
          rail). The rail is `absolute` so it no longer reserves flex width. */}
      <div className="relative flex min-h-0 flex-1">
        <NavigationRail />

        <main className="relative min-w-0 flex-1 overflow-hidden" aria-label="Workspace">
          {/* Reserved-space contract for the rail: a positioned wrapper, not
              padding, on <main>. Padding only offsets normal-flow children —
              every workspace here is `absolute inset-0` (or similar), and an
              absolutely-positioned box's containing block is its ancestor's
              PADDING box, not content box, so it ignores ancestor padding
              entirely and renders from x=0, under the rail. This wrapper's
              own left offset becomes the containing block those descendants
              inherit, so every workspace clears the rail without each one
              hand-rolling its own offset.
              Every workspace now bleeds to `left-0` instead (slice/ui-edge-
              fit, 2026-09-12): each one's own root surface/background
              renders from x=0 so there is no dead, off-color band between
              the viewport edge and where content used to start. Canvas
              workspaces (map/rf/plant/topology/twin/edu) already relied on
              this — their pannable content freely renders behind the rail,
              the rail just floats over it — only chrome actually sitting in
              the rail's vertical band (map's tool column) compensates with
              `CHROME_INSET`/`CHROME_INSET_PL` (theme/shell.ts); chrome
              pinned to a fixed top/bottom edge stays flush left instead, see
              theme/shell.ts for the full contract. List/table workspaces
              (projects/config/problems/reports) pad their own leftmost
              in-flow column with `pl-[116px]` (16px past the rail's x=100
              right edge) so real content is never rendered under the rail.
              BottomDrawer/SimulationDock live in a second, always-rail-inset
              wrapper below (not this one): the drawer is hosted on topology
              AND map, so if it rode inside the bleed wrapper it would render
              under the rail on the map view. */}
          <div className={cn('absolute inset-y-0 right-0', bleed ? 'left-0' : RAIL_INSET)}>
          {viewMode === 'projects' ? (
            <Suspense
              fallback={
                <div className="grid h-full w-full place-items-center bg-surface text-fg/50">
                  <Loader2 className="h-6 w-6 animate-spin text-accent" />
                </div>
              }
            >
              <ProjectsWorkspace />
            </Suspense>
          ) : viewMode === 'map' ? (
            <Suspense
              fallback={
                <div className="grid h-full w-full place-items-center bg-surface text-fg/50">
                  <Loader2 className="h-6 w-6 animate-spin text-accent" />
                </div>
              }
            >
              <MapView />
            </Suspense>
          ) : viewMode === 'twin' ? (
            <TwinWorkspace />
          ) : viewMode === 'rf' ? (
            <Suspense
              fallback={
                <div className="grid h-full w-full place-items-center bg-surface text-fg/50">
                  <Loader2 className="h-6 w-6 animate-spin text-accent" />
                </div>
              }
            >
              <RfWorkspace />
            </Suspense>
          ) : viewMode === 'fiber' ? (
            <FiberWorkspace />
          ) : viewMode === 'plant' ? (
            <PlantWorkspace />
          ) : viewMode === 'config' ? (
            <Suspense
              fallback={
                <div className="grid h-full w-full place-items-center bg-surface text-fg/50">
                  <Loader2 className="h-6 w-6 animate-spin text-accent" />
                </div>
              }
            >
              <ConfigWorkspace />
            </Suspense>
          ) : viewMode === 'problems' ? (
            <Suspense
              fallback={
                <div className="grid h-full w-full place-items-center bg-surface text-fg/50">
                  <Loader2 className="h-6 w-6 animate-spin text-accent" />
                </div>
              }
            >
              <ProblemsWorkspace />
            </Suspense>
          ) : viewMode === 'reports' ? (
            <Suspense
              fallback={
                <div className="grid h-full w-full place-items-center bg-surface text-fg/50">
                  <Loader2 className="h-6 w-6 animate-spin text-accent" />
                </div>
              }
            >
              <ReportsWorkspace />
            </Suspense>
          ) : viewMode === 'edu' ? (
            <Suspense
              fallback={
                <div className="grid h-full w-full place-items-center bg-surface text-fg/50">
                  <Loader2 className="h-6 w-6 animate-spin text-accent" />
                </div>
              }
            >
              <EduWorkspace />
            </Suspense>
          ) : (
            <>
              <div className="absolute inset-0">
                <TopologyCanvas />
              </div>
              <TopologyToolbar />
              <ContextInspector />
              <DevicePicker />
            </>
          )}
          </div>

          {/* Second, always-rail-inset layer: BottomDrawer (topology/map) and
              SimulationDock (topology + running sim) must never render under
              the rail, even when the workspace layer above bleeds — with the
              rail now vertically centered its lower half would
              otherwise overlap the drawer region. `pointer-events-none` here
              so the wrapper's empty area never blocks clicks on the bled map
              beneath it; the drawer/dock re-enable `pointer-events-auto` on
              their own root. */}
          <div className={cn('pointer-events-none absolute inset-y-0 right-0', RAIL_INSET)}>
            {drawerHosted && <BottomDrawer />}
            {viewMode === 'topology' && simMode && <SimulationDock />}
          </div>
        </main>
      </div>

      <StatusBar conn={conn} />
      <CommandPalette />
      <ModalLayer />
    </div>
  );
}
