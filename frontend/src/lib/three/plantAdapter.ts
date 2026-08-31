/**
 * plantAdapter — the only place that translates a project's `Topology` into
 * the `BuildOptions` the 2.5D scene builder (rack3d.ts) consumes. Replaces
 * the handoff prototype's hard-coded `FITOUT`/`LINKS` (NG-PH3D P1).
 *
 * Port ordinals are derived from `frontPortFractions()` (DeviceFaceplate.tsx)
 * — the same walk the 2D rack elevation already uses to place cable ends on
 * real ports — rather than a second port-layout implementation. rack3d's own
 * `portLayout()` only understands one port *type* per device and assigns
 * ordinals 0..n-1 in array order, so what this file needs from
 * `frontPortFractions()` isn't its pixel geometry but its ordinal walk: the
 * order in which a device type's real front-panel slots line up with
 * `node.interfaces`. That order is exactly what rack3d expects.
 */
import type { Cable, CableMedia, LinkModel, NodeKind, NodeModel, Rack, Topology } from '@/api/types';
import type { DeviceType as CatalogEntry } from '@/api/client';
import { frontPortFractions, frontPortList } from '@/components/rack/DeviceFaceplate';
import { DEVICE_TYPES, resolveDeviceType, type PortType as CatalogPortType } from '@/components/rack/deviceTypes';
import {
  devicePortWorld,
  RACK_SPECS,
  stockLength,
  U,
  type BuildOptions,
  type DeviceDef,
  type DeviceKind,
  type LinkDef,
  type PortType,
  type RackBay,
  type Registry,
} from './rack3d';

export const DEFAULT_ENCLOSURE = 'apc';

/** NodeKind → rack3d chassis art. rack3d has no dedicated router/host/ap/cpe/
 *  cloud model; those render as a generic 1U switch chassis until P5/P6 adds
 *  more art. odf/patch/duct (passive infra) have no NodeKind at all — they
 *  only ever existed as prototype fixtures, so there is nothing to map them
 *  from. */
const KIND_MAP: Partial<Record<NodeKind, DeviceKind>> = {
  switch: 'switch',
  firewall: 'fw',
  server: 'server',
  olt: 'olt',
};

/** Catalog port type → rack3d's coarser port-type set. */
const PTYPE_MAP: Record<CatalogPortType, PortType> = {
  rj45: 'rj45',
  'console-rj45': 'rj45',
  'mgmt-rj45': 'rj45',
  sfp: 'sfp28',
  'sfp+': 'sfp28',
  sfp28: 'sfp28',
  qsfp28: 'qsfp28',
  pon: 'pon',
  'console-usb': 'rj45',
  usb: 'rj45',
  'drive-sff': 'bay',
  'drive-lff': 'bay',
};

/** Backend `CableMedia` (9 values) → rack3d `MEDIA` key (22-PLANT-3D-PLAN §4.P1.a). */
const MEDIA_MAP: Record<CableMedia, string> = {
  cat5e: 'cat6a',
  cat6: 'cat6a',
  cat6a: 'cat6a',
  mmf_om3: 'om3',
  mmf_om4: 'om4',
  smf_os2: 'os2',
  dac: 'dac',
  coax: 'coax',
  gpon_drop: 'os2apc',
};

/** rack3d `MEDIA` key → backend `CableMedia`, for saving a new patch made in
 *  the 3D view (NG-PH3D P2). `MEDIA_MAP` above is lossy (9 backend values →
 *  7 visual ones), so this is a representative preimage per visual key, not
 *  a true inverse. Only covers the keys `mediaFor()` in rack3d.ts can
 *  actually produce; 'mpo' has no backend media at all (P1 §5 dead code) so
 *  it falls back to the closest real fiber trunk type. */
const VISUAL_TO_CABLE_MEDIA: Record<string, CableMedia> = {
  cat6a: 'cat6a',
  os2: 'smf_os2',
  om3: 'mmf_om3',
  om4: 'mmf_om4',
  dac: 'dac',
  coax: 'coax',
  os2apc: 'gpon_drop',
  mpo: 'mmf_om4',
};

export function cableMediaForVisual(visualKey: string): CableMedia {
  return VISUAL_TO_CABLE_MEDIA[visualKey] ?? 'cat6a';
}

const DEAD_LINK_STATUS = new Set(['down', 'admin_down', 'errored']);

function hexNum(hex: string, fallback: number): number {
  const n = Number.parseInt(hex.replace('#', ''), 16);
  return Number.isFinite(n) ? n : fallback;
}

/** Real front-panel port families in left-to-right order — e.g. a switch's
 *  48× RJ45 access bank followed by 4× SFP+ uplinks render as two distinct
 *  connector families in rack3d, instead of the old single "most common
 *  type wins" collapse. Run-length-encoded from `frontPortList()`, the same
 *  canonical port-ordinal walk `frontPortFractions()` uses (see file
 *  header) — not a second port-mapping implementation. */
function devicePortGroups(
  node: NodeModel,
  deviceTypesById?: Map<string, CatalogEntry>,
): { type: PortType; count: number }[] {
  const groups: { type: PortType; count: number }[] = [];
  for (const port of frontPortList(node, deviceTypesById)) {
    if (!port.iface) continue; // unprovisioned slot — no interface to cable
    const mapped = PTYPE_MAP[port.type] ?? 'rj45';
    const last = groups[groups.length - 1];
    if (last && last.type === mapped) last.count++;
    else groups.push({ type: mapped, count: 1 });
  }
  return groups;
}

/** One rack's placed devices, plus the port ordinal each of its interfaces
 *  landed on (needed to resolve LinkDef port indices below). Nodes with an
 *  invalid or RU-colliding placement are skipped, not thrown. */
function adaptRackDevices(
  rack: Rack,
  nodes: NodeModel[],
  deviceTypesById?: Map<string, CatalogEntry>,
): { devices: DeviceDef[]; ordinals: Map<string, number>; ifaceByDevPort: Map<string, string> } {
  const devices: DeviceDef[] = [];
  const ordinals = new Map<string, number>();
  const ifaceByDevPort = new Map<string, string>();
  const occupied = new Set<number>();

  for (const node of nodes) {
    if (node.rack_id !== rack.id) continue;
    const span = node.ru_span ?? 1;
    const start = node.ru_start;
    if (start == null || span < 1 || start < 1 || start + span - 1 > rack.ru_height) continue;
    const cells = Array.from({ length: span }, (_, i) => start + i);
    if (cells.some((c) => occupied.has(c))) continue; // colliding placement — skip, don't crash
    for (const c of cells) occupied.add(c);

    const pack = node.device_type_id ? deviceTypesById?.get(node.device_type_id) : undefined;
    const dt = resolveDeviceType(node.nos, node.kind, node.interfaces, pack);
    // True when `dt` is one of deviceTypes.ts's curated 9 real-model seeds
    // (not a generic-* fallback, not a pack-sourced buildFromPack() entry)
    // but its dimensions are UNVERIFIED (no `chassisMm`) — e.g.
    // arista-7050cx3-32s. Scoped to seed membership so real device-pack
    // nodes (which never carry chassisMm either) keep their existing
    // "not generic" treatment; only the curated-but-unverified case here
    // is new.
    const isUnverifiedCuratedSeed = !dt.chassisMm
      && DEVICE_TYPES.some((seed) => seed.slug === dt.slug && !seed.slug.startsWith('generic-'));
    const orderedIfaceIds = [...frontPortFractions(node, span, deviceTypesById).keys()];
    orderedIfaceIds.forEach((ifaceId, ordinal) => {
      ordinals.set(ifaceId, ordinal);
      ifaceByDevPort.set(`${node.id}:${ordinal}`, ifaceId);
    });

    devices.push({
      id: node.id,
      u: start,
      h: span,
      kind: KIND_MAP[node.kind] ?? 'switch',
      brand: dt.brand.label,
      model: dt.model,
      accent: hexNum(dt.brand.accent, 0x847e75),
      chassis: hexNum(dt.brand.chassis, 0x1c1c1a),
      ports: orderedIfaceIds.length,
      portGroups: orderedIfaceIds.length ? devicePortGroups(node, deviceTypesById) : undefined,
      // dt.slug is 'generic-*' when resolveDeviceType() couldn't match a
      // real curated model (see genericFor() there); isUnverifiedCuratedSeed
      // covers the curated-but-dimensionally-unverified case (§8.1) — both
      // get the same "approximate shape" marker (faceTexture()'s dashed
      // hachure border).
      generic: dt.slug.startsWith('generic-') || isUnverifiedCuratedSeed,
      bodyWidthM: dt.chassisMm ? dt.chassisMm.widthMm / 1000 : undefined,
      bodyDepthM: dt.chassisMm ? dt.chassisMm.depthMm / 1000 : undefined,
      hasLcd: dt.front.hasLcd,
    });
  }
  return { devices, ordinals, ifaceByDevPort };
}

/** Every real rack in `topology` that belongs to `siteId` ('' / null bucket
 *  for a rack with no site), in the topology's own order — the incremental-
 *  reveal contract (Surya's spec): whatever the backend returns for the
 *  currently viewed site, no more, no fewer, no manual A/B picking. */
export function racksForSite(racks: Rack[], siteId: string | null): string[] {
  return racks.filter((r) => (r.site_id ?? null) === siteId).map((r) => r.id);
}

/**
 * Build the N-bay scene input from a real project. `rackIds` is the
 * left-to-right row of real racks to show (NG-PH3D P41: was a fixed
 * rackAId/rackBId pair — rack3d.ts now lays out however many bays it's
 * given). Unknown ids are dropped. Returns null when that resolves to zero
 * real racks — the caller must not build a scene at all in that case (an
 * empty `racks: []` would still draw the shared CPI cabinet prop).
 */
export function adaptTopology(
  topology: Topology,
  rackIds: string[],
  /** N4: caller's already-fetched /device-types list, keyed by id — resolves
   *  each node's `device_type_id` to real pack port data for its faceplate. */
  deviceTypesById?: Map<string, CatalogEntry>,
): (BuildOptions & { ifaceByDevPort: Map<string, string>; cableIds: string[] }) | null {
  const allRacks = topology.racks ?? [];
  const rackById = new Map(allRacks.map((r) => [r.id, r]));
  const shown = rackIds.map((id) => rackById.get(id)).filter((r): r is Rack => !!r);
  if (shown.length === 0) return null;

  const nodes = topology.nodes ?? [];
  const nodeByIface = new Map<string, NodeModel>();
  for (const node of nodes) {
    for (const iface of node.interfaces ?? []) nodeByIface.set(iface.id, node);
  }

  const bays: RackBay[] = [];
  const ordinalsByIface = new Map<string, number>();
  const devIdToKey = new Map<string, string>();
  const ifaceByDevPort = new Map<string, string>();
  for (const rack of shown) {
    const { devices, ordinals, ifaceByDevPort: devPorts } = adaptRackDevices(rack, nodes, deviceTypesById);
    bays.push({ key: rack.id, enclosure: rack.enclosure_profile ?? DEFAULT_ENCLOSURE, devices });
    for (const d of devices) devIdToKey.set(d.id, rack.id);
    for (const [ifaceId, ordinal] of ordinals) ordinalsByIface.set(ifaceId, ordinal);
    for (const [key, ifaceId] of devPorts) ifaceByDevPort.set(key, ifaceId);
  }

  const linkById = new Map<string, LinkModel>((topology.links ?? []).map((l) => [l.id, l]));
  const links: LinkDef[] = [];
  // Parallel to `links` (same index) — the backend Cable id that realizes
  // each entry, so a caller can PATCH the right row back (NG-PH3D P3 §5:
  // recompute length_m after a placement change, reusing this join instead
  // of re-deriving it a second time).
  const cableIds: string[] = [];
  for (const cable of (topology.cables ?? []) as Cable[]) {
    const link = linkById.get(cable.link_id);
    if (!link) continue;
    const aNode = nodeByIface.get(link.a_iface);
    const bNode = nodeByIface.get(link.b_iface);
    if (!aNode || !bNode) continue;
    if (!devIdToKey.has(aNode.id) || !devIdToKey.has(bNode.id)) continue; // endpoint outside the shown bays
    const aOrd = ordinalsByIface.get(link.a_iface);
    const bOrd = ordinalsByIface.get(link.b_iface);
    if (aOrd === undefined || bOrd === undefined) continue; // interface has no cable-able front port
    links.push({
      a: [aNode.id, aOrd],
      b: [bNode.id, bOrd],
      m: MEDIA_MAP[cable.media] ?? 'cat6a',
      live: !DEAD_LINK_STATUS.has(link.status ?? 'up'),
    });
    cableIds.push(cable.id);
  }

  return { racks: bays, links, ifaceByDevPort, cableIds };
}

export const ENCLOSURE_KEYS = Object.keys(RACK_SPECS);

/**
 * NG-PH3D P3 §5: `Cable.length_m` is only ever set once, at creation
 * (`createPatch` in Rack3DElevationPanel, straight-line port distance
 * rounded by `stockLength()`) — moving one of the two devices afterwards
 * never touches it, so the stored length silently understates reality.
 *
 * Called right after a moved/placed node's rack rebuilds the scene: for
 * every cable that touches `nodeId` and has both endpoints in the just-built
 * registry, recompute the same way `createPatch` originally did and report
 * which cables actually changed. Pure — no network call here, the caller
 * PATCHes `/cables/{id}`. Reuses `devicePortWorld`/`stockLength`, no second
 * length formula.
 */
export function cableLengthUpdatesForNode(
  registry: Registry,
  adapted: { links: LinkDef[]; cableIds: string[] },
  cables: Cable[],
  nodeId: string,
): { cableId: string; lengthM: number }[] {
  const cableById = new Map(cables.map((c) => [c.id, c]));
  const updates: { cableId: string; lengthM: number }[] = [];
  adapted.links.forEach((l, i) => {
    if (l.a[0] !== nodeId && l.b[0] !== nodeId) return;
    const cable = cableById.get(adapted.cableIds[i]!);
    if (!cable) return;
    const pa = devicePortWorld(registry, l.a[0], l.a[1]);
    const pb = devicePortWorld(registry, l.b[0], l.b[1]);
    if (!pa || !pb) return;
    const lengthM = stockLength(pa.distanceTo(pb));
    if (lengthM !== cable.length_m) updates.push({ cableId: cable.id, lengthM });
  });
  return updates;
}

/* ------------------------------ Drag-to-move ------------------------------ */
/**
 * Direct 3D device move (Surya: "pindah perangkat bisa langsung di rack 3D
 * tanpa perlu ada lagi mode elevasi ... semua 3D"). These three pure
 * functions are the whole decision surface for a drag in
 * Rack3DElevationPanel — the panel's pointer handlers only turn a raycast
 * hit into world coordinates and call these, so "does this drop send a
 * request, and what does it send" is unit-testable without a WebGL context.
 */

/** Nearest rack (by x-distance) to a raycast hit point, and the RU slot at
 *  that height — the same nearest-bay + U arithmetic the click-to-add-device
 *  flow (`handleAddDevice`) already used inline; factored out so drag-move
 *  shares it instead of a second copy. Returns null when the hit is too far
 *  from every rack's centre line (`tolerance`, metres) to sensibly belong to
 *  one — matches `handleAddDevice`'s own 0.45 m default. */
export function resolveDropTarget(
  rackXs: { key: string; x: number }[],
  hit: { x: number; y: number },
  tolerance = 0.45,
): { rackKey: string; ru: number } | null {
  if (rackXs.length === 0) return null;
  let best = rackXs[0]!;
  let bd = Math.abs(hit.x - best.x);
  for (const r of rackXs.slice(1)) {
    const d = Math.abs(hit.x - r.x);
    if (d < bd) { bd = d; best = r; }
  }
  if (bd >= tolerance) return null;
  return { rackKey: best.key, ru: Math.floor((hit.y - 0.055) / U) + 1 };
}

/** Would `span` RUs starting at `ru` in `rackKey` be a legal placement? Same
 *  two checks the backend's `update_node` makes (in-range, no RU overlap —
 *  memory.py) run client-side first, so an occupied or oversized drop is
 *  refused before any request goes out (Surya: "slot yang sudah terisi atau
 *  tidak muat ditolak dengan jelas sebelum request dikirim"). `excludeId` is
 *  the device being moved itself, which must not collide with its own
 *  current slot. */
export function canPlaceDevice(
  bays: RackBay[],
  rackKey: string,
  ru: number,
  span: number,
  ruHeight: number,
  excludeId?: string,
): boolean {
  if (ru < 1 || ru + span - 1 > ruHeight) return false;
  const bay = bays.find((b) => b.key === rackKey);
  if (!bay) return false;
  const lo = ru, hi = ru + span - 1;
  return !bay.devices.some((d) => {
    if (d.id === excludeId) return false;
    const oLo = d.u, oHi = d.u + d.h - 1;
    return lo <= oHi && oLo <= hi;
  });
}

/** What a released drag should do: commit a PATCH, or send nothing at all.
 *  Nothing goes out for a target that's unresolved (dropped off every
 *  rack), invalid (`canPlaceDevice`), or identical to where the device
 *  already was — that last case matters as much as the others: without it,
 *  a plain reselect-and-release would fire an identical, pointless PATCH. */
export function dropDecision(
  target: { rackKey: string; ru: number } | null,
  span: number,
  ruHeightByRack: Record<string, number>,
  bays: RackBay[],
  origin: { rackKey: string; ru: number },
  deviceId: string,
): { commit: false } | { commit: true; rackId: string; ruStart: number } {
  if (!target) return { commit: false };
  if (target.rackKey === origin.rackKey && target.ru === origin.ru) return { commit: false };
  const ruHeight = ruHeightByRack[target.rackKey];
  if (ruHeight == null) return { commit: false };
  if (!canPlaceDevice(bays, target.rackKey, target.ru, span, ruHeight, deviceId)) return { commit: false };
  return { commit: true, rackId: target.rackKey, ruStart: target.ru };
}
