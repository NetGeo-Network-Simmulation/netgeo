/**
 * floatingEdge — React Flow's official "floating edges" geometry helper.
 *
 * Instead of anchoring an edge at a fixed handle side (top/bottom), we compute
 * the point on each node's border that faces the *other* node, so the cable
 * always leaves from the nearest side (n8n-style). Pure geometry, theme-agnostic.
 * See: https://reactflow.dev/examples/edges/floating-edges
 */
import { Position, type InternalNode, type Node } from '@xyflow/react';

/** Point on `node`'s border nearest to `target`, plus the side it exits from. */
function nodeIntersection(
  node: InternalNode<Node>,
  target: InternalNode<Node>,
): { x: number; y: number } {
  const w = (node.measured.width ?? 0) / 2;
  const h = (node.measured.height ?? 0) / 2;
  const nx = node.internals.positionAbsolute.x + w;
  const ny = node.internals.positionAbsolute.y + h;
  const tx = target.internals.positionAbsolute.x + (target.measured.width ?? 0) / 2;
  const ty = target.internals.positionAbsolute.y + (target.measured.height ?? 0) / 2;

  // Standard box-intersection: project the center-to-center ray onto the border.
  const xx1 = (tx - nx) / (2 * w) - (ty - ny) / (2 * h);
  const yy1 = (tx - nx) / (2 * w) + (ty - ny) / (2 * h);
  const a = 1 / (Math.abs(xx1) + Math.abs(yy1) || 1);
  const xx3 = a * xx1;
  const yy3 = a * yy1;
  return { x: w * (xx3 + yy3) + nx, y: h * (-xx3 + yy3) + ny };
}

/** Which border side the intersection point sits on (for the bezier tangent). */
function edgePosition(node: InternalNode<Node>, p: { x: number; y: number }): Position {
  const nx = Math.round(node.internals.positionAbsolute.x);
  const ny = Math.round(node.internals.positionAbsolute.y);
  const px = Math.round(p.x);
  const py = Math.round(p.y);
  if (px <= nx + 1) return Position.Left;
  if (px >= nx + (node.measured.width ?? 0) - 1) return Position.Right;
  if (py <= ny + 1) return Position.Top;
  return Position.Bottom;
}

/**
 * Centre of `node`'s port dot on `side`, in flow coords. React Flow measures
 * handle bounds via getBoundingClientRect / zoom, so `x`/`y` already include the
 * v12 `translate(±50%,±50%)` that centres the dot on the border — the centre of
 * that box is the exact point the user sees.
 * Falls back to the border midpoint on the frame before handles are measured,
 * which is where the dot renders anyway.
 */
function handlePoint(node: InternalNode<Node>, side: Position): { x: number; y: number } {
  const { x: ox, y: oy } = node.internals.positionAbsolute;
  const w = node.measured.width ?? 0;
  const h = node.measured.height ?? 0;
  const b = node.internals.handleBounds;
  const hit = [...(b?.source ?? []), ...(b?.target ?? [])].find((k) => k.position === side);
  if (hit) return { x: ox + hit.x + hit.width / 2, y: oy + hit.y + hit.height / 2 };
  switch (side) {
    case Position.Left:
      return { x: ox, y: oy + h / 2 };
    case Position.Right:
      return { x: ox + w, y: oy + h / 2 };
    case Position.Top:
      return { x: ox + w / 2, y: oy };
    default:
      return { x: ox + w / 2, y: oy + h };
  }
}

export interface FloatingEdgeParams {
  sx: number;
  sy: number;
  tx: number;
  ty: number;
  sourcePos: Position;
  targetPos: Position;
}

/** Anchor points + sides for a floating edge between two internal nodes. */
export function getFloatingEdgeParams(
  source: InternalNode<Node>,
  target: InternalNode<Node>,
): FloatingEdgeParams {
  // The *side* still comes from the centre-to-centre ray, so cables re-route as
  // nodes move. The *anchor* is then snapped to that side's actual port dot:
  // the raw border point drifts along the side (4.6px per 16px grid step on a
  // 184x54 card) and lands in the rounded-lg corner arc, which is the visible
  // gap between the curve tip and the card.
  const sourcePos = edgePosition(source, nodeIntersection(source, target));
  const targetPos = edgePosition(target, nodeIntersection(target, source));
  const s = handlePoint(source, sourcePos);
  const t = handlePoint(target, targetPos);
  return { sx: s.x, sy: s.y, tx: t.x, ty: t.y, sourcePos, targetPos };
}
