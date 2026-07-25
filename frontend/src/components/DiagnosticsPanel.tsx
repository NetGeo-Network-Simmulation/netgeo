/**
 * DiagnosticsPanel — packet-level tooling over the live lab (NG-CAP-03):
 *   - Ping / Traceroute between simulated devices (dual-stack, IPv4 + IPv6)
 *   - Packet capture inspector per link (built-in "Wireshark lite")
 *   - Per-node live tables: RIB v4/v6, ARP/ND caches, MAC table, OSPF/BGP
 *     adjacency state as colored badges (NG-TD-04)
 *
 * Design: clean tab strip, monospace results, no clutter — every action is
 * one click away, matching the "3 clicks to any feature" rule.
 */
import { useMemo, useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { Activity, ArrowRight, Radio, Route, Table2 } from 'lucide-react';
import {
  labApi,
  type CaptureRecord,
  type PingResult,
  type TracerouteResult,
} from '@/api/client';
import { useTopologyStore } from '@/store/topologyStore';
import { useUiStore } from '@/store/uiStore';
import { cn } from '@/lib/cn';

type Tab = 'ping' | 'trace' | 'capture' | 'tables';

export function DiagnosticsPanel() {
  const [tab, setTab] = useState<Tab>('ping');
  return (
    <div className="flex h-full flex-col text-[13px]">
      <div className="flex items-center gap-1 border-b border-fg/10 px-2 py-1.5">
        <TabButton active={tab === 'ping'} onClick={() => setTab('ping')} icon={Activity}>
          Ping
        </TabButton>
        <TabButton active={tab === 'trace'} onClick={() => setTab('trace')} icon={Route}>
          Traceroute
        </TabButton>
        <TabButton active={tab === 'capture'} onClick={() => setTab('capture')} icon={Radio}>
          Capture
        </TabButton>
        <TabButton active={tab === 'tables'} onClick={() => setTab('tables')} icon={Table2}>
          Tables
        </TabButton>
      </div>
      <div className="ng-scroll flex-1 overflow-auto p-3">
        {tab === 'ping' && <PingTool />}
        {tab === 'trace' && <TraceTool />}
        {tab === 'capture' && <CaptureTool />}
        {tab === 'tables' && <TablesTool />}
      </div>
    </div>
  );
}

function TabButton({
  active,
  onClick,
  icon: Icon,
  children,
}: {
  active: boolean;
  onClick: () => void;
  icon: typeof Activity;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        'flex items-center gap-1.5 rounded-md px-2.5 py-1 text-xs transition-colors',
        active ? 'bg-accent text-accent-fg' : 'text-fg/55 hover:bg-fg/8 hover:text-fg/85',
      )}
    >
      <Icon className="h-3.5 w-3.5" />
      {children}
    </button>
  );
}

/* ------------------------------ shared bits ------------------------------- */

function useNodeOptions() {
  const nodes = useTopologyStore((s) => s.nodes);
  return useMemo(
    () =>
      Array.from(nodes.values())
        .map((n) => ({ id: n.id, name: n.name, kind: n.kind }))
        .sort((a, b) => a.name.localeCompare(b.name)),
    [nodes],
  );
}

function SrcDstRow({
  src,
  setSrc,
  dst,
  setDst,
  onRun,
  running,
  runLabel,
}: {
  src: string;
  setSrc: (v: string) => void;
  dst: string;
  setDst: (v: string) => void;
  onRun: () => void;
  running: boolean;
  runLabel: string;
}) {
  const options = useNodeOptions();
  return (
    <div className="flex flex-wrap items-center gap-2">
      <select
        value={src}
        onChange={(e) => setSrc(e.target.value)}
        aria-label="Source device"
        className="min-w-[130px] rounded-md border border-fg/10 bg-fg/5 px-2 py-1.5 text-xs outline-none focus:border-accent"
      >
        <option value="">source device…</option>
        {options.map((o) => (
          <option key={o.id} value={o.id}>
            {o.name} ({o.kind})
          </option>
        ))}
      </select>
      <ArrowRight className="h-3.5 w-3.5 shrink-0 text-fg/35" />
      <input
        value={dst}
        onChange={(e) => setDst(e.target.value)}
        list="ng-diag-dst-options"
        placeholder="IPv4/IPv6 or device"
        aria-label="Destination"
        spellCheck={false}
        className="w-[190px] rounded-md border border-fg/10 bg-fg/5 px-2 py-1.5 text-xs outline-none placeholder:text-fg/30 focus:border-accent"
      />
      <datalist id="ng-diag-dst-options">
        {options.map((o) => (
          <option key={o.id} value={o.name} />
        ))}
      </datalist>
      <button
        onClick={onRun}
        disabled={!src || !dst || running}
        className={cn(
          'rounded-md px-3 py-1.5 text-xs font-medium transition-colors',
          !src || !dst || running
            ? 'bg-fg/10 text-fg/35'
            : 'bg-accent text-accent-fg hover:bg-accent/85',
        )}
      >
        {running ? 'Running…' : runLabel}
      </button>
    </div>
  );
}

function ErrorNote({ message }: { message?: string }) {
  if (!message) return null;
  return <p className="mt-3 rounded-md bg-danger/10 px-3 py-2 text-xs text-danger">{message}</p>;
}

/* --------------------------------- Ping ----------------------------------- */

function PingTool() {
  const projectId = useUiStore((s) => s.projectId);
  const [src, setSrc] = useState('');
  const [dst, setDst] = useState('');

  const m = useMutation<PingResult, { message?: string }>({
    mutationFn: () => labApi.ping(projectId!, src, dst.trim(), 5),
  });

  return (
    <div>
      <SrcDstRow
        src={src}
        setSrc={setSrc}
        dst={dst}
        setDst={setDst}
        onRun={() => m.mutate()}
        running={m.isPending}
        runLabel="Ping"
      />
      <ErrorNote message={m.error?.message} />
      {m.data && (
        <div className="mt-3 rounded-lg border border-fg/10 bg-recess/25 p-3 font-mono text-xs leading-relaxed">
          <p className="text-fg/60">
            {m.data.src} → {m.data.dst}
          </p>
          {m.data.rtts_ms.map((rtt, i) => (
            <p key={i} className="text-emerald-300/90">
              reply seq={i + 1} time={rtt} ms
            </p>
          ))}
          {m.data.errors.map((e, i) => (
            <p key={`e${i}`} className="text-warning">
              {e}
            </p>
          ))}
          <p className={cn('mt-1', m.data.loss_pct === 0 ? 'text-success' : 'text-warning')}>
            {m.data.received}/{m.data.sent} received, {m.data.loss_pct}% loss
            {m.data.avg_ms !== null &&
              ` — rtt min/avg/max ${m.data.min_ms}/${m.data.avg_ms}/${m.data.max_ms} ms`}
          </p>
        </div>
      )}
    </div>
  );
}

/* ------------------------------ Traceroute --------------------------------- */

function TraceTool() {
  const projectId = useUiStore((s) => s.projectId);
  const [src, setSrc] = useState('');
  const [dst, setDst] = useState('');

  const m = useMutation<TracerouteResult, { message?: string }>({
    mutationFn: () => labApi.traceroute(projectId!, src, dst.trim()),
  });

  return (
    <div>
      <SrcDstRow
        src={src}
        setSrc={setSrc}
        dst={dst}
        setDst={setDst}
        onRun={() => m.mutate()}
        running={m.isPending}
        runLabel="Trace"
      />
      <ErrorNote message={m.error?.message} />
      {m.data && (
        <div className="mt-3 rounded-lg border border-fg/10 bg-recess/25 p-3 font-mono text-xs leading-relaxed">
          <p className="text-fg/60">
            {m.data.src} → {m.data.dst}
          </p>
          {m.data.hops.map((h) => (
            <p key={h.hop}>
              <span className="text-fg/40">{String(h.hop).padStart(2)} </span>
              <span className={h.address ? 'text-emerald-300/90' : 'text-fg/30'}>
                {h.address ?? '*'}
              </span>
              {h.rtt_ms !== null && <span className="text-fg/50"> {h.rtt_ms} ms</span>}
            </p>
          ))}
          <p className={cn('mt-1', m.data.reached ? 'text-success' : 'text-warning')}>
            {m.data.reached ? 'Destination reached.' : 'Destination not reached.'}
          </p>
        </div>
      )}
    </div>
  );
}

/* -------------------------------- Capture ---------------------------------- */

function CaptureTool() {
  const projectId = useUiStore((s) => s.projectId);
  const links = useTopologyStore((s) => s.links);
  const [linkId, setLinkId] = useState<string>('');
  const [selected, setSelected] = useState<CaptureRecord | null>(null);
  const [filterDraft, setFilterDraft] = useState('');
  const [filter, setFilter] = useState('');
  const [view, setView] = useState<'frames' | 'conversations'>('frames');

  const q = useQuery({
    queryKey: ['captures', projectId, linkId, filter],
    queryFn: () => labApi.captures(projectId!, linkId || undefined, 300, filter),
    enabled: !!projectId,
    refetchInterval: 4000,
    retry: false,
  });

  const linkOptions = useMemo(() => Array.from(links.values()), [links]);
  const filterError =
    q.error && filter ? ((q.error as { message?: string }).message ?? 'bad filter') : null;

  return (
    <div className="flex h-full flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2">
        <select
          value={linkId}
          onChange={(e) => setLinkId(e.target.value)}
          aria-label="Capture link filter"
          className="rounded-md border border-fg/10 bg-fg/5 px-2 py-1.5 text-xs outline-none focus:border-accent"
        >
          <option value="">all links</option>
          {linkOptions.map((l) => (
            <option key={l.id} value={l.id}>
              {l.id.slice(0, 8)} ({l.type})
            </option>
          ))}
        </select>
        {/* Display filter mini-language (NG-CAP-02) */}
        <input
          value={filterDraft}
          onChange={(e) => setFilterDraft(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && setFilter(filterDraft.trim())}
          placeholder="filter: icmp && ip.addr==10.0.0.1"
          aria-label="Display filter"
          spellCheck={false}
          className={cn(
            'w-[240px] rounded-md border bg-fg/5 px-2 py-1.5 font-mono text-[11px] outline-none placeholder:text-fg/25',
            filterError ? 'border-danger/60' : 'border-fg/10 focus:border-accent',
          )}
        />
        <button
          onClick={() => setFilter(filterDraft.trim())}
          className="rounded-md bg-fg/10 px-2 py-1.5 text-xs text-fg/70 hover:bg-fg/15"
        >
          Apply
        </button>
        <button
          onClick={() => setView(view === 'frames' ? 'conversations' : 'frames')}
          className="rounded-md bg-fg/10 px-2 py-1.5 text-xs text-fg/70 hover:bg-fg/15"
          title="Toggle conversation view"
        >
          {view === 'frames' ? 'Conversations' : 'Frames'}
        </button>
        <button
          onClick={() => void labApi.downloadPcapng(projectId!, linkId || undefined)}
          className="rounded-md bg-accent/80 px-2 py-1.5 text-xs font-medium text-accent-fg hover:bg-accent"
          title="Download as .pcapng — opens in Wireshark"
        >
          .pcapng
        </button>
        <span className="text-xs text-fg/40">
          {q.data?.length ?? 0} frames · auto-refresh
        </span>
      </div>
      {filterError && (
        <p className="rounded-md bg-danger/10 px-2 py-1 text-[11px] text-danger">{filterError}</p>
      )}
      {view === 'conversations' && <ConversationView records={q.data ?? []} />}

      {view === 'frames' && (
      <div className="min-h-0 flex-1 overflow-auto rounded-lg border border-fg/10 bg-recess/25 font-mono text-[11px] leading-relaxed">
        {(q.data ?? []).length === 0 ? (
          <p className="p-3 text-fg/35">
            No frames captured yet — run a ping or let protocols talk.
          </p>
        ) : (
          <table className="w-full">
            <tbody>
              {(q.data ?? [])
                .slice()
                .reverse()
                .map((r) => (
                  <tr
                    key={`${r.frame_id}-${r.t}-${r.dir}-${r.iface}`}
                    onClick={() => setSelected(r)}
                    className={cn(
                      'cursor-pointer border-b border-fg/5 hover:bg-fg/8',
                      r.dir === 'drop' && 'text-danger/80',
                    )}
                  >
                    <td className="whitespace-nowrap px-2 py-0.5 text-fg/40">
                      {r.t.toFixed(6)}
                    </td>
                    <td className="px-1 py-0.5 uppercase text-fg/50">{r.dir}</td>
                    <td className="whitespace-nowrap px-1 py-0.5 text-fg/50">{r.iface}</td>
                    <td className="truncate px-2 py-0.5 text-emerald-200/85">{r.info}</td>
                  </tr>
                ))}
            </tbody>
          </table>
        )}
      </div>
      )}

      {view === 'frames' && selected && (
        <div className="max-h-[130px] overflow-auto rounded-lg border border-fg/10 bg-recess/40 p-2 font-mono text-[11px] text-sky-200/85">
          {Object.entries(selected.layers).map(([layer, fields]) => (
            <p key={layer}>
              <span className="text-fg/50">{layer}:</span> {JSON.stringify(fields)}
            </p>
          ))}
        </div>
      )}
    </div>
  );
}

/** Conversation view (NG-CAP-02): traffic grouped per endpoint pair. */
function ConversationView({ records }: { records: CaptureRecord[] }) {
  const rows = useMemo(() => {
    const acc = new Map<
      string,
      { a: string; b: string; proto: string; frames: number; bytes: number }
    >();
    for (const r of records) {
      const net = (r.layers.ipv4 ?? r.layers.ipv6) as
        | { src?: string; dst?: string }
        | undefined;
      const src = net?.src ?? (r.layers.eth as { src?: string } | undefined)?.src ?? '?';
      const dst = net?.dst ?? (r.layers.eth as { dst?: string } | undefined)?.dst ?? '?';
      const proto =
        ['icmp', 'icmpv6', 'tcp', 'udp', 'arp', 'stp', 'dns', 'dhcp']
          .find((k) => k in r.layers) ?? 'other';
      const [a = '?', b = '?'] = [src, dst].sort();
      const key = `${a}|${b}|${proto}`;
      const cur = acc.get(key) ?? { a, b, proto, frames: 0, bytes: 0 };
      cur.frames += 1;
      cur.bytes += r.size;
      acc.set(key, cur);
    }
    return Array.from(acc.values()).sort((x, y) => y.bytes - x.bytes);
  }, [records]);

  return (
    <div className="min-h-0 flex-1 overflow-auto rounded-lg border border-fg/10 bg-recess/25 font-mono text-[11px] leading-relaxed">
      {rows.length === 0 ? (
        <p className="p-3 text-fg/35">No conversations in the current capture.</p>
      ) : (
        <table className="w-full">
          <thead>
            <tr className="border-b border-fg/10 text-left text-fg/40">
              <th className="px-2 py-1 font-normal">Endpoint A</th>
              <th className="px-2 py-1 font-normal">Endpoint B</th>
              <th className="px-2 py-1 font-normal">Proto</th>
              <th className="px-2 py-1 text-right font-normal">Frames</th>
              <th className="px-2 py-1 text-right font-normal">Bytes</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((c) => (
              <tr key={`${c.a}|${c.b}|${c.proto}`} className="border-b border-fg/5">
                <td className="px-2 py-0.5 text-emerald-200/85">{c.a}</td>
                <td className="px-2 py-0.5 text-emerald-200/85">{c.b}</td>
                <td className="px-2 py-0.5 uppercase text-fg/55">{c.proto}</td>
                <td className="px-2 py-0.5 text-right text-fg/60">{c.frames}</td>
                <td className="px-2 py-0.5 text-right text-fg/60">{c.bytes}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

/* ------------------------- Live tables (NG-TD-04) --------------------------- */

interface NodeTables {
  node: string;
  kind: string;
  interfaces: {
    name: string;
    mac: string;
    ips: string[];
    ips6?: string[];
    link_local?: string;
    up: boolean;
    stp?: { state: string; role: string };
  }[];
  arp?: { ip: string; mac: string; iface: string }[];
  neighbors6?: { ip: string; mac: string; iface: string }[];
  routes?: RouteRow[];
  routes6?: RouteRow[];
  mac_table?: { vlan: number; mac: string; port: string }[];
  ospf_neighbors?: { router_id: string; state: string; ip: string; iface: string }[];
  bgp_peers?: { neighbor: string; remote_as: number; state: string; prefixes_received: number }[];
}

interface RouteRow {
  prefix: string;
  next_hop: string | null;
  iface: string | null;
  source: string;
  metric: number;
  ad: number;
}

function TablesTool() {
  const projectId = useUiStore((s) => s.projectId);
  const options = useNodeOptions();
  const [nodeRef, setNodeRef] = useState('');

  const q = useQuery({
    queryKey: ['lab-tables', projectId, nodeRef],
    queryFn: () => labApi.tables(projectId!, nodeRef) as Promise<unknown> as Promise<NodeTables>,
    enabled: !!projectId && !!nodeRef,
    refetchInterval: 5000,
  });

  const t = q.data;
  return (
    <div className="flex flex-col gap-3">
      <select
        value={nodeRef}
        onChange={(e) => setNodeRef(e.target.value)}
        aria-label="Device tables"
        className="w-fit min-w-[180px] rounded-md border border-fg/10 bg-fg/5 px-2 py-1.5 text-xs outline-none focus:border-accent"
      >
        <option value="">select device…</option>
        {options.map((o) => (
          <option key={o.id} value={o.id}>
            {o.name} ({o.kind})
          </option>
        ))}
      </select>

      {t && (
        <div className="flex flex-col gap-3 font-mono text-[11px] leading-relaxed">
          {/* Interfaces with live badges */}
          <TableCard title="Interfaces">
            {t.interfaces.map((i) => (
              <p key={i.name} className="flex flex-wrap items-center gap-1.5">
                <span className="text-fg/75">{i.name}</span>
                <Badge tone={i.up ? 'ok' : 'bad'}>{i.up ? 'up' : 'down'}</Badge>
                {i.stp && i.stp.state !== 'forwarding' && (
                  <Badge tone="warn">STP {i.stp.state}</Badge>
                )}
                <span className="text-fg/45">
                  {[...i.ips, ...(i.ips6 ?? [])].join(', ') || i.link_local || '—'}
                </span>
              </p>
            ))}
          </TableCard>

          {(t.routes?.length ?? 0) > 0 && (
            <RouteTable title="IPv4 routes" rows={t.routes!} />
          )}
          {(t.routes6?.length ?? 0) > 0 && (
            <RouteTable title="IPv6 routes" rows={t.routes6!} />
          )}

          {(t.arp?.length ?? 0) > 0 && (
            <TableCard title="ARP cache">
              {t.arp!.map((a) => (
                <p key={a.ip} className="text-fg/60">
                  {a.ip} <span className="text-fg/35">→</span> {a.mac}{' '}
                  <span className="text-fg/35">({a.iface})</span>
                </p>
              ))}
            </TableCard>
          )}
          {(t.neighbors6?.length ?? 0) > 0 && (
            <TableCard title="IPv6 neighbors (NDP)">
              {t.neighbors6!.map((a) => (
                <p key={a.ip} className="text-fg/60">
                  {a.ip} <span className="text-fg/35">→</span> {a.mac}{' '}
                  <span className="text-fg/35">({a.iface})</span>
                </p>
              ))}
            </TableCard>
          )}

          {(t.mac_table?.length ?? 0) > 0 && (
            <TableCard title="MAC address table">
              {t.mac_table!.map((m, i) => (
                <p key={i} className="text-fg/60">
                  vlan {m.vlan} · {m.mac} → {m.port}
                </p>
              ))}
            </TableCard>
          )}

          {(t.ospf_neighbors?.length ?? 0) > 0 && (
            <TableCard title="OSPF neighbors">
              {t.ospf_neighbors!.map((n) => (
                <p key={n.router_id} className="flex items-center gap-1.5 text-fg/60">
                  {n.router_id} via {n.iface}
                  <Badge tone={n.state.toLowerCase() === 'full' ? 'ok' : 'warn'}>{n.state}</Badge>
                </p>
              ))}
            </TableCard>
          )}
          {(t.bgp_peers?.length ?? 0) > 0 && (
            <TableCard title="BGP peers">
              {t.bgp_peers!.map((p) => (
                <p key={p.neighbor} className="flex items-center gap-1.5 text-fg/60">
                  {p.neighbor} (AS{p.remote_as})
                  <Badge tone={p.state.toLowerCase() === 'established' ? 'ok' : 'warn'}>
                    {p.state}
                  </Badge>
                  <span className="text-fg/35">{p.prefixes_received} pfx</span>
                </p>
              ))}
            </TableCard>
          )}
        </div>
      )}
    </div>
  );
}

function RouteTable({ title, rows }: { title: string; rows: RouteRow[] }) {
  return (
    <TableCard title={title}>
      {rows.map((r, i) => (
        <p key={i} className="text-fg/60">
          <span className="text-fg/40">{sourceCode(r.source)}</span> {r.prefix}{' '}
          <span className="text-fg/35">[{r.ad}/{r.metric}]</span>{' '}
          {r.next_hop ? `via ${r.next_hop}` : 'directly connected'}
          {r.iface ? <span className="text-fg/35">, {r.iface}</span> : null}
        </p>
      ))}
    </TableCard>
  );
}

const sourceCode = (s: string) =>
  ({ connected: 'C', static: 'S', ospf: 'O', ebgp: 'B', ibgp: 'B', rip: 'R' })[s] ?? '?';

function TableCard({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-fg/10 bg-recess/25 p-3">
      <p className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-fg/40">
        {title}
      </p>
      {children}
    </div>
  );
}

function Badge({ tone, children }: { tone: 'ok' | 'warn' | 'bad'; children: React.ReactNode }) {
  return (
    <span
      className={cn(
        'rounded-full px-1.5 py-0 text-[10px] font-medium',
        tone === 'ok' && 'bg-success/15 text-success',
        tone === 'warn' && 'bg-warning/15 text-warning',
        tone === 'bad' && 'bg-danger/15 text-danger',
      )}
    >
      {children}
    </span>
  );
}
