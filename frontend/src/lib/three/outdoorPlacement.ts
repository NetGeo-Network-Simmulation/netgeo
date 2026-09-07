/**
 * Outdoor placement (Slice 6): pure mapping from backend site/node fields to
 * the inputs `buildScene`'s `structure`/`outdoorNodes` options expect. Kept
 * free of `three` and of the real `Site`/`NodeMount` API types (just the
 * fields it reads) so it stays trivially unit-testable — same separation
 * plantAdapter.ts already keeps between backend shapes and rack3d.ts.
 */
import type { StructureFamily } from './bootAssets';

export type StructureType = 'monopole' | 'self-supporting-lattice' | 'guyed-mast';

/** tower-structure-taxonomy.md: only 3 `structure_type` values exist, but
 *  only 2 tower GLBs were built (Slice 7) — a guyed mast is a thin
 *  unsupported pole in cross-section (its guy wires are the missing
 *  structural element, not extra bulk), so it reuses the monopole mesh; the
 *  wires themselves are deliberately not modelled this slice
 *  (build_assets.py's own note on this). */
export const STRUCTURE_FAMILY_BY_TYPE: Record<StructureType, StructureFamily> = {
  monopole: 'tower-monopole',
  'guyed-mast': 'tower-monopole',
  'self-supporting-lattice': 'tower-lattice4',
};

// ponytail: no authoritative height source exists for any structure_type
// (tower-structure-taxonomy.md's own finding — only excluded vendor-blog
// numbers) — this is a render fallback so an unset site still shows a
// plausible-looking tower instead of a 1m stub, never a real dimension
// claim. Raise this only alongside a real source, not a "looks too short".
export const FALLBACK_STRUCTURE_HEIGHT_M = 30;

/** The GLB's normalized unit height is 1.0 with no independently-verified
 *  taper ratio to preserve (build_assets.py), so height_agl_m rescales the
 *  whole silhouette uniformly rather than stretching only Y. */
export function structureScale(heightAglM: number | null | undefined): number {
  return heightAglM && heightAglM > 0 ? heightAglM : FALLBACK_STRUCTURE_HEIGHT_M;
}

/** `mount_location === 'rooftop' && camouflage === true` has no mesh of its
 *  own — a disguised rooftop installation is a placement/material
 *  attribute, not a distinct model (taxonomy §5, build_assets.py's own
 *  note) — callers must skip the tower mesh for these sites rather than
 *  drawing the wrong shape. */
export function isHiddenStructure(
  mountLocation: 'ground' | 'rooftop' | null | undefined,
  camouflage: boolean | null | undefined,
): boolean {
  return mountLocation === 'rooftop' && camouflage === true;
}

export interface StructureSpecInput {
  structure_type?: StructureType | null;
  mount_location?: 'ground' | 'rooftop' | null;
  camouflage?: boolean | null;
  height_agl_m?: number | null;
}

export interface StructureSpec {
  family: StructureFamily;
  scale: number;
}

/** The `{family, scale}` `buildScene`'s `structure` option expects, or
 *  `null` when nothing should be drawn (no `structure_type` recorded yet,
 *  or the hidden-rooftop case above). */
export function structureSpecFor(site: StructureSpecInput): StructureSpec | null {
  if (!site.structure_type) return null;
  if (isHiddenStructure(site.mount_location, site.camouflage)) return null;
  return { family: STRUCTURE_FAMILY_BY_TYPE[site.structure_type], scale: structureScale(site.height_agl_m) };
}

/** World Y (metres) for a node mounted directly on a structure —
 *  `mount.height_agl_m` when recorded, ground level (0) otherwise (a
 *  ground/strand mount with no height entered yet, or a legacy node from
 *  before `mount` existed). */
export function mountElevationM(heightAglM: number | null | undefined): number {
  return heightAglM ?? 0;
}
