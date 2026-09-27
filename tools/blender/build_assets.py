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

Scope (NG-PH3D 3a + wave-1 face pass): only dimension-VERIFIED parts are
modelled with real numbers. QSFP-DD/XFP/FC/ST/MPO/E2000/IEC power connectors
stay UNVERIFIED and are deliberately not built here. MikroTik's official
product pages/brochures verify the outer dimensions and port counts for the
CCR2004-1G-12S+2XS, CRS317-1G-16S+RM and CRS328-24P-4S+RM. Their precise
vent/faceplate offsets are not published, so the ventilation pattern below is
only a visual-proportion cue from official product imagery. Front ports remain
catalog/anchor geometry in rack3d.ts: that keeps cable picking exact and avoids
duplicating a decorative Blender port field over the live port instances.

Outdoor placement track (Slice 5 + 7, see tower-structure-taxonomy.md memory):
adds the outdoor NEMA cabinet + tower structure meshes. These use a DIFFERENT
origin convention from the connector boots above — origin at the BASE centre
(bottom face, X/Y centred), since a site sits this chassis/structure directly
on the ground/pad rather than keying off a cable tip. Each part is still
authored with its vertical extent along local Z (base at Z=0), so the same
+Y-up export rotation lands it upright with the base at Y=0.
"""
import bpy
import os
import math
import sys
from mathutils import Vector

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


def cube_at(name, sx, sy, sz, x=0.0, y=0.0, z=0.0):
    """Axis-aligned box centred at an explicit point."""
    bpy.ops.mesh.primitive_cube_add(size=1, location=(x, y, z))
    obj = bpy.context.object
    obj.name = name
    obj.scale = (sx, sy, sz)
    bpy.ops.object.transform_apply(location=True, scale=True)
    return obj


def cut_chassis_vents(body, name, w, d, h, top_rows, side_rows):
    """Cut a low-poly ventilation pattern without changing the verified envelope.

    Counts/spacing are representative proportions from the vendor's official
    product imagery, not dimension claims. All cutters are joined and applied
    in one Boolean so the committed GLB remains a single cached geometry.
    """
    cutters = []
    top_pitch = min(0.012, max(0.007, (w * 0.42) / max(1, top_rows)))
    for row_y in (-d * 0.24, d * 0.24):
        for i in range(top_rows):
            x = (i - (top_rows - 1) / 2) * top_pitch
            cutters.append(cube_at(
                f'{name}-top-vent-{len(cutters)}',
                top_pitch * 0.46, min(0.055, d * 0.22), 0.004,
                x, row_y, h / 2,
            ))
    for side in (-1, 1):
        for i in range(side_rows):
            y = (i - (side_rows - 1) / 2) * min(0.018, d * 0.08)
            cutters.append(cube_at(
                f'{name}-side-vent-{side}-{i}',
                0.004, min(0.011, d * 0.05), h * 0.16,
                side * w / 2, y, 0,
            ))
    cutter = join(cutters, f'{name}-vent-cutters')
    mod = body.modifiers.new('vent-cutouts', 'BOOLEAN')
    mod.operation = 'DIFFERENCE'
    mod.solver = 'EXACT'
    mod.object = cutter
    bpy.context.view_layer.objects.active = body
    bpy.ops.object.modifier_apply(modifier=mod.name)
    bpy.data.objects.remove(cutter, do_unlink=True)


def build_mikrotik_chassis(name, filename, w, d, h, top_rows, side_rows):
    """Verified case envelope plus non-dimensional ventilation cues."""
    clear_scene()
    body = box(name, w, d, h, h / 2)
    bevel = body.modifiers.new('sheet-metal-edge', 'BEVEL')
    bevel.width = 0.0012
    bevel.segments = 2
    bpy.context.view_layer.objects.active = body
    bpy.ops.object.modifier_apply(modifier=bevel.name)
    cut_chassis_vents(body, name, w, d, h, top_rows, side_rows)
    add_material(body, 'warm-powder-coated-steel', (0.73, 0.72, 0.69))
    export_glb(body, filename)


def frustum(name, r_top, r_bottom, sz, cz, sides=8):
    """A tapered N-gon barrel: radius r_top at Z=cz down to r_bottom at Z=cz-sz."""
    bpy.ops.mesh.primitive_cone_add(vertices=sides, radius1=r_bottom, radius2=r_top, depth=sz, location=(0, 0, cz - sz / 2))
    obj = bpy.context.object
    obj.name = name
    obj.rotation_euler = (0, 0, 0)
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)
    return obj


def strut(name, p0, p1, thickness):
    """A thin square-cross-section bar connecting world-space points p0 -> p1 (metres/units).
    Generalises box() (which only ever spans vertically) to an arbitrary 3D edge — same
    align-to-direction technique rack3d.ts already uses in three.js for cable tangents
    (`Quaternion.setFromUnitVectors`). Used for lattice-tower legs and cross-bracing."""
    p0v, p1v = Vector(p0), Vector(p1)
    length = (p1v - p0v).length
    mid = (p0v + p1v) / 2
    bpy.ops.mesh.primitive_cube_add(size=1)
    obj = bpy.context.object
    obj.name = name
    obj.scale = (thickness, thickness, length)
    obj.location = mid
    direction = (p1v - p0v).normalized()
    obj.rotation_euler = Vector((0, 0, 1)).rotation_difference(direction).to_euler()
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
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


def painted_box(name, dims, centre, material, rgb, metallic=0.0):
    """Small material-preserving visual part; positions use Blender X/Y/Z."""
    obj = cube_at(name, *dims, *centre)
    mat = bpy.data.materials.get(material)
    if mat is None:
        mat = bpy.data.materials.new(material)
        mat.diffuse_color = (*rgb, 1)
        mat.use_nodes = True
        bsdf = mat.node_tree.nodes.get('Principled BSDF')
        bsdf.inputs['Base Color'].default_value = (*rgb, 1)
        bsdf.inputs['Metallic'].default_value = metallic
        bsdf.inputs['Roughness'].default_value = 0.38 if metallic else 0.65
    obj.data.materials.append(mat)
    return obj


def front_legend(name, label, x, z, size=0.003):
    """Embossed front lettering, converted to mesh for glTF portability."""
    bpy.ops.object.text_add(location=(x, -0.0655, z), rotation=(math.pi / 2, 0, 0))
    obj = bpy.context.object
    obj.name = name
    obj.data.body = label
    obj.data.size = size
    obj.data.align_x = 'CENTER'
    obj.data.extrude = 0.00003
    bpy.ops.object.convert(target='MESH')
    mat = bpy.data.materials.get('silk-grey')
    if mat is None:
        mat = bpy.data.materials.new('silk-grey')
        mat.diffuse_color = (0.54, 0.57, 0.59, 1)
    obj.data.materials.append(mat)


def export_visual_glb(filename):
    """Keep separate mesh materials: a merged, recoloured geometry loses them."""
    bpy.ops.object.select_all(action='DESELECT')
    for obj in bpy.context.scene.objects:
        if obj.type == 'MESH':
            obj.select_set(True)
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, filename)
    bpy.ops.export_scene.gltf(filepath=path, use_selection=True,
                              export_format='GLB', export_apply=True, export_yup=True)
    print('wrote', path)


def build_rb5009_k79():
    """RB5009UG+S+IN with its K-79 1U adapter, authored entirely in Blender.

    Sourced: RB5009 outer 220x125x22 mm and port TYPES/COUNTS from
    https://mikrotik.com/product/rb5009ug_s_in ; K-79 supports up to four
    units/1U from https://mikrotik.com/product/rb5009_mount and its assembly
    PDF https://cdn.mikrotik.com/web-assets/product_files/K79_230513.pdf .
    482.6 mm rack face width and 44.45 mm 1U height are EIA-310 nominal.
    All offsets, hole sizes, grille/fin pitch, port pitch, and metal thickness
    are PROPORTIONAL art cues from the official product imagery, not claims of
    vendor dimensions. The GLB has NO logical ports: rack3d's catalog anchors
    remain the only cable/picking targets.

    Blender -Y is the front; glTF export converts it to three.js +Z.
    """
    clear_scene()
    black = (0.056, 0.061, 0.067)
    charcoal = (0.12, 0.13, 0.14)
    rim = (0.33, 0.35, 0.36)
    gold = (0.56, 0.39, 0.16)
    blue = (0.018, 0.30, 0.58)
    # Exact device envelope; smaller inserts never extend its bounds.
    painted_box('rb5009-anodized-chassis', (0.220, 0.125, 0.018),
                (0, 0, -0.002), 'black-anodized-aluminium', black, 0.65)
    # Top/rear passive heat sink ribs. These sit on the silhouette, inset so
    # the 220x125x22 mm measured envelope remains exact.
    for i in range(21):
        x = -0.103 + i * 0.0103
        painted_box(f'heatsink-top-{i}', (0.0033, 0.073, 0.004),
                    (x, 0.020, 0.009), 'heatsink-ribs', charcoal, 0.52)
        painted_box(f'heatsink-rear-{i}', (0.0033, 0.0021, 0.013),
                    (x, 0.0614, 0), 'heatsink-ribs', charcoal, 0.52)
    # K-79: distinct left/right formed steel ears and short device rails.
    # It is not a stretched device faceplate. Screw/hole cues are proportional.
    for sign, side in ((-1, 'left'), (1, 'right')):
        painted_box(f'k79-{side}-ear', (0.1243, 0.0025, 0.04445),
                    (sign * 0.17915, -0.0624, 0), 'k79-black-steel', charcoal, 0.55)
        painted_box(f'k79-{side}-return', (0.003, 0.075, 0.018),
                    (sign * 0.1115, -0.022, 0), 'k79-black-steel', charcoal, 0.55)
        for x in (0.135, 0.226):
            for z in (-0.015, 0.015):
                painted_box(f'k79-{side}-screw-recess-{x}-{z}',
                            (0.010, 0.0007, 0.008), (sign*x, -0.0641, z),
                            'recess-black', (0.009, 0.010, 0.012))
                painted_box(f'k79-{side}-screw-{x}-{z}',
                            (0.003, 0.0008, 0.003), (sign*x, -0.0646, z),
                            'screw-steel', rim, 0.7)
    # Left-to-right front order visible on MikroTik's RB5009/K-79 image:
    # DC jack, SFP+, USB-A, 2.5G RJ45, then Ethernet 2-8.
    front_y = -0.0630
    def socket(name, x, width, height, insert, insert_name):
        painted_box(name+'-bezel', (width+0.002, 0.0014, height+0.002),
                    (x, front_y, 0), 'socket-nickel', rim, 0.5)
        painted_box(name+'-mouth', (width, 0.0016, height),
                    (x, front_y-0.0009, 0), 'socket-shadow', (0.005, 0.006, 0.008))
        painted_box(name+'-insert', (width*0.66, 0.0006, height*0.19),
                    (x, front_y-0.0020, -height*0.27), insert_name, insert)
    socket('dc-input', -0.100, 0.007, 0.008, gold, 'contact-gold')
    socket('sfpplus-1', -0.083, 0.014, 0.010, charcoal, 'heatsink-ribs')
    socket('usb-a-3', -0.061, 0.012, 0.007, blue, 'usb-blue')
    front_legend('label-dc', 'DC', -0.100, -0.009, 0.0022)
    front_legend('label-sfp', 'SFP+', -0.083, -0.009, 0.0022)
    front_legend('label-usb', 'USB', -0.061, -0.009, 0.0022)
    for n in range(8):
        x = -0.039 + n * 0.0193
        speed = '2p5g' if n == 0 else '1g'
        socket(f'ether{n+1}-{speed}', x, 0.014, 0.012, gold, 'contact-gold')
        painted_box(f'ether{n+1}-link-led', (0.002, 0.0007, 0.001),
                    (x-0.005, front_y-0.002, 0.008), 'led-green',
                    (0.05, 0.55, 0.22))
        front_legend(f'label-ether{n+1}', '2.5G' if n == 0 else str(n+1),
                     x, -0.009, 0.0022)
    # Third powering option is a side 2-pin terminal, separate from the
    # front DC jack and never represented as a network interface.
    painted_box('side-2pin-terminal', (0.001, 0.010, 0.008),
                (0.1095, 0.030, -0.001), 'terminal-green', (0.10, 0.27, 0.13))
    painted_box('front-power-led', (0.002, 0.0007, 0.001),
                (-0.107, front_y-0.002, 0.008), 'led-green', (0.05, 0.55, 0.22))
    export_visual_glb('brands/mikrotik/rb5009ug-s-in-k79.glb')


def render_rb5009_preview():
    """Optional local QA render, never bundled in the app."""
    scene = bpy.context.scene
    scene.render.engine = 'CYCLES'
    scene.cycles.samples = 24
    scene.render.resolution_x, scene.render.resolution_y = 1500, 700
    scene.render.resolution_percentage = 100
    scene.world.color = (0.025, 0.025, 0.025)
    bpy.ops.object.camera_add(location=(0.51, -0.55, 0.35))
    camera = bpy.context.object
    direction = Vector((0, 0, 0)) - camera.location
    camera.rotation_euler = direction.to_track_quat('-Z', 'Y').to_euler()
    camera.data.type = 'ORTHO'
    camera.data.ortho_scale = 0.63
    scene.camera = camera
    bpy.ops.object.light_add(type='AREA', location=(0, -0.3, 0.55))
    bpy.context.object.data.energy = 8
    bpy.context.object.data.shape = 'RECTANGLE'
    bpy.context.object.data.size = 0.7
    bpy.context.object.data.size_y = 0.4
    scene.render.filepath = '/tmp/netgeo-rb5009-k79-preview.png'
    bpy.ops.render.render(write_still=True)


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


# ─── MikroTik CCR2004-1G-12S+2XS chassis (wave 1, V — official vendor) ────
def build_chassis_mikrotik_ccr2004():
    """Official 443 x 224 x 44 mm envelope + representative vent pattern.

    Source: https://mikrotik.com/product/ccr2004_1g_12s_2xs and official
    brochure https://cdn.mikrotik.com/web-assets/product_files/
    CCR2004-1G-12S2XS_230155.pdf. The brochure also verifies the visible
    12x SFP+, 2x SFP28, dual AC and two-fan character; exact offsets are not
    published and remain catalog/visual proportions in rack3d.ts.
    """
    build_mikrotik_chassis(
        'chassis-mikrotik-ccr2004', 'chassis-mikrotik-ccr2004.glb',
        0.443, 0.224, 0.044, top_rows=13, side_rows=7,
    )


# ─── MikroTik CRS317-1G-16S+RM chassis (wave 1, V — official vendor) ───────
# Case dimensions 443(W) x 224(D) x 44(H) mm, independently present on both
# https://mikrotik.com/product/crs317_1g_16s_rm and the official hardware
# manual. Port counts are also verified there (16x SFP+, 1x GbE, 1x RJ45
# console). The official image is used only for ventilation proportions;
# rack3d.ts keeps the catalog-driven faceplate/cages and cable anchors. No
# vendor logo, image, CAD, texture, or mesh is imported.
def build_chassis_mikrotik_crs317():
    build_mikrotik_chassis(
        'chassis-mikrotik-crs317', 'chassis-mikrotik-crs317.glb',
        0.443, 0.224, 0.044, top_rows=16, side_rows=8,
    )


# ─── MikroTik CRS328-24P-4S+RM chassis (wave 1, V — official vendor) ────────
# 443 x 300 x 44 mm: https://mikrotik.com/product/crs328_24p_4s_rm and
# https://manual.mikrotik.com/hardware/crs328-24p-4s-plus-rm/.
# Official imagery verifies three RJ45 blocks, four SFP+ cages and the white
# case. Exact offsets are unpublished; the app's catalog faceplate retains
# the visual proportions and live cable anchors, without duplicate GLB ports.
def build_chassis_mikrotik_crs328():
    build_mikrotik_chassis(
        'chassis-mikrotik-crs328', 'chassis-mikrotik-crs328.glb',
        0.443, 0.300, 0.044, top_rows=18, side_rows=10,
    )


# ─── RJ-11/RJ-14 (6P6C) voice/FXS jack cage shell (Sesi port-fxs) ──────────
# 6-position modular connector is physically SMALLER than the 8-position
# 8P8C/RJ45 above and NOT interchangeable with it (keputusan Surya
# 2026-09-06) -- ONU voice/FXS ports must render as their own shape, not a
# mispainted rj45. Opening width 9.85mm / height 6.60mm: Wikipedia "Modular
# connector" typical-dimensions table (en.wikipedia.org/wiki/Modular_connector,
# citing ANSI/TIA-1096-A + ISO 8877), cross-checked against that same
# table's 8P8C row (11.68mm width) which matches this file's own
# already-used RJ45 body width exactly. This supersedes the lower-confidence
# 9.65mm figure in docs/design/24-DEVICE-PHYSICAL-SPEC.md's RJ11 section,
# which that doc itself flags as "low-confidence -- angka dari ingatan umum
# industri, TIDAK diverifikasi". Depth (13.7mm) is NOT independently
# sourced -- derived by scaling the RJ45 8P8C body length above (16.25mm) by
# the same width ratio (9.85/11.68); kept shallow like the LC/SFP cages
# purely for visual proportion, not a literal cage-depth claim.
def build_rj11_cage():
    clear_scene()
    wall = 0.0005
    w, h, depth = 0.00985, 0.0066, 0.0137
    outer = box('rj11-cage-outer', w + 2 * wall, h + 2 * wall, depth, 0.0)
    inner = box('rj11-cage-inner', w, h, depth + 0.002, 0.001)
    mod = outer.modifiers.new('cut', 'BOOLEAN')
    mod.operation = 'DIFFERENCE'
    mod.object = inner
    bpy.context.view_layer.objects.active = outer
    bpy.ops.object.modifier_apply(modifier=mod.name)
    bpy.data.objects.remove(inner, do_unlink=True)
    outer.name = 'rj11-cage'
    add_material(outer, 'rj11-jack-housing', (0.09, 0.09, 0.1))
    export_glb(outer, 'cage-rj11.glb')


# ─── Outdoor NEMA cabinet (Slice 5, V — Rittal TS 8 Type 3R SKU 8608548, ────
# rittal.com product page). 600(W) x 800(D) x 2000(H) mm — matches backend
# RackEnclosureProfile['outdoor-nema'] in schemas.py and
# RACK_SPECS['outdoor-nema'] in rack3d.ts; keep all three in sync.
# Modelled as a thin-walled shell (same boolean-shell technique as the SFP/
# QSFP/RJ11 cages above) plus a shallow door insert + handle on the front
# (+Y) face for readability — ponytail: the simplest shape that still reads
# as "outdoor cabinet" instead of a plain crate. No vents/hinges/lock
# modelled (decorative trim, not dimension-sourced, not load-bearing for
# identification). Origin at the BASE centre — see module docstring.
def build_cabinet_outdoor():
    clear_scene()
    w, d, h = 0.6, 0.8, 2.0
    wall = 0.01  # 10mm sheet-metal wall — proportional/decorative, not vendor-specified
    outer = box('cabinet-outer', w, d, h, h)
    inner = box('cabinet-inner', w - 2 * wall, d - 2 * wall, h - 2 * wall, h - wall)
    mod = outer.modifiers.new('cut', 'BOOLEAN')
    mod.operation = 'DIFFERENCE'
    mod.object = inner
    bpy.context.view_layer.objects.active = outer
    bpy.ops.object.modifier_apply(modifier=mod.name)
    bpy.data.objects.remove(inner, do_unlink=True)
    outer.name = 'cabinet-shell'

    door_w, door_h, door_t = w - 0.1, h - 0.2, 0.006
    bpy.ops.mesh.primitive_cube_add(size=1)
    door = bpy.context.object
    door.name = 'cabinet-door'
    door.scale = (door_w, door_t, door_h)
    door.location = (0, d / 2 + door_t / 2, h / 2)
    bpy.ops.object.transform_apply(location=True, scale=True)

    handle_w, handle_d, handle_h = 0.02, 0.03, 0.25
    bpy.ops.mesh.primitive_cube_add(size=1)
    handle = bpy.context.object
    handle.name = 'cabinet-handle'
    handle.scale = (handle_w, handle_d, handle_h)
    handle.location = (door_w / 2 - 0.05, d / 2 + door_t + handle_d / 2, h / 2)
    bpy.ops.object.transform_apply(location=True, scale=True)

    obj = join([outer, door, handle], 'cabinet-outdoor-nema')
    add_material(obj, 'cabinet-steel', (0.275, 0.282, 0.247))  # matches RACK_SPECS frame 0x46483f
    export_glb(obj, 'cabinet-outdoor.glb')


# ─── Tower structures (Slice 7, tower-structure-taxonomy.md memory) ────────
# NORMALIZED unit height (1.0) for every structure below — the taxonomy
# research deliberately found NO authoritative (standard/regulator) height
# range for any tower type, only vendor-blog numbers that are explicitly
# excluded as a fact source. So these meshes carry no real-world dimension
# claim; they are representative/derived silhouettes only, meant to be
# rescaled by Site.tower.height_agl_m at render time. Only 3 structure_type
# values exist (monopole/self-supporting-lattice/guyed-mast); guyed-mast
# reuses one of these two pole/lattice meshes (its guy cables are a separate
# line-render system, deliberately NOT modelled this slice). rooftop/
# ground-mount/camouflage are placement/material attributes, not distinct
# meshes (taxonomy §5) — no mesh is built for them.

def build_monopole():
    """Monopole tower (Perda Probolinggo §8(2)(c): round "tiang bundar" sub-shape). ponytail:
    the simplest shape that still reads as a monopole is a single tapered cylinder, so it's
    exactly that (reuses the frustum() helper already used for the RJ45/LC boots above)."""
    clear_scene()
    obj = frustum('tower-monopole', r_top=0.012, r_bottom=0.035, sz=1.0, cz=1.0, sides=12)
    add_material(obj, 'tower-monopole-steel', (0.5, 0.5, 0.52))
    export_glb(obj, 'tower-monopole.glb')


def build_lattice_tower(legs, name, filename):
    """Self-supporting lattice tower, generic (Perda Probolinggo §8(2)(a): kaki-4/kaki-3).
    Stepped taper: N modules stacked, each module's footprint constant within itself and
    narrower than the module below it (classic lattice "wedding-cake" silhouette) — legs are
    straight verticals per module rather than continuously slanted, kept as the simplest
    topology that still reads as a tapering lattice tower."""
    clear_scene()
    n_modules = 6
    r_base, r_top = 0.06, 0.015
    leg_w = 0.006
    dz = 1.0 / n_modules
    angles = [2 * math.pi * i / legs for i in range(legs)]
    parts = []
    for m in range(n_modules):
        r = r_base + (r_top - r_base) * (m / (n_modules - 1))
        z0, z1 = m * dz, (m + 1) * dz
        corners = [(r * math.cos(a), r * math.sin(a)) for a in angles]
        for i, (cx, cy) in enumerate(corners):
            nx, ny = corners[(i + 1) % legs]
            parts.append(strut(f'leg-{m}-{i}', (cx, cy, z0), (cx, cy, z1), leg_w))
            parts.append(strut(f'ring-{m}-{i}', (cx, cy, z1), (nx, ny, z1), leg_w))
            parts.append(strut(f'brace-{m}-{i}', (cx, cy, z0), (nx, ny, z1), leg_w))
    obj = join(parts, name)
    add_material(obj, f'{name}-steel', (0.5, 0.5, 0.52))
    export_glb(obj, filename)


def build_lattice4():
    build_lattice_tower(4, 'tower-lattice4', 'tower-lattice4.glb')


def build_lattice3():
    # Optional per briefing ("buat hanya kalau murah") — build_lattice_tower() already
    # parametrises leg count, so this costs one extra call, not new geometry code.
    build_lattice_tower(3, 'tower-lattice3', 'tower-lattice3.glb')


if __name__ == '__main__':
    build_rj45()
    build_lc()
    build_sfp_cage()
    build_qsfp_cage()
    build_chassis_mikrotik_ccr2004()
    build_chassis_mikrotik_crs317()
    build_chassis_mikrotik_crs328()
    build_rb5009_k79()
    if '--preview-rb5009' in sys.argv:
        render_rb5009_preview()
    build_rj11_cage()
    build_cabinet_outdoor()
    build_monopole()
    build_lattice4()
    build_lattice3()
    print('done')
