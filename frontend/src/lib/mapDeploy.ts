/**
 * mapDeploy — helpers for the map "Deploy Device" flow.
 *
 * Three exports:
 *  - nearestUpstream: find the best upstream node to auto-link to
 *  - deployAt:        create a real backend node + link + optional rack placement
 *  - ensureRackPlacement: find/create site+rack, place node in first free RU
 */
import { nodesApi, linksApi, physicalApi, projectsApi } from '@/api/client';
import type { NodeModel } from '@/api/types';
import type { NodeKind } from '@/api/types';
import { useTopologyStore } from '@/store/topologyStore';
import { haversineM } from '@/store/mapStore';
import { useRfStore } from '@/store/rfStore';
import { useUiStore } from '@/store/uiStore';

// ponytail: haversine already in mapStore — reuse, don't copy

/**
 * Find the best upstream anchor for a newly placed node.
 *
 * - cpe → nearest ap (needs a serving AP)
 * - ap  → nearest switch|olt|router (needs backhaul)
 * - cabled (switch|olt|router|host|firewall|server) → nearest switch|olt|router (excluding self)
 *
 * Returns null when no suitable upstream exists in the topology.
 */
export function nearestUpstream(
  nodes: NodeModel[],
  lat: number,
  lon: number,
  kind: NodeKind,
  excludeId?: string,
): NodeModel | null {
  const geoNodes = nodes.filter((n) => n.lat != null && n.lon != null && n.id !== excludeId);

  let candidates: NodeModel[];
  if (kind === 'cpe') {
    candidates = geoNodes.filter((n) => n.kind === 'ap');
  } else if (kind === 'ap') {
    candidates = geoNodes.filter((n) => n.kind === 'switch' || n.kind === 'olt' || n.kind === 'router');
  } else {
    // cabled node: any switch|olt|router
    candidates = geoNodes.filter((n) => n.kind === 'switch' || n.kind === 'olt' || n.kind === 'router');
  }

  if (candidates.length === 0) return null;

  // ponytail: O(n) scan is fine — map topologies are small (< 1000 nodes)
  let best: NodeModel = candidates[0]!;
  let bestDist = haversineM(lat, lon, best.lat!, best.lon!);
  for (let i = 1; i < candidates.length; i++) {
    const d = haversineM(lat, lon, candidates[i]!.lat!, candidates[i]!.lon!);
    if (d < bestDist) { bestDist = d; best = candidates[i]!; }
  }
  return best;
}

/** Wireless kinds (get a Radio block on creation). */
const WIRELESS_KINDS = new Set<NodeKind>(['ap', 'cpe']);

/** Kinds that go in a rack. */
const RACK_KINDS = new Set<NodeKind>(['olt', 'switch', 'router', 'firewall', 'server']);

/**
 * Auto-name: `KIND-N` where N = count of existing nodes of that kind + 1.
 * Matches the pattern used by placeDevice for consistency.
 */
function autoName(nodes: Iterable<NodeModel>, kind: NodeKind): string {
  const prefix = kind.toUpperCase() + '-';
  let n = 0;
  for (const node of nodes) {
    if (node.name.startsWith(prefix)) n++;
  }
  return `${prefix}${n + 1}`;
}

/**
 * Find a non-colliding canvas position for a new node.
 * Offset from upstream by (160, 120), shifting by 40px for each collision.
 */
function canvasPos(upstream: NodeModel | null, existing: NodeModel[]): { x: number; y: number } {
  const base = upstream ? { x: upstream.x + 160, y: upstream.y + 120 } : { x: 120, y: 120 };
  const occupied = new Set(existing.map((n) => `${Math.round(n.x)},${Math.round(n.y)}`));
  let { x, y } = base;
  let tries = 0;
  while (occupied.has(`${Math.round(x)},${Math.round(y)}`) && tries < 20) {
    x += 40; y += 40; tries++;
  }
  return { x, y };
}

/**
 * Ensure a site + rack exist in the project, then place the node in the first
 * free RU. If the node is already placed (rack_id set) this is a no-op.
 */
export async function ensureRackPlacement(projectId: string, node: NodeModel): Promise<void> {
  if (node.rack_id) return; // already placed

  const topo = await projectsApi.topology(projectId);

  let site = topo.sites?.[0];
  if (!site) {
    site = await physicalApi.createSite({ project_id: projectId, name: 'Site-1' });
  }

  let rack = topo.racks?.[0];
  if (!rack) {
    rack = await physicalApi.createRack({
      project_id: projectId,
      site_id: site.id,
      name: 'Rack-1',
      ru_height: 42,
    });
  }

  // Find the lowest free RU by scanning already-placed nodes in this rack
  const placed = topo.nodes.filter((n) => n.rack_id === rack!.id && n.ru_start != null);
  const used = new Set<number>();
  for (const n of placed) {
    for (let ru = n.ru_start!; ru < n.ru_start! + (n.ru_span ?? 1); ru++) used.add(ru);
  }

  let ru = 1;
  while (used.has(ru) && ru <= rack.ru_height) ru++;
  const ruStart = ru <= rack.ru_height ? ru : 1; // fallback: pack from top

  await nodesApi.update(node.id, { rack_id: rack.id, ru_start: ruStart, ru_span: 1 });
}

/**
 * Auto-open the RF workspace when a just-placed node is something the RF
 * panel can genuinely use — every real placement path (right-click catalog
 * picker, SitePopup's Add AP/Fiber, the toolbar Deploy Device tool) routes
 * through this one `deployAt`, so gating here — on the node's real `kind`,
 * never on which menu/category the user clicked — covers all of them at once
 * (root cause, not per-caller).
 *
 * - kind 'ap': the only kind RfLinkBar/pickEndpoint accept as a PtP/PtMP
 *   endpoint (rfStore.ts:207-209) — select it and open RF.
 * - kind 'cpe' or 'host': RfAnalysisPanel's PtMP CPE picker already treats
 *   both as the CPE role (RfAnalysisPanel.tsx:440-445, mirroring backend
 *   wireless.py `_ROLE_BY_KIND`) — including the fiber-pack ONU/ONT catalog
 *   devices, which deploy with kind='host'. Add it as a CPE and open RF in
 *   PtMP mode so it's visibly selected, not just a panel switch.
 * - everything else (router, switch, olt, firewall, server, cloud) has no
 *   RF-workspace support today: 'olt' is never matched by any RF endpoint
 *   filter (only backend wireless.py's engine-role table mentions it — no
 *   frontend selector ever offers it), and 'router' is the generic kind the
 *   cell-site and optical-transport catalog packs actually deploy (no radio/
 *   optical metadata). Left alone, no navigation.
 *
 * Fiber workspace is deliberately NOT wired here: `fiberStore.ts`'s
 * `FiberPath`/`FiberElement` model has no field referencing a topology Node
 * at all (grepped — zero occurrences of node ids in fiber components/store),
 * so no placed device can ever be "visible" there. Auto-opening it would
 * just show an unrelated, unaffected GPON planner — the same kind of fake
 * feature this function exists to avoid.
 */
function autoOpenRf(node: NodeModel): void {
  const rf = useRfStore.getState();
  if (node.kind === 'ap') {
    // RfAnalysisPanel renders per `mode` (ptp/ptmp/product_select) — without
    // forcing 'ptp' here, a session left in 'ptmp' would assign aId/bId (this
    // pickEndpoint call) but keep showing the PtMP body, so the just-placed
    // AP would never actually be visible. Mirrors the explicit setMode below.
    rf.setMode('ptp');
    rf.pickEndpoint(node.id);
    useUiStore.getState().setViewMode('rf');
  } else if (node.kind === 'cpe' || node.kind === 'host') {
    rf.addCpeFromDevice(node.id);
    rf.setMode('ptmp');
    useUiStore.getState().setViewMode('rf');
  }
}

/**
 * Full deploy orchestration:
 *  1. Name the node
 *  2. Compute canvas position relative to upstream
 *  3. Create the node via API (with lat/lon, optional radio, optional site_id)
 *  4. Link to upstream (tolerates 409 — iface already taken)
 *  5. Rack placement for cabled infrastructure nodes
 *  6. Upsert into topologyStore so the map refreshes immediately
 *  7. Auto-open RF if the placed kind is real RF material (see autoOpenRf)
 */
export async function deployAt(
  projectId: string,
  kind: NodeKind,
  lat: number,
  lon: number,
  onNotice?: (msg: string) => void,
  siteId?: string,
  /** Extension bag stamped onto the created node (e.g. `{ device_type_id }`
   *  from the map right-click catalog picker, MapContextMenu.tsx) — passed
   *  straight through to NodeCreate.intent, which the backend already
   *  round-trips generically (backend/app/api/nodes.py). */
  intent?: Record<string, unknown>,
): Promise<NodeModel> {
  const { nodes, upsertNode, upsertLink } = useTopologyStore.getState();
  const nodeList = Array.from(nodes.values());

  const upstream = nearestUpstream(nodeList, lat, lon, kind);
  const { x, y } = canvasPos(upstream, nodeList);
  const name = autoName(nodeList, kind);

  const created = await nodesApi.create({
    project_id: projectId,
    name,
    kind,
    lat,
    lon,
    x,
    y,
    site_id: siteId ?? null,
    intent,
    radio: WIRELESS_KINDS.has(kind)
      ? { tx_power_dbm: 20, frequency_ghz: 5.8, antenna_gain_dbi: 14, bandwidth_mhz: 20, rx_sensitivity_dbm: -85, misc_loss_db: 2, max_range_m: null, height_agl_m: 6 }
      : undefined,
  });

  upsertNode(created);

  if (upstream) {
    try {
      // Backend accepts node IDs as link endpoints and auto-mints interfaces
      const link = await linksApi.create({
        project_id: projectId,
        a_iface: created.id,
        b_iface: upstream.id,
        type: WIRELESS_KINDS.has(kind) ? 'wireless' : 'fiber',
      } as Parameters<typeof linksApi.create>[0]);
      upsertLink(link);
    } catch (err: unknown) {
      // 409 = iface conflict — still a deployed node, just note it
      const status = (err as { status?: number }).status;
      if (status === 409) {
        onNotice?.(`${name} deployed — could not auto-link (interface conflict).`);
      } else {
        onNotice?.(`${name} deployed — link to upstream failed.`);
      }
    }
  }

  if (RACK_KINDS.has(kind)) {
    await ensureRackPlacement(projectId, created).catch(() => {
      // rack placement is best-effort; don't crash the deploy flow
    });
  }

  autoOpenRf(created);
  return created;
}
