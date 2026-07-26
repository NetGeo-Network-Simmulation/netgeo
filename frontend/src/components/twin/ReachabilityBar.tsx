/**
 * ReachabilityBar — bottom-center "can src reach dst?" query (NG-TW-02).
 * Runs POST /projects/{id}/reachability on an isolated lab and shows the verdict
 * (reachable badge + hop count from the traceroute path + avg RTT).
 */
import { useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { Loader2, Radar, CheckCircle2, XCircle } from 'lucide-react';
import { twinApi, type ApiError } from '@/api/client';
import { useUiStore } from '@/store/uiStore';
import type { NodeModel } from '@/api/types';
import { cn } from '@/lib/cn';
import { zc } from '@/theme/z';
import { Select } from '@/components/ui/Select';

export function ReachabilityBar({ nodes }: { nodes: NodeModel[] }) {
  const projectId = useUiStore((s) => s.projectId);
  const [src, setSrc] = useState('');
  const [dst, setDst] = useState('');

  const q = useMutation({
    mutationFn: () => twinApi.reachability(projectId!, src, dst),
  });
  const err = q.error as ApiError | null;
  const res = q.data;

  const canRun = Boolean(projectId && src.trim() && dst.trim() && !q.isPending);
  const run = () => {
    if (canRun) q.mutate();
  };

  return (
    <div className={cn('pointer-events-none absolute bottom-4 left-0 right-[360px] flex justify-center px-4', zc.workspace)}>
      <div className="glass pointer-events-auto flex items-center gap-2 rounded-full border border-fg/10 px-3 py-2 shadow-glass">
        <span className="pl-1 text-xs text-fg/50">Can</span>
        <Select
          value={src}
          onChange={setSrc}
          aria-label="Source device"
          placeholder="source…"
          options={nodes.map((n) => ({ value: n.name, label: n.name }))}
          className="max-w-[130px]"
        />
        <span className="text-xs text-fg/50">reach</span>
        <input
          value={dst}
          onChange={(e) => setDst(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && run()}
          list="twin-reach-dst"
          placeholder="device or IP"
          aria-label="Destination device or IP"
          className="w-[140px] rounded-md border border-fg/10 bg-fg/5 px-2 py-1 text-xs text-fg/85 outline-none placeholder:text-fg/30 focus:border-accent/60"
        />
        <datalist id="twin-reach-dst">
          {nodes.map((n) => (
            <option key={n.id} value={n.name} />
          ))}
        </datalist>
        <button
          onClick={run}
          disabled={!canRun}
          className="flex items-center gap-1.5 rounded-md bg-accent px-3 py-1.5 text-xs font-semibold text-accent-fg transition-colors hover:bg-accent-soft disabled:opacity-40"
        >
          {q.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Radar className="h-3.5 w-3.5" />}
          Analyze
        </button>

        {err && <span className="pr-1 text-xs text-danger">{err.message || 'Query failed'}</span>}
        {res && !err && (
          <span
            className={cn(
              'flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium',
              res.reachable ? 'bg-success/15 text-success' : 'bg-danger/15 text-danger',
            )}
          >
            {res.reachable ? <CheckCircle2 className="h-3.5 w-3.5" /> : <XCircle className="h-3.5 w-3.5" />}
            {res.reachable ? 'Reachable' : 'Unreachable'}
            {res.reachable && (
              <span className="text-fg/50">
                · {res.path.length} hop{res.path.length === 1 ? '' : 's'}
                {res.rtt_avg_ms != null && ` · ${res.rtt_avg_ms} ms`}
              </span>
            )}
          </span>
        )}
      </div>
    </div>
  );
}
