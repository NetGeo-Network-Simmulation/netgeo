/**
 * plant — small pure helpers shared by the 2D (`RackElevationPanel`) and 2.5D
 * (`Rack3DElevationPanel`) physical-plant views (NG-PH3D P3).
 *
 * Both panels read the same `Topology`/`PlantReport` data; this file is the
 * single place that turns it into "watts for this node", "which cables are
 * over length", and "which nodes have nowhere to render" — so the two views
 * can never silently disagree on a number that means the same thing in both.
 */
import type { DeviceType } from '@/api/client';
import type { Cable, NodeKind, NodeModel, PlantLink } from '@/api/types';

/** Estimated steady-state draw per node kind, in watts — fallback only for
 *  kinds with no catalog match (e.g. `host`) or before the catalog loads. */
export const KIND_WATTS: Record<string, number> = {
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

/** Index device types by their builtin `icon` for O(1) watt lookups. */
export function wattsByIconMap(deviceTypes: DeviceType[] | undefined): Map<string, DeviceType> {
  const map = new Map<string, DeviceType>();
  for (const dt of deviceTypes ?? []) {
    if (dt.icon) map.set(dt.icon, dt);
  }
  return map;
}

/** Per-device wattage: prefers the /device-types datasheet, falls back to
 *  `KIND_WATTS` for kinds with no catalog match. */
export function nodeWatts(n: NodeModel, byIcon?: Map<string, DeviceType>): number {
  const dt = byIcon?.get(KIND_TO_ICON[n.kind] ?? n.kind);
  const watts = dt?.power_watts_max ?? dt?.power_watts_idle;
  return watts ?? KIND_WATTS[n.kind] ?? 150;
}

/** watts → heat load in BTU/hr (1 W ≈ 3.412 BTU/hr). */
export function wattsToBtu(w: number): number {
  return Math.round(w * 3.412);
}

/** A node has nowhere valid to render: no rack at all, or a rack assignment
 *  with no RU start (the P1 adapter's silent-skip case — surfaced here
 *  instead, NG-PH3D P3). */
export function isUnplaced(n: NodeModel): boolean {
  return !n.rack_id || n.ru_start == null;
}

export function unplacedNodes(nodes: NodeModel[]): NodeModel[] {
  return nodes.filter(isUnplaced);
}

/** Cables whose link is flagged over-length in the plant report (NG-PH-03),
 *  joined back to their media for display — same threshold/source the 2D
 *  panel uses, so both views always agree on which runs are bad. */
export function overLengthCables(
  cables: Cable[],
  plantLinks: Record<string, PlantLink> | undefined,
): { cable: Cable; media: string }[] {
  const links = plantLinks ?? {};
  return cables
    .filter((c) => links[c.link_id]?.over_length)
    .map((c) => ({ cable: c, media: links[c.link_id]?.over_media ?? c.media }));
}
