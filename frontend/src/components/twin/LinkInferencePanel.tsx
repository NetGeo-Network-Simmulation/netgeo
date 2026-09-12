/**
 * LinkInferencePanel — right inspector (~360px) listing proposed links derived
 * client-side (deriveProposals). Accept creates the link via the existing links
 * API; Reject dismisses it locally. "Infer all" runs the bulk, idempotent
 * backend endpoint. There is no per-proposal dry-run endpoint by design.
 */
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Check, X, Zap, Loader2, Link2 } from 'lucide-react';
import { linksApi, twinApi, type ApiError } from '@/api/client';
import { useUiStore } from '@/store/uiStore';
import { useTopologyStore } from '@/store/topologyStore';
import { zc } from '@/theme/z';
import type { LinkProposal } from './twinLogic';

export function LinkInferencePanel({
  proposals,
  resolved,
  onReject,
  onResolved,
}: {
  proposals: LinkProposal[];
  resolved: number;
  onReject: (id: string) => void;
  onResolved: () => void;
}) {
  const projectId = useUiStore((s) => s.projectId);
  const queryClient = useQueryClient();
  const total = resolved + proposals.length;

  // QA-visual #3: apply the created link to the shared topology store the
  // instant the backend confirms it, instead of only invalidating the query —
  // invalidate alone left the canvas/map waiting on a full topology refetch
  // before the new link became visible.
  const accept = useMutation({
    mutationFn: (p: LinkProposal) =>
      linksApi.create({ project_id: projectId!, a_iface: p.aIface.id, b_iface: p.bIface.id }),
    onSuccess: (link) => {
      useTopologyStore.getState().upsertLink(link);
      void queryClient.invalidateQueries({ queryKey: ['topology', projectId] });
      onResolved();
    },
  });

  const inferAll = useMutation({
    mutationFn: () => twinApi.inferLinks(projectId!),
    onSuccess: (links) => {
      for (const link of links) useTopologyStore.getState().upsertLink(link);
      void queryClient.invalidateQueries({ queryKey: ['topology', projectId] });
    },
  });

  const acceptErr = accept.error as ApiError | null;

  return (
    <aside
      aria-label="Link inference"
      className={`panel absolute right-0 top-0 ${zc.workspace} flex h-full w-[360px] max-w-[85vw] flex-col border-l border-fg/10 shadow-glass-lg`}
    >
      <header className="flex shrink-0 items-center justify-between border-b border-fg/10 px-4 py-3">
        <div className="flex items-center gap-2">
          <Link2 className="h-4 w-4 text-accent" />
          <h2 className="text-sm font-semibold text-fg/90">Link Inference</h2>
        </div>
        <span className="rounded-full bg-fg/10 px-2 py-0.5 text-[11px] font-medium text-fg/60">
          {resolved} of {total} resolved
        </span>
      </header>

      <div className="border-b border-fg/10 p-3">
        <button
          onClick={() => inferAll.mutate()}
          disabled={!projectId || inferAll.isPending}
          title="Wire every interface pair that shares a subnet (idempotent)"
          className="flex w-full items-center justify-center gap-1.5 rounded-md border border-fg/10 bg-fg/5 px-3 py-2 text-xs font-medium text-fg/80 transition-colors hover:border-accent/40 hover:text-fg disabled:opacity-40"
        >
          {inferAll.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Zap className="h-4 w-4" />}
          Infer all links
        </button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto p-3">
        {acceptErr && (
          <div className="mb-2 rounded-md border border-danger/40 bg-danger/10 px-3 py-2 text-xs text-danger">
            {acceptErr.message || 'Could not create link.'}
          </div>
        )}

        {proposals.length === 0 ? (
          <div className="grid h-full place-items-center text-center">
            <div className="text-fg/45">
              <Check className="mx-auto mb-2 h-6 w-6 text-success/70" />
              <p className="text-xs">
                {total > 0 ? 'All proposals resolved.' : 'No link proposals.'}
              </p>
              <p className="mt-1 text-[11px] text-fg/35">
                Import configs with shared subnets to see suggested links.
              </p>
            </div>
          </div>
        ) : (
          <ul className="flex flex-col gap-2">
            {proposals.map((p) => {
              const pending = accept.isPending && accept.variables?.id === p.id;
              return (
                <li
                  key={p.id}
                  className="rounded-lg border border-fg/10 bg-fg/5 p-3 transition-colors hover:border-fg/20"
                >
                  <div className="flex items-center gap-1.5 text-[13px] font-medium text-fg/90">
                    <span className="truncate">{p.aNode.name}</span>
                    <span className="font-mono text-[11px] text-fg/45">{p.aIface.name}</span>
                    <span className="text-fg/30">↔</span>
                    <span className="truncate">{p.bNode.name}</span>
                    <span className="font-mono text-[11px] text-fg/45">{p.bIface.name}</span>
                  </div>
                  <div className="mt-1 font-mono text-[11px] text-fg/50">shared subnet {p.subnet}</div>
                  <div className="mt-2 flex items-center gap-2">
                    <button
                      onClick={() => onReject(p.id)}
                      disabled={accept.isPending}
                      className="flex flex-1 items-center justify-center gap-1 rounded-md border border-fg/10 px-2 py-1.5 text-xs text-fg/60 transition-colors hover:border-danger/40 hover:text-danger disabled:opacity-40"
                    >
                      <X className="h-3.5 w-3.5" /> Reject
                    </button>
                    <button
                      onClick={() => accept.mutate(p)}
                      disabled={accept.isPending}
                      className="flex flex-1 items-center justify-center gap-1 rounded-md bg-accent px-2 py-1.5 text-xs font-semibold text-accent-fg transition-colors hover:bg-accent-soft disabled:opacity-40"
                    >
                      {pending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Check className="h-3.5 w-3.5" />}
                      Accept
                    </button>
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </aside>
  );
}
