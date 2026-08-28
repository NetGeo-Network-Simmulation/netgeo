/**
 * rack3d — port of the "Desain Rackmount 2.5" handoff prototype
 * (design_handoff_rack_3d/rack3d.js) into the NetGeo frontend.
 *
 * Pure three.js scene builder: no DOM stage, no React. It returns a root
 * Group plus a registry the host component uses for picking, toggles and
 * the animation tick. Geometry is EIA-310 real-world metres throughout.
 *
 * ponytail: kept the prototype's imperative builder as-is instead of
 * rewriting ~700 lines of scene graph into JSX — react-three-fiber would
 * buy nothing here; the host owns one renderer and one ortho camera.
 */
import * as THREE from 'three';

/* ─── Real-world geometry (EIA-310): 1U = 44.45 mm, 19" panel = 482.6 mm ─── */
export const U = 0.04445;
const PANEL_W = 0.4826;
const MOUNT_W = 0.4651; // 19" mounting-hole centre spacing (EIA-310)

export type ExitKind = 'top' | 'rear' | 'side';

export interface RackSpec {
  label: string;
  u: number;
  w: number;
  d: number;
  h: number;
  frame: number;
  door: boolean;
  exit: ExitKind;
}

/* Enclosure datasheets — width × depth × height in mm, per vendor spec. */
export const RACK_SPECS: Record<string, RackSpec> = {
  apc: { label: 'APC NetShelter SX AR3100', u: 42, w: 600, d: 1070, h: 1991, frame: 0x17171a, door: true, exit: 'top' },
  dell: { label: 'Dell PowerEdge 4210', u: 42, w: 600, d: 1200, h: 2010, frame: 0x191a1c, door: true, exit: 'top' },
  hpe: { label: 'HPE G2 Enterprise 42U', u: 42, w: 600, d: 1075, h: 2015, frame: 0x1c1d20, door: true, exit: 'rear' },
  vertiv: { label: 'Vertiv VR3350', u: 42, w: 600, d: 1100, h: 2000, frame: 0x18181a, door: true, exit: 'side' },
  eaton: { label: 'Eaton RE 42U', u: 42, w: 600, d: 1000, h: 2000, frame: 0x1a1a1c, door: true, exit: 'side' },
  rittal: { label: 'Rittal TS IT 42U', u: 42, w: 600, d: 1000, h: 2000, frame: 0x2a2c2e, door: true, exit: 'top' },
  cpi: { label: 'Chatsworth two-post 45U', u: 45, w: 500, d: 76, h: 2134, frame: 0x2e2e30, door: false, exit: 'side' },
};

export interface MediaSpec {
  label: string;
  jacket: number;
  boot: number;
  r: number;
}

/* ─── Cable media: jacket colour per TIA-598 / datacenter practice ─────────
 * os2/os2apc/om3/om4/cat6a/dac/coax map onto the 9 backend `CableMedia`
 * values (see plantAdapter.ts). om5/mpo/cat6a_xc/cat6a_oob/aoc/pwrA/pwrB have
 * no backend counterpart yet — dead in the P1 production path, kept for
 * P5/P6 (backend enum growth, patch-panel hops, PDU visuals). */
export const MEDIA: Record<string, MediaSpec> = {
  os2: { label: 'OS2 single-mode · LC/UPC', jacket: 0xf2c21b, boot: 0x1e6fd9, r: 0.0017 },
  os2apc: { label: 'OS2 single-mode · LC/APC', jacket: 0xf2c21b, boot: 0x1fa34a, r: 0.0017 },
  om3: { label: 'OM3 multimode · aqua', jacket: 0x36c6c0, boot: 0x36c6c0, r: 0.0017 },
  om4: { label: 'OM4 multimode · violet', jacket: 0xae7bc6, boot: 0xae7bc6, r: 0.0017 },
  cat6a: { label: 'Cat6A horizontal · blue', jacket: 0x2f6bff, boot: 0x2f6bff, r: 0.0034 },
  dac: { label: 'DAC twinax 25/100G · black', jacket: 0x16150f, boot: 0x2a2a26, r: 0.0042 },
  coax: { label: 'Coax 50Ω · black', jacket: 0x16150f, boot: 0x1a1a1c, r: 0.004 },
  // DEAD CODE — P6: no backend CableMedia counterpart today.
  om5: { label: 'OM5 wideband · lime', jacket: 0xa6d608, boot: 0xa6d608, r: 0.0017 },
  mpo: { label: 'MPO/MTP trunk 12F', jacket: 0x36c6c0, boot: 0x15171b, r: 0.0032 },
  cat6a_xc: { label: 'Cat6A cross-connect · orange', jacket: 0xf07020, boot: 0xf07020, r: 0.0034 },
  cat6a_oob: { label: 'Cat6A mgmt / OOB · green', jacket: 0x27c28b, boot: 0x27c28b, r: 0.003 },
  aoc: { label: 'AOC active optical · aqua', jacket: 0x2fa9a4, boot: 0x15171b, r: 0.0026 },
  pwrA: { label: 'Power C13/C14 · feed A', jacket: 0x121211, boot: 0x121211, r: 0.0038 },
  pwrB: { label: 'Power C13/C14 · feed B', jacket: 0xc0392b, boot: 0xc0392b, r: 0.0038 },
};

export type PortType = 'rj45' | 'sfp28' | 'qsfp28' | 'bay' | 'lc' | 'pon' | 'mpo';
export type DeviceKind = 'odf' | 'patch' | 'duct' | 'switch' | 'fw' | 'server' | 'olt';

export interface DeviceDef {
  id: string;
  u: number;
  h: number;
  kind: DeviceKind;
  brand: string;
  model: string;
  accent: number;
  ports: number;
  ptype?: PortType;
  chassis?: number;
}

export interface LinkDef {
  a: [string, number];
  b: [string, number];
  m: string;
  live: boolean;
}

export interface CableMeta {
  name: string;
  devs: string[];
  live: boolean;
  media: string;
}

export interface RackEntry {
  group: THREE.Group;
  spec: RackSpec;
  specKey: string;
  w: number;
  d: number;
  h: number;
  x: number;
}

export interface DeviceEntry {
  def: DeviceDef;
  group: THREE.Group;
  rackKey: string;
  portRefs: Record<number, THREE.Vector3>;
  faceZ: number;
}

export interface CableEntry {
  mesh: THREE.Mesh;
  curve: THREE.Curve<THREE.Vector3>;
  mediaKey: string;
  meta: CableMeta;
  length: number;
}

export interface Registry {
  racks: Record<string, RackEntry>;
  devices: Record<string, DeviceEntry>;
  cables: CableEntry[];
  fans: THREE.Group[];
  packets: { dot: THREE.Mesh; curve: THREE.Curve<THREE.Vector3>; t: number; speed: number }[];
  labels: THREE.Sprite[];
  doors: THREE.Group[];
  disposables: (THREE.BufferGeometry | THREE.Material | THREE.Texture)[];
}

function mat(name: string, color: number, opts: THREE.MeshStandardMaterialParameters = {}) {
  const m = new THREE.MeshStandardMaterial({ color, roughness: 0.62, metalness: 0.22, ...opts });
  m.name = name;
  return m;
}

function makeMaterials() {
  const m = {
    frame: mat('rack-frame', 0x1b1b1d, { roughness: 0.5, metalness: 0.35 }),
    panelDark: mat('panel-dark', 0x121211, { roughness: 0.7 }),
    mesh: mat('mesh-door', 0x232325, { roughness: 0.55, metalness: 0.3, transparent: true, opacity: 0.55 }),
    tray: mat('ladder-tray', 0x3a3a3c, { roughness: 0.45, metalness: 0.4 }),
    port: mat('port-cage', 0x0b0b0c, { roughness: 0.85, metalness: 0.08 }),
    ledOn: mat('led-green', 0x27c28b, { emissive: 0x27c28b, emissiveIntensity: 0.9, roughness: 0.4 }),
    ledAmber: mat('led-amber', 0xf5a623, { emissive: 0xf5a623, emissiveIntensity: 0.8, roughness: 0.4 }),
    fan: mat('fan-blade', 0x2a2a28, { roughness: 0.6 }),
    pdu: mat('pdu-body', 0x1a1a1c, { roughness: 0.6, metalness: 0.3 }),
    velcro: mat('velcro-strap', 0x101010, { roughness: 0.9, metalness: 0.05 }),
    bezel: mat('server-bezel', 0x4a4a4e, { roughness: 0.55, metalness: 0.3 }),
    plugShell: mat('plug-housing', 0x121214, { roughness: 0.42, metalness: 0.18 }),
    plugClip: mat('plug-latch', 0xc9cdd4, { roughness: 0.32, metalness: 0.55 }),
    handle: mat('bay-handle', 0x76767c, { roughness: 0.4, metalness: 0.5 }),
  };
  // media jackets/boots live in their own map: one entry per MEDIA key, so a
  // lookup by media string cannot collide with a named part material.
  const media: Record<string, { jk: THREE.MeshStandardMaterial; bt: THREE.MeshStandardMaterial }> = {};
  for (const [k, spec] of Object.entries(MEDIA)) {
    media[k] = {
      jk: mat('jacket-' + k, spec.jacket, { roughness: 0.5, metalness: 0.1 }),
      bt: mat('boot-' + k, spec.boot, { roughness: 0.45, metalness: 0.15 }),
    };
  }
  return { ...m, media };
}

function railTexture(u: number) {
  const perU = 24; // px per U in the texture
  const cvs = document.createElement('canvas');
  cvs.width = 32;
  cvs.height = perU * u;
  const ctx = cvs.getContext('2d')!;
  ctx.fillStyle = '#1B1B1D';
  ctx.fillRect(0, 0, cvs.width, cvs.height);
  // EIA-310: three holes per U at 15.875 / 15.875 / 12.7 mm centres
  const offs = [0.1786, 0.5, 0.8214].map((f) => f * perU);
  for (let i = 0; i < u; i++) {
    const y0 = i * perU;
    ctx.fillStyle = '#07070A';
    for (const o of offs) ctx.fillRect(11, y0 + o - 3.4, 7, 6.8);
    ctx.fillStyle = 'rgba(250,249,245,0.42)';
    ctx.font = '600 7px monospace';
    ctx.fillText(String(u - i), 1.5, y0 + perU * 0.62);
  }
  const tex = new THREE.CanvasTexture(cvs);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = 4;
  return tex;
}

function faceTexture(def: DeviceDef, rear: boolean) {
  const uPx = 96;
  const cvs = document.createElement('canvas');
  cvs.width = 1024;
  cvs.height = uPx * def.h;
  const ctx = cvs.getContext('2d')!;
  const pale = def.kind === 'patch' || def.kind === 'odf' || def.kind === 'duct';
  const base = pale ? '#8C8C90' : def.chassis === 0xe6e3db ? '#D8D5CD' : '#2A2A2C';
  const ink = pale || def.chassis === 0xe6e3db ? 'rgba(20,20,19,0.75)' : 'rgba(250,249,245,0.62)';
  ctx.fillStyle = base;
  ctx.fillRect(0, 0, cvs.width, cvs.height);
  // brushed horizontal grain
  for (let y = 0; y < cvs.height; y += 3) {
    ctx.fillStyle = 'rgba(255,255,255,' + (y % 6 ? 0.012 : 0.028) + ')';
    ctx.fillRect(0, y, cvs.width, 1.4);
  }
  const midY = cvs.height / 2;
  if (rear) {
    // exhaust honeycomb + PSU bays
    ctx.fillStyle = 'rgba(0,0,0,0.55)';
    for (let x = 210; x < 700; x += 13) {
      for (let y = 16; y < cvs.height - 16; y += 13) {
        ctx.beginPath();
        ctx.arc(x + (Math.floor(y / 13) % 2 ? 6 : 0), y, 4.1, 0, 7);
        ctx.fill();
      }
    }
    ctx.strokeStyle = 'rgba(0,0,0,0.5)';
    ctx.lineWidth = 2;
    ctx.strokeRect(720, 12, 260, cvs.height - 24);
    ctx.fillStyle = ink;
    ctx.font = '600 20px monospace';
    ctx.fillText('PSU 1', 742, 40);
  } else {
    // port field outlines: exactly where the 3D cages sit
    const n = def.ports || 0;
    if (n) {
      const twoRow = def.ptype === 'rj45' && n >= 20;
      const rows = twoRow ? 2 : 1;
      const perRow = Math.ceil(n / rows);
      const bankOf = def.ptype === 'rj45' ? 6 : 4;
      const banks = Math.ceil(perRow / bankOf);
      const usable = cvs.width * 0.79;
      const x0 = cvs.width * 0.115;
      const gap = banks > 1 ? cvs.width * 0.0115 : 0;
      const pitch = (usable - gap * (banks - 1)) / perRow;
      for (let i = 0; i < n; i++) {
        const r = Math.floor(i / perRow);
        const c = i % perRow;
        const bank = Math.floor(c / bankOf);
        const px = x0 + c * pitch + bank * gap;
        const py = rows === 1 ? midY - uPx * 0.2 : r === 0 ? midY - uPx * 0.36 : midY + uPx * 0.02;
        const pw = pitch * 0.8;
        const ph = uPx * 0.34;
        ctx.fillStyle = 'rgba(0,0,0,0.82)';
        ctx.fillRect(px, py, pw, ph);
        ctx.strokeStyle = pale ? 'rgba(250,249,245,0.85)' : 'rgba(250,249,245,0.28)';
        ctx.lineWidth = 2;
        ctx.strokeRect(px - 1, py - 1, pw + 2, ph + 2);
        if (i % 2 === 0) {
          ctx.fillStyle = ink;
          ctx.font = '600 13px monospace';
          ctx.fillText(String(i + 1), px + 1, py + ph + 15);
        }
      }
    }
    // brand + model, left of the port field
    ctx.fillStyle = ink;
    ctx.font = '700 ' + Math.round(uPx * 0.2) + 'px Inter, sans-serif';
    ctx.fillText(def.brand.toUpperCase(), 30, midY - uPx * 0.1);
    ctx.font = '500 ' + Math.round(uPx * 0.15) + 'px monospace';
    ctx.fillText(def.model.replace(/^.*?(?= )/, '').trim() || def.model, 30, midY + uPx * 0.14);
    if (def.kind === 'server') {
      // drive-bay carriers as artwork, not geometry
      const bays = def.h >= 2 ? 8 : 10;
      const bw = (cvs.width - 160) / bays;
      for (let i = 0; i < bays; i++) {
        const bx = 80 + i * bw;
        ctx.fillStyle = 'rgba(255,255,255,0.1)';
        ctx.fillRect(bx + 3, 14, bw - 8, cvs.height - 28);
        ctx.strokeStyle = 'rgba(0,0,0,0.45)';
        ctx.lineWidth = 2;
        ctx.strokeRect(bx + 3, 14, bw - 8, cvs.height - 28);
        ctx.fillStyle = 'rgba(0,0,0,0.35)';
        ctx.fillRect(bx + 7, 22, 7, cvs.height - 44);
      }
    }
  }
  const tex = new THREE.CanvasTexture(cvs);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = 8;
  return tex;
}

/* ─── Scene builder ──────────────────────────────────────────────────────── */

export interface BuildOptions {
  /** rack A / rack B enclosure keys, from RACK_SPECS */
  rackA: string;
  rackB: string;
  fitout: Record<string, DeviceDef[]>;
  links: LinkDef[];
}

export interface BuiltScene {
  root: THREE.Group;
  registry: Registry;
  trayY: number;
}

export function buildScene(opts: BuildOptions): BuiltScene {
  const mats = makeMaterials();
  const registry: Registry = {
    racks: {}, devices: {}, cables: [], fans: [], packets: [], labels: [], doors: [], disposables: [],
  };
  const root = new THREE.Group();
  root.name = 'netgeo-physical-plant';

  const track = <T extends THREE.BufferGeometry | THREE.Material | THREE.Texture>(x: T): T => {
    registry.disposables.push(x);
    return x;
  };
  for (const [k, m] of Object.entries(mats)) {
    if (k === 'media') continue;
    track(m as THREE.MeshStandardMaterial);
  }
  for (const pair of Object.values(mats.media)) {
    track(pair.jk);
    track(pair.bt);
  }

  const box = (w: number, h: number, d: number, material: THREE.Material, name?: string) => {
    const mesh = new THREE.Mesh(track(new THREE.BoxGeometry(w, h, d)), material);
    mesh.name = name || 'part';
    return mesh;
  };

  /* ─── Enclosure ───────────────────────────────────────────────────────── */
  function buildRack(key: string, specKey: string, x: number) {
    const s = RACK_SPECS[specKey]!;
    const w = s.w / 1000, d = s.d / 1000, h = s.h / 1000;
    const g = new THREE.Group();
    g.name = 'rack-' + key + '-' + specKey;
    g.position.set(x, 0, 0);

    const frameMat = track(mat('frame-' + specKey, s.frame, { roughness: 0.5, metalness: 0.35 }));
    const postW = 0.024, postD = 0.05;
    const railX = MOUNT_W / 2; // EIA-310 hole centres, 465.1 mm apart
    const postX = w / 2 - postW / 2;

    // four 16-gauge corner uprights; EIA rails front and rear, square-punched
    const railMat = track(mat('rail-' + specKey, 0xffffff, { roughness: 0.55, metalness: 0.35 }));
    railMat.map = track(railTexture(s.u));
    const railH = s.u * U;
    for (const sx of [-1, 1]) {
      for (const sz of [-1, 1]) {
        const p = box(postW, h, postD, frameMat, 'upright');
        p.position.set(sx * postX, h / 2, sz * (d / 2 - postD / 2));
        g.add(p);
      }
      for (const rz of [d / 2 - 0.09, -d / 2 + 0.16]) {
        const rail = box(0.022, railH, 0.02, railMat, 'mounting-rail');
        rail.position.set(sx * railX, 0.055 + railH / 2, rz);
        g.add(rail);
      }
    }
    // zero-U accessory channels in the rear corners (PDU / organiser bays)
    for (const sx of [-1, 1]) {
      const chan = box(0.05, railH, 0.05, frameMat, 'accessory-channel');
      chan.position.set(sx * (w / 2 - 0.028), 0.055 + railH / 2, -d / 2 + 0.095);
      g.add(chan);
    }
    // top/bottom frame rails
    for (const sz of [-1, 1]) {
      for (const sy of [0.02, h - 0.02]) {
        const r = box(w, 0.03, postD, frameMat, 'frame-rail');
        r.position.set(0, sy, sz * (d / 2 - postD / 2));
        g.add(r);
      }
    }
    // roof + plinth + side panels
    const ex = { x: w / 2 - 0.13, z: d / 2 - 0.2, w: 0.21, d: 0.11 };
    if (s.exit === 'top') {
      // roof in four pieces around the brush-plate aperture
      const parts: [number, number, number, number][] = [
        [w, d / 2 - ex.z - ex.d / 2, 0, (ex.z + ex.d / 2 + d / 2) / 2],
        [w, ex.z - ex.d / 2 + d / 2, 0, (ex.z - ex.d / 2 - d / 2) / 2],
        [ex.x - ex.w / 2 + w / 2, ex.d, (ex.x - ex.w / 2 - w / 2) / 2, ex.z],
        [w / 2 - (ex.x + ex.w / 2), ex.d, (ex.x + ex.w / 2 + w / 2) / 2, ex.z],
      ];
      for (const [pw, pd, px, pz] of parts) {
        if (pw <= 0.001 || pd <= 0.001) continue;
        const piece = box(pw, 0.02, pd, frameMat, 'roof');
        piece.position.set(px, h + 0.01, pz);
        g.add(piece);
      }
    } else {
      const roof = box(w, 0.02, d, frameMat, 'roof');
      roof.position.set(0, h + 0.01, 0);
      g.add(roof);
    }
    for (const sz of [-1, 1]) {
      const beam = box(w, 0.035, 0.045, frameMat, 'base-frame');
      beam.position.set(0, -0.018, sz * (d / 2 - 0.03));
      g.add(beam);
    }
    for (const sx of [-1, 1]) {
      for (const sz of [-1, 1]) {
        const caster = new THREE.Mesh(track(new THREE.CylinderGeometry(0.026, 0.026, 0.016, 12)), mats.velcro);
        caster.name = 'caster';
        caster.rotation.z = Math.PI / 2;
        caster.position.set(sx * (w / 2 - 0.06), -0.044, sz * (d / 2 - 0.07));
        g.add(caster);
        const foot = new THREE.Mesh(track(new THREE.CylinderGeometry(0.016, 0.019, 0.03, 10)), mats.handle);
        foot.name = 'levelling-foot';
        foot.position.set(sx * (w / 2 - 0.022), -0.037, sz * (d / 2 - 0.028));
        g.add(foot);
      }
    }
    if (s.door) {
      const sideExit = s.exit === 'side';
      for (const sx of [-1, 1]) {
        if (sideExit && sx === 1) {
          // right-hand panel split around the cable cutout
          const cy = h * 0.86, cz = d / 2 - 0.22, ch = 0.36, cd = 0.12;
          const segs: [number, number, number, number][] = [
            [h - 0.08 - (cy + ch / 2 - 0.04), d - 0.06, (cy + ch / 2 + h - 0.04) / 2, 0],
            [cy - ch / 2 - 0.04, d - 0.06, (0.04 + cy - ch / 2) / 2, 0],
            [ch, d / 2 - 0.03 - (cz + cd / 2), cy, (cz + cd / 2 + d / 2 - 0.03) / 2],
            [ch, cz - cd / 2 + (d / 2 - 0.03), cy, (cz - cd / 2 - d / 2 + 0.03) / 2],
          ];
          for (const [ph, pd, py, pz] of segs) {
            if (ph <= 0.001 || pd <= 0.001) continue;
            const seg = box(0.008, ph, pd, mats.panelDark, 'side-panel');
            seg.position.set(w / 2 - 0.004, py, pz);
            g.add(seg);
          }
        } else {
          for (const half of [0, 1]) {
            const ph = (h - 0.1) / 2;
            const side = box(0.008, ph - 0.004, d - 0.06, mats.panelDark, 'side-panel');
            side.position.set(sx * (w / 2 - 0.004), 0.05 + ph / 2 + half * ph, 0);
            g.add(side);
            const latch = box(0.01, 0.03, 0.012, mats.handle, 'panel-latch');
            latch.position.set(sx * (w / 2 - 0.008), 0.05 + ph * (half + 0.5), d / 2 - 0.09);
            g.add(latch);
          }
        }
      }
      // perforated front door on a hinge group
      const hinge = new THREE.Group();
      hinge.name = 'door-hinge-' + key;
      hinge.position.set(-w / 2, h / 2, d / 2 + 0.115);
      const door = box(w, h - 0.06, 0.014, mats.mesh, 'mesh-door');
      door.position.set(w / 2, 0, 0.007);
      hinge.add(door);
      // stand-off brackets that carry the door clear of the patch field
      for (const sy of [h * 0.47, -h * 0.47]) {
        const arm = box(0.016, 0.016, 0.115, frameMat, 'door-standoff');
        arm.position.set(0, sy, -0.0575);
        hinge.add(arm);
      }
      const handle = box(0.022, 0.13, 0.026, mats.handle, 'door-handle');
      handle.position.set(w - 0.052, 0, 0.022);
      hinge.add(handle);
      const lockRod = box(0.008, h - 0.3, 0.01, frameMat, 'three-point-rod');
      lockRod.position.set(w - 0.052, 0, 0.014);
      hinge.add(lockRod);
      g.add(hinge);
      registry.doors.push(hinge);

      // rear pair of split doors, hinged outboard on each side
      for (const sx of [-1, 1]) {
        const rh = new THREE.Group();
        rh.name = 'rear-door-hinge-' + key + (sx > 0 ? '-r' : '-l');
        rh.position.set(sx * (w / 2), h / 2, -d / 2 - 0.01);
        const leaf = box(w / 2, h - 0.08, 0.012, mats.mesh, 'rear-door');
        leaf.position.set(-sx * (w / 4), 0, -0.006);
        rh.add(leaf);
        const rHandle = box(0.02, 0.1, 0.022, mats.handle, 'rear-door-handle');
        rHandle.position.set(-sx * (w / 2 - 0.05), 0, -0.016);
        rh.add(rHandle);
        rh.userData.rear = sx;
        g.add(rh);
        registry.doors.push(rh);
      }
    }
    // vertical PDU in the rear channel
    const pdu = box(0.052, h * 0.78, 0.05, mats.pdu, 'pdu-vertical');
    pdu.position.set(w / 2 - 0.05, h * 0.5, -d / 2 + 0.1);
    g.add(pdu);
    for (let i = 0; i < 20; i++) {
      const outlet = box(0.03, 0.012, 0.006, mats.panelDark, 'pdu-outlet');
      outlet.position.set(w / 2 - 0.05, h * 0.13 + i * ((h * 0.74) / 20), -d / 2 + 0.075);
      g.add(outlet);
    }
    // cable exit: brush plate on the roof, side cutout, or rear gland plate
    if (s.exit === 'top') {
      // AR3100 roof: brush-filled slots
      const slots: [number, number, number, number][] = [
        [0.09, 0.06, w / 2 - 0.048, d / 2 - 0.2], [0.175, 0.06, -(w / 2 - 0.13), d / 2 - 0.2],
        [0.175, 0.06, 0, d / 2 - 0.2], [0.175, 0.06, w / 2 - 0.13, -0.02],
        [0.175, 0.06, -(w / 2 - 0.13), -0.02], [0.167, 0.079, 0, -0.02],
        [0.167, 0.079, w / 2 - 0.13, -d / 2 + 0.16], [0.245, 0.079, -0.06, -d / 2 + 0.16],
      ];
      for (const [sw, sd, sx2, sz2] of slots) {
        const plate = box(Math.min(sw, w - 0.06), 0.005, sd, mats.panelDark, 'exit-brush-plate');
        plate.position.set(sx2, h + 0.012, sz2);
        g.add(plate);
        const n = Math.max(3, Math.round(sd / 0.012));
        for (let i = 0; i < n; i++) {
          const bristle = box(Math.min(sw, w - 0.07), 0.012, 0.0035, mats.velcro, 'exit-brush');
          bristle.position.set(sx2, h + 0.019, sz2 - sd / 2 + 0.004 + i * (sd / n));
          g.add(bristle);
        }
      }
    } else if (s.exit === 'side') {
      const cut = box(0.008, 0.34, 0.1, mats.panelDark, 'exit-side-cutout');
      cut.position.set(w / 2 - 0.004, h * 0.86, d / 2 - 0.22);
      g.add(cut);
    } else {
      const gland = box(0.2, 0.1, 0.006, mats.panelDark, 'exit-gland-plate');
      gland.position.set(0, h * 0.9, -d / 2 + 0.02);
      g.add(gland);
    }

    // vertical manager: backing trough + D-ring anchors every 2U
    const mgX = w / 2 - 0.041, mgZ = d / 2 - 0.115;
    const railTop = 0.055 + s.u * U;
    const trough = box(0.03, railTop - 0.1, 0.012, frameMat, 'manager-trough');
    trough.position.set(mgX + 0.017, 0.075 + (railTop - 0.1) / 2, mgZ - 0.012);
    g.add(trough);
    for (let uu = 2; uu < s.u - 1; uu += 2) {
      const y = 0.055 + uu * U;
      const ring = new THREE.Mesh(track(new THREE.TorusGeometry(0.019, 0.0022, 5, 14, Math.PI * 1.35)), mats.handle);
      ring.name = 'manager-d-ring';
      ring.rotation.y = Math.PI / 2;
      ring.rotation.z = -Math.PI * 0.32;
      ring.position.set(mgX, y, mgZ);
      g.add(ring);
      const post = box(0.006, 0.006, 0.026, frameMat, 'd-ring-post');
      post.position.set(mgX + 0.012, y, mgZ - 0.006);
      g.add(post);
    }

    // engraved nameplate strip
    const plate = box(0.16, 0.02, 0.004, frameMat, 'nameplate');
    plate.position.set(0, h + 0.03, d / 2 - 0.02);
    g.add(plate);

    registry.racks[key] = { group: g, spec: s, specKey, w, d, h, x };
    return g;
  }

  /* ─── Devices ─────────────────────────────────────────────────────────── */
  function portLayout(def: DeviceDef) {
    const n = def.ports;
    if (!n) return [] as { i: number; row: number; bank: number; x: number; y: number; w: number }[];
    // real faceplates: RJ45 switches run two rows of 24 in banks of 6; patch
    // panels run one row of 24 in two banks of 12; SFP cages sit in one row.
    const twoRow = (def.ptype === 'rj45' && n >= 20) || (def.ptype === 'bay' && def.h >= 2);
    const bankOf = def.ptype === 'rj45' ? 6 : def.ptype === 'bay' ? 0 : 4;
    const rows = twoRow ? 2 : 1;
    const perRow = Math.ceil(n / rows);
    const banks = bankOf ? Math.ceil(perRow / bankOf) : 1;
    const usable = PANEL_W - (def.ptype === 'bay' ? 0.12 : 0.155); // room for LEDs + uplinks
    const gap = banks > 1 ? 0.0055 : 0;
    const pitch = (usable - gap * (banks - 1)) / perRow;
    const out = [];
    for (let i = 0; i < n; i++) {
      const r = Math.floor(i / perRow), c = i % perRow;
      const bank = bankOf ? Math.floor(c / bankOf) : 0;
      const rowY = rows === 1
        ? def.ptype === 'bay' ? 0 : def.h * U * 0.04
        : r === 0 ? def.h * U * 0.21 : -def.h * U * 0.2;
      out.push({
        i, row: r, bank,
        x: -PANEL_W / 2 + 0.085 + c * pitch + pitch / 2 + bank * gap,
        y: rowY,
        w: Math.min(pitch * 0.78, def.ptype === 'rj45' ? 0.0115 : 0.0135),
      });
    }
    return out;
  }

  function buildDevice(def: DeviceDef, rackKey: string) {
    const rack = registry.racks[rackKey]!;
    const g = new THREE.Group();
    g.name = 'device-' + def.id;
    const h = def.h * U - 0.0015;
    const depth = def.kind === 'server' ? Math.min(0.72, rack.d - 0.18) : def.kind === 'switch' ? 0.42 : 0.16;
    // faceplate art is dim under an ortho key light; lift the chassis tone so
    // the panel reads at 2.5D scale instead of going to mud.
    const lift = (hex: number) => {
      const c = new THREE.Color(hex).convertSRGBToLinear();
      c.r = Math.min(1, c.r * 2.6 + 0.06);
      c.g = Math.min(1, c.g * 2.6 + 0.06);
      c.b = Math.min(1, c.b * 2.6 + 0.065);
      return c.convertLinearToSRGB();
    };
    const baseHex = def.chassis ?? (def.kind === 'patch' || def.kind === 'odf' || def.kind === 'duct' ? 0x3a3a3c : 0x1c1c1a);
    const chassisMat = track(new THREE.MeshStandardMaterial({ color: lift(baseHex), roughness: 0.5, metalness: 0.34 }));
    chassisMat.name = 'chassis-' + def.id;
    const body = box(PANEL_W - 0.004, h, depth, chassisMat, 'chassis');
    body.position.set(0, 0, rack.d / 2 - 0.09 - depth / 2);
    body.userData.dev = def.id;
    g.add(body);
    // rack ears
    for (const sx of [-1, 1]) {
      const ear = box(0.016, h, 0.004, chassisMat, 'rack-ear');
      ear.position.set(sx * (PANEL_W / 2 + 0.008), 0, rack.d / 2 - 0.088);
      ear.userData.dev = def.id;
      g.add(ear);
    }
    const faceZ = rack.d / 2 - 0.088;
    const faceMat = track(mat('face-' + def.id, 0xffffff, { roughness: 0.52, metalness: 0.28 }));
    faceMat.map = track(faceTexture(def, false));
    const plate = box(PANEL_W - 0.006, h - 0.0018, 0.003, faceMat, 'front-plate');
    plate.position.set(0, 0, faceZ - 0.0005);
    plate.userData.dev = def.id;
    g.add(plate);
    // brand accent stripe
    const stripe = box(0.006, h * 0.7, 0.003, track(mat('accent-' + def.id, def.accent, { roughness: 0.4 })), 'brand-stripe');
    stripe.position.set(-PANEL_W / 2 + 0.014, 0, faceZ + 0.001);
    g.add(stripe);
    // white designation label, as on every real panel
    const labelStrip = box(PANEL_W * 0.34, h * 0.16, 0.0025, mats.handle, 'designation-label');
    labelStrip.position.set(-PANEL_W / 2 + 0.09 + PANEL_W * 0.17, h * 0.34, faceZ + 0.002);
    g.add(labelStrip);

    // status LEDs
    for (let i = 0; i < 2; i++) {
      const led = new THREE.Mesh(track(new THREE.CylinderGeometry(0.0022, 0.0022, 0.003, 12)), i ? mats.ledAmber : mats.ledOn);
      led.name = 'led';
      led.rotation.x = Math.PI / 2;
      led.position.set(-PANEL_W / 2 + 0.032 + i * 0.009, h * 0.22, faceZ + 0.002);
      g.add(led);
    }
    // ports / drive bays / duct fingers
    const ports = portLayout(def);
    const portRefs: Record<number, THREE.Vector3> = {};
    for (const p of ports) {
      // aspect ratios follow the real connector families
      const geoms: Record<string, { w: number; h: number }> = {
        rj45: { w: Math.min(p.w, 0.0125), h: h * 0.3 },
        sfp28: { w: Math.min(p.w, 0.0145), h: h * 0.2 },
        qsfp28: { w: Math.min(p.w, 0.0165), h: h * 0.23 },
        bay: { w: p.w, h: h * 0.3 },
        lc: { w: Math.min(p.w, 0.0092), h: h * 0.17 },
        pon: { w: Math.min(p.w, 0.0088), h: h * 0.16 },
        mpo: { w: Math.min(p.w, 0.0135), h: h * 0.15 },
      };
      const geo = (def.ptype ? geoms[def.ptype] : undefined) ?? { w: p.w, h: h * 0.28 };
      const pw = geo.w, ph = geo.h;
      let cage: THREE.Mesh;
      if (def.ptype === 'pon' || def.ptype === 'lc') {
        // LC duplex: two bores side by side inside one bezel
        for (const off of [-pw * 0.3, pw * 0.3]) {
          const bore = new THREE.Mesh(track(new THREE.CylinderGeometry(pw * 0.24, pw * 0.24, 0.007, 10)), mats.port);
          bore.name = 'lc-bore';
          bore.rotation.x = Math.PI / 2;
          bore.position.set(p.x + off, p.y, faceZ - 0.002);
          g.add(bore);
        }
        cage = box(pw, ph, 0.005, mats.bezel, 'lc-adapter');
        cage.position.set(p.x, p.y, faceZ - 0.0035);
      } else {
        cage = box(pw, ph, 0.006, mats.port, 'port-' + def.ptype + '-' + p.i);
        cage.position.set(p.x, p.y, faceZ - 0.0025);
        if (def.ptype === 'rj45') {
          // RJ45: the latch notch in the top edge is what makes it readable
          const notch = box(pw * 0.36, ph * 0.3, 0.005, mats.port, 'rj45-latch-slot');
          notch.position.set(p.x, p.y + ph * 0.5, faceZ - 0.002);
          g.add(notch);
        } else if (def.ptype === 'sfp28' || def.ptype === 'qsfp28') {
          // SFP/QSFP: bright EMI cage lip around a wide, shallow slot
          const lip = box(pw * 1.12, ph * 1.3, 0.0022, mats.handle, 'sfp-cage-lip');
          lip.position.set(p.x, p.y, faceZ - 0.0012);
          lip.userData.dev = def.id;
          g.add(lip);
        }
      }
      cage.userData.dev = def.id;
      cage.userData.port = p.i;
      g.add(cage);
      if (def.ptype === 'rj45') {
        const keystone = box(pw * 1.16, ph * 1.22, 0.004, mats.bezel, 'keystone-body');
        keystone.position.set(p.x, p.y, faceZ - 0.0002);
        keystone.userData.dev = def.id;
        keystone.userData.port = p.i;
        g.add(keystone);
      }
      if (def.ptype === 'rj45' || def.ptype === 'sfp28' || def.ptype === 'qsfp28') {
        const pip = box(pw * 0.28, 0.0016, 0.002, mats.ledOn, 'port-led');
        pip.position.set(p.x - pw * 0.26, p.y + ph * 0.32, faceZ + 0.004);
        g.add(pip);
      }
      portRefs[p.i] = new THREE.Vector3(p.x, p.y, faceZ + 0.01);
    }
    if (def.kind === 'switch' || def.kind === 'fw' || def.kind === 'olt') {
      // bezel lip + louvred intake beside the port field
      const lip = box(PANEL_W - 0.004, 0.003, 0.005, mats.bezel, 'bezel-lip');
      lip.position.set(0, h / 2 - 0.002, faceZ + 0.002);
      g.add(lip);
      const lipB = lip.clone();
      lipB.position.y = -h / 2 + 0.002;
      g.add(lipB);
      for (let i = 0; i < 5; i++) {
        const louvre = box(0.02, 0.0035, 0.003, mats.port, 'intake-louvre');
        louvre.position.set(PANEL_W / 2 - 0.028, -h * 0.3 + i * (h * 0.14), faceZ + 0.001);
        g.add(louvre);
      }
      // console + mgmt ports at the left
      const cons = box(0.012, h * 0.3, 0.005, mats.port, 'console-port');
      cons.position.set(-PANEL_W / 2 + 0.05, -h * 0.24, faceZ + 0.001);
      g.add(cons);
    }
    if (def.kind === 'duct') {
      for (let i = 0; i < 9; i++) {
        const finger = box(0.012, h * 0.8, 0.05, mats.frame, 'duct-finger');
        finger.position.set(-PANEL_W / 2 + 0.04 + i * 0.05, 0, faceZ + 0.026);
        g.add(finger);
      }
    }
    // ── rear detail: PSUs, IEC inlets, mgmt ports, exhaust grille ─────────
    const rearZ = rack.d / 2 - 0.09 - depth;
    const rearMat = track(mat('rear-face-' + def.id, 0xffffff, { roughness: 0.55, metalness: 0.3 }));
    rearMat.map = track(faceTexture(def, true));
    const rearPlate = box(PANEL_W - 0.006, h - 0.0018, 0.003, rearMat, 'rear-plate');
    rearPlate.position.set(0, 0, rearZ - 0.0015);
    rearPlate.userData.dev = def.id;
    g.add(rearPlate);
    if (def.kind === 'server' || def.kind === 'switch' || def.kind === 'olt') {
      const psus = def.h >= 2 ? 2 : def.kind === 'server' ? 2 : 1;
      for (let i = 0; i < psus; i++) {
        const psu = box(0.096, h * (def.h >= 2 ? 0.42 : 0.76), 0.01, mats.bezel, 'psu-module');
        psu.position.set(PANEL_W / 2 - 0.062, def.h >= 2 ? (i ? -h * 0.24 : h * 0.24) : 0, rearZ - 0.005);
        psu.userData.dev = def.id;
        g.add(psu);
        // C14 inlet + retention clip
        const inlet = box(0.019, 0.014, 0.008, mats.port, 'iec-c14-inlet');
        inlet.position.set(PANEL_W / 2 - 0.062, psu.position.y - h * 0.1, rearZ - 0.011);
        g.add(inlet);
        const clip = box(0.026, 0.0025, 0.004, mats.handle, 'psu-clip');
        clip.position.set(PANEL_W / 2 - 0.062, psu.position.y + h * 0.22, rearZ - 0.012);
        g.add(clip);
        const psuLed = new THREE.Mesh(track(new THREE.CylinderGeometry(0.0018, 0.0018, 0.003, 10)), mats.ledOn);
        psuLed.rotation.x = Math.PI / 2;
        psuLed.name = 'psu-led';
        psuLed.position.set(PANEL_W / 2 - 0.03, psu.position.y, rearZ - 0.008);
        g.add(psuLed);
        const psuHandle = box(0.03, 0.006, 0.006, mats.handle, 'psu-handle');
        psuHandle.position.set(PANEL_W / 2 - 0.062, psu.position.y - h * 0.28, rearZ - 0.012);
        g.add(psuHandle);
      }
      // rear mgmt / console ports
      for (let i = 0; i < 3; i++) {
        const rp = box(0.012, h * 0.3, 0.005, mats.port, 'rear-mgmt-port');
        rp.position.set(-PANEL_W / 2 + 0.045 + i * 0.02, -h * 0.2, rearZ - 0.004);
        g.add(rp);
      }
      // exhaust grille across the middle
      for (let i = 0; i < 8; i++) {
        const slot = box(0.014, h * 0.5, 0.003, mats.port, 'exhaust-slot');
        slot.position.set(-PANEL_W / 2 + 0.13 + i * 0.019, h * 0.06, rearZ - 0.003);
        g.add(slot);
      }
    }

    // rear fans (they spin)
    if (def.kind === 'switch' || def.kind === 'server' || def.kind === 'olt') {
      const fanCount = def.h >= 2 ? 4 : 2;
      for (let i = 0; i < fanCount; i++) {
        const hub = new THREE.Group();
        hub.name = 'fan-' + def.id + '-' + i;
        hub.position.set(-0.12 + i * 0.08, 0, rack.d / 2 - 0.09 - depth + 0.012);
        const ring = new THREE.Mesh(track(new THREE.TorusGeometry(h * 0.32, 0.0022, 8, 24)), mats.frame);
        ring.name = 'fan-ring';
        hub.add(ring);
        const rotor = new THREE.Group();
        rotor.name = 'fan-rotor';
        for (let b = 0; b < 5; b++) {
          const blade = box(h * 0.5, 0.004, 0.006, mats.fan, 'fan-blade');
          blade.rotation.z = (b / 5) * Math.PI * 2;
          rotor.add(blade);
        }
        hub.add(rotor);
        registry.fans.push(rotor);
        g.add(hub);
      }
    }

    const yTop = 0.055 + (def.u - 1 + def.h) * U;
    g.position.y = yTop - (def.h * U) / 2;
    registry.devices[def.id] = { def, group: g, rackKey, portRefs, faceZ };
    return g;
  }

  /* ─── Overhead ladder tray + cable runway between racks ────────────────── */
  function buildTray(minX: number, maxX: number, y: number) {
    const g = new THREE.Group();
    g.name = 'overhead-ladder-tray';
    const len = maxX - minX;
    for (const sz of [-0.14, 0.14]) {
      const side = box(len, 0.05, 0.008, mats.tray, 'tray-rail');
      side.position.set((minX + maxX) / 2, y, sz);
      g.add(side);
    }
    const rungs = Math.max(1, Math.floor(len / 0.16));
    for (let i = 0; i <= rungs; i++) {
      const rung = box(0.012, 0.012, 0.3, mats.tray, 'tray-rung');
      rung.position.set(minX + i * (len / rungs), y - 0.018, 0);
      g.add(rung);
    }
    // threaded drop rods to the ceiling
    for (const x of [minX + 0.15, (minX + maxX) / 2, maxX - 0.15]) {
      const rod = new THREE.Mesh(track(new THREE.CylinderGeometry(0.006, 0.006, 0.5, 10)), mats.tray);
      rod.name = 'drop-rod';
      rod.position.set(x, y + 0.25, 0);
      g.add(rod);
    }
    return g;
  }

  /* ─── Cables as real tubes, routed like a datacenter ───────────────────── */
  function worldPort(devId: string, index: number) {
    const d = registry.devices[devId];
    if (!d) return null;
    const local = d.portRefs[index] || d.portRefs[0] || new THREE.Vector3(0, 0, d.faceZ + 0.01);
    const v = local.clone();
    d.group.localToWorld(v);
    return v;
  }

  const LANE_PITCH = 0.0072;
  const LANE_WRAP = 4; // the channel holds five lanes; beyond that runs bundle
  const OUT_Z = 0.026; // lead standoff: just proud of the faceplate
  const COMB_Z = 0.03; // channel depth: inside the rack, clear of the posts

  /** Chaikin-style corner cutting: replaces every sharp joint with a fillet, so
   *  a tube through these points reads as bent cable, not folded pipe. */
  function smoothCurve(pts: THREE.Vector3[], passes = 3) {
    let p = pts.map((v) => v.clone());
    for (let k = 0; k < passes; k++) {
      const out = [p[0]!];
      for (let i = 0; i < p.length - 1; i++) {
        const a = p[i]!, b = p[i + 1]!;
        out.push(a.clone().lerp(b, 0.26), a.clone().lerp(b, 0.74));
      }
      out.push(p[p.length - 1]!);
      p = out;
    }
    return new THREE.CatmullRomCurve3(p, false, 'catmullrom', 0.5);
  }

  /** World-space point where a rack lets cable out, per its datasheet. */
  function exitPoint(rack: RackEntry) {
    const kind = rack.spec.exit;
    // must agree with the aperture cut in buildRack (ex.x / ex.z)
    const chx = rack.x + rack.w / 2 - 0.13;
    if (kind === 'side') return { p: new THREE.Vector3(rack.x + rack.w / 2 + 0.016, rack.h * 0.86, rack.d / 2 - 0.22), kind };
    if (kind === 'rear') return { p: new THREE.Vector3(chx, rack.h * 0.93, -rack.d / 2 + 0.1), kind };
    return { p: new THREE.Vector3(chx, rack.h + 0.028, rack.d / 2 - 0.2), kind };
  }

  /** The gathered bundle: each run keeps its jacket colour, strands twist
   *  around the channel axis, and velcro straps cinch them every 180 mm. */
  function buildBundle(rack: RackEntry, medias: string[]) {
    if (!medias.length) return;
    const g = new THREE.Group();
    g.name = 'cable-bundle-' + rack.specKey;
    // same source as the routing, converted to the group's local frame
    const ex = exitPoint(rack);
    const bx = ex.p.x - rack.x;
    const bz = ex.kind === 'rear' ? ex.p.z + 0.05 : ex.p.z;
    // start where the runs leave the comb, not at the very top of the rail
    const topU = 0.055 + (rack.spec.u - 9) * U;
    const y1 = ex.kind === 'top' ? rack.h + 0.02 : ex.p.y; // stop at the port it leaves by
    const span = y1 - topU;
    if (span < 0.03) return;
    const ring = Math.min(0.0075, 0.0016 * Math.sqrt(medias.length) + 0.003);
    medias.forEach((mk, i) => {
      const spec = MEDIA[mk] ?? MEDIA.cat6a!;
      const jacket = mats.media[mk]?.jk ?? mats.media.cat6a!.jk;
      const phase = (i / medias.length) * Math.PI * 2;
      const pts: THREE.Vector3[] = [];
      for (let s = 0; s <= 14; s++) {
        const t = s / 14;
        const a = phase + t * Math.PI * 1.15; // gentle lay twist
        const r = ring * (0.55 + 0.45 * Math.min(1, t * 3));
        pts.push(new THREE.Vector3(bx + Math.cos(a) * r, topU + span * t, bz + Math.sin(a) * r * 0.85));
      }
      const strand = new THREE.Mesh(
        track(new THREE.TubeGeometry(new THREE.CatmullRomCurve3(pts, false, 'catmullrom', 0.5), 26, spec.r, 6, false)),
        jacket,
      );
      strand.name = 'bundle-strand-' + mk;
      g.add(strand);
    });
    const straps = Math.max(2, Math.floor(span / 0.18));
    for (let i = 0; i < straps; i++) {
      const strap = new THREE.Mesh(track(new THREE.TorusGeometry(ring * 1.5, 0.0028, 6, 16)), mats.velcro);
      strap.name = 'velcro-strap';
      strap.rotation.x = Math.PI / 2;
      strap.position.set(bx, topU + span * ((i + 0.6) / straps), bz);
      g.add(strap);
    }
    rack.group.add(g);
  }

  /** Short front jumper between two panels in the same rack: out of the port,
   *  a soft bight that droops toward the lower port, back in. */
  function jumperCurve(a: THREE.Vector3, b: THREE.Vector3) {
    const dy = b.y - a.y;
    const bight = 0.03 + Math.min(0.032, Math.abs(dy) * 0.34);
    const midY = (a.y + b.y) / 2 - Math.max(0.012, Math.abs(dy) * 0.16);
    return smoothCurve([
      a.clone(),
      new THREE.Vector3(a.x, a.y - 0.004, a.z + OUT_Z * 0.7),
      new THREE.Vector3(a.x + (b.x - a.x) * 0.22, a.y - Math.abs(dy) * 0.2, a.z + bight),
      new THREE.Vector3((a.x + b.x) / 2, midY, a.z + bight * 1.06),
      new THREE.Vector3(b.x - (b.x - a.x) * 0.22, b.y - Math.abs(dy) * 0.2, b.z + bight),
      new THREE.Vector3(b.x, b.y - 0.004, b.z + OUT_Z * 0.7),
      b.clone(),
    ]);
  }

  /** Collinear ladder: Chaikin preserves collinear runs exactly, so a straight
   *  descent survives smoothing where a single waypoint gets filleted away. */
  const tzOf = (i: number) => ((i % 7) - 3) * 0.0115;
  function descend(pts: THREE.Vector3[], x: number, z: number, yFrom: number, yTo: number, steps = 5) {
    for (let i = 0; i <= steps; i++) {
      pts.push(new THREE.Vector3(x, yFrom + (yTo - yFrom) * (i / steps), z));
    }
  }

  type ExitRef = { p: THREE.Vector3; kind: ExitKind };

  /** Structured run: port → forward → droop → side comb → (tray) → back in. */
  function cableCurve(
    a: THREE.Vector3, b: THREE.Vector3, chanA: number, chanB: number, trayY: number,
    sameRack: boolean, laneA: number, laneB: number, exitA: ExitRef, exitB: ExitRef, runIx = 0,
  ) {
    const lane = (ch: number, n: number) => ch - 0.004 + ((n % LANE_WRAP) - (LANE_WRAP - 1) / 2) * LANE_PITCH;
    // lane → 2-D grid: x from lane % WRAP, z from the wrap count, both steps
    // larger than the widest jacket so neighbours cannot interpenetrate
    const tierA = (laneA % 7) * 0.012; // 7 is coprime with LANE_WRAP
    const tierB = (laneB % 7) * 0.012;
    const la = lane(chanA, laneA);
    const lb = lane(chanB, laneB);
    // a run sags in proportion to how far it has to reach — real cable behaviour
    const drop = (from: number, to: number) => Math.min(0.012, Math.abs(to - from) * 0.045 + 0.002);
    const legIn = (p: THREE.Vector3, l: number, tier: number) => {
      const sag = drop(p.x, l);
      const oz = OUT_Z + tier, cz = COMB_Z + tier;
      return [
        new THREE.Vector3(p.x, p.y, p.z + oz * 0.5),
        new THREE.Vector3(p.x + (l - p.x) * 0.2, p.y - sag * 0.4, p.z + oz * 1.5),
        new THREE.Vector3(p.x + (l - p.x) * 0.62, p.y - sag, p.z + cz * 0.95),
        new THREE.Vector3(l, p.y - sag * 0.3, p.z + cz),
      ];
    };
    const pts = [a.clone(), ...legIn(a, la, tierA)];
    if (sameRack) {
      const mid = (a.y + b.y) / 2;
      pts.push(new THREE.Vector3(chanA, mid, a.z + COMB_Z + tierA));
      pts.push(new THREE.Vector3(la, b.y - drop(b.x, lb) * 0.3, b.z + COMB_Z + tierB));
    } else {
      const ea = exitA.p, eb = exitB.p;
      const climbA = ea.y - 0.22;
      if (climbA > a.y + 0.03) pts.push(new THREE.Vector3(la, climbA, a.z + COMB_Z + tierA));
      pts.push(new THREE.Vector3(la, ea.y - 0.06, (a.z + COMB_Z + tierA + ea.z) / 2));
      const sx = ((runIx % 5) - 2) * 0.016; // across the 210 mm aperture
      const sz = tzOf(runIx);
      if (exitA.kind === 'top') {
        pts.push(new THREE.Vector3(ea.x + sx, ea.y, ea.z + sz * 0.5));
        pts.push(new THREE.Vector3(ea.x + sx, trayY - 0.015, ea.z * 0.55 + sz));
      } else {
        pts.push(ea.clone());
        descend(pts, ea.x, ea.z + sz, ea.y + 0.02, trayY - 0.02);
      }
      const tz = ((runIx % 9) - 4) * 0.0115; // spread the crossing across the tray
      pts.push(new THREE.Vector3(ea.x + (eb.x - ea.x) * 0.28, trayY + 0.012, tz * 0.6));
      pts.push(new THREE.Vector3((ea.x + eb.x) / 2, trayY + 0.012, tz));
      pts.push(new THREE.Vector3(eb.x - (eb.x - ea.x) * 0.28, trayY + 0.012, tz * 0.6));
      if (exitB.kind === 'top') {
        pts.push(new THREE.Vector3(eb.x + sx, trayY - 0.015, eb.z * 0.55 + sz));
        pts.push(new THREE.Vector3(eb.x + sx, eb.y, eb.z + sz * 0.5));
      } else {
        descend(pts, eb.x, eb.z + sz, trayY - 0.02, eb.y + 0.02);
        pts.push(eb.clone());
      }
      pts.push(new THREE.Vector3(lb, eb.y - 0.06, (b.z + COMB_Z + tierB + eb.z) / 2));
      const climbB = eb.y - 0.22;
      if (climbB > b.y + 0.03) pts.push(new THREE.Vector3(lb, climbB, b.z + COMB_Z + tierB));
      pts.push(new THREE.Vector3(lb, b.y - drop(b.x, lb) * 0.3, b.z + COMB_Z + tierB));
    }
    pts.push(...legIn(b, lb, tierB).reverse().slice(1));
    pts.push(b.clone());
    return smoothCurve(pts);
  }


  function addCable(curve: THREE.Curve<THREE.Vector3>, mediaKey: string, meta: CableMeta) {
    const spec = MEDIA[mediaKey]!;
    const geo = track(new THREE.TubeGeometry(curve as THREE.Curve<THREE.Vector3> & { getPointAt: (t: number) => THREE.Vector3 }, 140, spec.r, 8, false));
    const pair = mats.media[mediaKey]!;
    const mesh = new THREE.Mesh(geo, pair.jk);
    mesh.name = 'cable-' + mediaKey + '-' + meta.name;
    mesh.userData.link = meta;
    root.add(mesh);
    // connector: plug body + tapered strain relief, shaped per family
    const fam = ['cat6a', 'cat6a_xc', 'cat6a_oob'].includes(mediaKey) ? 'rj45'
      : ['dac', 'aoc'].includes(mediaKey) ? 'sfp'
      : ['pwrA', 'pwrB'].includes(mediaKey) ? 'iec' : 'lc';
    for (const t of [0.004, 0.996]) {
      const tan = curve.getTangentAt(t).normalize();
      const q = new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 1, 0), tan);
      const inward = t < 0.5 ? 1 : -1;
      // seat the body just off the panel so it is not buried in the faceplate
      const f = Math.min(0.4, 0.01 / Math.max(0.05, curve.getLength()));
      const seat = curve.getPointAt(t < 0.5 ? f : 1 - f);
      const cm = mats.plugShell; // dark housing, not the jacket colour
      const add = (m: THREE.Mesh, along: number, up = 0) => {
        m.quaternion.copy(q);
        m.position.copy(seat).addScaledVector(tan, along * inward);
        if (up) m.position.y += up;
        root.add(m);
        return m;
      };
      if (fam === 'rj45') {
        add(box(0.0117, 0.024, 0.0085, cm, 'rj45-plug'), 0);
        const tab = box(0.0068, 0.012, 0.0042, mats.plugClip, 'rj45-latch-tab');
        tab.rotation.x = -0.32;
        add(tab, -0.012, 0.008);
      } else if (fam === 'sfp') {
        add(box(0.0138, 0.04, 0.0092, cm, 'sfp-module'), 0);
        add(box(0.0125, 0.0135, 0.0022, mats.handle, 'sfp-bail-latch'), 0.019);
      } else if (fam === 'iec') {
        add(box(0.0148, 0.032, 0.0148, cm, 'iec-connector'), 0);
        add(box(0.0168, 0.0062, 0.0168, mats.plugClip, 'iec-grip-rib'), -0.009);
      } else {
        // LC duplex: two ferrule bodies under one uniboot clip
        for (const off of [-0.0033, 0.0033]) {
          const leg = box(0.0054, 0.024, 0.0054, mats.plugClip, 'lc-connector');
          add(leg, 0).position.x += off;
        }
        add(box(0.0128, 0.0075, 0.0062, mats.handle, 'lc-uniboot-clip'), -0.014);
      }
      const boot = new THREE.Mesh(
        track(new THREE.CylinderGeometry(spec.r * 2.3, spec.r * 1.05, 0.024, 10)), pair.bt);
      boot.name = 'strain-relief';
      add(boot, -0.026);
    }
    const length = curve.getLength();
    registry.cables.push({ mesh, curve, mediaKey, meta, length });
    if (meta.live) {
      const dotMat = track(mat('packet-' + meta.name, 0xeaf7f0, { emissive: 0xeaf7f0, emissiveIntensity: 1.1, roughness: 0.3 }));
      const dot = new THREE.Mesh(track(new THREE.SphereGeometry(spec.r * 1.15, 8, 8)), dotMat);
      dot.name = 'packet';
      root.add(dot);
      registry.packets.push({ dot, curve, t: Math.random(), speed: 0.16 + Math.random() * 0.1 });
    }
    return length;
  }

  function makeLabel(text: string, pos: THREE.Vector3) {
    const cvs = document.createElement('canvas');
    cvs.width = 512;
    cvs.height = 96;
    const ctx = cvs.getContext('2d')!;
    ctx.fillStyle = 'rgba(15,15,14,0.86)';
    ctx.beginPath();
    ctx.roundRect(2, 20, 508, 56, 12);
    ctx.fill();
    ctx.fillStyle = '#FAF9F5';
    ctx.font = '600 38px "JetBrains Mono", monospace';
    ctx.textBaseline = 'middle';
    ctx.fillText(text, 18, 49);
    const tex = track(new THREE.CanvasTexture(cvs));
    tex.colorSpace = THREE.SRGBColorSpace;
    const spriteMat = new THREE.SpriteMaterial({ map: tex, transparent: true, depthTest: false });
    registry.disposables.push(spriteMat);
    const sprite = new THREE.Sprite(spriteMat);
    sprite.name = 'cable-label';
    sprite.scale.set(0.86, 0.161, 1);
    sprite.position.copy(pos);
    sprite.visible = false;
    root.add(sprite);
    sprite.userData.devs = null;
    registry.labels.push(sprite);
    return sprite;
  }

  /* ─── Assembly ─────────────────────────────────────────────────────────── */
  const specA = RACK_SPECS[opts.rackA]!, specB = RACK_SPECS[opts.rackB]!;
  const gap = 0.1;
  const xA = -(specA.w / 1000 + gap) / 2;
  const xB = (specB.w / 1000 + gap) / 2;

  root.add(buildRack('A', opts.rackA, xA));
  root.add(buildRack('B', opts.rackB, xB));
  const cpiX = xB + specB.w / 2000 + 0.75;
  root.add(buildRack('C', 'cpi', cpiX));

  for (const def of opts.fitout.A!) registry.racks.A!.group.add(buildDevice(def, 'A'));
  for (const def of opts.fitout.B!) registry.racks.B!.group.add(buildDevice(def, 'B'));

  root.updateMatrixWorld(true); // port anchors are read in world space below

  const trayY = Math.max(specA.h, specB.h) / 1000 + 0.11;
  root.add(buildTray(xA - specA.w / 2000 - 0.1, cpiX + 0.4, trayY));

  // vertical manager channel: between the 19" rail and the side panel
  const chanA = xA + specA.w / 2000 - 0.037;
  const chanB = xB + specB.w / 2000 - 0.037;

  // Sort every run by the height of its endpoints first: the comb then reads as
  // a parallel fan (reference photos) instead of a cat's cradle of crossings.
  const rackOf = (id: string) => registry.devices[id]?.rackKey;
  const laneOrder = new Map<string, number>();
  for (const key of ['A', 'B']) {
    const ys = opts.links
      .map((l, i) => ({
        i,
        y: Math.max(
          registry.devices[l.a[0]] && rackOf(l.a[0]) === key ? worldPort(l.a[0]!, l.a[1]!)!.y : -9,
          registry.devices[l.b[0]] && rackOf(l.b[0]) === key ? worldPort(l.b[0]!, l.b[1]!)!.y : -9,
        ),
      }))
      .filter((o) => o.y > -9)
      .sort((p, q) => q.y - p.y);
    ys.forEach((o, n) => laneOrder.set(key + ':' + o.i, n));
  }
  const laneCount: Record<string, number> = { A: 0, B: 0 };
  const nextLane = (key: string) => {
    const n = laneCount[key] ?? 0;
    laneCount[key] = n + 1;
    return n;
  };
  const bundleMedia: Record<string, string[]> = { A: [], B: [] };
  let crossIx = 0;
  for (const [i, l] of opts.links.entries()) {
    if (!registry.devices[l.a[0]] || !registry.devices[l.b[0]]) continue;
    const a = worldPort(l.a[0], l.a[1]);
    const b = worldPort(l.b[0], l.b[1]);
    if (!a || !b) continue;
    const ka = rackOf(l.a[0])!, kb = rackOf(l.b[0])!;
    const same = ka === kb;
    const cA = ka === 'A' ? chanA : chanB;
    const cB = kb === 'A' ? chanA : chanB;
    const kindOf = (id: string) => registry.devices[id]!.def.kind;
    const nearBy = Math.abs(a.y - b.y) <= U * 3.2;
    const panelHop = same && nearBy
      && (['patch', 'odf'].includes(kindOf(l.a[0])) || ['patch', 'odf'].includes(kindOf(l.b[0])));
    let curve: THREE.Curve<THREE.Vector3>;
    if (panelHop) {
      curve = jumperCurve(a, b);
    } else {
      const lnA = laneOrder.get(ka + ':' + i) ?? nextLane(ka);
      const lnB = same ? lnA : (laneOrder.get(kb + ':' + i) ?? nextLane(kb));
      curve = cableCurve(a, b, cA, cB, trayY - 0.03, same, lnA, lnB,
        exitPoint(registry.racks[ka]!), exitPoint(registry.racks[kb]!), same ? 0 : crossIx++);
      bundleMedia[ka]?.push(l.m);
      bundleMedia[kb]?.push(l.m);
    }
    const len = addCable(curve, l.m, {
      name: l.a[0] + ':' + l.a[1] + '→' + l.b[0] + ':' + l.b[1],
      devs: [l.a[0], l.b[0]], live: l.live, media: l.m,
    });
    const sprite = makeLabel(
      MEDIA[l.m]!.label.split(' · ')[0] + '  ' + stockLength(len) + ' m',
      curve.getPointAt(0.5).clone().add(new THREE.Vector3(0, 0.06, 0)),
    );
    sprite.userData.devs = [l.a[0], l.b[0]];
    sprite.userData.keyRun = !same; // cross-rack runs are the ones worth naming
  }

  for (const k of ['A', 'B']) buildBundle(registry.racks[k]!, bundleMedia[k]!);

  // power cords: PDU → each server rear, A feed black / B feed red
  for (const rackKey of ['A', 'B']) {
    const rack = registry.racks[rackKey]!;
    const powered = opts.fitout[rackKey]!.filter((d) => d.kind === 'server' || d.kind === 'switch');
    powered.forEach((def, i) => {
      const d = registry.devices[def.id];
      if (!d) return;
      const y = d.group.position.y;
      const rearZ = -rack.d / 2 + 0.18;
      const pduX = rack.x + rack.w / 2 - 0.05;
      const feed = i % 2 === 0 ? 'pwrA' : 'pwrB';
      // the outlet strip faces +z at z = -d/2 + 0.075; stop just in front of it
      const outletZ = -rack.d / 2 + 0.075;
      const inletX = pduX - 0.042; // clear of the 52 mm PDU body
      const pts = [
        new THREE.Vector3(rack.x + 0.09, y, rearZ),
        new THREE.Vector3(rack.x + 0.19, y - 0.022, rearZ + 0.02),
        new THREE.Vector3(inletX - 0.05, y + 0.012, outletZ + 0.055),
        new THREE.Vector3(inletX, y + 0.03, outletZ + 0.022),
        new THREE.Vector3(inletX, y + 0.038, outletZ + 0.008),
      ];
      addCable(new THREE.CatmullRomCurve3(pts, false, 'catmullrom', 0.4), feed,
        { name: 'power-' + def.id, devs: [def.id], live: false, media: feed });
    });
  }

  const amb = new THREE.HemisphereLight(0xdce6ff, 0x1a1a18, 1.25);
  amb.name = 'room-ambient';
  root.add(amb);
  const fill = new THREE.DirectionalLight(0xfff6ec, 2.1);
  fill.name = 'faceplate-fill';
  fill.position.set(-2.2, 2.0, 2.6);
  root.add(fill);
  const face = new THREE.DirectionalLight(0xffffff, 1.05);
  face.name = 'faceplate-key';
  face.position.set(-1.1, 1.3, 2.2);
  root.add(face);
  const rear = new THREE.DirectionalLight(0xe8f0ff, 0.7);
  rear.name = 'rear-fill';
  rear.position.set(-0.6, 2.2, -3.4);
  root.add(rear);
  const rim = new THREE.PointLight(0xbfe9ff, 0.9, 4.2, 2);
  rim.name = 'aisle-rim';
  rim.position.set(chanA - 0.9, trayY - 0.7, 1.5);
  root.add(rim);

  return { root, registry, trayY };
}

/* ─── Host-facing helpers ────────────────────────────────────────────────── */

/** doors: closed = swung open past the cable plane (default), so no lead ever
 *  crosses the slab; the toggle shuts them flush for the enclosure look. */
export function applyDoors(registry: Registry, closed: boolean) {
  for (const hinge of registry.doors) {
    const rear = hinge.userData.rear as number | undefined;
    if (rear) hinge.rotation.y = closed ? 0 : rear * 1.85; // split pair
    else hinge.rotation.y = closed ? 0 : -1.95; // front leaf
  }
}

/** with 25 runs in frame, showing every label at once is unreadable: without a
 *  selection show only the live fibre hops, with one show just that device's. */
export function applyLabels(registry: Registry, on: boolean, sel: string | null) {
  for (const s of registry.labels) {
    if (!on) {
      s.visible = false;
      continue;
    }
    const devs = (s.userData.devs as string[] | null) || [];
    s.visible = sel ? devs.includes(sel) : !!s.userData.keyRun;
  }
}

/** Dim every cable that does not touch the selected device. */
export function applySelection(registry: Registry, sel: string | null) {
  for (const c of registry.cables) {
    const on = !sel || (c.meta.devs && c.meta.devs.includes(sel));
    const m = c.mesh.material as THREE.MeshStandardMaterial;
    m.transparent = !!sel;
    m.opacity = on ? 1 : 0.12;
    m.needsUpdate = true;
  }
}

/** One animation step: fans spin, packets crawl their curve. */
export function tick(registry: Registry, dt: number, anim: boolean) {
  if (!anim) {
    for (const p of registry.packets) p.dot.visible = false;
    return;
  }
  for (const rotor of registry.fans) rotor.rotation.z += dt * 9;
  for (const p of registry.packets) {
    if (!Number.isFinite(p.t)) p.t = 0;
    p.t += dt * p.speed;
    if (p.t >= 1) p.t -= Math.floor(p.t);
    // clamp inside the arc-length table: getPointAt(0) / (1) can walk off it
    const u = Math.min(0.999, Math.max(0.001, p.t));
    p.dot.position.copy(p.curve.getPointAt(u));
    p.dot.visible = true;
  }
}

/** Pick the media a new patch between two devices would actually use. */
export function mediaFor(a: DeviceDef, b: DeviceDef) {
  const t = [a.ptype, b.ptype];
  if (t.includes('mpo')) return 'mpo';
  if (t.includes('lc') || t.includes('pon')) return 'os2';
  if (t.every((x) => x === 'qsfp28')) return 'om4';
  if (t.includes('sfp28') || t.includes('qsfp28')) return 'dac';
  return 'cat6a';
}

/** Free every geometry/material/texture the build allocated. */
export function disposeScene(built: BuiltScene) {
  for (const d of built.registry.disposables) d.dispose();
  built.registry.disposables.length = 0;
  built.root.removeFromParent();
}

/** Patch cords ship in fixed lengths; report the next size up. */
const STOCK_M = [0.25, 0.5, 1, 1.5, 2, 3, 5, 7, 10, 15, 20, 30];
export function stockLength(m: number) {
  return STOCK_M.find((s) => s >= m * 1.12) || Math.ceil(m / 5) * 5;
}

/** World-space position of an already-built device's port. For callers
 *  outside buildScene's own closure (NG-PH3D P2: estimating a new patch's
 *  length before it's saved) — same transform buildScene uses internally
 *  for cable routing, just addressed by the public Registry it returns. */
export function devicePortWorld(registry: Registry, devId: string, index: number): THREE.Vector3 | null {
  const d = registry.devices[devId];
  if (!d) return null;
  const local = d.portRefs[index] ?? d.portRefs[0] ?? new THREE.Vector3(0, 0, d.faceZ + 0.01);
  return d.group.localToWorld(local.clone());
}
