/**
 * UnrackedDevicesPanel — outdoor placement, Slice 3 (netgeo-plan-outdoor-
 * placement.md). A node can have `site_id` set with no `rack_id` (backend
 * has supported this since NG-PH-01), but nothing in the UI ever showed it
 * or let anyone edit its `mount` (pole/wall/strand/ground/ceiling + install
 * height) — this panel is that read/edit surface. Deliberately plain: a
 * list with two inline controls per row, not a 3D view (that's Slice 6).
 */
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Radio as RadioIcon } from 'lucide-react';
import { nodesApi } from '@/api/client';
import type { NodeMountType, UnrackedNode } from '@/api/types';
import { Select } from '@/components/ui/Select';
import { cn } from '@/lib/cn';

const MOUNT_TYPES: NodeMountType[] = ['pole', 'wall', 'strand', 'ground', 'ceiling'];

const fieldCls =
  'min-w-0 rounded-md border border-fg/10 bg-transparent px-1.5 py-1 text-xs text-fg outline-none focus:border-accent/50';

export function UnrackedDevicesPanel({
  projectId,
  nodes,
}: {
  projectId: string;
  nodes: UnrackedNode[];
}) {
  const queryClient = useQueryClient();
  const patchMount = useMutation({
    mutationFn: (v: { nodeId: string; type: NodeMountType | ''; heightAglM: number | null }) =>
      nodesApi.update(v.nodeId, {
        mount: v.type ? { type: v.type, height_agl_m: v.heightAglM } : null,
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['plant', projectId] });
      void queryClient.invalidateQueries({ queryKey: ['topology', projectId] });
    },
  });

  if (nodes.length === 0) return null;

  return (
    <div className="border-b border-fg/10 px-3 py-1.5 text-xs" data-testid="unracked-devices-panel">
      <div className="mb-1 flex items-center gap-1.5 text-fg-muted">
        <RadioIcon className="size-3.5" />
        Penempatan luar rak ({nodes.length})
      </div>
      <div className="flex flex-col gap-1">
        {nodes.map((n) => (
          <div key={n.id} className="flex flex-wrap items-center gap-1.5">
            <span className="min-w-0 flex-1 truncate text-fg">{n.name}</span>
            <Select
              aria-label={`Tipe mount untuk ${n.name}`}
              className="w-28"
              value={n.mount?.type ?? ''}
              onChange={(v) => {
                const type = v as NodeMountType | '';
                patchMount.mutate({ nodeId: n.id, type, heightAglM: n.mount?.height_agl_m ?? null });
              }}
              options={[
                { value: '', label: '(belum dipasang)' },
                ...MOUNT_TYPES.map((t) => ({ value: t, label: t })),
              ]}
            />
            <input
              type="number"
              aria-label={`Tinggi AGL (m) untuk ${n.name}`}
              placeholder="tinggi (m)"
              disabled={!n.mount}
              className={cn(fieldCls, 'w-24 disabled:cursor-not-allowed disabled:opacity-40')}
              value={n.mount?.height_agl_m ?? ''}
              onChange={(e) => {
                if (!n.mount) return;
                const raw = e.target.value;
                patchMount.mutate({
                  nodeId: n.id,
                  type: n.mount.type,
                  heightAglM: raw === '' ? null : Number(raw),
                });
              }}
            />
          </div>
        ))}
      </div>
    </div>
  );
}
