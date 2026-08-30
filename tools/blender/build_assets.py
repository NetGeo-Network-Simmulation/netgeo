"""NetGeo rackmount 3D — headless Blender asset pipeline (Sesi 3a).

Run:  blender --background --python tools/blender/build_assets.py
Output: frontend/public/3d/*.glb (committed — this script is the source of
truth; the .glb files are its reproducible output, not a manual export).

Every dimension below is sourced from docs/design/24-DEVICE-PHYSICAL-SPEC.md
§2.a/§2.c (SFF-8432 Rev 5.1, SFF-8663 Rev 1.7, Senko LC datasheet — all
"V"/"V(2nd)" status, opened & read directly, not the vendor's own CAD). Models
are authored from scratch here; no vendor 3D asset is imported (legal
boundary — datasheets are a dimension reference only).

Axis convention: each part is authored with its LENGTH along Blender's local
Z (the exporter's default "+Y Up" conversion rotates Blender Z -> glTF/three.js
Y), so the exported mesh's local +Y is the connector's plug-in axis — the
axis rack3d.ts already aligns to a cable's tangent via
`Quaternion.setFromUnitVectors(Vector3(0,1,0), tangent)`. The origin sits at
the connector's TIP (the face that seats into a port); the body/boot extend
in -Z (-> -Y post-export) from there, so an instance placed at a cable's
endpoint with the tip at that point looks flush with the port.

Scope (NG-PH3D 3a, hard boundary — see briefing): only dimension-VERIFIED
parts are modelled with real numbers. QSFP-DD/XFP/FC/ST/MPO/E2000/IEC power
connectors stay UNVERIFIED and are deliberately not built here.
"""
import bpy
import os
import math

OUT_DIR = os.path.normpath(os.path.join(os.path.dirname(bpy.data.filepath or __file__), '..', '..', 'frontend', 'public', '3d'))


def clear_scene():
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    for coll in (bpy.data.meshes, bpy.data.materials, bpy.data.objects):
        for block in list(coll):
            if block.users == 0:
                coll.remove(block)


def add_material(obj, name, rgb):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get('Principled BSDF')
    if bsdf:
        bsdf.inputs['Base Color'].default_value = (*rgb, 1.0)
        bsdf.inputs['Roughness'].default_value = 0.45
    obj.data.materials.append(mat)


def box(name, sx, sy, sz, cz):
    """A box sx(X) x sy(Y) x sz(Z), centred in X/Y, spanning [cz-sz, cz] in Z.

    primitive_cube_add(size=1) already spans exactly [-0.5, 0.5] (edge length
    1), so `scale` is set directly to the target edge lengths — not halved.
    """
    bpy.ops.mesh.primitive_cube_add(size=1)
    obj = bpy.context.object
    obj.name = name
    obj.scale = (sx, sy, sz)
    obj.location = (0, 0, cz - sz / 2)
    bpy.ops.object.transform_apply(location=True, scale=True)
    return obj


def frustum(name, r_top, r_bottom, sz, cz, sides=8):
    """A tapered N-gon barrel: radius r_top at Z=cz down to r_bottom at Z=cz-sz."""
    bpy.ops.mesh.primitive_cone_add(vertices=sides, radius1=r_bottom, radius2=r_top, depth=sz, location=(0, 0, cz - sz / 2))
    obj = bpy.context.object
    obj.name = name
    obj.rotation_euler = (0, 0, 0)
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)
    return obj


def join(objs, name):
    bpy.ops.object.select_all(action='DESELECT')
    for o in objs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = objs[0]
    bpy.ops.object.join()
    joined = bpy.context.object
    joined.name = name
    return joined


def export_glb(obj, filename):
    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, filename)
    bpy.ops.export_scene.gltf(
        filepath=path,
        use_selection=True,
        export_format='GLB',
        export_apply=True,
        export_yup=True,
    )
    print('wrote', path)


# ─── RJ45 plug + boot (§2.a "RJ45 — DIKOREKSI Sesi 1b", V(2nd)) ────────────
# Body: width 11.68mm, height 8.5-9.0mm (mid 8.75mm), length 16.0-16.5mm
# (mid 16.25mm). Boot: 10-15mm (mid 12.5mm), tapering to the Cat6A family's
# jacket radius already in MEDIA (cat6a/cat6a_xc/cat6a_oob average ~3.27mm).
def build_rj45():
    clear_scene()
    body = box('rj45-body', 0.01168, 0.00875, 0.01625, 0.0)
    tab = box('rj45-latch-tab', 0.0068, 0.0042, 0.006, -0.004)
    tab.location.y += 0.006  # sits proud on the +Y face, near the tip
    bpy.ops.object.transform_apply(location=True)
    boot = frustum('rj45-boot', 0.0042, 0.00327, 0.0125, -0.01625)  # top <= body/2 (8.75mm)
    obj = join([body, tab, boot], 'rj45-connector')
    add_material(obj, 'rj45-housing', (0.07, 0.07, 0.075))
    export_glb(obj, 'boot-rj45.glb')


# ─── LC simplex connector + boot (§2.a "LC (simplex)", V — Senko datasheet)─
# Body: 42mm x 5.58mm x 10.43mm. Boot: 8mm, tapering to the LC-family's
# fibre jacket radius already in MEDIA (os2/om3/om4/om5 = 1.7mm).
# Duplex clip pitch is UNVERIFIED (doc §2.a) -> simplex body only, reused
# for every LC-family media even where the real cable is duplex.
def build_lc():
    clear_scene()
    body = box('lc-body', 0.00558, 0.01043, 0.042, 0.0)
    boot = frustum('lc-boot', 0.0025, 0.0017, 0.008, -0.042)  # top <= body/2 (5.58mm)
    obj = join([body, boot], 'lc-connector')
    add_material(obj, 'lc-housing', (0.75, 0.75, 0.78))
    export_glb(obj, 'boot-lc.glb')


# ─── SFP/SFP+/SFP28 cage shell (§2.c, V — SFF-8432 Rev 5.1 Table, direct) ──
# Cage opening 14.00mm x 8.95mm, module/cage depth (Dim T) 47.50mm. Modelled
# as a thin-walled shell (opening dims = inner cavity) — reference geometry
# for Sesi 3b's device faceplates; not wired into the live scene this
# session (full chassis modelling is out of Sesi 3a's scope).
def build_sfp_cage():
    clear_scene()
    wall = 0.0005
    outer = box('sfp-cage-outer', 0.014 + 2 * wall, 0.00895 + 2 * wall, 0.0475, 0.0)
    inner = box('sfp-cage-inner', 0.014, 0.00895, 0.0475 + 0.002, 0.001)
    mod = outer.modifiers.new('cut', 'BOOLEAN')
    mod.operation = 'DIFFERENCE'
    mod.object = inner
    bpy.context.view_layer.objects.active = outer
    bpy.ops.object.modifier_apply(modifier=mod.name)
    bpy.data.objects.remove(inner, do_unlink=True)
    outer.name = 'sfp-cage'
    add_material(outer, 'cage-metal', (0.55, 0.56, 0.58))
    export_glb(outer, 'cage-sfp.glb')


# ─── QSFP+/QSFP28 cage shell (§2.c, V — SFF-8663 Rev 1.7 Table 5-1) ────────
# Width = Cage Footprint Width #20 (22.15mm min). "Height" here is Width of
# Component Free Area #17 (15.02mm max) — the clearest second verified
# cross-section number in Table 5-1; NOT a literal claim that #20 x #17 is
# the cage's full envelope, just the two verified numbers used as width x
# height for this reference shell. Depth = Datum L/K-to-PCB-edge #3 (37.00mm
# max). Reserved for Sesi 3b; not wired into the live scene this session.
def build_qsfp_cage():
    clear_scene()
    wall = 0.0005
    outer = box('qsfp-cage-outer', 0.02215 + 2 * wall, 0.01502 + 2 * wall, 0.037, 0.0)
    inner = box('qsfp-cage-inner', 0.02215, 0.01502, 0.037 + 0.002, 0.001)
    mod = outer.modifiers.new('cut', 'BOOLEAN')
    mod.operation = 'DIFFERENCE'
    mod.object = inner
    bpy.context.view_layer.objects.active = outer
    bpy.ops.object.modifier_apply(modifier=mod.name)
    bpy.data.objects.remove(inner, do_unlink=True)
    outer.name = 'qsfp-cage'
    add_material(outer, 'cage-metal', (0.55, 0.56, 0.58))
    export_glb(outer, 'cage-qsfp.glb')


if __name__ == '__main__':
    build_rj45()
    build_lc()
    build_sfp_cage()
    build_qsfp_cage()
    print('done')
