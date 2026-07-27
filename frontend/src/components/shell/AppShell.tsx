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
              inherit, so every workspace (and BottomDrawer) clears the rail
              without each one hand-rolling its own offset. */}
          <div className="absolute inset-y-0 left-[120px] right-0">
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

          {/* Shared diagnostics drawer — topology/map only; other workspaces own
              their own bottom edge. Simulation transport — topology + running sim. */}
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
