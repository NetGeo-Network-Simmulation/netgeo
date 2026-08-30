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
import { resolveDeviceType, type PortType as CatalogPortType } from '@/components/rack/deviceTypes';
import {
  devicePortWorld,
  RACK_SPECS,
  stockLength,
  type BuildOptions,
  type DeviceDef,
  type DeviceKind,
  type LinkDef,
  type PortType,
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
      // dt.slug is 'generic-*' only when resolveDeviceType() couldn't match
      // a real curated model (deviceTypes.ts) — see genericFor() there.
      generic: dt.slug.startsWith('generic-'),
    });
  }
  return { devices, ordinals, ifaceByDevPort };
}

/**
 * Build the two-bay scene input from a real project. `rackAId`/`rackBId`
 * pick which of the project's racks fill the two 3D bays (rack3d.ts only
 * lays out two side by side today — see report). Either may be null; a
 * missing rack renders as an empty bay with its default enclosure.
 * Returns null only when there is nothing to show at all.
 */
export function adaptTopology(
  topology: Topology,
  rackAId: string | null,
  rackBId: string | null,
  /** N4: caller's already-fetched /device-types list, keyed by id — resolves
   *  each node's `device_type_id` to real pack port data for its faceplate. */
  deviceTypesById?: Map<string, CatalogEntry>,
): (BuildOptions & { ifaceByDevPort: Map<string, string>; cableIds: string[] }) | null {
  const racks = topology.racks ?? [];
  const rackA = racks.find((r) => r.id === rackAId);
  const rackB = racks.find((r) => r.id === rackBId);
  if (!rackA && !rackB) return null;

  const nodes = topology.nodes ?? [];
  const nodeByIface = new Map<string, NodeModel>();
  for (const node of nodes) {
    for (const iface of node.interfaces ?? []) nodeByIface.set(iface.id, node);
  }

  const fitout: Record<string, DeviceDef[]> = { A: [], B: [] };
  const ordinalsByIface = new Map<string, number>();
  const devIdToSlot = new Map<string, string>();
  const ifaceByDevPort = new Map<string, string>();
  for (const [slot, rack] of [['A', rackA], ['B', rackB]] as const) {
    if (!rack) continue;
    const { devices, ordinals, ifaceByDevPort: devPorts } = adaptRackDevices(rack, nodes, deviceTypesById);
    fitout[slot] = devices;
    for (const d of devices) devIdToSlot.set(d.id, slot);
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
    if (!devIdToSlot.has(aNode.id) || !devIdToSlot.has(bNode.id)) continue; // endpoint outside the two shown bays
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

  return {
    rackA: rackA?.enclosure_profile ?? DEFAULT_ENCLOSURE,
    rackB: rackB?.enclosure_profile ?? DEFAULT_ENCLOSURE,
    fitout,
    links,
    ifaceByDevPort,
    cableIds,
  };
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
