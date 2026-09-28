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
`Quaternion.setFromUnitVectors(Vector3(0,1,0), inward)`. The origin sits at
the connector/cage datum. Cable-side bodies extend in -Z (-> -Y after
export); pluggable module shells may also extend in +Z into the chassis.

Scope (NG-PH3D 3a + physical wave 2): only dimension-VERIFIED parts are
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


def top_legend(name, label, x, y, size=0.007):
    bpy.ops.object.text_add(location=(x, y, 0.0072))
    obj = bpy.context.object
    obj.name = name
    obj.data.body = label
    obj.data.size = size
    bpy.ops.object.convert(target='MESH')
    mat = bpy.data.materials.get('silk-grey')
    if mat is None:
        mat = bpy.data.materials.new('silk-grey')
        mat.diffuse_color = (0.54, 0.57, 0.59, 1)
    obj.data.materials.append(mat)


def pierced_k79_plate(name, dims, centre, holes):
    """One formed metal plate with genuine through-slots in its front face."""
    plate = painted_box(name, dims, centre, 'k79-black-steel',
                        (0.095, 0.102, 0.113), 0.52)
    for x, z, width, height in holes:
        radius = height / 2
        half = (width-height)/2
        tools = []
        if width > height:
            tools.append(cube_at('slot-core', width-height, 0.009, height,
                                 x, centre[1], z))
        for end in ((-1, 1) if width > height else (0,)):
            bpy.ops.mesh.primitive_cylinder_add(vertices=16, radius=radius,
                                                depth=0.009,
                                                location=(x+end*half, centre[1], z),
                                                rotation=(math.pi/2, 0, 0))
            tools.append(bpy.context.object)
        for tool in tools:
            bpy.context.view_layer.objects.active = plate
            mod = plate.modifiers.new('punched-slot', 'BOOLEAN')
            mod.operation = 'DIFFERENCE'
            mod.solver = 'EXACT'
            mod.object = tool
            bpy.ops.object.modifier_apply(modifier=mod.name)
            bpy.data.objects.remove(tool, do_unlink=True)
    return plate


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


def compact_scene_by_material(prefix):
    """Collapse repeated visual parts to one mesh per material/draw call."""
    groups = {}
    for obj in list(bpy.context.scene.objects):
        if obj.type != 'MESH' or not obj.data.materials:
            continue
        if any(mat is None for mat in obj.data.materials):
            continue
        key = tuple(mat.name for mat in obj.data.materials)
        groups.setdefault(key, []).append(obj)
    for index, objs in enumerate(groups.values()):
        if len(objs) > 1:
            join(objs, f'{prefix}-material-{index}')


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
    charcoal = (0.10, 0.11, 0.12)
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
    top_legend('mikrotik-top-mark', 'MikroTik', -0.094, -0.048)
    top_legend('rb5009-top-mark', 'RB5009', -0.094, -0.057, 0.0035)
    # K-79: pierced outer rack wings, narrower stepped inner tabs, folds and
    # long side carriers. Openings are real voids rather than square black art.
    # Their pitch/shape is proportional to the official image, not measured.
    for sign, side in ((-1, 'left'), (1, 'right')):
        pierced_k79_plate(f'k79-{side}-ear', (0.090, 0.0025, 0.04445),
                          (sign*0.1963, -0.063, 0),
                          [(sign*x, z, 0.013, 0.007)
                           for x in (0.159, 0.229) for z in (-0.015, 0.015)] +
                          [(sign*x, z, 0.0045, 0.0045)
                           for x in (0.186, 0.210) for z in (-0.008, 0.008)])
        pierced_k79_plate(f'k79-{side}-adapter-tab', (0.040, 0.0025, 0.030),
                          (sign*0.1313, -0.063, 0),
                          [(sign*0.136, 0, 0.014, 0.006)])
        for z in (-0.020, 0.020):
            painted_box(f'k79-{side}-fold-{z}', (0.075, 0.012, 0.002),
                        (sign*0.197, -0.055, z), 'k79-fold-dark', charcoal, 0.55)
        painted_box(f'k79-{side}-side-carrier', (0.0025, 0.078, 0.016),
                    (sign*0.1112, -0.015, 0), 'k79-black-steel',
                    (0.095, 0.102, 0.113), 0.52)
        for z in (-0.012, 0.012):
            painted_box(f'k79-{side}-interconnect-rail-{z}',
                        (0.026, 0.054, 0.002), (sign*0.123, -0.013, z),
                        'k79-fold-dark', charcoal, 0.55)
        for y in (-0.046, 0.010):
            painted_box(f'k79-{side}-side-fastener-{y}', (0.001, 0.006, 0.006),
                        (sign*0.113, y, 0), 'screw-steel', rim, 0.7)
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
    painted_box('sfpplus-cage-lip', (0.015, 0.001, 0.001),
                (-0.083, front_y-0.0022, 0.0055), 'socket-nickel', rim, 0.5)
    painted_box('usb-a-blue-tongue', (0.009, 0.0008, 0.002),
                (-0.061, front_y-0.0023, -0.0015), 'usb-blue', blue)
    front_legend('label-dc', 'DC', -0.100, -0.009, 0.0022)
    front_legend('label-sfp', 'SFP+', -0.083, -0.009, 0.0022)
    front_legend('label-usb', 'USB', -0.061, -0.009, 0.0022)
    for n in range(8):
        x = -0.039 + n * 0.0193
        speed = '2p5g' if n == 0 else '1g'
        socket(f'ether{n+1}-{speed}', x, 0.014, 0.012, gold, 'contact-gold')
        for pin in range(6):
            painted_box(f'ether{n+1}-contact-{pin}', (0.0008, 0.0007, 0.002),
                        (x-0.0045+pin*0.0018, front_y-0.0022, -0.001),
                        'contact-gold', gold, 0.6)
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
# Molex 1300478008 drawing: 52.2mm overall, 11.68mm mating width,
# 13.8mm overmould body, 5-9mm accepted cable. Source:
# https://www.molex.com/content/dam/molex/molex-dot-com/products/automated/
# en-us/salesdrawingpdf/130/130047/1300478008_sd.pdf
def build_rj45():
    clear_scene()
    body = box('rj45-body', 0.01168, 0.00875, 0.01625, 0.0)
    tab = box('rj45-latch-tab', 0.0068, 0.0042, 0.006, -0.004)
    tab.location.y += 0.006  # sits proud on the +Y face, near the tip
    bpy.ops.object.transform_apply(location=True)
    overmould = box('rj45-overmould', 0.0138, 0.0138, 0.02995, -0.01625)
    relief = frustum('rj45-cable-entry', 0.0069, 0.00325, 0.006, -0.0462)
    obj = join([body, tab, overmould, relief], 'rj45-connector')
    add_material(obj, 'rj45-housing', (0.07, 0.07, 0.075))
    export_glb(obj, 'boot-rj45.glb')


# ─── LC simplex connector + boot (§2.a "LC (simplex)", V — Senko datasheet)─
# SENKO duplex family: 54mm length, 6.25mm ferrule pitch, 8.6mm height,
# 2mm cable. Source:
# https://www.senko.com/wp-content/uploads/2024/08/DS-LC-000006-RevA-EN.pdf
def build_lc():
    clear_scene()
    parts = []
    for side in (-1, 1):
        leg = box(f'lc-body-{side}', 0.00558, 0.0086, 0.046, 0.0)
        leg.location.x = side * 0.003125
        bpy.ops.object.transform_apply(location=True)
        parts.append(leg)
    parts.append(box('lc-duplex-clip', 0.01183, 0.0065, 0.009, -0.004))
    parts.append(frustum('lc-duplex-boot', 0.0038, 0.001, 0.008, -0.046))
    obj = join(parts, 'lc-duplex-connector')
    add_material(obj, 'lc-housing', (0.75, 0.75, 0.78))
    export_glb(obj, 'boot-lc.glb')


# SFP/SFP+/SFP28 share one mechanical envelope. SFF-8432 R5.2a:
# https://members.snia.org/document/dl/25892 — 13.55±0.25 x
# 8.55±0.15 x 47.50±0.20mm, maximum 10mm in front of the cage datum.
def sfp_module_shell(name):
    return cube_at(name, 0.01355, 0.00855, 0.0475, z=0.01375)


def build_sfp_lc():
    clear_scene()
    parts = [sfp_module_shell('sfp-lc-module')]
    for side in (-1, 1):
        leg = box(f'sfp-lc-leg-{side}', 0.00558, 0.0086, 0.054, -0.010)
        leg.location.x = side * 0.003125
        bpy.ops.object.transform_apply(location=True)
        parts.append(leg)
    parts.append(box('sfp-lc-duplex-clip', 0.01183, 0.0065, 0.009, -0.014))
    obj = join(parts, 'sfp-lc-termination')
    add_material(obj, 'sfp-lc-neutral', (0.34, 0.36, 0.38))
    export_glb(obj, 'connector-sfp-lc.glb')


def build_sfp_dac():
    """One SFP-family DAC/AOC geometry; colour is an instance variant.

    TE's SFP+ family accepts 4.7-6.48mm cable and its SFP28 cable is 5.96mm:
    https://www.te.com/en/product-CAT-C11-H5.html . The relief stays inside
    SFF-8432's verified 10mm front allowance.
    """
    clear_scene()
    module = sfp_module_shell('sfp-dac-module')
    relief = frustum('sfp-dac-relief', 0.0042, 0.00298, 0.010, -0.010)
    obj = join([module, relief], 'sfp-dac-termination')
    add_material(obj, 'sfp-dac-neutral', (0.18, 0.19, 0.20))
    export_glb(obj, 'connector-sfp-dac.glb')


def build_qsfp_dac():
    """QSFP+/QSFP28 family; SFF-8661 max 18.4 x 13.5 x 72.4mm.

    Source: https://members.snia.org/document/dl/25959 . The cage-datum
    split is a placement cue; total module length remains the verified max.
    """
    clear_scene()
    module = cube_at('qsfp-dac-module', 0.0184, 0.0135, 0.0724, z=0.0227)
    relief = frustum('qsfp-dac-relief', 0.0054, 0.00324, 0.0135, -0.0135)
    obj = join([module, relief], 'qsfp-dac-termination')
    add_material(obj, 'qsfp-dac-neutral', (0.18, 0.19, 0.20))
    export_glb(obj, 'connector-qsfp-dac.glb')


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


def build_cisco_c9300_48p_nm8x():
    """Catalyst C9300-48P + C9300-NM-8X, authored from primary specs.

    Cisco verifies 44 x 445 x 488mm, 48 front 1G PoE+ ports, front UID/
    status/USB mini-B/USB-A, the modular uplink bay, rear 3 fans + 2 PSUs,
    console/mgmt and StackWise/StackPower. The NM-8X guide verifies 8 SFP+
    slots. Sources:
    https://www.cisco.com/c/en/us/td/docs/switches/lan/catalyst9300/
    hardware/install/b_c9300_hig/technical-specs.html
    https://www.cisco.com/c/en/us/td/docs/switches/lan/catalyst9300/
    hardware/install/b_c9300_hig/Product-overview.html
    https://www.cisco.com/c/en/us/td/docs/switches/lan/catalyst9300/
    hardware/install/b_c9300_hig/Installing-a-network-module.html

    Exact unpublished offsets, perforation pitch and bracket fold depth are
    proportional visual cues. The chassis envelope is exact; separate ears
    extend it to the EIA-310 482.6mm face width. No logo/CAD is imported.
    """
    clear_scene()
    dark = (0.045, 0.052, 0.058)
    face = (0.075, 0.082, 0.088)
    metal = (0.36, 0.38, 0.40)
    gold = (0.54, 0.37, 0.14)
    blue = (0.05, 0.34, 0.55)
    front_y, rear_y = -0.245, 0.245
    painted_box('c9300-48p-chassis', (0.445, 0.488, 0.044), (0, 0, 0),
                'c9300-black-steel', dark, 0.48)
    painted_box('c9300-front-panel', (0.445, 0.002, 0.044),
                (0, front_y, 0), 'c9300-face', face, 0.42)

    # Separate 19-inch mounting brackets with real through-slots.
    ear_w = (0.4826 - 0.445) / 2
    for sign, side in ((-1, 'left'), (1, 'right')):
        pierced_k79_plate(f'c9300-{side}-rack-ear', (ear_w, 0.003, 0.044),
                          (sign * (0.445 / 2 + ear_w / 2), front_y, 0),
                          [(sign * 0.233, z, 0.007, 0.005)
                           for z in (-0.014, 0.014)])

    # 48 copper ports, two rows of 24. Offsets/pitch are proportional cues;
    # connector envelopes are Molex-derived, while count/order are Cisco V.
    pitch = 0.0129
    for row, z in enumerate((0.0082, -0.0082)):
        for col in range(24):
            n = row * 24 + col + 1
            x = -0.172 + col * pitch
            painted_box(f'ge-{n}-bezel', (0.0119, 0.0015, 0.0091),
                        (x, front_y - 0.0018, z), 'port-nickel', metal, 0.52)
            painted_box(f'ge-{n}-mouth', (0.0108, 0.001, 0.0078),
                        (x, front_y - 0.0027, z), 'port-shadow', (0.004, 0.005, 0.006))
            painted_box(f'ge-{n}-contacts', (0.0078, 0.0005, 0.0012),
                        (x, front_y - 0.0033, z - 0.0023), 'contact-gold', gold, 0.55)

    # C9300-NM-8X modular card: 8 SFP+ slots, two rows of four.
    painted_box('c9300-nm8x-module', (0.069, 0.0022, 0.036),
                (0.181, front_y - 0.0015, 0), 'nm8x-panel', (0.10, 0.11, 0.12), 0.5)
    for row, z in enumerate((0.008, -0.008)):
        for col in range(4):
            n = row * 4 + col + 1
            x = 0.158 + col * 0.0152
            painted_box(f'nm8x-sfp-{n}', (0.014, 0.0014, 0.00895),
                        (x, front_y - 0.003, z), 'port-nickel', metal, 0.52)
            painted_box(f'nm8x-sfp-{n}-mouth', (0.0128, 0.0008, 0.0078),
                        (x, front_y - 0.004, z), 'port-shadow', (0.004, 0.005, 0.006))

    # Front service controls. Geometry follows component type/count only.
    painted_box('front-usb-mini-b', (0.007, 0.0014, 0.0045),
                (-0.207, front_y - 0.002, -0.008), 'usb-blue', blue)
    painted_box('front-usb-a', (0.010, 0.0014, 0.0047),
                (-0.194, front_y - 0.002, -0.008), 'usb-blue', blue)
    for i in range(5):
        painted_box(f'front-status-{i}', (0.0018, 0.0008, 0.0018),
                    (-0.208 + (i % 2) * 0.005, front_y - 0.0027,
                     0.012 - (i // 2) * 0.005), 'led-green', (0.04, 0.55, 0.23))

    # Rear replaceable units: 3 fan trays, two PSUs, stack/mgmt cues.
    painted_box('c9300-rear-panel', (0.445, 0.002, 0.044),
                (0, rear_y, 0), 'c9300-face', face, 0.42)
    for i in range(3):
        painted_box(f'rear-fan-{i+1}', (0.046, 0.003, 0.034),
                    (-0.183 + i * 0.050, rear_y + 0.002, 0), 'fan-dark', (0.08, 0.09, 0.10))
    for i in range(2):
        painted_box(f'rear-psu-{i+1}', (0.079, 0.003, 0.034),
                    (0.174 - i * 0.083, rear_y + 0.002, 0), 'psu-dark', (0.12, 0.13, 0.14), 0.4)
    for i, x in enumerate((-0.020, 0.003, 0.026)):
        painted_box(f'rear-stack-mgmt-{i}', (0.017, 0.003, 0.012),
                    (x, rear_y + 0.002, -0.008), 'port-shadow', (0.004, 0.005, 0.006))

    compact_scene_by_material('c9300-48p')
    export_visual_glb('brands/cisco/c9300-48p-nm8x.glb')


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
    build_sfp_lc()
    build_sfp_dac()
    build_qsfp_dac()
    build_sfp_cage()
    build_qsfp_cage()
    build_chassis_mikrotik_ccr2004()
    build_chassis_mikrotik_crs317()
    build_chassis_mikrotik_crs328()
    build_rb5009_k79()
    if '--preview-rb5009' in sys.argv:
        render_rb5009_preview()
    build_cisco_c9300_48p_nm8x()
    build_rj11_cage()
    build_cabinet_outdoor()
    build_monopole()
    build_lattice4()
    build_lattice3()
    print('done')
