/**
 * NG-PH3D 3a — minimum bend-radius invariant: a routed cable must never bend
 * tighter than its own media's physical limit (docs/design/24-DEVICE-
 * PHYSICAL-SPEC.md §2.b — MEDIA[...].minBendM). Same shape as P4's
 * no-intersection test: build real scenes through the production
 * buildScene(), sample each cable's actual curve, and check a real
 * geometric property instead of re-deriving the routing math.
 *
 * Curvature is estimated via the Menger curvature of three closely-spaced
 * points on the curve (radius = |AB|·|BC|·|CA| / (4·area(ABC))) — exact for
 * a circular arc, a good local estimate for any smooth curve at small Δt.
 */
import { describe, expect, it } from 'vitest';
import * as THREE from 'three';
import { buildScene, disposeScene, MEDIA, type BuildOptions, type DeviceDef, type LinkDef } from './rack3d';

function dev(id: string, u: number, h: number, kind: DeviceDef['kind'], ports = 0, ptype: DeviceDef['ptype'] = undefined): DeviceDef {
  return { id, u, h, kind, brand: 'Test', model: 'TST-1', accent: 0x847e75, chassis: 0x1c1c1a, ports, ptype };
}
function link(a: [string, number], b: [string, number], m = 'cat6a', live = true): LinkDef {
  return { a, b, m, live };
}

/** Local radius of curvature at parameter t, via the Menger curvature of
 *  three points spaced dt apart. Returns Infinity for a (near-)straight run. */
function curvatureRadiusAt(curve: THREE.Curve<THREE.Vector3>, t: number, dt = 0.004): number {
  const lo = Math.max(0, t - dt), hi = Math.min(1, t + dt);
  const A = curve.getPointAt(lo), B = curve.getPointAt(t), C = curve.getPointAt(hi);
  const ab = A.distanceTo(B), bc = B.distanceTo(C), ca = C.distanceTo(A);
  const area = new THREE.Vector3().subVectors(B, A).cross(new THREE.Vector3().subVectors(C, A)).length() / 2;
  if (area < 1e-9) return Infinity; // collinear — no bend here
  return (ab * bc * ca) / (4 * area);
}

function assertBendRadius(built: ReturnType<typeof buildScene>, samplesPerCable = 60) {
  const violations: string[] = [];
  for (const cable of built.registry.cables) {
    const minBend = MEDIA[cable.mediaKey]?.minBendM;
    if (!minBend) continue; // no verified number for this media — not asserted
    for (let i = 1; i < samplesPerCable; i++) {
      const t = i / samplesPerCable;
      if (t < 0.03 || t > 0.97) continue; // connector seat, not a routing bend
      const r = curvatureRadiusAt(cable.curve, t);
      if (r < minBend) {
        violations.push(`${cable.meta.name} (${cable.mediaKey}) @ t=${t.toFixed(2)}: radius ${(r * 1000).toFixed(1)}mm < min ${(minBend * 1000).toFixed(0)}mm`);
      }
    }
  }
  expect(violations).toEqual([]);
}

function bigScene(): BuildOptions {
  return {
    racks: [
      { key: 'A', enclosure: 'apc', devices: [
        dev('a-patch', 1, 1, 'patch', 24, 'rj45'),
        dev('a-odf', 2, 1, 'odf', 24, 'lc'),
        dev('a-sw1', 3, 1, 'switch', 24, 'rj45'),
        dev('a-sw2', 20, 1, 'switch', 12, 'sfp28'),
        dev('a-top', 41, 1, 'switch', 24, 'rj45'),
      ] },
      { key: 'B', enclosure: 'hpe', devices: [dev('b-sw', 5, 1, 'switch', 24, 'rj45')] },
    ],
    links: [
      link(['a-patch', 0], ['a-sw1', 0], 'cat6a'), // near jumper, catenary path
      link(['a-patch', 1], ['a-odf', 0], 'os2'), // near jumper, fibre
      link(['a-sw1', 5], ['a-sw2', 0], 'om3'), // full channel run
      link(['a-sw1', 8], ['a-top', 0], 'cat6a_oob'), // full-height run
      link(['a-sw1', 10], ['b-sw', 4], 'cat6a'), // cross-rack
      link(['a-odf', 2], ['b-sw', 6], 'om4'), // cross-rack fibre
    ],
  };
}

describe('minimum bend-radius invariant (NG-PH3D 3a)', () => {
  it('every media with a verified §2.b minBendM never bends tighter than it, across near jumpers, channel runs and cross-rack spans', () => {
    const built = buildScene(bigScene());
    built.root.updateMatrixWorld(true);
    try {
      assertBendRadius(built);
    } finally {
      disposeScene(built);
    }
  });
});
