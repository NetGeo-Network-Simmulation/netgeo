# Blender asset pipeline (NG-PH3D 3a)

Headless script that generates the connector-boot / cage GLB assets used by
`frontend/src/lib/three/rack3d.ts` (via `frontend/src/lib/three/bootAssets.ts`).

## Rebuild from scratch

Requires Blender 5.2.0 LTS (tested at `/usr/bin/blender`):

```sh
blender --background --python tools/blender/build_assets.py
```

Writes `frontend/public/3d/{boot-rj45,boot-lc,cage-sfp,cage-qsfp,cage-rj11,
cabinet-outdoor,tower-monopole,tower-lattice4,tower-lattice3}.glb`. A few
non-fatal `ERROR`/`DeprecationWarning` lines about OCIO colour management,
Draco, and MeshOptimizer are expected — none affect the exported geometry.

## Source of truth

`build_assets.py` is the source of truth, not the `.glb` files. The GLBs
**are committed** so CI and normal builds never need Blender installed — only
someone changing the model geometry needs to re-run the script and commit
the new output.

The pipeline is deterministic: re-running it reproduces byte-identical GLBs
(verified via `sha256sum`) as long as the script and Blender version are
unchanged.

Every dimension modelled is sourced from
`docs/design/24-DEVICE-PHYSICAL-SPEC.md` §2.a/§2.c — see the script's own
module docstring for the exact citations and scope boundary (only
dimension-verified parts are modelled with real numbers).

The outdoor cabinet (Slice 5) uses a real, sourced footprint (Rittal TS 8
Type 3R, 600x800x2000mm) — see `build_cabinet_outdoor()`. The tower
structures (Slice 7, `build_monopole()`/`build_lattice_tower()`) are
NORMALIZED to unit height (1.0) instead: no authoritative height range
exists for any tower type (only vendor-blog numbers, excluded as a fact
source — see the `tower-structure-taxonomy` memory note), so these meshes
are representative silhouettes meant to be rescaled at render time, not a
vendor dimension claim.
