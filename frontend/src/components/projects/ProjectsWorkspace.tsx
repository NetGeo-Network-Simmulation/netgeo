/**
 * ProjectsWorkspace — the Projects Portal (v1.2.029, clay design). A card grid
 * of every project + a Recently Opened table + a New project flow. Opening a
 * card selects it as the active workspace and drops into Topology.
 *
 * Reuses the existing REST surface:
 *  - `projectsApi.list()` for the grid (shares the ['projects'] cache with App),
 *  - `projectsApi.topology(id)` per card for real node/link counts (shares the
 *    ['topology', id] cache App already fills for the active project).
 *
 * Honest deviations from the clay mock (the backend has no data for them):
 *  - No Archived status — the Project model has no archive flag, so every card
 *    shows Active and there is no Archive action.
 *  - "Created" replaces the mock's "Modified" — the model only has created_at.
 *  - No thumbnails / owner — no snapshot image or owner field exists, so cards
 *    use a schematic placeholder and the table drops the Owner column.
 * ponytail: counts fetch one topology per card (fine at self-hosted scale); the
 * upgrade path is a node/link count field on the /projects list payload.
 */
import { useMemo, useRef, useState, type ReactNode } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Download, ExternalLink, FolderKanban, Loader2, MoreHorizontal, Network, Plus, Upload } from 'lucide-react';
import { projectsApi } from '@/api/client';
import type { Project } from '@/api/types';
import { useUiStore } from '@/store/uiStore';
import { WorkspaceEmptyState } from '@/components/shell/WorkspaceEmptyState';
import { ConfirmDialog } from '@/components/shell/ConfirmDialog';
import { cn } from '@/lib/cn';

const RECENT_KEY = 'netgeo.recentProjects';

function errorMessage(error: unknown, fallback: string): string {
  return typeof error === 'object' && error !== null && 'message' in error && typeof error.message === 'string'
    ? error.message : fallback;
}

function getRecent(): string[] {
  try {
    const v = JSON.parse(localStorage.getItem(RECENT_KEY) ?? '[]');
    return Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : [];
  } catch {
    return [];
  }
}
function pushRecent(id: string): string[] {
  const next = [id, ...getRecent().filter((x) => x !== id)].slice(0, 8);
  localStorage.setItem(RECENT_KEY, JSON.stringify(next));
  return next;
}

/** Compact relative time — the model only exposes created_at. */
function timeAgo(iso: string): string {
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return '—';
  const s = Math.max(0, (Date.now() - t) / 1000);
  const units: [number, string][] = [
    [31_536_000, 'y'],
    [2_592_000, 'mo'],
    [604_800, 'w'],
    [86_400, 'd'],
    [3_600, 'h'],
    [60, 'm'],
  ];
  for (const [sec, label] of units) if (s >= sec) return `${Math.floor(s / sec)}${label} ago`;
  return 'just now';
}

/** Real node/link counts from the project's topology snapshot (cached 60s). */
function useCounts(id: string): { nodes: number; links: number } | null {
  const { data } = useQuery({
    queryKey: ['topology', id],
    queryFn: () => projectsApi.topology(id),
    staleTime: 60_000,
  });
  return data ? { nodes: data.nodes.length, links: data.links.length } : null;
}

export function ProjectsWorkspace() {
  const setProject = useUiStore((s) => s.setProject);
  const setViewMode = useUiStore((s) => s.setViewMode);
  const qc = useQueryClient();

  const { data: projects, isLoading, error: listError } = useQuery({
    queryKey: ['projects'],
    queryFn: () => projectsApi.list(),
    staleTime: 60_000,
  });

  const inputRef = useRef<HTMLInputElement>(null);
  const [error, setError] = useState('');
  const [deleting, setDeleting] = useState<Project | null>(null);
  const [busy, setBusy] = useState(false);
  const [recent, setRecent] = useState<string[]>(getRecent);
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState('');

  const open = (id: string) => {
    setRecent(pushRecent(id));
    setProject(id);
    setViewMode('topology');
  };

  const createMut = useMutation({
    mutationFn: (name: string) => projectsApi.create({ name }),
    onSuccess: async (p) => {
      setCreating(false);
      setNewName('');
      await qc.invalidateQueries({ queryKey: ['projects'] });
      open(p.id);
    },
  });

  const importProject = async (file?: File) => {
    if (!file) return;
    setBusy(true);
    setError('');
    try {
      const project = await projectsApi.importArchive(JSON.parse(await file.text()));
      await qc.invalidateQueries({ queryKey: ['projects'] });
      open(project.id);
    } catch (e) {
      setError(e instanceof SyntaxError ? 'Invalid JSON file.' : errorMessage(e, 'Import failed.'));
    } finally {
      setBusy(false);
      if (inputRef.current) inputRef.current.value = '';
    }
  };

  const exportProject = async (project: Project) => {
    setError('');
    try {
      const archive = await projectsApi.archive(project.id);
      const url = URL.createObjectURL(new Blob([JSON.stringify(archive, null, 2)], { type: 'application/json' }));
      const link = document.createElement('a');
      link.href = url;
      link.download = `${project.name.replace(/[^a-z0-9-]+/gi, '-') || 'project'}.netgeo-archive.json`;
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (e) {
      setError(errorMessage(e, 'Export failed.'));
    }
  };

  const removeProject = async () => {
    if (!deleting || busy) return;
    setBusy(true);
    setError('');
    try {
      await projectsApi.remove(deleting.id);
      const nextRecent = getRecent().filter((id) => id !== deleting.id);
      setRecent(nextRecent);
      localStorage.setItem(RECENT_KEY, JSON.stringify(nextRecent));
      if (useUiStore.getState().projectId === deleting.id) setProject(null);
      await qc.invalidateQueries({ queryKey: ['projects'] });
      qc.removeQueries({ queryKey: ['topology', deleting.id] });
      setDeleting(null);
    } catch (e) {
      setError(errorMessage(e, 'Delete failed.'));
      setDeleting(null);
    } finally {
      setBusy(false);
    }
  };

  const list = projects ?? [];
  const byId = useMemo(() => new Map(list.map((p) => [p.id, p])), [list]);
  const recentProjects = recent
    .map((id) => byId.get(id))
    .filter((p): p is Project => Boolean(p));

  return (
    <div className="absolute inset-0 overflow-y-auto bg-surface">
      {/* pl-[116px]: 16px past the floating rail's x=100 right edge (rail is
          left-6 + 76px wide) — the page's own surface bleeds to x=0 (AppShell)
          but this scrolling column is real content, not a pannable canvas, so
          it needs a real gutter instead of rendering under the rail. */}
      <div className="w-full pt-4 pr-6 pb-6 pl-[116px]">
        <div className="glass mb-4 inline-flex flex-wrap items-center gap-2 rounded-xl border border-fg/12 p-1.5 shadow-glass">
            <input ref={inputRef} type="file" accept=".json,application/json" className="hidden" aria-label="Import project archive" onChange={(e) => void importProject(e.target.files?.[0])} />
            <button onClick={() => inputRef.current?.click()} disabled={busy} className="flex h-9 items-center gap-2 rounded-lg px-3 text-sm text-fg/80 hover:bg-fg/10 disabled:opacity-40">
              <Upload className="h-4 w-4" aria-hidden /> Import project
            </button>
            <button
              onClick={() => setCreating(true)}
              className="flex h-9 items-center gap-2 rounded-lg bg-accent px-3 text-sm font-medium text-accent-fg transition-colors hover:bg-accent-soft"
            >
              <Plus className="h-4 w-4" aria-hidden /> New project
            </button>
        </div>

        {(error || listError || createMut.error) && <p role="alert" className="mb-4 rounded-lg border border-danger/30 bg-danger/10 px-3 py-2 text-sm text-danger">{error || errorMessage(listError || createMut.error, 'Could not load projects.')}</p>}

        {isLoading ? (
          <div className="grid h-[50vh] place-items-center text-fg/50">
            <Loader2 className="h-6 w-6 animate-spin text-accent" />
          </div>
        ) : listError ? null : list.length === 0 && !creating ? (
          <div className="relative h-[60vh]">
            <WorkspaceEmptyState
              icon={FolderKanban}
              title="No projects yet"
              hint="A project holds one network topology and its simulations. Create your first to start designing."
              action={{ label: 'New project', onClick: () => setCreating(true) }}
            />
          </div>
        ) : (
          <>
            <div className="mb-10 grid grid-cols-1 gap-5 md:grid-cols-2 xl:grid-cols-3">
              {creating && (
                <NewProjectCard
                  name={newName}
                  setName={setNewName}
                  busy={createMut.isPending}
                  onSubmit={() => {
                    const n = newName.trim();
                    if (n) createMut.mutate(n);
                  }}
                  onCancel={() => {
                    setCreating(false);
                    setNewName('');
                  }}
                />
              )}
              {list.map((p) => (
                <ProjectCard key={p.id} project={p} onOpen={() => open(p.id)} onExport={() => void exportProject(p)} onDelete={() => setDeleting(p)} />
              ))}
            </div>

            {recentProjects.length > 0 && (
              <section className="mb-4">
                <h2 className="mb-4 font-display text-lg font-semibold text-fg">Recently opened</h2>
                <div className="overflow-x-auto rounded-xl border border-fg/10 bg-panel">
                  <table className="w-full border-collapse text-left">
                    <thead className="border-b border-fg/10 bg-recess/20">
                      <tr>
                        <Th>Name</Th>
                        <Th className="w-24">Nodes</Th>
                        <Th className="w-24">Links</Th>
                        <Th className="w-32">Created</Th>
                      </tr>
                    </thead>
                    <tbody>
                      {recentProjects.map((p) => (
                        <RecentRow key={p.id} project={p} onOpen={() => open(p.id)} />
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>
            )}
          </>
        )}
      </div>
      {deleting && <ConfirmDialog title="Delete project?" message={`Delete “${deleting.name}” and all its saved network data? This cannot be undone.`} confirmLabel={busy ? 'Deleting…' : 'Delete project'} danger onConfirm={() => void removeProject()} onCancel={() => !busy && setDeleting(null)} />}
    </div>
  );
}

function ProjectCard({ project, onOpen, onExport, onDelete }: { project: Project; onOpen: () => void; onExport: () => void; onDelete: () => void }) {
  const counts = useCounts(project.id);
  return (
    <div className="group relative flex flex-col gap-4 rounded-xl border border-fg/10 bg-panel-2 p-5 text-left shadow-soft transition-colors hover:border-accent/40">
      <div className="flex items-center gap-3">
        <span className="grid h-8 w-8 shrink-0 place-items-center rounded-md border border-fg/10 bg-recess/30">
          <Network className="h-4 w-4 text-accent" aria-hidden />
        </span>
        <button onClick={onOpen} className="min-w-0 flex-1 truncate text-left font-medium text-fg hover:text-accent" aria-label={`Open ${project.name}`}>{project.name}</button>
        <details className="relative">
          <summary className="grid h-8 w-8 cursor-pointer list-none place-items-center rounded-lg text-fg/60 hover:bg-fg/10" aria-label={`Actions for ${project.name}`}><MoreHorizontal className="h-4 w-4" /></summary>
          <div className="glass-strong absolute right-0 top-9 z-20 w-36 rounded-lg border border-fg/15 p-1 shadow-glass-lg">
            <button onClick={onExport} className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs text-fg hover:bg-fg/10"><Download className="h-3.5 w-3.5" /> Export</button>
            <button onClick={onDelete} className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs text-danger hover:bg-danger/10">Delete</button>
          </div>
        </details>
      </div>

      {/* Schematic preview placeholder — no thumbnails are generated server-side. */}
      <button onClick={onOpen} aria-label={`Open ${project.name} workspace`} className="relative grid h-28 place-items-center overflow-hidden rounded-lg border border-fg/10 bg-recess/25">
        <Network className="h-8 w-8 text-fg/10" aria-hidden />
        <span className="absolute bottom-2 left-2 flex items-center gap-1.5 rounded border border-fg/10 bg-surface px-2 py-0.5 backdrop-blur-sm">
          <span className="h-1.5 w-1.5 rounded-full bg-success" aria-hidden />
          <span className="font-mono text-[11px] text-success">Active</span>
        </span>
        {/* Hover / focus affordance for the card's Open action. */}
        <span className="pointer-events-none absolute inset-0 grid place-items-center bg-recess/70 opacity-0 backdrop-blur-[1px] transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100">
          <span className="flex items-center gap-2 rounded-lg bg-accent px-3 py-1.5 text-sm font-medium text-accent-fg">
            <ExternalLink className="h-4 w-4" aria-hidden /> Open workspace
          </span>
        </span>
      </button>

      <div className="mt-auto grid grid-cols-3 gap-2 border-t border-fg/10 pt-3">
        <Meta label="Nodes" value={counts ? counts.nodes.toLocaleString() : '—'} />
        <Meta label="Links" value={counts ? counts.links.toLocaleString() : '—'} />
        <Meta label="Created" value={timeAgo(project.created_at)} />
      </div>
    </div>
  );
}

function RecentRow({ project, onOpen }: { project: Project; onOpen: () => void }) {
  const counts = useCounts(project.id);
  return (
    <tr
      onClick={onOpen}
      className="h-9 cursor-pointer border-b border-fg/5 transition-colors last:border-0 hover:bg-fg/5"
    >
      <td className="px-4">
        <span className="flex items-center gap-2">
          <Network className="h-4 w-4 text-accent" aria-hidden />
          <span className="text-sm font-medium text-fg">{project.name}</span>
        </span>
      </td>
      <td className="px-4 font-mono text-[13px] text-fg/70">
        {counts ? counts.nodes.toLocaleString() : '—'}
      </td>
      <td className="px-4 font-mono text-[13px] text-fg/70">
        {counts ? counts.links.toLocaleString() : '—'}
      </td>
      <td className="px-4 font-mono text-[13px] text-fg/70">{timeAgo(project.created_at)}</td>
    </tr>
  );
}

function NewProjectCard({
  name,
  setName,
  busy,
  onSubmit,
  onCancel,
}: {
  name: string;
  setName: (v: string) => void;
  busy: boolean;
  onSubmit: () => void;
  onCancel: () => void;
}) {
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        onSubmit();
      }}
      className="flex flex-col justify-center gap-3 rounded-xl border border-dashed border-accent/40 bg-panel-2 p-5"
    >
      <label
        htmlFor="new-project-name"
        className="font-mono text-[10px] uppercase tracking-wide text-fg/45"
      >
        New project name
      </label>
      <input
        id="new-project-name"
        autoFocus
        value={name}
        onChange={(e) => setName(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Escape') onCancel();
        }}
        placeholder="e.g. jakarta-metro-ring"
        className="rounded-lg border border-fg/10 bg-recess/30 px-3 py-2 text-sm text-fg placeholder:text-fg/35 focus:border-accent/50 focus:outline-none"
      />
      <div className="flex gap-2">
        <button
          type="submit"
          disabled={!name.trim() || busy}
          className="flex flex-1 items-center justify-center gap-2 rounded-lg bg-accent px-3 py-2 text-sm font-medium text-accent-fg transition-colors hover:bg-accent-soft disabled:opacity-40"
        >
          {busy && <Loader2 className="h-4 w-4 animate-spin" aria-hidden />}
          Create
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="rounded-lg border border-fg/10 px-3 py-2 text-sm text-fg/70 transition-colors hover:bg-fg/5"
        >
          Cancel
        </button>
      </div>
    </form>
  );
}

function Meta({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col">
      <span className="font-mono text-[10px] uppercase tracking-wide text-fg/40">{label}</span>
      <span className="font-mono text-[13px] text-fg/85">{value}</span>
    </div>
  );
}

function Th({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <th
      className={cn(
        'px-4 py-2.5 font-mono text-[11px] font-medium uppercase tracking-wider text-fg/45',
        className,
      )}
    >
      {children}
    </th>
  );
}
