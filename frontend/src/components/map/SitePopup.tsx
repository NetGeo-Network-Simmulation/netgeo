/**
 * SitePopup — popover for a clicked Site marker (UISP-parity P2).
 *
 * Mirrors MapDeployMenu's pattern exactly: pixel-anchored popover, local state
 * in MapView, never touches `uiStore.activeModal` (that slot is for full-cover
 * modals, not map popovers — see mapStore.ts boundary note). Only one map
 * popover lives at a time; MapView is responsible for closing its sibling.
 *
 * v1.2.60 UISP-parity close-out: adds a Devices section (nodes with this
 * site's id, real count) above the link table, and groups links by their
 * REAL `LinkType` instead of a flat list. UISP's reference groups links as
 * "PtP (n)" — NetGeo's `LinkModel.type` (types.ts:35) is only
 * `copper|fiber|wireless|virtual`, there is no PtP/PtMP field anywhere on the
 * link model (grepped backend RF schemas — `Ptp*`/`Ptmp*` there are RF-study
 * request/response shapes, not link data). Labeling a wireless-link group
 * "PtP (n)" would claim a topology fact (point-to-point vs point-to-multipoint)
 * NetGeo cannot see, so groups use the link's actual type instead:
 * Wireless / Fiber / Copper / Virtual. See NOTES.md section 4 for the fuller
 * writeup of this gap. Distance is haversine over real node coordinates
 * (WGS84 a=6378137, matches UISP — see mapStore.haversineM). Capacity is
 * `Link.bandwidth`, the only capacity NetGeo actually computes today
 * (RF-predicted throughput is a later slice, B2) — labelled honestly as
 * configured, not measured.
 *
 * Rename (inline edit here) and Move (hands off to mapStore's 'site-move'
 * tool, see MapView's MapClickHandler) both PATCH /sites/{id} — the only
 * way to edit a placed site short of delete-and-recreate (N1). Unchanged by
 * this pass — still both work, see MapView's site-move tool wiring.
 *
 * Footer: "Add AP" / "Add Fiber" reopen `MapContextMenu` pre-filtered to that
 * category with THIS site's id, so the picked product attaches here instead
 * of minting a new site (its own default behavior for a bare map click).
 * The category back-arrow still works, so either button doubles as a
 * general "add any device to this site" entry point (Routing & Switching and
 * Compute are reachable that way too). "Add PtP" / "Add Backbone" are
 * disabled — both imply creating a LINK between two endpoints, and no
 * two-endpoint link-creation flow exists yet (only single-device placement
 * via `deployAt`); wiring them to a device-category picker instead would
 * silently give a device with no such link, which is the same kind of
 * dishonesty the grouping rule above forbids. The old generic "Add Device"
 * button (-> MapDeployMenu) is removed: `MapDeployMenu`'s CABLED_PRESETS
 * never covered `server` either (NOTES.md section 6, item 7), so nothing
 * that used to work is lost — this is a strict upgrade to real catalog
 * products, not a swap of equal capability.
 */
import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { ArrowLeftRight, Building2, Cable, Check, Move, Network, Pencil, Radio, Trash2, X } from 'lucide-react';
import { cn } from '@/lib/cn';
import { zc } from '@/theme/z';
import { semantic } from '@/theme/tokens';
import { physicalApi, type ApiError } from '@/api/client';
import { haversineM, useMapStore } from '@/store/mapStore';
import { useTopologyStore } from '@/store/topologyStore';
import { useUiStore } from '@/store/uiStore';
import { fmtKm } from '@/components/rf/rfLogic';
import { DeviceIcon } from '@/components/canvas/DeviceIcon';
import { MapContextMenu } from './MapContextMenu';
import type { LinkType, NodeModel, NodeStatus, Site } from '@/api/types';

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

/** Real `LinkType` values only — no invented PtP/PtMP bucket, see module doc. */
const LINK_TYPE_ORDER: LinkType[] = ['wireless', 'fiber', 'copper', 'virtual'];
const LINK_TYPE_LABEL: Record<LinkType, string> = {
  wireless: 'Wireless',
  fiber: 'Fiber',
  copper: 'Copper',
  virtual: 'Virtual',
};

/** Mirrors PropertiesPanel's STATUS_COLORS (not exported there) — small
 *  enough to duplicate rather than refactor that file for one shared const. */
const STATUS_DOT: Record<NodeStatus, string> = {
  running: semantic.success,
  booting: semantic.warning,
  degraded: semantic.warning,
  error: semantic.danger,
  stopped: '#8A93A6',
};

export function SitePopup({ site, px, onClose }: Props) {
  const projectId = useUiStore((s) => s.projectId);
  const queryClient = useQueryClient();
  const nodes = useTopologyStore((s) => s.nodeList());
  const links = useTopologyStore((s) => s.linkList());
  const [addCategory, setAddCategory] = useState<'wireless' | 'fiber' | null>(null);
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(site.name);
  const [renameError, setRenameError] = useState<string | null>(null);

  const nodeById = new Map(nodes.map((n) => [n.id, n]));
  const devicesAtSite = nodes.filter((n) => n.site_id === site.id);
  const rows = links
    .map((link) => {
      const a = endpointNode(link.a_iface, nodeById);
      const b = endpointNode(link.b_iface, nodeById);
      if (!a || !b) return null;
      if (a.site_id !== site.id && b.site_id !== site.id) return null;
      const hasCoords = a.lat != null && a.lon != null && b.lat != null && b.lon != null;
      return {
        id: link.id,
        type: link.type,
        label: `${a.name} ↔ ${b.name}`,
        distance: hasCoords ? fmtKm(haversineM(a.lat!, a.lon!, b.lat!, b.lon!)) : null,
        capacity: formatCapacity(link.bandwidth),
      };
    })
    .filter((r): r is NonNullable<typeof r> => r !== null);
  const linkGroups = LINK_TYPE_ORDER.map((type) => ({
    type,
    label: LINK_TYPE_LABEL[type],
    rows: rows.filter((r) => r.type === type),
  })).filter((g) => g.rows.length > 0);

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

        {/* Devices + links — scrollable so a busy site still fits on screen (f). */}
        <div className="ng-scroll max-h-80 overflow-y-auto px-3.5 py-2.5">
          <div className="mb-3">
            <p className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-fg/40">
              Devices ({devicesAtSite.length})
            </p>
            {devicesAtSite.length === 0 ? (
              <p className="py-1 text-[11px] text-fg/40">No devices at this site yet.</p>
            ) : (
              <ul className="space-y-1">
                {devicesAtSite.map((n) => (
                  <li key={n.id} className="flex items-center gap-2 py-0.5">
                    <DeviceIcon kind={n.kind} className="h-3.5 w-3.5 shrink-0 text-fg/60" />
                    <span className="min-w-0 flex-1 truncate text-[11px] text-fg/80" title={n.name}>
                      {n.name}
                    </span>
                    <span
                      className="h-1.5 w-1.5 shrink-0 rounded-full"
                      style={{ background: STATUS_DOT[n.status] }}
                      title={n.status}
                    />
                  </li>
                ))}
              </ul>
            )}
          </div>

          {linkGroups.length === 0 ? (
            <p className="py-1 text-[11px] text-fg/40">No links at this site yet.</p>
          ) : (
            linkGroups.map((g) => (
              <div key={g.type} className="mb-2 last:mb-0">
                <p className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-fg/40">
                  {g.label} ({g.rows.length})
                </p>
                <table className="w-full text-left text-[11px]">
                  <thead>
                    <tr className="text-fg/40">
                      <th className="pb-1 font-medium">Link</th>
                      <th className="pb-1 font-medium">Distance</th>
                      <th className="pb-1 font-medium">Capacity</th>
                    </tr>
                  </thead>
                  <tbody>
                    {g.rows.map((r) => (
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
              </div>
            ))
          )}
        </div>

        {/* Footer actions — "Add AP"/"Add Fiber" reopen MapContextMenu scoped to
            this site (see module doc for why PtP/Backbone are disabled). */}
        <div className="border-t border-fg/10 p-2">
          <div className="grid grid-cols-2 gap-1.5">
            <button
              onClick={() => setAddCategory('wireless')}
              disabled={site.lat == null || site.lon == null}
              title={site.lat == null || site.lon == null ? 'Site has no coordinates yet' : 'Add a wireless AP to this site'}
              className="flex items-center justify-center gap-1 rounded-lg border border-accent/20 bg-accent/10 px-2 py-1.5 text-[11px] font-semibold text-accent transition-colors hover:bg-accent/20 disabled:opacity-40"
            >
              <Radio className="h-3 w-3" /> Add AP
            </button>
            <button
              disabled
              title="Add PtP would create a link between two endpoints — no two-endpoint link-creation flow exists yet, only single-device placement."
              className="flex cursor-not-allowed items-center justify-center gap-1 rounded-lg border border-fg/10 px-2 py-1.5 text-[11px] font-medium text-fg/30"
            >
              <ArrowLeftRight className="h-3 w-3" /> Add PtP
            </button>
            <button
              disabled
              title="Add Backbone would create a link between two endpoints — no two-endpoint link-creation flow exists yet, only single-device placement."
              className="flex cursor-not-allowed items-center justify-center gap-1 rounded-lg border border-fg/10 px-2 py-1.5 text-[11px] font-medium text-fg/30"
            >
              <Network className="h-3 w-3" /> Add Backbone
            </button>
            <button
              onClick={() => setAddCategory('fiber')}
              disabled={site.lat == null || site.lon == null}
              title={site.lat == null || site.lon == null ? 'Site has no coordinates yet' : 'Add a fiber device to this site'}
              className="flex items-center justify-center gap-1 rounded-lg border border-accent/20 bg-accent/10 px-2 py-1.5 text-[11px] font-semibold text-accent transition-colors hover:bg-accent/20 disabled:opacity-40"
            >
              <Cable className="h-3 w-3" /> Add Fiber
            </button>
          </div>
        </div>
      </div>

      {/* Reopens the right-click device menu, scoped to this site's id and
          pre-filtered to the picked category — see module doc. */}
      {addCategory && site.lat != null && site.lon != null && (
        <MapContextMenu
          px={{ x: px.x + 60, y: px.y }}
          lat={site.lat}
          lon={site.lon}
          siteId={site.id}
          initialCategoryKey={addCategory}
          onClose={() => setAddCategory(null)}
        />
      )}
    </>
  );
}
