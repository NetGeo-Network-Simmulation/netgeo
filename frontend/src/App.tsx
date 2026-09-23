/**
 * App — authentication gate + workspace bootstrap around the AppShell.
 * On first mount it:
 *   - applies the persisted theme,
 *   - bootstraps the open project (loads topology snapshot),
 *   - connects the /ws/topology realtime channel.
 *
 * The chrome (top bar, navigation rail, full-canvas topology, inspector,
 * command palette, floating windows) lives in AppShell. If the user is not
 * authenticated, renders the LoginPage instead; after the first login, an
 * onboarding wizard is shown once.
 *
 * Real graph/window state lives in Zustand stores; server data is fetched via
 * the typed REST client and reconciled by the realtime channel.
 */
import { useEffect, useRef } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { AppShell } from '@/components/shell/AppShell';
import { ResizeHandles, useWindowChrome } from '@/components/shell/NativeTitleBar';
import { LoginPage } from '@/components/LoginPage';
import { projectsApi } from '@/api/client';
import { useUiStore } from '@/store/uiStore';
import { useTopologyStore } from '@/store/topologyStore';
import { useAuthStore } from '@/store/authStore';
import { useTopologyChannel } from '@/hooks/useTopologyChannel';
import { useCollaboration } from '@/hooks/useCollaboration';
import { applyTheme } from '@/theme/tokens';
import { cn } from '@/lib/cn';

export default function App() {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const { isNative, isMaximized, api } = useWindowChrome();
  const theme = useUiStore((s) => s.theme);
  const projectId = useUiStore((s) => s.projectId);
  const setProject = useUiStore((s) => s.setProject);
  const loadSnapshot = useTopologyStore((s) => s.loadSnapshot);
  const queryClient = useQueryClient();
  // Guards the one-time first-run project provisioning against React's
  // double-invoke (StrictMode) and re-renders, so we never create duplicates.
  const provisioningRef = useRef(false);

  // Apply persisted theme on mount.
  useEffect(() => applyTheme(theme), [theme]);

  // Gates the rounded-corner native window frame (theme/globals.css
  // `.native-shell`) — browser tabs (#4/#5) never get this class, so they
  // keep body's plain square fill untouched. `<html>`/`<body>` live outside
  // React's tree, so this is the one native-only side effect that has to
  // reach them directly instead of via className.
  useEffect(() => {
    document.documentElement.classList.toggle('native-shell', isNative);
  }, [isNative]);

  // Bootstrap: pick the first project and select it as the active workspace.
  const { data: projects } = useQuery({
    queryKey: ['projects'],
    queryFn: () => projectsApi.list(),
    staleTime: 60_000,
    retry: 1,
    enabled: isAuthenticated,
  });

  // Select an active project. On a fresh install the backend has zero projects,
  // so we provision a default one here — without a projectId the realtime
  // channel never connects (status stuck on "connecting") and drag-drop on the
  // canvas is a no-op (nothing renders). Auto-creating closes that first-run gap.
  useEffect(() => {
    if (projectId || !projects) return; // wait until the list has loaded
    if (projects.length > 0) {
      setProject(projects[0]!.id);
      return;
    }
    if (provisioningRef.current) return;
    provisioningRef.current = true;
    projectsApi
      .create({ name: 'My Network', description: 'Default workspace' })
      .then((p) => {
        setProject(p.id);
        void queryClient.invalidateQueries({ queryKey: ['projects'] });
      })
      .catch(() => {
        provisioningRef.current = false; // allow a retry on the next render
      });
  }, [projects, projectId, setProject, queryClient]);

  // Load the topology snapshot for the active project.
  const { data: topology } = useQuery({
    queryKey: ['topology', projectId],
    queryFn: () => projectsApi.topology(projectId!),
    enabled: !!projectId && isAuthenticated,
  });

  useEffect(() => {
    if (topology) loadSnapshot(topology);
  }, [topology, loadSnapshot]);

  // Realtime channel (auto-reconnect) — only when a project is active.
  const conn = useTopologyChannel(isAuthenticated, projectId);

  // Realtime collaboration presence (gated behind VITE_COLLAB until backend ready).
  useCollaboration(isAuthenticated, projectId);

  const projectName =
    projects?.find((p) => p.id === projectId)?.name ?? 'Untitled Project';

  return (
    <div
      className={cn(
        'relative flex h-screen w-screen flex-col overflow-hidden',
        isNative && 'ng-native-frame',
        isNative && isMaximized && 'ng-native-frame--maximized',
      )}
    >
      {isNative && api && <ResizeHandles api={api} />}
      <div className="relative min-h-0 flex-1 overflow-hidden">
        {isAuthenticated ? <AppShell projectName={projectName} conn={conn} /> : <LoginPage />}
      </div>
    </div>
  );
}
