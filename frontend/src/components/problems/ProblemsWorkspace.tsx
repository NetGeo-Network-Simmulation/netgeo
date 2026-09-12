/**
 * ProblemsWorkspace — the Problem Center (v1.2.031, clay design). A left table of
 * problems derived from the active project's topology + a right inspector with
 * the selected problem's evidence and suggested actions. It's the standing,
 * full-workspace view of network health.
 *
 * Reuses the existing REST surface — NO new backend. Problems are DERIVED
 * client-side from the topology snapshot the app already caches:
 *  - `projectsApi.topology(id)` (shares ['topology', id]) → nodes + links,
 *  - deriveProblems() reads link status, interface addressing and peering to
 *    surface Critical / Warning / Info findings. Memoized over the cache so a
 *    large topology re-derives only when nodes/links actually change.
 *
 * Honest deviations from the clay mock (the backend has no data for them):
 *  - "Detected" is not a real timestamp — the topology model carries no per-
 *    fault detection time. It shows a stable, severity-ordered relative label
 *    ("live" / "recent" / "earlier") so the column isn't a fabricated clock.
 *  - "Acknowledge" is a client-only concept (localStorage per project) — there
 *    is no fault/ack store server-side, so acks don't survive a different
 *    browser. Acknowledged problems drop to the bottom, dimmed.
 *  - Counts (14/2/5/7 in the mock) are whatever the live topology yields.
 */
import { useMemo, useState, useCallback } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  AlertTriangle,
  CircleAlert,
  Info,
  Search,
  Check,
  Network,
  ShieldCheck,
  Wrench,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { projectsApi } from '@/api/client';
import type { Topology, NodeModel } from '@/api/types';
import { useUiStore } from '@/store/uiStore';
import { WorkspaceEmptyState } from '@/components/shell/WorkspaceEmptyState';
import { cn } from '@/lib/cn';

type Severity = 'critical' | 'warning' | 'info';

interface Problem {
  id: string;
  severity: Severity;
  /** Short problem title (table + inspector heading). */
  title: string;
  /** Affected device name (or link id). */
  node: string;
  /** Node id for "Open in Topology", when the fault is node-scoped. */
  nodeId?: string;
  /** One-paragraph explanation for the inspector. */
  detail: string;
  /** Monospace evidence lines (interface names, ips, link ids). */
  evidence: string[];
  /** Bulleted remediation steps. */
  actions: string[];
}

const SEV_META: Record<Severity, { icon: LucideIcon; tone: string; label: string; order: number }> = {
  critical: { icon: CircleAlert, tone: 'text-danger', label: 'Critical', order: 0 },
  warning: { icon: AlertTriangle, tone: 'text-warning', label: 'Warning', order: 1 },
  info: { icon: Info, tone: 'text-accent', label: 'Info', order: 2 },
};

/** Stable relative-freshness label — NOT a real clock (see header). */
const DETECTED_LABEL: Record<Severity, string> = {
  critical: 'live',
  warning: 'recent',
  info: 'earlier',
};

/**
 * Derive problems from a topology snapshot. Pure + deterministic so it memoizes
 * cleanly. Ordered by severity, then by name for a stable table.
 */
function deriveProblems(topo: Topology): Problem[] {
  const nodes = topo.nodes;
  const links = topo.links;
  const byIface = new Map<string, { node: NodeModel; ifaceName: string }>();
  for (const n of nodes) {
    for (const i of n.interfaces) byIface.set(i.id, { node: n, ifaceName: i.name });
  }
  const endpointName = (ifaceId: string) => byIface.get(ifaceId)?.node.name ?? ifaceId;

  const out: Problem[] = [];

  // 1) Links that are down / errored / admin-down — the loudest faults.
  for (const l of links) {
    if (!l.status || l.status === 'up' || l.status === 'unknown') continue;
    const a = endpointName(l.a_iface);
    const b = endpointName(l.b_iface);
    const errored = l.status === 'errored';
    const admin = l.status === 'admin_down';
    out.push({
      id: `link:${l.id}`,
      severity: admin ? 'info' : 'critical',
      title: errored
        ? 'Link errored (physical fault)'
        : admin
          ? 'Link administratively down'
          : 'Link down',
      node: `${a} ↔ ${b}`,
      detail: errored
        ? `The ${l.type} link between ${a} and ${b} was brought down by a physical fault (e.g. a cable run over its rated length). Traffic across it is dropped.`
        : admin
          ? `The link between ${a} and ${b} is administratively disabled. It carries no traffic until re-enabled.`
          : `The link between ${a} and ${b} is down. Any path that relied on it is broken.`,
      evidence: [
        `link ${l.id}`,
        `status ${l.status}`,
        `type ${l.type}  bw ${l.bandwidth}Mbps  delay ${l.delay}ms`,
      ],
      actions: errored
        ? [
            'Check the physical media length against its rated maximum.',
            'Replace or re-route the cable, then bring the link back up.',
          ]
        : admin
          ? ['Re-enable the interfaces on both ends if the link should be live.']
          : [
              'Verify both endpoint interfaces are up.',
              'Inspect the physical media and reseat if needed.',
            ],
    });
  }

  // 2) Routers/firewalls with no interface carrying an IP — can't route.
  for (const n of nodes) {
    if (n.kind !== 'router' && n.kind !== 'firewall') continue;
    const hasIp = n.interfaces.some((i) => i.ip.length > 0);
    if (hasIp) continue;
    out.push({
      id: `noip:${n.id}`,
      severity: 'critical',
      title: 'No IP addressing assigned',
      node: n.name,
      nodeId: n.id,
      detail: `${n.name} is a ${n.kind} but none of its interfaces have an IP address. It cannot participate in routing until addressed.`,
      evidence: [`node ${n.name}`, `interfaces ${n.interfaces.length}`, 'addressed 0'],
      actions: [
        'Run the Auto-addressing wizard to assign a dual-stack plan.',
        'Or set interface IPs manually from the device inspector.',
      ],
    });
  }

  // 3) Hosts without a default gateway — reachable locally, not off-subnet.
  for (const n of nodes) {
    if (n.kind !== 'host' && n.kind !== 'server') continue;
    const gw = (n.intent as Record<string, unknown> | null | undefined)?.gateway;
    if (gw) continue;
    if (n.interfaces.every((i) => i.ip.length === 0)) continue; // unaddressed host handled elsewhere-ish
    out.push({
      id: `nogw:${n.id}`,
      severity: 'warning',
      title: 'No default gateway',
      node: n.name,
      nodeId: n.id,
      detail: `${n.name} has addressing but no default gateway. It can reach its own subnet but nothing beyond it.`,
      evidence: [`node ${n.name}`, 'intent.gateway (unset)'],
      actions: [
        'Run the Auto-addressing wizard (it points hosts at their domain router).',
        'Or set intent.gateway on the device.',
      ],
    });
  }

  // 4) Dangling router interfaces (up, addressed, but not cabled to anything).
  for (const n of nodes) {
    if (n.kind !== 'router' && n.kind !== 'switch' && n.kind !== 'firewall') continue;
    const dangling = n.interfaces.filter((i) => i.peer_link_id === null && i.ip.length > 0);
    if (dangling.length === 0) continue;
    out.push({
      id: `dangle:${n.id}`,
      severity: 'warning',
      title: 'Addressed interface not cabled',
      node: n.name,
      nodeId: n.id,
      detail: `${n.name} has ${dangling.length} interface(s) with an IP but no link. The subnet is configured but unreachable.`,
      evidence: dangling.slice(0, 6).map((i) => `${i.name}  ${i.ip[0] ?? '(no ip)'}  peer: none`),
      actions: [
        'Cable the interface to its intended neighbour, or',
        'Remove the stale addressing if the port is unused.',
      ],
    });
  }

  out.sort((x, y) => SEV_META[x.severity].order - SEV_META[y.severity].order || x.node.localeCompare(y.node));
  return out;
}

const ACK_KEY = (pid: string) => `netgeo.problemAcks.${pid}`;
function getAcks(pid: string): Set<string> {
  try {
    const v = JSON.parse(localStorage.getItem(ACK_KEY(pid)) ?? '[]');
    return new Set(Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : []);
  } catch {
    return new Set();
  }
}

export function ProblemsWorkspace() {
  const projectId = useUiStore((s) => s.projectId);
  const setViewMode = useUiStore((s) => s.setViewMode);
  const [search, setSearch] = useState('');
  const [filter, setFilter] = useState<Severity | 'all'>('all');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [acks, setAcks] = useState<Set<string>>(() => (projectId ? getAcks(projectId) : new Set()));

  const { data: topo, isLoading } = useQuery({
    queryKey: ['topology', projectId],
    queryFn: () => projectsApi.topology(projectId!),
    enabled: !!projectId,
    staleTime: 60_000,
  });

  // Memoized over the cache — a large topology re-derives only on real change.
  const problems = useMemo(() => (topo ? deriveProblems(topo) : []), [topo]);

  const counts = useMemo(() => {
    const c = { all: problems.length, critical: 0, warning: 0, info: 0 };
    for (const p of problems) c[p.severity]++;
    return c;
  }, [problems]);

  const visible = useMemo(() => {
    const q = search.trim().toLowerCase();
    const rows = problems
      .filter((p) => filter === 'all' || p.severity === filter)
      .filter((p) => !q || p.title.toLowerCase().includes(q) || p.node.toLowerCase().includes(q));
    // Acknowledged sink to the bottom, keeping severity order within each group.
    return rows.sort((a, b) => Number(acks.has(a.id)) - Number(acks.has(b.id)));
  }, [problems, filter, search, acks]);

  const selected = problems.find((p) => p.id === selectedId) ?? visible[0] ?? null;

  const persistAcks = useCallback(
    (next: Set<string>) => {
      if (projectId) localStorage.setItem(ACK_KEY(projectId), JSON.stringify([...next]));
      setAcks(new Set(next));
    },
    [projectId],
  );

  const ack = useCallback(
    (id: string) => {
      const next = new Set(acks);
      next.add(id);
      persistAcks(next);
    },
    [acks, persistAcks],
  );

  const ackAll = useCallback(() => {
    persistAcks(new Set(problems.map((p) => p.id)));
  }, [problems, persistAcks]);

  const openInTopology = useCallback(
    (nodeId?: string) => {
      setViewMode('topology');
      // Store, not a window event: TopologyCanvas isn't mounted yet during the
      // workspace switch, so an event fired here would be lost (BUG-08).
      if (nodeId) useUiStore.getState().setFocusNode(nodeId);
    },
    [setViewMode],
  );

  if (!projectId) {
    return (
      <div className="absolute inset-0">
        <WorkspaceEmptyState
          icon={ShieldCheck}
          title="No project open"
          hint="Open a project from the Projects portal to see its network health and problems."
        />
      </div>
    );
  }

  return (
    <div className="absolute inset-0 flex bg-surface" role="region" aria-label="Problem Center">
      {/* Left: problem list. This column's own background/border-r bleed to
          x=0 (AppShell); its content gets pl-[116px] — 16px past the
          floating rail's x=100 right edge — instead so the header controls
          and table rows never render under the rail. No max-w cap here:
          Surya reported the previous max-w-[1400px] leaving empty margins on
          both sides instead of hugging the viewport edges. */}
      <div className="flex min-w-0 flex-1 flex-col border-r border-fg/10">
        {/* Header — no page title: the Problems tab in TopBar's sub-nav
            already carries this page's identity, so an in-page h1 was a
            duplicate (root gets aria-label="Problem Center" above instead). */}
        <div className="flex flex-col gap-4 border-b border-fg/10 bg-panel pt-5 pr-6 pb-4 pl-[116px]">
          <div className="flex items-center justify-end gap-3">
            <label className="flex items-center gap-2 rounded-lg border border-fg/10 bg-recess/30 px-2.5 py-1.5">
              <Search className="h-4 w-4 text-fg/40" aria-hidden />
              <input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search nodes, problems…"
                aria-label="Search problems"
                className="w-56 bg-transparent text-sm text-fg/85 placeholder:text-fg/35 focus:outline-none"
              />
            </label>
            <button
              onClick={ackAll}
              disabled={counts.all === 0}
              className="rounded-lg border border-fg/10 px-4 py-1.5 text-sm font-medium text-fg/85 transition-colors hover:bg-fg/5 disabled:opacity-40"
            >
              Acknowledge all
            </button>
          </div>
          {/* Filter chips */}
          <div className="flex items-center gap-2" role="tablist" aria-label="Filter by severity">
            <FilterChip active={filter === 'all'} onClick={() => setFilter('all')} label="All" count={counts.all} />
            <FilterChip
              active={filter === 'critical'}
              onClick={() => setFilter('critical')}
              label="Critical"
              count={counts.critical}
              icon={CircleAlert}
              tone="text-danger"
            />
            <FilterChip
              active={filter === 'warning'}
              onClick={() => setFilter('warning')}
              label="Warning"
              count={counts.warning}
              icon={AlertTriangle}
              tone="text-warning"
            />
            <FilterChip
              active={filter === 'info'}
              onClick={() => setFilter('info')}
              label="Info"
              count={counts.info}
              icon={Info}
              tone="text-accent"
            />
          </div>
        </div>

        {/* Table / empty */}
        <div className="ng-scroll min-h-0 flex-1 overflow-auto pt-6 pr-6 pb-6 pl-[116px]">
          {isLoading ? (
              <p className="p-4 text-sm text-fg/40">Deriving problems…</p>
            ) : counts.all === 0 ? (
              <div className="grid h-full place-items-center text-center">
                <div className="max-w-sm space-y-2">
                  <ShieldCheck className="mx-auto h-9 w-9 text-success" aria-hidden />
                  <p className="text-sm text-fg/70">No problems detected</p>
                  <p className="text-xs leading-relaxed text-fg/40">
                    Every link is up and every device is addressed. New faults appear here as soon as the topology changes.
                  </p>
                </div>
              </div>
            ) : visible.length === 0 ? (
              <p className="p-4 text-sm text-fg/40">No problem matches the current filter.</p>
            ) : (
              <div className="overflow-hidden rounded-lg border border-fg/10">
                <table className="w-full border-collapse text-left">
                  <thead>
                    <tr className="border-b border-fg/10 bg-recess/30 text-[12px] text-fg/55">
                      <th className="w-10 px-4 py-2 font-normal" aria-label="Severity" />
                      <th className="px-4 py-2 font-normal">Problem</th>
                      <th className="px-4 py-2 font-normal">Affected node</th>
                      <th className="px-4 py-2 font-normal">Detected</th>
                      <th className="px-4 py-2 font-normal">Status</th>
                    </tr>
                  </thead>
                  <tbody className="font-mono text-[12px]">
                    {visible.map((p) => (
                      <ProblemRow
                        key={p.id}
                        problem={p}
                        active={p.id === selected?.id}
                        acked={acks.has(p.id)}
                        onSelect={() => setSelectedId(p.id)}
                      />
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>

        {/* Right: inspector */}
        <aside className="flex w-[360px] shrink-0 flex-col bg-panel">
          {selected ? (
            <Inspector
              problem={selected}
              acked={acks.has(selected.id)}
              onAck={() => ack(selected.id)}
              onOpenTopology={() => openInTopology(selected.nodeId)}
            />
          ) : (
            <div className="grid h-full place-items-center p-6 text-center">
              <div className="max-w-[15rem] space-y-2 text-fg/40">
                <ShieldCheck className="mx-auto h-8 w-8" aria-hidden />
                <p className="text-xs leading-relaxed">Select a problem to see its evidence and suggested actions.</p>
              </div>
            </div>
          )}
        </aside>
    </div>
  );
}

function FilterChip({
  active,
  onClick,
  label,
  count,
  icon: Icon,
  tone,
}: {
  active: boolean;
  onClick: () => void;
  label: string;
  count: number;
  icon?: LucideIcon;
  tone?: string;
}) {
  return (
    <button
      role="tab"
      aria-selected={active}
      onClick={onClick}
      className={cn(
        'flex items-center gap-2 rounded-full border px-3 py-1 text-[13px] transition-colors',
        active
          ? 'border-accent bg-accent/10 text-fg'
          : 'border-fg/10 text-fg/55 hover:bg-fg/5 hover:text-fg/85',
      )}
    >
      {Icon && <Icon className={cn('h-4 w-4', tone)} aria-hidden />}
      <span>{label}</span>
      <span className="rounded bg-fg/10 px-1.5 text-[11px] tabular-nums text-fg/70">{count}</span>
    </button>
  );
}

function ProblemRow({
  problem,
  active,
  acked,
  onSelect,
}: {
  problem: Problem;
  active: boolean;
  acked: boolean;
  onSelect: () => void;
}) {
  const { icon: Icon, tone } = SEV_META[problem.severity];
  return (
    <tr
      onClick={onSelect}
      className={cn(
        'relative cursor-pointer border-b border-fg/10 transition-colors last:border-b-0',
        active ? 'bg-fg/8' : 'hover:bg-fg/5',
        acked && 'opacity-55',
      )}
    >
      {/* Active accent bar — an absolutely-positioned span INSIDE the first cell,
          never its own <td>. A sibling <td> shifted every column one to the
          right (BUG-06); the icon/problem/node/detected mapping now lines up. */}
      <td className="relative px-4 py-2.5">
        {active && (
          <span className="absolute inset-y-0 left-0 w-1 rounded-r bg-accent" aria-hidden />
        )}
        <Icon className={cn('h-[18px] w-[18px]', tone)} aria-hidden />
      </td>
      <td className="px-4 py-2.5 font-sans text-[13px] text-fg">{problem.title}</td>
      <td className="px-4 py-2.5 text-fg/60">{problem.node}</td>
      <td className="px-4 py-2.5 text-fg/45">{acked ? 'acknowledged' : DETECTED_LABEL[problem.severity]}</td>
      <td className="px-4 py-2.5">
        {acked ? (
          <span className="inline-flex items-center gap-1 text-[11px] text-fg/45">
            <Check className="h-3.5 w-3.5" aria-hidden /> Acknowledged
          </span>
        ) : (
          <span className="inline-flex items-center rounded border border-fg/10 bg-recess/40 px-2 py-0.5 text-[11px] text-fg/80">
            Open
          </span>
        )}
      </td>
    </tr>
  );
}

function Inspector({
  problem,
  acked,
  onAck,
  onOpenTopology,
}: {
  problem: Problem;
  acked: boolean;
  onAck: () => void;
  onOpenTopology: () => void;
}) {
  const { icon: Icon, tone, label } = SEV_META[problem.severity];
  const bannerTone =
    problem.severity === 'critical' ? 'bg-danger' : problem.severity === 'warning' ? 'bg-warning' : 'bg-accent';
  return (
    <>
      <div className={cn('h-1.5 w-full shrink-0', bannerTone)} aria-hidden />
      <div className="ng-scroll flex min-h-0 flex-1 flex-col overflow-y-auto p-6">
        <div className="mb-2 flex items-center gap-2">
          <Icon className={cn('h-5 w-5', tone)} aria-hidden />
          <span className={cn('font-mono text-[12px] uppercase tracking-wider', tone)}>{label} event</span>
        </div>
        <h2 className="mb-1 font-display text-xl font-semibold text-fg">{problem.title}</h2>
        <div className="mb-5 font-mono text-[12px] text-fg/55">{problem.node}</div>
        <p className="mb-5 text-[13px] leading-relaxed text-fg/70">{problem.detail}</p>

        <div className="mb-5">
          <h3 className="mb-2 text-[13px] font-medium text-fg/85">Evidence</h3>
          <pre className="ng-scroll overflow-x-auto rounded-lg border border-fg/10 bg-recess/50 p-3 font-mono text-[11px] leading-relaxed text-fg/70">
            {problem.evidence.join('\n')}
          </pre>
        </div>

        <div className="mb-auto rounded-lg border border-fg/10 bg-recess/30 p-4">
          <div className="mb-2 flex items-center gap-2">
            <Wrench className="h-[18px] w-[18px] text-accent" aria-hidden />
            <h3 className="text-[13px] font-medium text-fg">Suggested Actions</h3>
          </div>
          <ul className="list-inside list-disc space-y-1 text-[13px] text-fg/70">
            {problem.actions.map((a, i) => (
              <li key={i}>{a}</li>
            ))}
          </ul>
        </div>

        <div className="mt-6 flex flex-col gap-2">
          <button
            onClick={onOpenTopology}
            className="flex w-full items-center justify-center gap-2 rounded-lg border border-fg/10 px-4 py-2 text-[13px] font-medium text-fg/85 transition-colors hover:bg-fg/5"
          >
            <Network className="h-[18px] w-[18px]" aria-hidden />
            Open in Topology
          </button>
          <button
            onClick={onAck}
            disabled={acked}
            className="w-full rounded-lg bg-accent px-4 py-2 text-[13px] font-medium text-accent-fg transition-colors hover:bg-accent-soft disabled:opacity-40"
          >
            {acked ? 'Acknowledged' : 'Acknowledge'}
          </button>
        </div>
      </div>
    </>
  );
}
