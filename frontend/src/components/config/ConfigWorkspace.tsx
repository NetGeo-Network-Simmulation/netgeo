/**
 * ConfigWorkspace — the Config Center (v1.2.030, clay design). Left rail of the
 * active project's devices; main area with three tabs: Running config · Diff ·
 * Export. It's the full-workspace sibling of the BottomDrawer's quick Config
 * tab (which stays for topology-scoped peeks).
 *
 * Reuses the existing REST surface — NO new backend:
 *  - `projectsApi.topology(id)` for the device list (shares ['topology', id]),
 *  - `configsApi.forNode(nodeId)` for Running config (same call ConfigViewer uses),
 *  - `configsApi.diff(nodeId)` for the Diff tab (NG-CFG-03 unified diff),
 *  - `configsApi.exportProject / downloadProjectConfigs` for Export (NG-CFG-01).
 *
 * Honest deviations from the clay mock:
 *  - The Diff "Compare: intent vs running" dropdown is a static label. The
 *    backend diffs stored-vs-regenerated only; running-vs-startup / v2-vs-v1
 *    have no endpoint, so a multi-option select would be a lie.
 *  - Side-by-side is reconstructed from the unified diff the API returns (stored
 *    = running/right, regenerated-from-intent = intent/left). Added lines land
 *    left, removed lines right, with gap rows — the faithful split of a unified
 *    diff, not two independently stored full configs.
 */
import { useMemo, useState, type ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Check, Copy, Download, FileCode2, Router, Network, Server, Cpu, Search } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { projectsApi, configsApi } from '@/api/client';
import type { NodeModel, Nos, NodeKind } from '@/api/types';
import { useUiStore } from '@/store/uiStore';
import { WorkspaceEmptyState } from '@/components/shell/WorkspaceEmptyState';
import { Select } from '@/components/ui/Select';
import { cn } from '@/lib/cn';

type Tab = 'running' | 'diff' | 'export';

const NOS_LABEL: Record<Nos, string> = {
  forgeos: 'NetGeo OS',
  ios: 'Cisco IOS',
  iosxr: 'Cisco IOS-XR',
  nxos: 'Cisco NX-OS',
  junos: 'Juniper JunOS',
  eos: 'Arista EOS',
  routeros: 'MikroTik RouterOS',
  vyos: 'VyOS',
  sros: 'Nokia SR-OS',
  frr: 'FRRouting',
  vrp: 'Huawei VRP',
};

// Target dialects for Export — mirrors ConsolePanel's render-vendor list.
const EXPORT_VENDORS = [
  { id: '', label: 'Native (per device)' },
  { id: 'ios', label: 'Cisco IOS' },
  { id: 'junos', label: 'Juniper JunOS' },
  { id: 'eos', label: 'Arista EOS' },
  { id: 'routeros', label: 'MikroTik RouterOS' },
  { id: 'vyos', label: 'VyOS' },
  { id: 'frr', label: 'FRRouting' },
  { id: 'sros', label: 'Nokia SR-OS' },
  { id: 'vrp', label: 'Huawei VRP' },
];

function kindIcon(kind: NodeKind): LucideIcon {
  if (kind === 'switch') return Network;
  if (kind === 'server' || kind === 'host') return Server;
  if (kind === 'router' || kind === 'firewall') return Router;
  return Cpu;
}

export function ConfigWorkspace() {
  const projectId = useUiStore((s) => s.projectId);
  const [tab, setTab] = useState<Tab>('running');
  const [search, setSearch] = useState('');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [vendor, setVendor] = useState('');
  const [copied, setCopied] = useState(false);

  const { data: topo, isLoading } = useQuery({
    queryKey: ['topology', projectId],
    queryFn: () => projectsApi.topology(projectId!),
    enabled: !!projectId,
    staleTime: 60_000,
  });

  const nodes = topo?.nodes ?? [];
  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return q
      ? nodes.filter((n) => n.name.toLowerCase().includes(q) || NOS_LABEL[n.nos].toLowerCase().includes(q))
      : nodes;
  }, [nodes, search]);

  // Default selection follows the (filtered) list; falls back to the first node.
  const selected =
    nodes.find((n) => n.id === selectedId) ?? filtered[0] ?? nodes[0] ?? null;
  const nodeId = selected?.id ?? null;

  const runningQ = useQuery({
    queryKey: ['configs', nodeId],
    queryFn: () => configsApi.forNode(nodeId!),
    enabled: tab === 'running' && !!nodeId,
    staleTime: 30_000,
  });
  const diffQ = useQuery({
    queryKey: ['config-diff', nodeId],
    queryFn: () => configsApi.diff(nodeId!),
    enabled: tab === 'diff' && !!nodeId,
    staleTime: 30_000,
  });
  const exportQ = useQuery({
    queryKey: ['config-export', projectId, vendor],
    queryFn: () => configsApi.exportProject(projectId!, vendor || undefined),
    enabled: tab === 'export' && !!projectId,
    staleTime: 30_000,
  });

  if (!projectId) {
    return (
      <div className="absolute inset-0">
        <WorkspaceEmptyState
          icon={FileCode2}
          title="No project open"
          hint="Open a project from the Projects portal to inspect and export its device configs."
        />
      </div>
    );
  }

  const runningText = runningQ.data?.[0]?.content ?? '';
  const exportText = selected ? exportQ.data?.configs[selected.name] ?? '' : '';
  const copyText = tab === 'running' ? runningText : tab === 'diff' ? diffQ.data?.diff ?? '' : exportText;

  const copy = () => {
    if (!copyText) return;
    void navigator.clipboard.writeText(copyText);
    setCopied(true);
    setTimeout(() => setCopied(false), 1200);
  };

  return (
    <div className="absolute inset-0 flex bg-surface">
      {/* Device rail */}
      <aside className="flex w-[280px] shrink-0 flex-col border-r border-fg/10 bg-panel">
        <div className="border-b border-fg/10 p-3">
          <label className="flex items-center gap-2 rounded-lg border border-fg/10 bg-recess/30 px-2.5 py-1.5">
            <Search className="h-4 w-4 text-fg/40" aria-hidden />
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search devices…"
              aria-label="Search devices"
              className="w-full bg-transparent text-sm text-fg/85 placeholder:text-fg/35 focus:outline-none"
            />
          </label>
        </div>
        <div className="ng-scroll flex-1 overflow-y-auto" role="listbox" aria-label="Devices">
          {isLoading ? (
            <p className="p-4 text-xs text-fg/40">Loading devices…</p>
          ) : filtered.length === 0 ? (
            <p className="p-4 text-xs text-fg/40">
              {nodes.length === 0 ? 'This project has no devices yet.' : `No device matches “${search}”.`}
            </p>
          ) : (
            filtered.map((n) => (
              <DeviceRow
                key={n.id}
                node={n}
                active={n.id === nodeId}
                onSelect={() => setSelectedId(n.id)}
              />
            ))
          )}
        </div>
      </aside>

      {/* Main */}
      <main className="flex min-w-0 flex-1 flex-col">
        {/* Toolbar + tabs */}
        <div className="flex h-14 shrink-0 items-center justify-between gap-4 border-b border-fg/10 bg-panel px-6">
          <div className="flex h-full gap-6 font-mono text-[12px]" role="tablist" aria-label="Config views">
            {(['running', 'diff', 'export'] as const).map((t) => (
              <button
                key={t}
                role="tab"
                aria-selected={tab === t}
                onClick={() => setTab(t)}
                className={cn(
                  'h-full border-b-2 px-1 transition-colors',
                  tab === t
                    ? 'border-accent text-accent'
                    : 'border-transparent text-fg/55 hover:text-fg/90',
                )}
              >
                {t === 'running' ? 'Running config' : t === 'diff' ? 'Diff' : 'Export'}
              </button>
            ))}
          </div>

          <div className="flex items-center gap-3">
            {tab === 'diff' && (
              <span className="rounded border border-fg/10 bg-recess/30 px-3 py-1.5 font-mono text-[12px] text-fg/60">
                Compare: intent vs running
              </span>
            )}
            {tab === 'export' && (
              <Select
                value={vendor}
                onChange={setVendor}
                aria-label="Export target vendor"
                options={EXPORT_VENDORS.map((v) => ({ value: v.id, label: v.label }))}
                className="w-48"
              />
            )}
            <button
              onClick={copy}
              disabled={!copyText}
              className="flex items-center gap-2 rounded border border-fg/10 px-4 py-1.5 text-sm font-medium text-fg/85 transition-colors hover:bg-fg/5 disabled:opacity-40"
            >
              {copied ? <Check className="h-4 w-4 text-success" aria-hidden /> : <Copy className="h-4 w-4" aria-hidden />}
              Copy
            </button>
            <button
              onClick={() => configsApi.downloadProjectConfigs(projectId, vendor || undefined)}
              className="flex items-center gap-2 rounded bg-accent px-4 py-1.5 text-sm font-medium text-accent-fg transition-colors hover:bg-accent-soft"
            >
              <Download className="h-4 w-4" aria-hidden />
              Export vendor config
            </button>
          </div>
        </div>

        {/* Body */}
        <div className="min-h-0 flex-1">
          {!selected ? (
            <WorkspaceEmptyState icon={FileCode2} title="Select a device" hint="Pick a device to view its config." />
          ) : tab === 'running' ? (
            <PaneState loading={runningQ.isLoading} error={runningQ.error} empty={!runningText}
              emptyMsg={`${selected.name} has no generated config yet — generate one from the topology's Config panel.`}>
              <CodePane text={runningText} />
            </PaneState>
          ) : tab === 'diff' ? (
            <PaneState loading={diffQ.isLoading} error={diffQ.error} empty={false}>
              {diffQ.data && !diffQ.data.changed ? (
                <div className="grid h-full place-items-center text-center text-fg/45">
                  <div className="space-y-2">
                    <Check className="mx-auto h-8 w-8 text-success" aria-hidden />
                    <p className="text-sm">
                      {diffQ.data.had_stored
                        ? 'Stored config is in sync with intent.'
                        : 'No stored config to compare — intent matches a fresh render.'}
                    </p>
                  </div>
                </div>
              ) : (
                <DiffPane node={selected} diff={diffQ.data?.diff ?? ''} />
              )}
            </PaneState>
          ) : (
            <PaneState loading={exportQ.isLoading} error={exportQ.error} empty={!exportText}
              emptyMsg={`No renderable config for ${selected.name} in this dialect (hosts and clouds have no device config).`}>
              <CodePane text={exportText} />
            </PaneState>
          )}
        </div>
      </main>
    </div>
  );
}

function DeviceRow({ node, active, onSelect }: { node: NodeModel; active: boolean; onSelect: () => void }) {
  const Icon = kindIcon(node.kind);
  return (
    <button
      role="option"
      aria-selected={active}
      onClick={onSelect}
      className={cn(
        'relative flex w-full items-center gap-3 p-3 text-left transition-colors',
        active ? 'bg-fg/8' : 'hover:bg-fg/5',
      )}
    >
      {active && <span className="absolute inset-y-0 left-0 w-1 rounded-r bg-accent" aria-hidden />}
      <span className="grid h-8 w-8 shrink-0 place-items-center rounded border border-fg/10 bg-recess/30">
        <Icon className={cn('h-[18px] w-[18px]', active ? 'text-accent' : 'text-fg/55')} aria-hidden />
      </span>
      <span className="min-w-0 flex-1">
        <span className={cn('block truncate font-mono text-[12px]', active ? 'text-fg' : 'text-fg/70')}>
          {node.name}
        </span>
        <span className="mt-0.5 block truncate text-[11px] text-fg/45">
          {NOS_LABEL[node.nos]} · {node.kind}
        </span>
      </span>
    </button>
  );
}

/** Loading / error / empty gate shared by the three panes. */
function PaneState({
  loading,
  error,
  empty,
  emptyMsg,
  children,
}: {
  loading: boolean;
  error: unknown;
  empty: boolean;
  emptyMsg?: string;
  children: ReactNode;
}) {
  if (loading) return <p className="p-6 text-sm text-fg/40">Loading…</p>;
  if (error)
    return <p className="p-6 text-sm text-danger">{(error as Error)?.message ?? 'Failed to load.'}</p>;
  if (empty)
    return (
      <div className="grid h-full place-items-center p-6 text-center text-fg/40">
        <div className="space-y-2">
          <FileCode2 className="mx-auto h-8 w-8" aria-hidden />
          <p className="max-w-sm text-xs leading-relaxed">{emptyMsg}</p>
        </div>
      </div>
    );
  return <>{children}</>;
}

/** Line-numbered mono config view (JetBrains Mono via font-mono token). */
function CodePane({ text }: { text: string }) {
  const lines = text.replace(/\n$/, '').split('\n');
  return (
    <div className="ng-scroll h-full overflow-auto bg-recess/40 p-4 font-mono text-[12.5px] leading-relaxed">
      <table className="w-full border-collapse">
        <tbody>
          {lines.map((line, i) => (
            <tr key={i}>
              <td className="w-10 select-none border-r border-fg/10 pr-3 text-right text-fg/30">{i + 1}</td>
              <td className="whitespace-pre-wrap pl-4 text-fg/85">{line || ' '}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

type Cell = { n: number | null; text: string; kind: 'ctx' | 'add' | 'del' | 'gap' };

/** Reconstruct a side-by-side split from a unified diff string.
 *  Left = regenerated-from-intent (`+`), Right = stored/running (`-`). */
function splitUnified(diff: string): { left: Cell; right: Cell }[] {
  const rows: { left: Cell; right: Cell }[] = [];
  let leftNo = 1;
  let rightNo = 1;
  let dels: string[] = [];
  let adds: string[] = [];
  const gap: Cell = { n: null, text: '', kind: 'gap' };

  const flush = () => {
    const m = Math.max(dels.length, adds.length);
    for (let k = 0; k < m; k++) {
      const left: Cell = k < adds.length ? { n: leftNo++, text: adds[k]!, kind: 'add' } : gap;
      const right: Cell = k < dels.length ? { n: rightNo++, text: dels[k]!, kind: 'del' } : gap;
      rows.push({ left, right });
    }
    dels = [];
    adds = [];
  };

  for (const raw of diff.split('\n')) {
    if (raw.startsWith('+++') || raw.startsWith('---')) continue;
    if (raw.startsWith('@@')) {
      flush();
      const m = /@@ -(\d+),?\d* \+(\d+)/.exec(raw);
      if (m) {
        rightNo = Number(m[1]);
        leftNo = Number(m[2]);
      }
      continue;
    }
    if (raw.startsWith('-')) dels.push(raw.slice(1));
    else if (raw.startsWith('+')) adds.push(raw.slice(1));
    else {
      // Context line (may be prefixed by a single space, or a trailing empty line).
      flush();
      const text = raw.startsWith(' ') ? raw.slice(1) : raw;
      rows.push({
        left: { n: leftNo++, text, kind: 'ctx' },
        right: { n: rightNo++, text, kind: 'ctx' },
      });
    }
  }
  flush();
  return rows;
}

function DiffPane({ node, diff }: { node: NodeModel; diff: string }) {
  const rows = useMemo(() => splitUnified(diff), [diff]);
  return (
    <div className="flex h-full">
      <DiffColumn title="Intent Config" rows={rows} side="left" />
      <div className="w-px shrink-0 bg-fg/10" aria-hidden />
      <DiffColumn title={`Running Config (${node.name})`} rows={rows} side="right" />
    </div>
  );
}

function DiffColumn({
  title,
  rows,
  side,
}: {
  title: string;
  rows: { left: Cell; right: Cell }[];
  side: 'left' | 'right';
}) {
  return (
    <div className="flex min-w-0 flex-1 flex-col">
      <div className="shrink-0 border-b border-fg/10 bg-panel px-4 py-2 font-mono text-[12px] text-fg/60">
        {title}
      </div>
      <div className="ng-scroll flex-1 overflow-auto bg-recess/40 p-4 font-mono text-[12.5px] leading-relaxed">
        <table className="w-full border-collapse">
          <tbody>
            {rows.map((r, i) => {
              const cell = side === 'left' ? r.left : r.right;
              return (
                <tr key={i}>
                  <td className="w-10 select-none border-r border-fg/10 pr-3 text-right text-fg/30">
                    {cell.n ?? ''}
                  </td>
                  <td
                    className={cn(
                      'whitespace-pre-wrap pl-4',
                      cell.kind === 'add' && 'bg-success/10 text-success',
                      cell.kind === 'del' && 'bg-danger/10 text-danger',
                      cell.kind === 'ctx' && 'text-fg/70',
                      cell.kind === 'gap' && 'select-none bg-fg/5',
                    )}
                  >
                    {cell.kind === 'add' && <span className="mr-2 opacity-50">+</span>}
                    {cell.kind === 'del' && <span className="mr-2 opacity-50">-</span>}
                    {cell.kind === 'gap' ? ' ' : cell.text || ' '}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
