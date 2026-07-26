/**
 * RackElevationPanel — the physical-mode rack view (NG-PH-04).
 *
 * A front, RU-accurate elevation of every rack in the project. Devices placed
 * via `node.rack_id` / `ru_start` / `ru_span` render as blocks spanning their
 * rack-units; unplaced devices sit in a tray. Drag a device onto a free RU span
 * to place it (collision-checked, persisted via PATCH /nodes/{id}); per-site
 * power + heat totals roll up from device wattage.
 *
 * It also surfaces the NG-PH-03 teachable failure: cables that exceed their
 * rated length error their link, listed here as warnings so the physical view
 * explains *why* a link is down.
 *
 * Per-device wattage prefers the /device-types datasheet (power_watts_max,
 * falling back to power_watts_idle) matched by node kind → builtin icon;
 * `KIND_WATTS` below is the fallback for kinds with no catalog match (e.g.
 * `host`) or if the catalog fetch hasn't landed yet.
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertTriangle, Cable, Plus, Server, Zap } from 'lucide-react';
import { deviceTypesApi, linksApi, nodesApi, physicalApi, projectsApi } from '@/api/client';
import type { DeviceType } from '@/api/client';
import type { LinkModel, LinkStatus, NodeKind, NodeModel, Rack, Site } from '@/api/types';
import { useUiStore } from '@/store/uiStore';
import { WorkspaceEmptyState } from '@/components/shell/WorkspaceEmptyState';
import { DeviceFaceplate, frontPortFractions, type Face } from '@/components/rack/DeviceFaceplate';
import { linkStatusColors } from '@/theme/tokens';
import { cn } from '@/lib/cn';

const RU_PX = 24; // on-screen height of one rack-unit
const DEFAULT_RU_HEIGHT = 42;

/** Selectable rack heights (10U–48U). */
const RACK_SIZES = [10, 12, 18, 24, 36, 42, 48];

/** Kinds addable straight into a rack, with their default RU span. */
const RACK_KINDS: { kind: NodeKind; span: number }[] = [
  { kind: 'switch', span: 1 },
  { kind: 'router', span: 1 },
  { kind: 'firewall', span: 1 },
  { kind: 'olt', span: 1 },
  { kind: 'server', span: 2 },
];

/** Estimated steady-state draw per node kind, in watts (see ponytail note). */
const KIND_WATTS: Record<string, number> = {
  router: 250,
  switch: 150,
  firewall: 200,
  olt: 300,
  server: 400,
  host: 100,
  ap: 20,
  cloud: 0,
};

/** Builtin device-type `icon` values don't always match `NodeKind` 1:1. */
const KIND_TO_ICON: Partial<Record<NodeKind, string>> = { firewall: 'fw' };

function nodeWatts(n: NodeModel, byIcon?: Map<string, DeviceType>): number {
  const dt = byIcon?.get(KIND_TO_ICON[n.kind] ?? n.kind);
  const watts = dt?.power_watts_max ?? dt?.power_watts_idle;
  return watts ?? KIND_WATTS[n.kind] ?? 150;
}

/** watts → heat load in BTU/hr (1 W ≈ 3.412 BTU/hr). */
function wattsToBtu(w: number): number {
  return Math.round(w * 3.412);
}

interface Dragload {
  nodeId: string;
  span: number;
}

/** The link's first locked endpoint, while the user picks the second port. */
interface PendingPort {
  nodeId: string;
  ifaceId: string;
}

export function RackElevationPanel() {
  const projectId = useUiStore((s) => s.projectId);
  const queryClient = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const [newSite, setNewSite] = useState('');
  const [newRackName, setNewRackName] = useState('');
  const [newRackSite, setNewRackSite] = useState<string>('');
  const [newRackU, setNewRackU] = useState(42);
  const [face, setFace] = useState<Face>('front');

  // E1: Cable Mode — click port A then port B to create a physical link.
  const [cableMode, setCableMode] = useState(false);
  const [pendingPort, setPendingPort] = useState<PendingPort | null>(null);
  const [rejectMsg, setRejectMsg] = useState<string | null>(null);
  const [mousePos, setMousePos] = useState<{ x: number; y: number } | null>(null);
  const rejectTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const topoQ = useQuery({
    queryKey: ['topology', projectId],
    queryFn: () => projectsApi.topology(projectId!),
    enabled: !!projectId,
  });
  const plantQ = useQuery({
    queryKey: ['plant', projectId],
    queryFn: () => physicalApi.plant(projectId!),
    enabled: !!projectId,
  });
  // Static catalog (builtins never change at runtime) — fetch once, reuse
  // across every watts lookup below instead of a per-node call.
  const deviceTypesQ = useQuery({
    queryKey: ['device-types'],
    queryFn: () => deviceTypesApi.list(),
    staleTime: Infinity,
  });
  const wattsByIcon = useMemo(() => {
    const map = new Map<string, DeviceType>();
    for (const dt of deviceTypesQ.data ?? []) {
      if (dt.icon) map.set(dt.icon, dt);
    }
    return map;
  }, [deviceTypesQ.data]);

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['topology', projectId] });
    queryClient.invalidateQueries({ queryKey: ['plant', projectId] });
  };

  const place = useMutation({
    mutationFn: (v: { nodeId: string; rackId: string; ruStart: number; ruSpan: number }) =>
      nodesApi.update(v.nodeId, {
        rack_id: v.rackId,
        ru_start: v.ruStart,
        ru_span: v.ruSpan,
      }),
    onSuccess: () => {
      setError(null);
      invalidate();
    },
    onError: () => setError('Failed to save device placement.'),
  });

  const unplace = useMutation({
    mutationFn: (nodeId: string) =>
      nodesApi.update(nodeId, { rack_id: null, ru_start: null }),
    onSuccess: invalidate,
  });

  // E1: create the physical link once both ports are picked. Reuses the same
  // a_iface/b_iface POST /links contract as TopologyCanvas.onConnect — no new
  // endpoint, no optimistic temp-link (this panel already follows the
  // mutate-then-invalidate idiom used by place/unplace/addDevice above).
  const createLink = useMutation({
    mutationFn: (v: { aIface: string; bIface: string }) =>
      linksApi.create({ project_id: projectId!, a_iface: v.aIface, b_iface: v.bIface, type: 'copper' }),
    onSuccess: () => {
      setError(null);
      invalidate();
    },
    onError: () => setError('Failed to create link.'),
  });

  const createSite = useMutation({
    mutationFn: (name: string) => physicalApi.createSite({ project_id: projectId!, name }),
    onSuccess: () => {
      setNewSite('');
      invalidate();
    },
  });

  const createRack = useMutation({
    mutationFn: (v: { name: string; siteId: string | null; ruHeight: number }) =>
      physicalApi.createRack({ project_id: projectId!, name: v.name, site_id: v.siteId, ru_height: v.ruHeight }),
    onSuccess: () => {
      setNewRackName('');
      invalidate();
    },
  });

  // Add a brand-new device straight into a rack slot (create → place).
  const addDevice = useMutation({
    mutationFn: async (v: { rackId: string; kind: NodeKind; ruStart: number; ruSpan: number }) => {
      const created = await nodesApi.create({
        project_id: projectId!,
        name: nameFor(v.kind),
        kind: v.kind,
        x: 0,
        y: 0,
      });
      return nodesApi.update(created.id, { rack_id: v.rackId, ru_start: v.ruStart, ru_span: v.ruSpan });
    },
    onSuccess: () => {
      setError(null);
      invalidate();
    },
    onError: () => setError('Failed to add device.'),
  });

  /** Auto-name `KIND-N` from existing nodes (mirrors mapDeploy). */
  const nameFor = (kind: NodeKind): string => {
    const prefix = kind.toUpperCase() + '-';
    const n = (topoQ.data?.nodes ?? []).filter((x) => x.name.startsWith(prefix)).length;
    return `${prefix}${n + 1}`;
  };

  const nodes = topoQ.data?.nodes ?? [];
  const racks = topoQ.data?.racks ?? [];
  const sites = topoQ.data?.sites ?? [];
  const cables = topoQ.data?.cables ?? [];
  const links = topoQ.data?.links ?? [];

  // Resolve a link endpoint (interface id, or a bare node id from map-deploy)
  // to its owning node id — used to draw cables between placed devices.
  const nodeByEndpoint = useMemo(() => {
    const m = new Map<string, string>();
    for (const n of nodes) {
      m.set(n.id, n.id);
      for (const i of n.interfaces ?? []) m.set(i.id, i.node_id ?? n.id);
    }
    return m;
  }, [nodes]);

  // Rack#1: real per-port LED status. Maps an interface id to the status of
  // whichever link uses it as an endpoint — DeviceFaceplate looks this up
  // instead of the old idx%3 cosmetic. Absent = no data (neutral LED).
  const linkStatusByIface = useMemo(() => {
    const m = new Map<string, LinkStatus>();
    for (const l of links) {
      if (l.a_iface) m.set(l.a_iface, l.status ?? 'unknown');
      if (l.b_iface) m.set(l.b_iface, l.status ?? 'unknown');
    }
    return m;
  }, [links]);

  const nodesByRack = useMemo(() => {
    const m = new Map<string, NodeModel[]>();
    for (const n of nodes) {
      if (!n.rack_id) continue;
      const list = m.get(n.rack_id) ?? [];
      list.push(n);
      m.set(n.rack_id, list);
    }
    return m;
  }, [nodes]);

  const unplaced = useMemo(() => nodes.filter((n) => !n.rack_id), [nodes]);

  // Over-length cables (NG-PH-03): join the plant report to cables for names.
  const overLength = useMemo(() => {
    const links = plantQ.data?.links ?? {};
    return cables
      .filter((c) => links[c.link_id]?.over_length)
      .map((c) => ({ cable: c, media: links[c.link_id]?.over_media ?? c.media }));
  }, [cables, plantQ.data]);

  // E1: Escape cancels the pending port while Cable Mode is mid-selection.
  useEffect(() => {
    if (!pendingPort) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setPendingPort(null);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [pendingPort]);

  // Clear the reject-toast timer on unmount so it never fires into a dead component.
  useEffect(() => () => {
    if (rejectTimer.current) clearTimeout(rejectTimer.current);
  }, []);

  const flashReject = (msg: string) => {
    setRejectMsg(msg);
    if (rejectTimer.current) clearTimeout(rejectTimer.current);
    rejectTimer.current = setTimeout(() => setRejectMsg(null), 2500);
  };

  /** Port-click state machine for Cable Mode: pick A, pick B, link created. */
  const handlePortClick = (nodeId: string, ifaceId: string) => {
    if (linkStatusByIface.has(ifaceId)) {
      flashReject('Port in use — unlink first.');
      return;
    }
    if (!pendingPort) {
      setPendingPort({ nodeId, ifaceId });
      return;
    }
    if (ifaceId === pendingPort.ifaceId) {
      setPendingPort(null); // clicking the locked port again cancels it
      return;
    }
    if (nodeId === pendingPort.nodeId) {
      flashReject('Cannot link a port to itself.');
      return;
    }
    createLink.mutate({ aIface: pendingPort.ifaceId, bIface: ifaceId });
    setPendingPort(null);
  };

  if (!projectId) {
    return <div className="p-6 text-sm text-fg/50">Select a project to view racks.</div>;
  }

  /** Racks grouped under their site (null site_id → an "Unassigned" bucket). */
  const bucketBySite: { site: Site | null; racks: Rack[] }[] = [
    ...sites.map((s) => ({ site: s, racks: racks.filter((r) => r.site_id === s.id) })),
    { site: null, racks: racks.filter((r) => !r.site_id || !sites.some((s) => s.id === r.site_id)) },
  ].filter((b) => b.site !== null || b.racks.length > 0);

  return (
    <div className="flex h-full flex-col bg-panel text-fg/90">
      {/* toolbar */}
      <div className="flex flex-wrap items-center gap-2 border-b border-fg/10 px-3 py-2 text-xs">
        <Server size={14} className="text-fg/50" />
        <span className="font-medium">Rack Elevation</span>
        <span className="text-fg/30">·</span>
        <input
          value={newSite}
          onChange={(e) => setNewSite(e.target.value)}
          placeholder="New site name"
          className="w-32 rounded bg-fg/10 px-2 py-1 outline-none placeholder:text-fg/30"
        />
        <button
          onClick={() => newSite.trim() && createSite.mutate(newSite.trim())}
          className="flex items-center gap-1 rounded bg-fg/10 px-2 py-1 hover:bg-fg/20"
        >
          <Plus size={12} /> Site
        </button>
        <span className="text-fg/30">·</span>
        <input
          value={newRackName}
          onChange={(e) => setNewRackName(e.target.value)}
          placeholder="New rack name"
          className="w-32 rounded bg-fg/10 px-2 py-1 outline-none placeholder:text-fg/30"
        />
        <select
          value={newRackSite}
          onChange={(e) => setNewRackSite(e.target.value)}
          className="rounded bg-fg/10 px-2 py-1 outline-none"
        >
          <option value="">(no site)</option>
          {sites.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}
            </option>
          ))}
        </select>
        <select
          value={newRackU}
          onChange={(e) => setNewRackU(Number(e.target.value))}
          className="rounded bg-fg/10 px-2 py-1 outline-none"
          title="Rack height"
        >
          {RACK_SIZES.map((u) => (
            <option key={u} value={u}>
              {u}U
            </option>
          ))}
        </select>
        <button
          onClick={() =>
            newRackName.trim() &&
            createRack.mutate({ name: newRackName.trim(), siteId: newRackSite || null, ruHeight: newRackU })
          }
          className="flex items-center gap-1 rounded bg-fg/10 px-2 py-1 hover:bg-fg/20"
        >
          <Plus size={12} /> Rack
        </button>

        {/* E1: Cable Mode toggle — click two ports to link them. Mimics the
            Front/Back pill's visual language, plus an engaged glow since this
            one also changes drag behavior (Front/Back doesn't). */}
        <button
          onClick={() => {
            setCableMode((v) => !v);
            setPendingPort(null);
            setRejectMsg(null);
          }}
          className={cn(
            'ml-auto flex items-center gap-1 rounded px-2 py-1 text-[11px] font-medium',
            cableMode
              ? 'bg-accent text-accent-fg shadow-[0_0_0_2px_rgb(var(--ng-accent-rgb)/0.35)]'
              : 'bg-fg/10 text-fg/70 hover:bg-fg/20',
          )}
          title="Click two ports to create a physical link"
        >
          <Cable size={12} /> Cable Mode
        </button>

        {/* Front / Back faceplate toggle */}
        <div className="flex items-center rounded bg-fg/10 p-0.5">
          {(['front', 'back'] as Face[]).map((f) => (
            <button
              key={f}
              onClick={() => setFace(f)}
              className={cn(
                'rounded px-2 py-0.5 text-[11px] capitalize',
                face === f ? 'bg-accent text-accent-fg' : 'text-fg/60 hover:text-fg',
              )}
            >
              {f}
            </button>
          ))}
        </div>
      </div>

      {cableMode && (
        <div className="border-b border-amber-500/20 bg-amber-500/10 px-3 py-1.5 text-xs text-amber-300">
          Drag disabled while Cable Mode is active — click two ports to connect them, press Escape to cancel.
        </div>
      )}

      {rejectMsg && (
        <div className="flex items-center gap-2 bg-red-500/15 px-3 py-1.5 text-xs text-red-300">
          <AlertTriangle size={13} /> {rejectMsg}
        </div>
      )}

      {error && (
        <div className="flex items-center gap-2 bg-red-500/15 px-3 py-1.5 text-xs text-red-300">
          <AlertTriangle size={13} /> {error}
        </div>
      )}

      {overLength.length > 0 && (
        <div className="border-b border-amber-500/20 bg-amber-500/10 px-3 py-1.5 text-xs text-amber-300">
          <div className="flex items-center gap-1 font-medium">
            <AlertTriangle size={13} /> Cable exceeds maximum length (link errored)
          </div>
          <ul className="mt-1 space-y-0.5 pl-5">
            {overLength.map(({ cable, media }) => (
              <li key={cable.id} className="list-disc text-amber-200/80">
                {cable.label || cable.id.slice(0, 6)} — {media} @ {cable.length_m} m
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="flex min-h-0 flex-1">
        {/* tray of unplaced devices */}
        <div className="w-44 shrink-0 overflow-y-auto border-r border-fg/10 p-2">
          <div className="mb-1 text-[11px] uppercase tracking-wide text-fg/40">
            Unplaced ({unplaced.length})
          </div>
          {unplaced.map((n) => (
            <div
              key={n.id}
              draggable={!cableMode}
              onDragStart={(e) =>
                e.dataTransfer.setData(
                  'application/netgeo-device',
                  JSON.stringify({ nodeId: n.id, span: n.ru_span ?? 1 } satisfies Dragload),
                )
              }
              className={cn(
                'mb-1 rounded border border-fg/10 bg-fg/5 px-2 py-1 text-xs hover:bg-fg/10',
                cableMode ? 'cursor-not-allowed opacity-50' : 'cursor-grab active:cursor-grabbing',
              )}
              title={`${n.kind} · ${nodeWatts(n, wattsByIcon)} W`}
            >
              {n.name}
              <span className="ml-1 text-fg/40">{n.ru_span ?? 1}U</span>
            </div>
          ))}
          {unplaced.length === 0 && (
            <div className="text-xs text-fg/30">All devices are placed.</div>
          )}
        </div>

        {/* racks grouped by site */}
        <div
          className="relative flex-1 overflow-auto p-3"
          onMouseMove={cableMode && pendingPort ? (e) => setMousePos({ x: e.clientX, y: e.clientY }) : undefined}
        >
          {bucketBySite.length === 0 && (
            <WorkspaceEmptyState
              icon={Server}
              title="No racks yet"
              hint="Create a site, then a rack, using the toolbar above — placed devices render as RU-accurate blocks."
            />
          )}
          {bucketBySite.map((bucket) => {
            const siteNodes = bucket.racks.flatMap((r) => nodesByRack.get(r.id) ?? []);
            const watts = siteNodes.reduce((sum, n) => sum + nodeWatts(n, wattsByIcon), 0);
            return (
              <div key={bucket.site?.id ?? '_unassigned'} className="mb-6">
                <div className="mb-2 flex items-center gap-2 text-sm">
                  <span className="font-medium">{bucket.site?.name ?? 'No site'}</span>
                  {bucket.site?.region && (
                    <span className="text-fg/40">· {bucket.site.region}</span>
                  )}
                  <span className="ml-2 flex items-center gap-1 text-xs text-amber-300/80">
                    <Zap size={12} /> {watts} W · {wattsToBtu(watts)} BTU/hr
                  </span>
                </div>
                <div className="flex flex-wrap gap-5">
                  {bucket.racks.map((rack) => (
                    <RackColumn
                      key={rack.id}
                      rack={rack}
                      devices={nodesByRack.get(rack.id) ?? []}
                      face={face}
                      links={links}
                      nodeByEndpoint={nodeByEndpoint}
                      linkStatusByIface={linkStatusByIface}
                      onPlace={(nodeId, ruStart, ruSpan) =>
                        place.mutate({ nodeId, rackId: rack.id, ruStart, ruSpan })
                      }
                      onUnplace={(nodeId) => unplace.mutate(nodeId)}
                      onAdd={(kind, ruStart, ruSpan) =>
                        addDevice.mutate({ rackId: rack.id, kind, ruStart, ruSpan })
                      }
                      onError={setError}
                      wattsByIcon={wattsByIcon}
                      cableMode={cableMode}
                      pendingPort={pendingPort}
                      mousePos={mousePos}
                      onPortClick={handlePortClick}
                    />
                  ))}
                  {bucket.racks.length === 0 && (
                    <div className="text-xs text-fg/30">This site has no racks yet.</div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

interface RackColumnProps {
  rack: Rack;
  devices: NodeModel[];
  face: Face;
  links: LinkModel[];
  nodeByEndpoint: Map<string, string>;
  linkStatusByIface: Map<string, LinkStatus>;
  onPlace: (nodeId: string, ruStart: number, ruSpan: number) => void;
  onUnplace: (nodeId: string) => void;
  onAdd: (kind: NodeKind, ruStart: number, ruSpan: number) => void;
  onError: (msg: string | null) => void;
  wattsByIcon: Map<string, DeviceType>;
  cableMode: boolean;
  pendingPort: PendingPort | null;
  mousePos: { x: number; y: number } | null;
  onPortClick: (nodeId: string, ifaceId: string) => void;
}

function RackColumn({
  rack,
  devices,
  face,
  links,
  nodeByEndpoint,
  linkStatusByIface,
  onPlace,
  onUnplace,
  onAdd,
  onError,
  wattsByIcon,
  cableMode,
  pendingPort,
  mousePos,
  onPortClick,
}: RackColumnProps) {
  const ruHeight = rack.ru_height || DEFAULT_RU_HEIGHT;
  const bodyRef = useRef<HTMLDivElement>(null);
  // null = picker closed; number = picker is open at that RU start
  const [pickerRu, setPickerRu] = useState<number | null>(null);

  /** RUs occupied by every device except `exceptId`. */
  const occupied = (exceptId?: string): Set<number> => {
    const s = new Set<number>();
    for (const d of devices) {
      if (d.id === exceptId || d.ru_start == null) continue;
      const span = d.ru_span ?? 1;
      for (let u = d.ru_start; u < d.ru_start + span; u++) s.add(u);
    }
    return s;
  };

  /** Find the lowest contiguous free RU that fits `span` units. */
  const firstFreeRu = (span = 1): number | null => {
    const taken = occupied();
    for (let u = 1; u <= ruHeight - span + 1; u++) {
      let fits = true;
      for (let i = 0; i < span; i++) if (taken.has(u + i)) { fits = false; break; }
      if (fits) return u;
    }
    return null;
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    // ponytail: draggable={!cableMode} on every drag source already prevents a
    // drag from starting; this is a defensive no-op guard, not the real gate.
    if (cableMode) return;
    const raw = e.dataTransfer.getData('application/netgeo-device');
    if (!raw) return;
    let load: Dragload;
    try {
      load = JSON.parse(raw);
    } catch {
      return;
    }
    const body = bodyRef.current;
    if (!body) return;
    const rect = body.getBoundingClientRect();
    // RU 1 sits at the bottom; convert cursor Y (top-down) to a bottom-up RU.
    const yFromBottom = rect.bottom - e.clientY;
    let ruStart = Math.floor(yFromBottom / RU_PX) + 1;
    const span = load.span || 1;
    ruStart = Math.max(1, Math.min(ruStart, ruHeight - span + 1));

    const taken = occupied(load.nodeId);
    for (let u = ruStart; u < ruStart + span; u++) {
      if (taken.has(u)) {
        onError(`Collision at RU ${u} in rack ${rack.name}.`);
        return;
      }
    }
    onError(null);
    onPlace(load.nodeId, ruStart, span);
  };

  // ── Cable overlay ─────────────────────────────────────────────────────────
  // Build a node-id → ru_start map for placed devices in THIS rack.
  const placedRu = new Map<string, { ruStart: number; ruSpan: number }>();
  const devicesById = new Map<string, NodeModel>();
  for (const d of devices) {
    if (d.ru_start != null) placedRu.set(d.id, { ruStart: d.ru_start, ruSpan: d.ru_span ?? 1 });
    devicesById.set(d.id, d);
  }

  const bodyH = ruHeight * RU_PX;
  const bodyW = 160; // w-40
  const BLOCK_INSET = 4; // matches `inset-x-1` on the faceplate block (0.25rem @ 16px root)

  // Real port anchor for one link endpoint: faceplate fraction (from
  // frontPortFractions) mapped onto this device block's on-screen box.
  // No match (back face, drive bay, or non-iface map-deploy endpoint) ->
  // fall back to the old vertical-center-of-block anchor.
  const portAnchor = (
    dev: NodeModel | undefined,
    ru: { ruStart: number; ruSpan: number },
    ifaceId: string,
    fallbackY: number,
  ): { x: number; y: number } => {
    const frac = face === 'front' && dev ? frontPortFractions(dev, ru.ruSpan).get(ifaceId) : undefined;
    if (!frac) return { x: bodyW, y: fallbackY };
    const blockW = bodyW - BLOCK_INSET * 2;
    const blockH = ru.ruSpan * RU_PX - 2;
    const blockTop = bodyH - (ru.ruStart - 1) * RU_PX - blockH;
    return { x: BLOCK_INSET + frac.x * blockW, y: blockTop + frac.y * blockH };
  };

  const cableLines: { x1: number; y1: number; x2: number; y2: number; color: string }[] = [];
  for (const lnk of links) {
    const aNode = nodeByEndpoint.get(lnk.a_iface);
    const bNode = nodeByEndpoint.get(lnk.b_iface);
    if (!aNode || !bNode || aNode === bNode) continue;
    const a = placedRu.get(aNode);
    const b = placedRu.get(bNode);
    if (!a || !b) continue;
    // vertical center of each device block (RU 1 = bottom, so flip to top-down px)
    // — the fallback anchor when the real port position can't be resolved.
    const yCenterA = bodyH - (a.ruStart - 1 + a.ruSpan / 2) * RU_PX;
    const yCenterB = bodyH - (b.ruStart - 1 + b.ruSpan / 2) * RU_PX;
    const anchorA = portAnchor(devicesById.get(aNode), a, lnk.a_iface, yCenterA);
    const anchorB = portAnchor(devicesById.get(bNode), b, lnk.b_iface, yCenterB);
    const color = linkStatusColors[lnk.status ?? 'unknown'];
    cableLines.push({ x1: anchorA.x, y1: anchorA.y, x2: anchorB.x, y2: anchorB.y, color });
  }

  // E1: rubber-band line from the locked port to the live cursor, drawn only
  // in the rack column that actually holds the pending device (cross-rack
  // pending is a known gap — see NG-PH cable overlay bug at the `!a || !b`
  // guard above, deliberately left untouched per slice scope).
  let pendingLine: { x1: number; y1: number; x2: number; y2: number } | null = null;
  if (cableMode && pendingPort && mousePos && devicesById.has(pendingPort.nodeId)) {
    const ru = placedRu.get(pendingPort.nodeId);
    const dev = devicesById.get(pendingPort.nodeId);
    const rect = bodyRef.current?.getBoundingClientRect();
    if (ru && dev && rect) {
      const yCenter = bodyH - (ru.ruStart - 1 + ru.ruSpan / 2) * RU_PX;
      const origin = portAnchor(dev, ru, pendingPort.ifaceId, yCenter);
      pendingLine = { x1: origin.x, y1: origin.y, x2: mousePos.x - rect.left, y2: mousePos.y - rect.top };
    }
  }

  // ── Add-device affordance ─────────────────────────────────────────────────
  // Find the lowest free RU (default span 1 for the affordance slot).
  const freeRu = firstFreeRu(1);

  return (
    <div className="select-none">
      <div className="mb-1 text-xs font-medium text-fg/70">
        {rack.name} <span className="text-fg/30">{ruHeight}U</span>
      </div>
      <div className="flex">
        {/* RU number gutter (top = highest RU) */}
        <div className="flex flex-col text-right text-[9px] leading-none text-fg/30">
          {Array.from({ length: ruHeight }, (_, i) => ruHeight - i).map((u) => (
            <div key={u} style={{ height: RU_PX }} className="pr-1 pt-0.5">
              {u}
            </div>
          ))}
        </div>
        {/* rack body */}
        <div
          ref={bodyRef}
          onDragOver={(e) => e.preventDefault()}
          onDrop={handleDrop}
          className="relative w-40 rounded border border-fg/15 bg-recess/40"
          style={{ height: bodyH }}
        >
          {/* RU gridlines */}
          {Array.from({ length: ruHeight }, (_, i) => (
            <div
              key={i}
              className="absolute inset-x-0 border-t border-fg/5"
              style={{ top: i * RU_PX, height: RU_PX }}
            />
          ))}

          {/* placed devices — faceplate fills the block, name label overlays top-left */}
          {devices
            .filter((d) => d.ru_start != null)
            .map((d) => {
              const span = d.ru_span ?? 1;
              const bottom = (d.ru_start! - 1) * RU_PX;
              return (
                <div
                  key={d.id}
                  draggable={!cableMode}
                  onDragStart={(e) =>
                    e.dataTransfer.setData(
                      'application/netgeo-device',
                      JSON.stringify({ nodeId: d.id, span } satisfies Dragload),
                    )
                  }
                  onDoubleClick={() => onUnplace(d.id)}
                  title={`${d.kind} · ${nodeWatts(d, wattsByIcon)} W — double-click to remove`}
                  className={cn(
                    'absolute inset-x-1 overflow-hidden rounded',
                    cableMode ? 'cursor-crosshair' : 'cursor-grab active:cursor-grabbing',
                  )}
                  style={{ bottom, height: span * RU_PX - 2 }}
                >
                  <DeviceFaceplate
                    node={d}
                    span={span}
                    face={face}
                    linkStatusByIface={linkStatusByIface}
                    cableMode={cableMode}
                    pendingIface={pendingPort?.ifaceId ?? null}
                    onPortClick={(ifaceId) => onPortClick(d.id, ifaceId)}
                  />
                  {/* name label: small overlay so it doesn't hide the faceplate */}
                  <span className="absolute left-0.5 top-0.5 truncate rounded bg-recess/20 px-1 text-[9px] leading-tight text-fg/80 pointer-events-none">
                    {d.name}
                  </span>
                </div>
              );
            })}

          {/* Add-device affordance: dashed slot at the lowest free RU */}
          {freeRu != null && (
            <div
              className="absolute inset-x-1 cursor-pointer"
              style={{ bottom: (freeRu - 1) * RU_PX, height: RU_PX - 2 }}
              onClick={() => setPickerRu(pickerRu === freeRu ? null : freeRu)}
            >
              <div className="flex h-full items-center justify-center rounded border border-dashed border-fg/20 text-[10px] text-fg/40 hover:border-accent/60 hover:text-accent/80">
                + Add device
              </div>
              {/* inline kind picker */}
              {pickerRu === freeRu && (
                <div className="glass-strong absolute bottom-full left-0 z-10 mb-1 flex flex-wrap gap-1 rounded p-1.5 shadow-lg">
                  {RACK_KINDS.map(({ kind, span: kSpan }) => {
                    const fits = firstFreeRu(kSpan) != null;
                    return (
                      <button
                        key={kind}
                        disabled={!fits}
                        onClick={(e) => {
                          e.stopPropagation();
                          const ru = firstFreeRu(kSpan);
                          if (ru == null) return;
                          onAdd(kind, ru, kSpan);
                          setPickerRu(null);
                        }}
                        className={cn(
                          'rounded px-2 py-0.5 text-[10px] capitalize',
                          fits
                            ? 'bg-fg/10 text-fg/80 hover:bg-accent/20 hover:text-fg'
                            : 'cursor-not-allowed opacity-40 bg-fg/5 text-fg/30',
                        )}
                        title={!fits ? `No ${kSpan}U gap available` : undefined}
                      >
                        {kind}
                        {kSpan > 1 && <span className="ml-0.5 text-fg/40">{kSpan}U</span>}
                      </button>
                    );
                  })}
                </div>
              )}
            </div>
          )}

          {/* Cable overlay — best-effort/decorative, pointer-events-none */}
          {(cableLines.length > 0 || pendingLine) && (
            <svg
              className="pointer-events-none absolute inset-0"
              width={bodyW}
              height={bodyH}
              style={{ overflow: 'visible' }}
            >
              {cableLines.map((c, i) => {
                const mx = bodyW + 10; // bow out to the right
                const my = (c.y1 + c.y2) / 2;
                return (
                  <path
                    key={i}
                    d={`M ${c.x1} ${c.y1} Q ${mx} ${my} ${c.x2} ${c.y2}`}
                    fill="none"
                    stroke={c.color}
                    strokeWidth="1.5"
                    strokeOpacity="0.7"
                    className="ng-cable-flow"
                  />
                );
              })}
              {/* E1: rubber-band from the locked port to the live cursor */}
              {pendingLine && (
                <line
                  x1={pendingLine.x1}
                  y1={pendingLine.y1}
                  x2={pendingLine.x2}
                  y2={pendingLine.y2}
                  stroke="rgb(var(--ng-accent-rgb))"
                  strokeWidth="1.5"
                  strokeDasharray="4 3"
                  strokeOpacity="0.85"
                />
              )}
            </svg>
          )}
        </div>
      </div>
    </div>
  );
}
