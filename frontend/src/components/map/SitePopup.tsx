/**
 * SitePopup — popover for a clicked Site marker (UISP-parity P2).
 *
 * Mirrors MapDeployMenu's pattern exactly: pixel-anchored popover, local state
 * in MapView, never touches `uiStore.activeModal` (that slot is for full-cover
 * modals, not map popovers — see mapStore.ts boundary note). Only one map
 * popover lives at a time; MapView is responsible for closing its sibling.
 *
 * Table columns are `PtP | Distance | Capacity` (UISP reference), renamed to
 * NetGeo's own link vocabulary. Distance is haversine over real node
 * coordinates (WGS84 a=6378137, matches UISP — see mapStore.haversineM).
 * Capacity is `Link.bandwidth`, the only capacity NetGeo actually computes
 * today (RF-predicted throughput is a later slice, B2) — labelled honestly
 * as configured, not measured.
 *
 * Rename (inline edit here) and Move (hands off to mapStore's 'site-move'
 * tool, see MapView's MapClickHandler) both PATCH /sites/{id} — the only
 * way to edit a placed site short of delete-and-recreate (N1).
 */
import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Building2, Check, Link2, Move, Pencil, Plus, Trash2, X } from 'lucide-react';
import { cn } from '@/lib/cn';
import { zc } from '@/theme/z';
import { physicalApi, type ApiError } from '@/api/client';
import { haversineM, useMapStore } from '@/store/mapStore';
import { useTopologyStore } from '@/store/topologyStore';
import { useUiStore } from '@/store/uiStore';
import { fmtKm } from '@/components/rf/rfLogic';
import { MapDeployMenu } from './MapDeployMenu';
import type { NodeModel, Site } from '@/api/types';

interface Props {
  site: Site;
  px: { x: number; y: number };
  onClose: () => void;
}

/** `Link.bandwidth` is Mbps — format like UISP ("1.62 Gbps" / "875 Mbps"). */
function formatCapacity(mbps: number): string {
  return mbps >= 1000 ? `${(mbps / 1000).toFixed(2)} Gbps` : `${mbps} Mbps`;
}

/** Resolve a link endpoint (iface id or bare node id) to its owning node. */
function endpointNode(ref: string, nodeById: Map<string, NodeModel>): NodeModel | undefined {
  const direct = nodeById.get(ref);
  if (direct) return direct;
  for (const n of nodeById.values()) {
    if (n.interfaces.some((i) => i.id === ref)) return n;
  }
  return undefined;
}

export function SitePopup({ site, px, onClose }: Props) {
  const projectId = useUiStore((s) => s.projectId);
  const queryClient = useQueryClient();
  const nodes = useTopologyStore((s) => s.nodeList());
  const links = useTopologyStore((s) => s.linkList());
  const [deploying, setDeploying] = useState(false);
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(site.name);
  const [renameError, setRenameError] = useState<string | null>(null);

  const nodeById = new Map(nodes.map((n) => [n.id, n]));
  const rows = links
    .map((link) => {
      const a = endpointNode(link.a_iface, nodeById);
      const b = endpointNode(link.b_iface, nodeById);
      if (!a || !b) return null;
      if (a.site_id !== site.id && b.site_id !== site.id) return null;
      const hasCoords = a.lat != null && a.lon != null && b.lat != null && b.lon != null;
      return {
        id: link.id,
        label: `${a.name} ↔ ${b.name}`,
        distance: hasCoords ? fmtKm(haversineM(a.lat!, a.lon!, b.lat!, b.lon!)) : null,
        capacity: formatCapacity(link.bandwidth),
      };
    })
    .filter((r): r is NonNullable<typeof r> => r !== null);

  const removeSite = useMutation({
    mutationFn: () => physicalApi.removeSite(site.id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['topology', projectId] });
      onClose();
    },
  });

  const renameSite = useMutation({
    mutationFn: (newName: string) => physicalApi.updateSite(site.id, { name: newName }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['topology', projectId] });
      setEditing(false);
      setRenameError(null);
    },
    onError: (e) => setRenameError((e as unknown as ApiError).message || 'Failed to rename site.'),
  });

  const cancelEdit = () => {
    setEditing(false);
    setName(site.name);
    setRenameError(null);
  };

  const saveEdit = () => {
    const trimmed = name.trim();
    if (!trimmed || renameSite.isPending) return;
    if (trimmed === site.name) {
      setEditing(false);
      return;
    }
    renameSite.mutate(trimmed);
  };

  // "Move" hands off to the map's site-move tool (mapStore.setMovingSiteId) —
  // this popup closes and the next map click PATCHes the new lat/lon (see
  // MapClickHandler in MapView.tsx). No drag machinery: sites render as a
  // MapLibre circle layer, not a draggable Marker.
  const startMove = () => {
    useMapStore.getState().setMovingSiteId(site.id);
    onClose();
  };

  return (
    <>
      <div
        style={{ position: 'absolute', left: px.x + 12, top: px.y - 8, transform: 'translateY(-50%)' }}
        className={cn(
          'glass-strong pointer-events-auto w-72 rounded-xl border border-fg/15 shadow-glass-lg',
          zc.popover,
        )}
        role="dialog"
        aria-label={`Site ${site.name}`}
      >
        {/* Header */}
        <div className="flex items-center gap-2.5 border-b border-fg/10 px-3.5 py-2.5">
          <div className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-success/15 text-success">
            <Building2 className="h-4 w-4" />
          </div>
          <div className="min-w-0 flex-1">
            {editing ? (
              <input
                autoFocus
                value={name}
                onChange={(e) => setName(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') saveEdit();
                  if (e.key === 'Escape') cancelEdit();
                }}
                disabled={renameSite.isPending}
                className="w-full rounded-md border border-accent/40 bg-recess/30 px-1.5 py-0.5 text-sm font-semibold text-fg/90 outline-none"
              />
            ) : (
              <div className="flex items-center gap-1">
                <p className="truncate text-sm font-semibold text-fg/90">{site.name}</p>
                <button
                  onClick={() => setEditing(true)}
                  aria-label="Rename site"
                  className="grid h-5 w-5 shrink-0 place-items-center rounded text-fg/35 hover:text-fg/80"
                >
                  <Pencil className="h-3 w-3" />
                </button>
              </div>
            )}
            {site.region && !editing && <p className="truncate text-[10px] text-fg/45">{site.region}</p>}
          </div>
          {editing ? (
            <>
              <button
                onClick={saveEdit}
                disabled={renameSite.isPending || !name.trim()}
                aria-label="Save name"
                className="grid h-6 w-6 place-items-center rounded-md text-success hover:bg-success/10 disabled:opacity-40"
              >
                <Check className="h-3.5 w-3.5" />
              </button>
              <button
                onClick={cancelEdit}
                aria-label="Cancel rename"
                className="grid h-6 w-6 place-items-center rounded-md text-fg/40 hover:bg-fg/10 hover:text-fg"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </>
          ) : (
            <>
              <button
                onClick={startMove}
                disabled={site.lat == null || site.lon == null}
                title={site.lat == null || site.lon == null ? 'Site has no coordinates yet' : 'Move this site'}
                aria-label="Move site"
                className="grid h-6 w-6 place-items-center rounded-md text-fg/40 hover:bg-accent/10 hover:text-accent disabled:opacity-30"
              >
                <Move className="h-3.5 w-3.5" />
              </button>
              <button
                onClick={() => removeSite.mutate()}
                disabled={removeSite.isPending}
                aria-label="Delete site"
                className="grid h-6 w-6 place-items-center rounded-md text-fg/40 hover:bg-danger/10 hover:text-danger disabled:opacity-50"
              >
                <Trash2 className="h-3.5 w-3.5" />
              </button>
              <button
                onClick={onClose}
                aria-label="Close"
                className="grid h-6 w-6 place-items-center rounded-md text-fg/40 hover:bg-fg/10 hover:text-fg"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </>
          )}
        </div>

        {renameError && (
          <div className="flex items-center gap-1.5 bg-danger/15 px-3.5 py-1.5 text-[11px] text-danger">
            <X className="h-3 w-3 shrink-0" /> {renameError}
          </div>
        )}

        {/* Link table */}
        <div className="max-h-64 overflow-y-auto px-3.5 py-2.5">
          {rows.length === 0 ? (
            <p className="py-1 text-center text-[11px] text-fg/40">No links at this site yet.</p>
          ) : (
            <table className="w-full text-left text-[11px]">
              <thead>
                <tr className="text-fg/40">
                  <th className="pb-1 font-medium">Link</th>
                  <th className="pb-1 font-medium">Distance</th>
                  <th className="pb-1 font-medium">Capacity</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.id} className="border-t border-fg/5">
                    <td className="max-w-[7.5rem] truncate py-1.5 pr-2 text-fg/80" title={r.label}>
                      {r.label}
                    </td>
                    <td className="py-1.5 pr-2 font-mono text-fg/60">{r.distance ?? '—'}</td>
                    <td className="py-1.5 font-mono text-fg/60">{r.capacity}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        {/* Add device */}
        <div className="border-t border-fg/10 p-2">
          <button
            onClick={() => setDeploying(true)}
            className="flex w-full items-center justify-center gap-1.5 rounded-lg border border-accent/20 bg-accent/10 px-3 py-1.5 text-xs font-semibold text-accent transition-colors hover:bg-accent/20"
          >
            <Plus className="h-3.5 w-3.5" /> Add Device
          </button>
          <p className="mt-1 flex items-center gap-1 text-center text-[10px] text-fg/35">
            <Link2 className="h-2.5 w-2.5 shrink-0" /> New devices auto-link to the nearest upstream.
          </p>
        </div>
      </div>

      {/* Reuses the same deploy popover as an empty-map click, anchored here
          and bound to this site's id — no new device-creation UI needed. */}
      {deploying && site.lat != null && site.lon != null && (
        <MapDeployMenu
          px={{ x: px.x + 60, y: px.y }}
          lat={site.lat}
          lon={site.lon}
          siteId={site.id}
          onClose={() => setDeploying(false)}
        />
      )}
    </>
  );
}
