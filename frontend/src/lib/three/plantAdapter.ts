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
import { frontPortFractions } from '@/components/rack/DeviceFaceplate';
import { resolveDeviceType, type PortType as CatalogPortType } from '@/components/rack/deviceTypes';
import {
  RACK_SPECS,
  type BuildOptions,
  type DeviceDef,
  type DeviceKind,
  type LinkDef,
  type PortType,
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

const DEAD_LINK_STATUS = new Set(['down', 'admin_down', 'errored']);

function hexNum(hex: string, fallback: number): number {
  const n = Number.parseInt(hex.replace('#', ''), 16);
  return Number.isFinite(n) ? n : fallback;
}

/** The port type rack3d should render for this device: the most common
 *  catalog port type among its zones, weighted by port count. */
function dominantPtype(node: NodeModel): PortType | undefined {
  const dt = resolveDeviceType(node.nos, node.kind, node.interfaces);
  const counts = new Map<PortType, number>();
  for (const zone of dt.front.portZones) {
    for (const spec of zone.ports) {
      const mapped = PTYPE_MAP[spec.type] ?? 'rj45';
      counts.set(mapped, (counts.get(mapped) ?? 0) + spec.count);
    }
  }
  let best: PortType | undefined;
  let bestN = 0;
  for (const [ptype, n] of counts) {
    if (n > bestN) { best = ptype; bestN = n; }
  }
  return best;
}

/** One rack's placed devices, plus the port ordinal each of its interfaces
 *  landed on (needed to resolve LinkDef port indices below). Nodes with an
 *  invalid or RU-colliding placement are skipped, not thrown. */
function adaptRackDevices(
  rack: Rack,
  nodes: NodeModel[],
): { devices: DeviceDef[]; ordinals: Map<string, number> } {
  const devices: DeviceDef[] = [];
  const ordinals = new Map<string, number>();
  const occupied = new Set<number>();

  for (const node of nodes) {
    if (node.rack_id !== rack.id) continue;
    const span = node.ru_span ?? 1;
    const start = node.ru_start;
    if (start == null || span < 1 || start < 1 || start + span - 1 > rack.ru_height) continue;
    const cells = Array.from({ length: span }, (_, i) => start + i);
    if (cells.some((c) => occupied.has(c))) continue; // colliding placement — skip, don't crash
    for (const c of cells) occupied.add(c);

    const dt = resolveDeviceType(node.nos, node.kind, node.interfaces);
    const orderedIfaceIds = [...frontPortFractions(node, span).keys()];
    orderedIfaceIds.forEach((ifaceId, ordinal) => ordinals.set(ifaceId, ordinal));

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
      ptype: orderedIfaceIds.length ? dominantPtype(node) : undefined,
    });
  }
  return { devices, ordinals };
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
): BuildOptions | null {
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
  for (const [slot, rack] of [['A', rackA], ['B', rackB]] as const) {
    if (!rack) continue;
    const { devices, ordinals } = adaptRackDevices(rack, nodes);
    fitout[slot] = devices;
    for (const d of devices) devIdToSlot.set(d.id, slot);
    for (const [ifaceId, ordinal] of ordinals) ordinalsByIface.set(ifaceId, ordinal);
  }

  const linkById = new Map<string, LinkModel>((topology.links ?? []).map((l) => [l.id, l]));
  const links: LinkDef[] = [];
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
  }

  return {
    rackA: rackA?.enclosure_profile ?? DEFAULT_ENCLOSURE,
    rackB: rackB?.enclosure_profile ?? DEFAULT_ENCLOSURE,
    fitout,
    links,
  };
}

export const ENCLOSURE_KEYS = Object.keys(RACK_SPECS);
