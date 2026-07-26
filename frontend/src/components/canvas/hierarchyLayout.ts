/**
 * hierarchyLayout — pure BFS + post-order tree layout for the topology canvas.
 * §A5.2 of docs/design/16-UISP-PARITY-PLAN.md: no dagre/elk needed, the shape
 * we render (Internet -> backbone -> tower -> device -> client) is a tree.
 *
 * Root: the `cloud` node (NetGeo's real-world uplink, UISP's "Internet" node),
 * falling back to the highest-degree node. BFS assigns each node a `depth`
 * (-> x). A post-order sweep hands leaves sequential slots and parents the
 * average of their children's slots (simplified Reingold-Tilford, -> y).
 * Disconnected components get their own root and stack below the first tree
 * (the slot counter is shared and keeps advancing across components).
 */
import type { LinkModel, NodeModel } from '@/api/types';

const COL_W = 220;
const ROW_H = 100;

export function hierarchyLayout(
  nodes: NodeModel[],
  links: LinkModel[],
): Map<string, { x: number; y: number }> {
  const ifaceToNode = new Map<string, string>();
  for (const n of nodes) for (const i of n.interfaces) ifaceToNode.set(i.id, n.id);

  const adj = new Map<string, Set<string>>();
  for (const n of nodes) adj.set(n.id, new Set());
  for (const l of links) {
    const a = ifaceToNode.get(l.a_iface) ?? l.a_iface;
    const b = ifaceToNode.get(l.b_iface) ?? l.b_iface;
    if (a === b || !adj.has(a) || !adj.has(b)) continue;
    adj.get(a)!.add(b);
    adj.get(b)!.add(a);
  }

  // BFS per component: depth + tree parent/children (first-seen edge wins ties).
  const depth = new Map<string, number>();
  const children = new Map<string, string[]>();
  const roots: string[] = [];
  const unvisited = new Set(nodes.map((n) => n.id));
  for (const n of nodes) children.set(n.id, []);

  while (unvisited.size > 0) {
    const cloudRoot = nodes.find((n) => unvisited.has(n.id) && n.kind === 'cloud');
    let root = cloudRoot?.id ?? null;
    if (!root) {
      let bestDeg = -1;
      for (const id of unvisited) {
        const deg = adj.get(id)!.size;
        if (deg > bestDeg) { bestDeg = deg; root = id; }
      }
    }
    if (!root) break; // unreachable (unvisited non-empty implies a candidate exists)

    roots.push(root);
    depth.set(root, 0);
    unvisited.delete(root);
    const queue = [root];
    while (queue.length) {
      const cur = queue.shift()!;
      for (const nb of adj.get(cur)!) {
        if (!unvisited.has(nb)) continue;
        unvisited.delete(nb);
        depth.set(nb, depth.get(cur)! + 1);
        children.get(cur)!.push(nb);
        queue.push(nb);
      }
    }
  }

  // Post-order: leaves get the next free slot, parents average their children.
  let nextSlot = 0;
  const slot = new Map<string, number>();
  const assign = (id: string): number => {
    const kids = children.get(id)!;
    const s = kids.length === 0
      ? nextSlot++
      : kids.map(assign).reduce((sum, v) => sum + v, 0) / kids.length;
    slot.set(id, s);
    return s;
  };
  for (const root of roots) assign(root);

  const positions = new Map<string, { x: number; y: number }>();
  for (const n of nodes) {
    positions.set(n.id, { x: (depth.get(n.id) ?? 0) * COL_W, y: (slot.get(n.id) ?? 0) * ROW_H });
  }
  return positions;
}
