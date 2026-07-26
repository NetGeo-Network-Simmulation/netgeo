/**
 * ConsolePanel — interactive device terminal bound to /ws/console/{node_id}.
 * Renders the streamed output ring buffer and a command input, with a Cisco-
 * grade feel: dynamic per-device prompt (server-authoritative), command history
 * (↑/↓), history-based Tab completion, and `?` help. Inline config diff/export
 * (NG-CFG-01/03) surface straight from the terminal header.
 *
 * This stays a line-oriented terminal (not a full xterm) to protect the RAM
 * budget; the CLI grammar is line-oriented so xterm buys nothing here. Swap to
 * xterm.js only if raw TTY control is ever needed.
 */
import { useEffect, useRef, useState } from 'react';
import { Download, GitCompareArrows, TerminalSquare, X } from 'lucide-react';
import { useConsoleChannel } from '@/hooks/useConsoleChannel';
import { useTopologyStore } from '@/store/topologyStore';
import { useUiStore } from '@/store/uiStore';
import { configsApi, type ConfigDiff } from '@/api/client';
import { cn } from '@/lib/cn';
import { Select } from '@/components/ui/Select';

// Vendors the backend can render/diff to (mirrors configgen._TEMPLATE_MAP; the
// server is authoritative and 422s on anything it lacks a template for).
// "native" → each node's own NOS. ponytail: static list, resync if templates change.
const VENDORS = ['native', 'ios', 'junos', 'eos', 'routeros', 'vyos', 'frr', 'forgeos', 'sros', 'vrp'];
const VENDOR_OPTIONS = VENDORS.map((v) => ({ value: v, label: v }));

export function ConsolePanel() {
  const nodeId = useTopologyStore((s) => s.selectedNodeId);
  const nodes = useTopologyStore((s) => s.nodes);
  const projectId = useUiStore((s) => s.projectId);
  const node = nodeId ? nodes.get(nodeId) : null;

  const { lines, prompt, state, send } = useConsoleChannel(nodeId);
  const [cmd, setCmd] = useState('');
  const [history, setHistory] = useState<string[]>([]);
  const [histIdx, setHistIdx] = useState(-1); // -1 = composing a fresh line
  const [vendor, setVendor] = useState('native');
  const [diff, setDiff] = useState<ConfigDiff | null>(null);
  const [busy, setBusy] = useState<null | 'diff' | 'export'>(null);
  const [err, setErr] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  // A new device is a new session — drop history/diff/errors.
  useEffect(() => {
    setHistory([]);
    setHistIdx(-1);
    setDiff(null);
    setErr(null);
  }, [nodeId]);

  // Auto-scroll to bottom on new output.
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [lines.length]);

  if (!nodeId) {
    return (
      <div className="grid h-full place-items-center text-center text-fg/40">
        <div className="space-y-2">
          <TerminalSquare className="mx-auto h-8 w-8" />
          <p className="text-sm">No device console open</p>
          <p className="text-xs">Select a node, then open the Console tab in the drawer.</p>
        </div>
      </div>
    );
  }

  const run = (raw: string) => {
    const c = raw.trim();
    if (!c) return;
    send(c);
    setHistory((h) => (h[h.length - 1] === c ? h : [...h, c])); // skip dup of last
    setHistIdx(-1);
    setCmd('');
  };

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    run(cmd);
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'ArrowUp') {
      e.preventDefault();
      if (!history.length) return;
      const i = histIdx < 0 ? history.length - 1 : Math.max(0, histIdx - 1);
      setHistIdx(i);
      setCmd(history[i] ?? '');
    } else if (e.key === 'ArrowDown') {
      e.preventDefault();
      if (histIdx < 0) return;
      const i = histIdx + 1;
      if (i >= history.length) {
        setHistIdx(-1);
        setCmd('');
      } else {
        setHistIdx(i);
        setCmd(history[i] ?? '');
      }
    } else if (e.key === 'Tab') {
      // Readline-style completion from *history* only — no client-side grammar
      // parser (the server owns the CLI; `?` gives real context help).
      // ponytail: history-prefix complete; add a backend completion endpoint
      // only if true grammar-aware completion is ever needed.
      e.preventDefault();
      const matches = [...new Set(history.filter((h) => h.startsWith(cmd) && h !== cmd))];
      if (matches.length === 1) setCmd(matches[0] ?? '');
      else if (matches.length > 1) setCmd(longestCommonPrefix(matches));
    } else if (e.key === '?' && cmd === '') {
      // Cisco reflex: a bare `?` asks the device for help immediately.
      e.preventDefault();
      run('?');
    }
  };

  const showDiff = async () => {
    setBusy('diff');
    setErr(null);
    try {
      setDiff(await configsApi.diff(nodeId, vendor === 'native' ? undefined : vendor));
    } catch (e) {
      setErr((e as { message?: string })?.message ?? 'Diff failed');
    } finally {
      setBusy(null);
    }
  };

  const doExport = async () => {
    if (!projectId) return;
    setBusy('export');
    setErr(null);
    try {
      await configsApi.downloadProjectConfigs(projectId, vendor === 'native' ? undefined : vendor);
    } catch (e) {
      setErr((e as { message?: string })?.message ?? 'Export failed');
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="flex h-full flex-col bg-recess/40 font-mono text-[12.5px]">
      <div className="flex items-center gap-2 border-b border-fg/10 px-3 py-1 text-[11px] text-fg/50">
        <span className="text-fg/70">{node?.name ?? nodeId}</span>
        <span className={state === 'open' ? 'text-success' : 'text-warning'}>● {state}</span>

        <div className="ml-auto flex items-center gap-1.5">
          <label htmlFor="cfg-vendor" className="sr-only">
            Target vendor for diff/export
          </label>
          <Select
            id="cfg-vendor"
            aria-label="Target vendor for diff/export"
            value={vendor}
            onChange={setVendor}
            options={VENDOR_OPTIONS}
            className="w-24"
          />
          <button
            type="button"
            onClick={showDiff}
            disabled={busy !== null}
            aria-label="Show config diff for this device"
            className="flex h-7 items-center gap-1 rounded px-1.5 text-[11px] text-fg/70 hover:bg-fg/10 disabled:opacity-40"
          >
            <GitCompareArrows className="h-3.5 w-3.5" /> Diff
          </button>
          <button
            type="button"
            onClick={doExport}
            disabled={busy !== null || !projectId}
            aria-label="Export all project configs"
            className="flex h-7 items-center gap-1 rounded px-1.5 text-[11px] text-fg/70 hover:bg-fg/10 disabled:opacity-40"
          >
            <Download className="h-3.5 w-3.5" /> Export
          </button>
        </div>
      </div>

      {err && (
        <div role="alert" className="border-b border-danger/30 bg-danger/10 px-3 py-1 text-[11px] text-danger">
          {err}
        </div>
      )}

      {diff ? (
        <DiffView diff={diff} onClose={() => setDiff(null)} />
      ) : (
        <>
          <div
            ref={scrollRef}
            className="ng-scroll flex-1 overflow-auto px-3 py-2 leading-relaxed text-emerald-200/90"
          >
            {lines.length === 0 ? (
              <p className="text-fg/30">Connecting to {node?.nos.toUpperCase()} console…</p>
            ) : (
              lines.map((l, i) => (
                <div key={i} className="whitespace-pre-wrap break-words">
                  {l}
                </div>
              ))
            )}
          </div>

          <form onSubmit={onSubmit} className="flex items-center gap-2 border-t border-fg/10 px-3 py-2">
            <span className="shrink-0 text-accent-soft">{prompt || `${node?.name ?? 'device'}>`}</span>
            <input
              value={cmd}
              onChange={(e) => setCmd(e.target.value)}
              onKeyDown={onKeyDown}
              autoFocus
              spellCheck={false}
              autoComplete="off"
              aria-label="Console command input"
              className="w-full bg-transparent text-emerald-100 outline-none placeholder:text-fg/30"
              placeholder="↑ history · Tab complete · ? help"
            />
          </form>
        </>
      )}
    </div>
  );
}

/** Colored unified-diff view. The leading +/- char carries meaning too, so the
 *  diff stays legible without color (WCAG color-not-only). */
function DiffView({ diff, onClose }: { diff: ConfigDiff; onClose: () => void }) {
  const status = !diff.had_stored
    ? 'no stored config — all lines are new'
    : diff.changed
      ? 'changes pending'
      : 'no changes';
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center gap-2 border-b border-fg/10 px-3 py-1 text-[11px] text-fg/60">
        <span className="text-fg/70">diff · {diff.vendor}</span>
        <span className="text-fg/40">{status}</span>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close diff and return to console"
          className="ml-auto grid h-6 w-6 place-items-center rounded text-fg/60 hover:bg-fg/10"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>
      <div className="ng-scroll flex-1 overflow-auto bg-recess/40 px-3 py-2 leading-relaxed">
        {!diff.diff ? (
          <p className="text-fg/40">
            {diff.had_stored
              ? 'Stored config already matches the regenerated config.'
              : 'No stored config to compare against yet — generate one from the Config viewer first.'}
          </p>
        ) : (
          diff.diff.split('\n').map((line, i) => (
            <div key={i} className={cn('whitespace-pre-wrap break-words', diffLineClass(line))}>
              {line || ' '}
            </div>
          ))
        )}
      </div>
    </div>
  );
}

function diffLineClass(line: string): string {
  if (line.startsWith('+++') || line.startsWith('---')) return 'text-fg/50'; // file headers
  if (line.startsWith('@@')) return 'text-info'; // hunk header
  if (line.startsWith('+')) return 'bg-success/10 text-success';
  if (line.startsWith('-')) return 'bg-danger/10 text-danger';
  return 'text-fg/70';
}

/** Longest common prefix — used for multi-match Tab completion. */
function longestCommonPrefix(strs: string[]): string {
  let p = strs[0] ?? '';
  for (const s of strs) while (p && !s.startsWith(p)) p = p.slice(0, -1);
  return p;
}
