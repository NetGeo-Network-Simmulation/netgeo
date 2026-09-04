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
import { mergeGeometries } from 'three/examples/jsm/utils/BufferGeometryUtils.js';
import { getBootGeometry, type BootFamily, type CageFamily } from './bootAssets';

/* ─── Real-world geometry (EIA-310): 1U = 44.45 mm, 19" panel = 482.6 mm ─── */
export const U = 0.04445;
const PANEL_W = 0.4826;
const MOUNT_W = 0.4651; // 19" mounting-hole centre spacing (EIA-310)
/* Chassis BODY width (not the faceplate/ears above) — 482.6mm faceplate and
 * 465.1mm hole-spacing are NOT the body width; a device's actual sheet-metal
 * box is narrower so it clears the rack rails. 437mm is the doc's derived
 * mid-point of the 435–440mm industry-common body width (docs/design/
 * 24-DEVICE-PHYSICAL-SPEC.md §1.2.1, status V(2nd)/derived — no primary EIA-
 * 310 text was read). Same for every 19" rackmount device; not a per-SKU
 * number, so it lives here once instead of being duplicated onto every
 * DeviceDef. */
const CHASSIS_BODY_W = 0.437;

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
// QA 2026-09-01 (Slice G bug 2): these were 0x17171a..0x2e2e30 — within a
// few percent of the scene's own near-black background (--ng-bg-0 #0F0F0E),
// so every enclosure read as a flat silhouette instead of dark metal. Lifted
// each to sit clearly above the background while keeping the per-vendor
// shade differences (still reads as "dark rack", not light gray).
export const RACK_SPECS: Record<string, RackSpec> = {
  apc: { label: 'APC NetShelter SX AR3100', u: 42, w: 600, d: 1070, h: 1991, frame: 0x35353a, door: true, exit: 'top' },
  dell: { label: 'Dell PowerEdge 4210', u: 42, w: 600, d: 1200, h: 2010, frame: 0x38393e, door: true, exit: 'top' },
  hpe: { label: 'HPE G2 Enterprise 42U', u: 42, w: 600, d: 1075, h: 2015, frame: 0x3d3f45, door: true, exit: 'rear' },
  vertiv: { label: 'Vertiv VR3350', u: 42, w: 600, d: 1100, h: 2000, frame: 0x363639, door: true, exit: 'side' },
  eaton: { label: 'Eaton RE 42U', u: 42, w: 600, d: 1000, h: 2000, frame: 0x393a3e, door: true, exit: 'side' },
  rittal: { label: 'Rittal TS IT 42U', u: 42, w: 600, d: 1000, h: 2000, frame: 0x4c4f52, door: true, exit: 'top' },
  cpi: { label: 'Chatsworth two-post 45U', u: 45, w: 500, d: 76, h: 2134, frame: 0x525356, door: false, exit: 'side' },
};

/** A rack's real backend `ru_height` almost never matches its enclosure
 *  profile's datasheet U count (RACK_SPECS is a fixed template per vendor
 *  SKU) — Slice F: derive the mesh spec actually built from the real height
 *  instead of always building the template's own U count regardless. The
 *  frame cap / base thickness above the rail zone is real hardware, not
 *  proportional to U count, so it's held constant (taken from the template)
 *  while only the rail section scales. */
function deriveSpec(specKey: string, ruHeight?: number): RackSpec {
  const t = RACK_SPECS[specKey]!;
  if (!ruHeight || ruHeight === t.u) return t;
  const capMm = t.h - t.u * U * 1000;
  return { ...t, u: ruHeight, h: ruHeight * U * 1000 + capMm };
}

export interface MediaSpec {
  label: string;
  jacket: number;
  boot: number;
  r: number;
  /** Minimum installed bend radius (metres), docs/design/24-DEVICE-PHYSICAL-
   *  SPEC.md §2.b. Undefined where the doc has no verified number for this
   *  media (dac/aoc/pwrA/pwrB) — the invariant test skips those rather than
   *  guessing a threshold. */
  minBendM?: number;
}

/* ─── Cable media: jacket colour per TIA-598 / datacenter practice ─────────
 * os2/os2apc/om3/om4/cat6a/dac/coax map onto the 9 backend `CableMedia`
 * values (see plantAdapter.ts). om5/mpo/cat6a_xc/cat6a_oob/aoc/pwrA/pwrB have
 * no backend counterpart yet — dead in the P1 production path, kept for
 * P5/P6 (backend enum growth, patch-panel hops, PDU visuals). */
// Bend-radius numbers: docs/design/24-DEVICE-PHYSICAL-SPEC.md §2.b. Fibre
// patch (os2/os2apc) uses the "typ" installed figure for that exact cable
// class (20mm); MM/MPO use the loose-tube figure (25-30mm, upper bound);
// Cat6A/coax use the doc's own numbers directly (no range to pick from, or
// the single value given).
export const MEDIA: Record<string, MediaSpec> = {
  os2: { label: 'OS2 single-mode · LC/UPC', jacket: 0xf2c21b, boot: 0x1e6fd9, r: 0.0017, minBendM: 0.02 },
  os2apc: { label: 'OS2 single-mode · LC/APC', jacket: 0xf2c21b, boot: 0x1fa34a, r: 0.0017, minBendM: 0.02 },
  om3: { label: 'OM3 multimode · aqua', jacket: 0x36c6c0, boot: 0x36c6c0, r: 0.0017, minBendM: 0.03 },
  om4: { label: 'OM4 multimode · violet', jacket: 0xae7bc6, boot: 0xae7bc6, r: 0.0017, minBendM: 0.03 },
  cat6a: { label: 'Cat6A horizontal · blue', jacket: 0x2f6bff, boot: 0x2f6bff, r: 0.0034, minBendM: 0.051 },
  dac: { label: 'DAC twinax 25/100G · black', jacket: 0x16150f, boot: 0x2a2a26, r: 0.0042 },
  coax: { label: 'Coax 50Ω · black', jacket: 0x16150f, boot: 0x1a1a1c, r: 0.004, minBendM: 0.05 },
  // DEAD CODE — P6: no backend CableMedia counterpart today.
  om5: { label: 'OM5 wideband · lime', jacket: 0xa6d608, boot: 0xa6d608, r: 0.0017, minBendM: 0.03 },
  mpo: { label: 'MPO/MTP trunk 12F', jacket: 0x36c6c0, boot: 0x15171b, r: 0.0032, minBendM: 0.03 },
  cat6a_xc: { label: 'Cat6A cross-connect · orange', jacket: 0xf07020, boot: 0xf07020, r: 0.0034, minBendM: 0.051 },
  cat6a_oob: { label: 'Cat6A mgmt / OOB · green', jacket: 0x27c28b, boot: 0x27c28b, r: 0.003, minBendM: 0.051 },
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
  /** Real front-panel port families in left-to-right order (e.g. a switch's
   *  48× RJ45 access bank followed by 4× SFP+ uplinks) — sourced from a real
   *  device's own port catalog (deviceTypes.ts, via plantAdapter), not
   *  guessed. When absent, `ports`/`ptype` above render as a single uniform
   *  family — the generic/legacy fallback path (manual DeviceDef literals,
   *  or a node that resolved to a `generic-*` device type). */
  portGroups?: { type: PortType; count: number }[];
  /** True when this device resolved to deviceTypes.ts's `generic-*` fallback
   *  (no real model matched) rather than a curated real SKU — surfaced as an
   *  "approximate shape" marker, never used to fake real geometry. */
  generic?: boolean;
  /** Real chassis body width/depth in metres, from deviceTypes.ts's
   *  `chassisMm` (docs/design/24-DEVICE-PHYSICAL-SPEC.md §8.1, V/V(2nd)
   *  only). Undefined falls back to CHASSIS_BODY_W / the kind-based depth
   *  heuristic below — never a guessed per-model number. */
  bodyWidthM?: number;
  bodyDepthM?: number;
  /** True only for deviceTypes.ts entries with `front.hasLcd` (§8.2 V(2nd) —
   *  currently just ubiquiti-usw-pro-48) — draws a small LCD touchscreen. */
  hasLcd?: boolean;
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
  /** NG-PH3D 3b LOD: per-port fine detail (cages, latch notches, LC bores,
   *  LED pips) — hidden by `applyLod()` once the camera is zoomed out past
   *  a full rack, shown up close. Not chassis/faceplate: those stay visible
   *  at every zoom (faceTexture() already paints the port pattern at low
   *  res, so hiding the 3D detail on top of it reads as one continuous
   *  simplification, not a pop). */
  fineDetail: THREE.Object3D[];
}

function mat(name: string, color: number, opts: THREE.MeshStandardMaterialParameters = {}) {
  const m = new THREE.MeshStandardMaterial({ color, roughness: 0.62, metalness: 0.22, ...opts });
  m.name = name;
  return m;
}

function makeMaterials() {
  const m = {
    frame: mat('rack-frame', 0x35353a, { roughness: 0.5, metalness: 0.35 }),
    // QA 2026-09-01 (Slice G bug 2): panelDark/mesh were 0x121211/0x232325 —
    // the side panels and mesh front door are the largest visible surfaces
    // in the 2.5D POV, so leaving them this dark is most of why the whole
    // enclosure read as a black silhouette even after the frame/rail lift.
    panelDark: mat('panel-dark', 0x3c3b38, { roughness: 0.7 }),
    mesh: mat('mesh-door', 0x505055, { roughness: 0.55, metalness: 0.3, transparent: true, opacity: 0.55 }),
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
  // QA 2026-09-01 (Slice G bug 2): was #1B1B1D, near the scene's own
  // near-black background — matches the RACK_SPECS frame lift above.
  ctx.fillStyle = '#35353A';
  ctx.fillRect(0, 0, cvs.width, cvs.height);
  // EIA-310: three holes per U at 15.875 / 15.875 / 12.7 mm centres
  const offs = [0.1786, 0.5, 0.8214].map((f) => f * perU);
  for (let i = 0; i < u; i++) {
    const y0 = i * perU;
    ctx.fillStyle = '#141416';
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

/** `DeviceDef.portGroups` if present, else the single legacy `ports`/`ptype`
 *  pair wrapped as one group — the one place both `portLayout()` (3D cages)
 *  and `faceTexture()` (the outline artwork behind them) read a device's
 *  port families from, so the two never drift apart. */
function portGroupsOf(def: DeviceDef): { type: PortType; count: number }[] {
  if (def.portGroups?.length) return def.portGroups;
  if (!def.ports) return [];
  return [{ type: def.ptype ?? 'rj45', count: def.ports }];
}

/** Resolve a `--ng-*-rgb` theme token (theme/tokens.ts, applied to
 *  `document.documentElement` by `applyTheme()`) into an rgba() string —
 *  canvas 2D doesn't understand `var()`, so this is the one spot that reads
 *  the live cascade instead of hardcoding a colour. Falls back to a neutral
 *  grey outside a browser (tests). */
function themeRgba(cssVar: string, alpha: number, fallback = '148,163,184'): string {
  const triplet = typeof document !== 'undefined'
    ? getComputedStyle(document.documentElement).getPropertyValue(cssVar).trim()
    : '';
  return `rgba(${(triplet || fallback).replace(/\s+/g, ',')},${alpha})`;
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
    // port field outlines: roughly where the 3D cages sit (decorative — the
    // real geometry in buildDevice()/portLayout() is the source of truth).
    // Walks the same left-to-right port groups so a multi-family device
    // (e.g. 48× RJ45 + 8× SFP+) doesn't paint one giant fake RJ45 block.
    const groups = portGroupsOf(def);
    const n = groups.reduce((s, gr) => s + gr.count, 0);
    if (n) {
      const usableTotal = cvs.width * 0.79;
      let x0 = cvs.width * 0.115;
      let i = 0;
      for (const grp of groups) {
        const gn = grp.count;
        const twoRow = grp.type === 'rj45' && gn >= 20;
        const rows = twoRow ? 2 : 1;
        const perRow = Math.ceil(gn / rows);
        const bankOf = grp.type === 'rj45' ? 6 : 4;
        const banks = Math.ceil(perRow / bankOf);
        const groupW = usableTotal * (gn / n);
        const gap = banks > 1 ? cvs.width * 0.0115 : 0;
        const pitch = (groupW - gap * (banks - 1)) / perRow;
        for (let k = 0; k < gn; k++) {
          const r = Math.floor(k / perRow);
          const c = k % perRow;
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
          i++;
        }
        x0 += groupW;
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
    if (def.generic) {
      // Approximate-shape marker: dashed hachure border, drawn as discrete
      // filled segments via fillRect (not strokeRect+setLineDash, which the
      // test suite's canvas 2D stub — vitest.setup.ts — doesn't implement).
      // This device didn't match a curated real SKU (deviceTypes.ts
      // genericFor()) — the faceplate art is a plausible stand-in, not a
      // verified chassis.
      ctx.fillStyle = themeRgba('--ng-fg-rgb', 0.5);
      const dash = 18, gap = 10, bw = 4;
      for (let x = 6; x < cvs.width - 6; x += dash + gap) {
        ctx.fillRect(x, 6, Math.min(dash, cvs.width - 6 - x), bw);
        ctx.fillRect(x, cvs.height - 6 - bw, Math.min(dash, cvs.width - 6 - x), bw);
      }
      for (let y = 6; y < cvs.height - 6; y += dash + gap) {
        ctx.fillRect(6, y, bw, Math.min(dash, cvs.height - 6 - y));
        ctx.fillRect(cvs.width - 6 - bw, y, bw, Math.min(dash, cvs.height - 6 - y));
      }
    }
  }
  const tex = new THREE.CanvasTexture(cvs);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = 8;
  return tex;
}

/**
 * NG-PH3D 3a: the physically correct hang of a cable between two points —
 * not an eyeballed bezier bow. A catenary `y(u) = a·cosh((u−x0)/a) + c` has
 * curvature radius `a·cosh²((u−x0)/a) ≥ a` everywhere, minimum exactly `a`
 * at its vertex — so using the media's own minimum bend radius AS the
 * catenary parameter `a` (with a small safety margin) guarantees the hang
 * itself never bends tighter than the cable can physically take, by
 * construction, no invariant-test-and-adjust loop required. `x0` (the
 * vertex's horizontal offset from p0) is the standard closed-form two-point
 * solution once `a` is fixed. The hang is computed in the vertical plane
 * through both endpoints (x/z lerp linearly with the horizontal parameter)
 * — cable sagging sideways out of that plane isn't a real catenary effect
 * gravity produces, so it isn't modelled. Falls back to a straight line
 * when the two points coincide or have no horizontal separation.
 */
export function catenarySpan(p0: THREE.Vector3, p1: THREE.Vector3, minRadius: number, n: number): THREE.Vector3[] {
  const dx = p1.x - p0.x, dz = p1.z - p0.z;
  const L = Math.hypot(dx, dz);
  const dy = p1.y - p0.y;
  if (L < 1e-5) {
    return Array.from({ length: n + 1 }, (_, i) => p0.clone().lerp(p1, i / n));
  }
  const a = Math.max(1e-4, minRadius) * 1.2; // 20% margin over the enforced floor
  const x0 = L / 2 - a * Math.asinh(dy / (2 * a * Math.sinh(L / (2 * a))));
  const ux = dx / L, uz = dz / L;
  const pts: THREE.Vector3[] = [];
  for (let i = 0; i <= n; i++) {
    const u = (i / n) * L;
    const y = a * (Math.cosh((u - x0) / a) - Math.cosh(x0 / a));
    pts.push(new THREE.Vector3(p0.x + ux * u, p0.y + y, p0.z + uz * u));
  }
  return pts;
}

/* ─── Scene builder ──────────────────────────────────────────────────────── */

/** One real, user-created rack bay. `key` is the caller's stable id for it
 *  (the backend rack id in production) — used as the `registry.racks`/
 *  `DeviceEntry.rackKey` join, so it must be unique per call. */
export interface RackBay {
  key: string;
  /** RACK_SPECS enclosure key */
  enclosure: string;
  devices: DeviceDef[];
  /** Real backend `ru_height` (RU) — when it differs from the enclosure
   *  profile's own U count, the built mesh follows this instead (Slice F).
   *  Undefined/omitted keeps the profile's own U count (existing fixtures). */
  ruHeight?: number;
}

export interface BuildOptions {
  /** Left-to-right row of real racks, in display order. At least one —
   *  callers that have zero real racks to show should not call buildScene
   *  at all (NG-PH3D P41: an empty scene is "nothing built", not a scene
   *  with zero bays). */
  racks: RackBay[];
  links: LinkDef[];
}

export interface BuiltScene {
  root: THREE.Group;
  registry: Registry;
  trayY: number;
  /** Total real-rack row width in metres (sum of enclosure widths + 0.1m
   *  gaps, N-1 gaps for N bays). Exposed so the host panel's camera framing
   *  can widen the frustum for a wide row instead of duplicating this
   *  arithmetic (NG-PH3D P41). */
  rowWidthM: number;
}

export function buildScene(opts: BuildOptions): BuiltScene {
  const mats = makeMaterials();
  const registry: Registry = {
    racks: {}, devices: {}, cables: [], fans: [], packets: [], labels: [], doors: [], disposables: [], fineDetail: [],
  };
  const root = new THREE.Group();
  root.name = 'netgeo-physical-plant';

  // NG-PH3D 3a: instance transforms for the Blender-authored rj45/lc boot
  // assets, collected while cables are built and flushed into one
  // InstancedMesh per family right before buildScene returns.
  const bootInstances: Record<BootFamily, { pos: THREE.Vector3; quat: THREE.Quaternion; color: THREE.Color }[]> = {
    rj45: [], lc: [],
  };

  // NG-PH3D 3b: same idea for the Blender-authored SFP/QSFP cage shells —
  // one InstancedMesh per family instead of a box (+ EMI lip) per port, so a
  // 48-port switch with 8 SFP+ uplinks costs one draw call for its cages,
  // not eight. `dev`/`port` ride along per-instance so click-to-patch
  // picking (Rack3DElevationPanel's raycaster) can resolve an InstancedMesh
  // hit's `instanceId` back to a real port — see the portMap stashed on the
  // flushed mesh below.
  const cageInstances: Record<CageFamily, { pos: THREE.Vector3; dev: string; port: number }[]> = {
    'cage-sfp': [], 'cage-qsfp': [],
  };
  const CAGE_FAM: Record<'sfp28' | 'qsfp28', CageFamily> = { sfp28: 'cage-sfp', qsfp28: 'cage-qsfp' };
  // Every cage is authored with its length along local Y, origin at the
  // front mouth, body extending -Y into the chassis (build_assets.py header
  // comment). Devices always face +Z (portRefs sit in front of faceZ, see
  // devicePortWorld), so the same fixed rotation mounts every cage flush
  // with the faceplate — no per-port orientation needed.
  const CAGE_QUAT = new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 1, 0), new THREE.Vector3(0, 0, 1));

  /* Sesi LOD tuning (2026-09-02): the procedural RJ45/LC port decoration —
   * cage box, latch notch, keystone body, port LED, LC bores, LC adapter —
   * used to be one THREE.Mesh per port per part (a 48-port switch alone
   * cost ~190 draw calls just for these). Every part shares one fixed
   * material (mats.port/bezel/ledOn) and only differs by position + a
   * per-device box/cylinder size (faceplate height scales with U count), so
   * the same InstancedMesh-with-non-uniform-scale trick the SFP/QSFP cages
   * already use (above) applies: a single unit geometry (1x1x1 box, or a
   * unit cylinder for the LC bore) with each instance's real w/h/d baked
   * into that instance's own TRS matrix scale, not the shared geometry.
   * `dev`/`port` ride along so click-to-patch picking can resolve an
   * InstancedMesh hit back to a real port exactly like the cage instancing
   * above (see the portMap comment there). */
  interface PortDetailInstance { pos: THREE.Vector3; quat: THREE.Quaternion; scale: THREE.Vector3; dev: string; port: number }
  const portDetailInstances = {
    'rj45-cage': [] as PortDetailInstance[],
    'rj45-notch': [] as PortDetailInstance[],
    'keystone': [] as PortDetailInstance[],
    'port-led': [] as PortDetailInstance[],
    'lc-bore': [] as PortDetailInstance[],
    'lc-adapter': [] as PortDetailInstance[],
  };
  const IDENTITY_QUAT = new THREE.Quaternion();
  const LC_BORE_QUAT = new THREE.Quaternion().setFromEuler(new THREE.Euler(Math.PI / 2, 0, 0));

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

  /** Bake a local transform into a geometry's vertices — the same transform
   *  an Object3D with this position/Euler rotation would apply at render
   *  time, computed the same way (compose from position+quaternion) so a
   *  merged part lands exactly where the individual mesh it replaces would
   *  have. Mutates and returns `g`; used only on geometries about to be
   *  merged, never on one already in the scene. */
  const place = (g: THREE.BufferGeometry, x: number, y: number, z: number, rx = 0, ry = 0, rz = 0) => {
    if (rx || ry || rz) {
      const q = new THREE.Quaternion().setFromEuler(new THREE.Euler(rx, ry, rz, 'XYZ'));
      g.applyMatrix4(new THREE.Matrix4().compose(new THREE.Vector3(x, y, z), q, new THREE.Vector3(1, 1, 1)));
    } else {
      g.translate(x, y, z);
    }
    return g;
  };

  /** A positioned (never-added-to-scene) box geometry, for `mergeParts` below.
   *  Not tracked — it's consumed and disposed by the merge, never rendered
   *  on its own. */
  const boxGeo = (w: number, h: number, d: number, x: number, y: number, z: number) =>
    place(new THREE.BoxGeometry(w, h, d), x, y, z);

  /** NG-PH3D P4: fold N identical-material, non-interactive part copies (rail
   *  holes aside — those are already a texture) into one draw call instead of
   *  N. Only for parts nothing ever picks individually (no `userData.dev`) —
   *  callers are responsible for that invariant, since a merged mesh can't
   *  carry per-part pick data. Inputs are disposed after merging; only the
   *  merged result is tracked for scene cleanup.
   *
   *  Also stashes each input's own local bounding box in `userData.partBoxes`
   *  *before* disposing it — one enclosing `Box3.setFromObject` over parts
   *  scattered on opposite sides of a rack (e.g. left+right side panels)
   *  would falsely claim the empty gap between them as solid. The
   *  no-intersection test (rack3d.test.ts) reads this instead of the mesh's
   *  own envelope for exactly that reason. */
  const mergeParts = (name: string, material: THREE.Material, geoms: THREE.BufferGeometry[]) => {
    const partBoxes = geoms.map((g) => {
      g.computeBoundingBox();
      return g.boundingBox!.clone();
    });
    const merged = track(mergeGeometries(geoms, false));
    for (const g of geoms) g.dispose();
    const mesh = new THREE.Mesh(merged, material);
    mesh.name = name;
    mesh.userData.partBoxes = partBoxes;
    return mesh;
  };

  /* ─── Enclosure ───────────────────────────────────────────────────────── */
  function buildRack(key: string, specKey: string, x: number, ruHeight?: number) {
    const s = deriveSpec(specKey, ruHeight);
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
    // NG-PH3D P4: uprights/rails/accessory channels/frame rails/roof/base
    // beams/casters/feet are fixed per rack (not device-count-scaled) but
    // were still ~30 draw calls of static, never-picked geometry — accumulate
    // by material below, merge once each instead of one draw per part.
    const frameGeos: THREE.BufferGeometry[] = [];
    const railGeos: THREE.BufferGeometry[] = [];
    for (const sx of [-1, 1]) {
      for (const sz of [-1, 1]) {
        frameGeos.push(boxGeo(postW, h, postD, sx * postX, h / 2, sz * (d / 2 - postD / 2)));
      }
      for (const rz of [d / 2 - 0.09, -d / 2 + 0.16]) {
        railGeos.push(boxGeo(0.022, railH, 0.02, sx * railX, 0.055 + railH / 2, rz));
      }
    }
    // zero-U accessory channels in the rear corners (PDU / organiser bays)
    for (const sx of [-1, 1]) {
      frameGeos.push(boxGeo(0.05, railH, 0.05, sx * (w / 2 - 0.028), 0.055 + railH / 2, -d / 2 + 0.095));
    }
    // top/bottom frame rails
    for (const sz of [-1, 1]) {
      for (const sy of [0.02, h - 0.02]) {
        frameGeos.push(boxGeo(w, 0.03, postD, 0, sy, sz * (d / 2 - postD / 2)));
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
        frameGeos.push(boxGeo(pw, 0.02, pd, px, h + 0.01, pz));
      }
    } else {
      frameGeos.push(boxGeo(w, 0.02, d, 0, h + 0.01, 0));
    }
    for (const sz of [-1, 1]) {
      frameGeos.push(boxGeo(w, 0.035, 0.045, 0, -0.018, sz * (d / 2 - 0.03)));
    }
    g.add(mergeParts('rack-frame-merged', frameMat, frameGeos));
    g.add(mergeParts('rack-rails-merged', railMat, railGeos));

    const casterGeos: THREE.BufferGeometry[] = [];
    const footGeos: THREE.BufferGeometry[] = [];
    for (const sx of [-1, 1]) {
      for (const sz of [-1, 1]) {
        casterGeos.push(place(new THREE.CylinderGeometry(0.026, 0.026, 0.016, 12), sx * (w / 2 - 0.06), -0.044, sz * (d / 2 - 0.07), 0, 0, Math.PI / 2));
        footGeos.push(place(new THREE.CylinderGeometry(0.016, 0.019, 0.03, 10), sx * (w / 2 - 0.022), -0.037, sz * (d / 2 - 0.028)));
      }
    }
    g.add(mergeParts('casters-merged', mats.velcro, casterGeos));
    g.add(mergeParts('feet-merged', mats.handle, footGeos));
    if (s.door) {
      const sideExit = s.exit === 'side';
      const sideGeos: THREE.BufferGeometry[] = [];
      const latchGeos: THREE.BufferGeometry[] = [];
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
            sideGeos.push(boxGeo(0.008, ph, pd, w / 2 - 0.004, py, pz));
          }
        } else {
          for (const half of [0, 1]) {
            const ph = (h - 0.1) / 2;
            sideGeos.push(boxGeo(0.008, ph - 0.004, d - 0.06, sx * (w / 2 - 0.004), 0.05 + ph / 2 + half * ph, 0));
            latchGeos.push(boxGeo(0.01, 0.03, 0.012, sx * (w / 2 - 0.008), 0.05 + ph * (half + 0.5), d / 2 - 0.09));
          }
        }
      }
      if (sideGeos.length) g.add(mergeParts('side-panels-merged', mats.panelDark, sideGeos));
      if (latchGeos.length) g.add(mergeParts('panel-latches-merged', mats.handle, latchGeos));
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
    // NG-PH3D P4: 20 identical outlet boxes were 20 draw calls per rack for a
    // part nothing ever picks — one merged mesh instead (§ perf budget).
    const outletGeos: THREE.BufferGeometry[] = [];
    for (let i = 0; i < 20; i++) {
      outletGeos.push(boxGeo(0.03, 0.012, 0.006, w / 2 - 0.05, h * 0.13 + i * ((h * 0.74) / 20), -d / 2 + 0.075));
    }
    g.add(mergeParts('pdu-outlets-merged', mats.panelDark, outletGeos));
    // cable exit: brush plate on the roof, side cutout, or rear gland plate
    if (s.exit === 'top') {
      // AR3100 roof: brush-filled slots. Plates + bristles were up to ~60
      // draw calls per rack on their own (the plan's own named worst offender)
      // — merged per material into two meshes instead of one per part.
      const slots: [number, number, number, number][] = [
        [0.09, 0.06, w / 2 - 0.048, d / 2 - 0.2], [0.175, 0.06, -(w / 2 - 0.13), d / 2 - 0.2],
        [0.175, 0.06, 0, d / 2 - 0.2], [0.175, 0.06, w / 2 - 0.13, -0.02],
        [0.175, 0.06, -(w / 2 - 0.13), -0.02], [0.167, 0.079, 0, -0.02],
        [0.167, 0.079, w / 2 - 0.13, -d / 2 + 0.16], [0.245, 0.079, -0.06, -d / 2 + 0.16],
      ];
      const plateGeos: THREE.BufferGeometry[] = [];
      const bristleGeos: THREE.BufferGeometry[] = [];
      for (const [sw, sd, sx2, sz2] of slots) {
        plateGeos.push(boxGeo(Math.min(sw, w - 0.06), 0.005, sd, sx2, h + 0.012, sz2));
        const n = Math.max(3, Math.round(sd / 0.012));
        for (let i = 0; i < n; i++) {
          bristleGeos.push(boxGeo(Math.min(sw, w - 0.07), 0.012, 0.0035, sx2, h + 0.019, sz2 - sd / 2 + 0.004 + i * (sd / n)));
        }
      }
      g.add(mergeParts('exit-brush-plates-merged', mats.panelDark, plateGeos));
      g.add(mergeParts('exit-brush-bristles-merged', mats.velcro, bristleGeos));
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
    // NG-PH3D P4: up to ~20 ring+post pairs on a 42U rack (40 draws) for
    // parts nothing ever picks — merged per material (ring/post use
    // different geometry AND material, so two merges, not one).
    const ringGeos: THREE.BufferGeometry[] = [];
    const postGeos: THREE.BufferGeometry[] = [];
    for (let uu = 2; uu < s.u - 1; uu += 2) {
      const y = 0.055 + uu * U;
      ringGeos.push(place(new THREE.TorusGeometry(0.019, 0.0022, 5, 14, Math.PI * 1.35), mgX, y, mgZ, 0, Math.PI / 2, -Math.PI * 0.32));
      postGeos.push(boxGeo(0.006, 0.006, 0.026, mgX + 0.012, y, mgZ - 0.006));
    }
    if (ringGeos.length) {
      g.add(mergeParts('manager-d-rings-merged', mats.handle, ringGeos));
      g.add(mergeParts('d-ring-posts-merged', frameMat, postGeos));
    }

    // engraved nameplate strip
    const plate = box(0.16, 0.02, 0.004, frameMat, 'nameplate');
    plate.position.set(0, h + 0.03, d / 2 - 0.02);
    g.add(plate);

    registry.racks[key] = { group: g, spec: s, specKey, w, d, h, x };
    return g;
  }

  /* ─── Devices ─────────────────────────────────────────────────────────── */
  /** Real faceplates: RJ45 switches run two rows of 24 in banks of 6; patch
   *  panels run one row of 24 in two banks of 12; SFP cages sit in one row.
   *  Walks `portGroupsOf(def)` left-to-right, each group getting a width
   *  slice proportional to its own port count, so a device with several
   *  real port families (e.g. 48× RJ45 + 8× SFP+ uplinks) draws each family
   *  with its own connector geometry instead of one uniform block. */
  function portLayout(def: DeviceDef) {
    const groups = portGroupsOf(def);
    const total = groups.reduce((s, gr) => s + gr.count, 0);
    if (!total) return [] as { i: number; ptype: PortType; row: number; bank: number; x: number; y: number; w: number }[];
    const marginFor = (t: PortType) => (t === 'bay' ? 0.12 : 0.155); // room for LEDs + uplinks
    const usableTotal = PANEL_W - Math.max(...groups.map((gr) => marginFor(gr.type)));
    const out: { i: number; ptype: PortType; row: number; bank: number; x: number; y: number; w: number }[] = [];
    let i = 0;
    let x0 = -PANEL_W / 2 + 0.085;
    for (const grp of groups) {
      const n = grp.count;
      const ptype = grp.type;
      const twoRow = (ptype === 'rj45' && n >= 20) || (ptype === 'bay' && def.h >= 2);
      const bankOf = ptype === 'rj45' ? 6 : ptype === 'bay' ? 0 : 4;
      const rows = twoRow ? 2 : 1;
      const perRow = Math.ceil(n / rows);
      const banks = bankOf ? Math.ceil(perRow / bankOf) : 1;
      const groupW = usableTotal * (n / total);
      const gap = banks > 1 ? 0.0055 : 0;
      const pitch = (groupW - gap * (banks - 1)) / perRow;
      for (let k = 0; k < n; k++) {
        const r = Math.floor(k / perRow), c = k % perRow;
        const bank = bankOf ? Math.floor(c / bankOf) : 0;
        const rowY = rows === 1
          ? ptype === 'bay' ? 0 : def.h * U * 0.04
          : r === 0 ? def.h * U * 0.21 : -def.h * U * 0.2;
        out.push({
          i, ptype, row: r, bank,
          x: x0 + c * pitch + pitch / 2 + bank * gap,
          y: rowY,
          w: Math.min(pitch * 0.78, ptype === 'rj45' ? 0.0115 : 0.0135),
        });
        i++;
      }
      x0 += groupW;
    }
    return out;
  }

  function buildDevice(def: DeviceDef, rackKey: string) {
    const rack = registry.racks[rackKey]!;
    const g = new THREE.Group();
    g.name = 'device-' + def.id;
    const h = def.h * U - 0.0015;
    // world Y this device's group lands at (set on `g` at the end, computed
    // here too since NG-PH3D 3b's scene-level cage InstancedMesh needs each
    // port's world position while devices are still being built).
    const devY = 0.055 + (def.u - 1 + def.h) * U - (def.h * U) / 2;
    const heuristicDepth = def.kind === 'server' ? Math.min(0.72, rack.d - 0.18) : def.kind === 'switch' ? 0.42 : 0.16;
    // Real per-SKU depth (§8.1), clamped so a deep-but-real chassis (e.g.
    // Dell R740's 737.5mm) never pokes out of a shallow enclosure profile —
    // same safety margin the server heuristic above already applies.
    const depth = def.bodyDepthM != null ? Math.min(def.bodyDepthM, Math.max(0.05, rack.d - 0.18)) : heuristicDepth;
    const bodyW = def.bodyWidthM ?? CHASSIS_BODY_W;
    // faceplate art is dim under an ortho key light; lift the chassis tone so
    // the panel reads at 2.5D scale instead of going to mud. QA 2026-09-01:
    // the old 2.6x lift plus a very hot "faceplate-fill" key light (see
    // makeMaterials's caller below) blew this out to near-white next to the
    // rack's near-black frame — toned down together with that light so the
    // faceplate reads as "lit metal" rather than "overexposed", not just
    // uniformly dimmer (a single-light cut alone would still clip here).
    const lift = (hex: number) => {
      const c = new THREE.Color(hex).convertSRGBToLinear();
      c.r = Math.min(1, c.r * 1.7 + 0.05);
      c.g = Math.min(1, c.g * 1.7 + 0.05);
      c.b = Math.min(1, c.b * 1.7 + 0.055);
      return c.convertLinearToSRGB();
    };
    const baseHex = def.chassis ?? (def.kind === 'patch' || def.kind === 'odf' || def.kind === 'duct' ? 0x3a3a3c : 0x1c1c1a);
    const chassisMat = track(new THREE.MeshStandardMaterial({ color: lift(baseHex), roughness: 0.5, metalness: 0.34 }));
    chassisMat.name = 'chassis-' + def.id;
    // body is the sheet-metal box between the rack ears — narrower than the
    // 482.6mm faceplate/ears (CHASSIS_BODY_W, see its own comment above).
    const body = box(bodyW, h, depth, chassisMat, 'chassis');
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
    // LCD touchscreen — a verified faceplate feature (§8.2 V(2nd)), only set
    // for ubiquiti-usw-pro-48 among the 9 curated models; a no-op otherwise.
    if (def.hasLcd) {
      const lcdBezel = box(0.05, h * 0.55, 0.003, mats.panelDark, 'lcd-bezel');
      lcdBezel.position.set(-PANEL_W / 2 + 0.075, 0, faceZ + 0.0015);
      g.add(lcdBezel);
      const lcdScreen = box(0.042, h * 0.4, 0.001, mats.ledOn, 'lcd-screen');
      lcdScreen.position.set(-PANEL_W / 2 + 0.075, 0, faceZ + 0.003);
      g.add(lcdScreen);
      registry.fineDetail.push(lcdBezel, lcdScreen);
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
      const geo = geoms[p.ptype] ?? { w: p.w, h: h * 0.28 };
      const pw = geo.w, ph = geo.h;
      // NG-PH3D 3b: real SFP/QSFP cage geometry, once loaded, replaces the
      // procedural box+lip with a queued InstancedMesh transform — no
      // per-port mesh at all, so this branch adds nothing to `g` and skips
      // the box-cage fallback below entirely.
      const cageFam = (p.ptype === 'sfp28' || p.ptype === 'qsfp28') ? CAGE_FAM[p.ptype] : undefined;
      const cageGeo = cageFam ? getBootGeometry(cageFam) : undefined;
      let cage: THREE.Mesh | undefined;
      if (cageFam && cageGeo) {
        // world position: this InstancedMesh is flushed straight into
        // `root`, not `g` — bake in rack.x and the device's own Y (g's own
        // position.y, computed above as `devY`) since there's no parent
        // transform to inherit it from.
        cageInstances[cageFam].push({
          pos: new THREE.Vector3(rack.x + p.x, devY + p.y, faceZ + 0.0005),
          dev: def.id, port: p.i,
        });
      } else if (p.ptype === 'pon' || p.ptype === 'lc') {
        // LC duplex: two bores side by side inside one bezel — queued for
        // InstancedMesh flush (Sesi LOD tuning, see portDetailInstances
        // comment) instead of a mesh per bore/adapter.
        for (const off of [-pw * 0.3, pw * 0.3]) {
          portDetailInstances['lc-bore'].push({
            pos: new THREE.Vector3(rack.x + p.x + off, devY + p.y, faceZ - 0.002),
            quat: LC_BORE_QUAT, scale: new THREE.Vector3(pw * 0.24, 0.007, pw * 0.24),
            dev: def.id, port: p.i,
          });
        }
        portDetailInstances['lc-adapter'].push({
          pos: new THREE.Vector3(rack.x + p.x, devY + p.y, faceZ - 0.0035),
          quat: IDENTITY_QUAT, scale: new THREE.Vector3(pw, ph, 0.005),
          dev: def.id, port: p.i,
        });
      } else if (p.ptype === 'rj45') {
        // RJ45 cage + latch notch + keystone (below) — by far the densest
        // port family (a 48-port switch alone used to cost ~190 draw calls
        // here), so these are the ones that actually move the draw-call
        // budget; queued for InstancedMesh flush instead of one mesh each.
        portDetailInstances['rj45-cage'].push({
          pos: new THREE.Vector3(rack.x + p.x, devY + p.y, faceZ - 0.0025),
          quat: IDENTITY_QUAT, scale: new THREE.Vector3(pw, ph, 0.006),
          dev: def.id, port: p.i,
        });
        portDetailInstances['rj45-notch'].push({
          pos: new THREE.Vector3(rack.x + p.x, devY + p.y + ph * 0.5, faceZ - 0.002),
          quat: IDENTITY_QUAT, scale: new THREE.Vector3(pw * 0.36, ph * 0.3, 0.005),
          dev: def.id, port: p.i,
        });
      } else {
        cage = box(pw, ph, 0.006, mats.port, 'port-' + p.ptype + '-' + p.i);
        cage.position.set(p.x, p.y, faceZ - 0.0025);
        if (p.ptype === 'sfp28' || p.ptype === 'qsfp28') {
          // fallback only — real cage asset not loaded yet. SFP/QSFP: bright
          // EMI cage lip around a wide, shallow slot
          const lip = box(pw * 1.12, ph * 1.3, 0.0022, mats.handle, 'sfp-cage-lip');
          lip.position.set(p.x, p.y, faceZ - 0.0012);
          lip.userData.dev = def.id;
          g.add(lip);
          registry.fineDetail.push(lip);
        }
      }
      if (cage) {
        cage.userData.dev = def.id;
        cage.userData.port = p.i;
        g.add(cage);
        registry.fineDetail.push(cage);
      }
      if (p.ptype === 'rj45') {
        portDetailInstances['keystone'].push({
          pos: new THREE.Vector3(rack.x + p.x, devY + p.y, faceZ - 0.0002),
          quat: IDENTITY_QUAT, scale: new THREE.Vector3(pw * 1.16, ph * 1.22, 0.004),
          dev: def.id, port: p.i,
        });
      }
      if (p.ptype === 'rj45' || p.ptype === 'sfp28' || p.ptype === 'qsfp28') {
        portDetailInstances['port-led'].push({
          pos: new THREE.Vector3(rack.x + p.x - pw * 0.26, devY + p.y + ph * 0.32, faceZ + 0.004),
          quat: IDENTITY_QUAT, scale: new THREE.Vector3(pw * 0.28, 0.0016, 0.002),
          dev: def.id, port: p.i,
        });
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
      // rear mgmt/console ports + exhaust grille: 11 identical-material
      // (mats.port), never-picked boxes per device — one merged mesh
      // instead of 11 draw calls (NG-PH3D P4 perf budget).
      const rearVentGeos: THREE.BufferGeometry[] = [];
      for (let i = 0; i < 3; i++) {
        rearVentGeos.push(boxGeo(0.012, h * 0.3, 0.005, -PANEL_W / 2 + 0.045 + i * 0.02, -h * 0.2, rearZ - 0.004));
      }
      for (let i = 0; i < 8; i++) {
        rearVentGeos.push(boxGeo(0.014, h * 0.5, 0.003, -PANEL_W / 2 + 0.13 + i * 0.019, h * 0.06, rearZ - 0.003));
      }
      g.add(mergeParts('rear-vents-merged-' + def.id, mats.port, rearVentGeos));
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
        // 5 blades, one draw call instead of 5 (NG-PH3D P4) — merged as one
        // static shape; `rotor` (the parent Group) still spins every frame
        // in `tick()`, so the whole fanned shape rotates exactly as before.
        const bladeGeos: THREE.BufferGeometry[] = [];
        for (let b = 0; b < 5; b++) {
          bladeGeos.push(place(new THREE.BoxGeometry(h * 0.5, 0.004, 0.006), 0, 0, 0, 0, 0, (b / 5) * Math.PI * 2));
        }
        rotor.add(mergeParts('fan-blades-merged', mats.fan, bladeGeos));
        hub.add(rotor);
        registry.fans.push(rotor);
        g.add(hub);
      }
    }

    g.position.y = devY;
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
  function chaikin(pts: THREE.Vector3[], passes: number) {
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
    return p;
  }

  function smoothCurve(pts: THREE.Vector3[], passes = 3) {
    return new THREE.CatmullRomCurve3(chaikin(pts, passes), false, 'catmullrom', 0.5);
  }

  /** NG-PH3D 3a: a TRUE circular fillet of radius R at vertex V (incoming
   *  from A, outgoing to B), lying in the A-V-B plane. Chaikin corner-
   *  cutting on its own can't be trusted for this: it's angle-driven and
   *  converges to a fixed limit shape as passes increase (confirmed
   *  empirically — 8 passes reproduced the same violating radius as 5, to
   *  the tenth of a millimetre), and just lerping extra points toward a
   *  sharp vertex creates uneven spacing that provoked Catmull-Rom
   *  overshoot and made curvature WORSE at neighbouring joints (measured
   *  while chasing this). An exact tangent-circle construction (this
   *  function) is the only way to GUARANTEE the resulting radius instead
   *  of hoping a spline behaves. Returns the polyline with V replaced by
   *  `segs+1` points along the fillet arc; A and B are untouched (the
   *  caller supplies them as the neighbours either side). Clamped to at
   *  most 49% of each adjacent edge so two fillets sharing an edge can
   *  never overlap. */
  function filletCorner(A: THREE.Vector3, V: THREE.Vector3, B: THREE.Vector3, R: number, segs = 32): THREE.Vector3[] {
    const d1 = V.clone().sub(A);
    const d2 = B.clone().sub(V);
    const len1 = d1.length(), len2 = d2.length();
    if (len1 < 1e-6 || len2 < 1e-6 || R <= 0) return [V];
    d1.normalize();
    d2.normalize();
    const cosTurn = THREE.MathUtils.clamp(d1.dot(d2), -1, 1);
    if (cosTurn > 0.999) return [V]; // already ~straight — nothing to fillet
    const halfInterior = (Math.PI - Math.acos(cosTurn)) / 2;
    const tanHalf = Math.tan(halfInterior);
    if (tanHalf < 1e-6) return [V]; // ~180° reversal — no well-defined fillet
    let t = R / tanHalf;
    t = Math.min(t, len1 * 0.49, len2 * 0.49);
    const achievedR = t * tanHalf;
    const sinHalf = Math.sin(halfInterior);
    if (achievedR < 1e-6 || sinHalf < 1e-6) return [V];
    const p0 = V.clone().sub(d1.clone().multiplyScalar(t)); // tangent point on A→V
    const p1 = V.clone().add(d2.clone().multiplyScalar(t)); // tangent point on V→B
    const bisector = d2.clone().sub(d1);
    if (bisector.lengthSq() < 1e-12) return [V];
    bisector.normalize();
    const center = V.clone().add(bisector.multiplyScalar(achievedR / sinHalf));
    const u = p0.clone().sub(center); // |u| === |v| === achievedR by construction
    const v = p1.clone().sub(center);
    const out: THREE.Vector3[] = [];
    for (let i = 0; i <= segs; i++) {
      const s = i / segs;
      const dir = u.clone().multiplyScalar(1 - s).add(v.clone().multiplyScalar(s)).normalize();
      out.push(center.clone().add(dir.multiplyScalar(achievedR)));
    }
    return out;
  }

  /** Fillets every interior vertex of a raw control-point run at radius R,
   *  then Chaikin-smooths only the plain straight-run stretches BETWEEN
   *  fillets — never the arcs themselves. Running Chaikin over an arc's own
   *  points shrinks it: Chaikin corner-cutting treats an arc's short
   *  segments as more corners to cut, eating back into the very radius the
   *  fillet just guaranteed (measured — a uniform 5-pass Chaikin after
   *  filleting pushed cables back under their own minimum). Splitting the
   *  route into alternating plain/arc runs and only Chaikin-ing the plain
   *  ones keeps the fillet's radius exact while still rounding every joint
   *  filletCorner declined (near-straight, no fillet needed). */
  function routeCurve(pts: THREE.Vector3[], R: number, chaikinPasses: number, from = 1, to = pts.length - 2) {
    const runs: THREE.Vector3[][] = [];
    let plain: THREE.Vector3[] = [pts[0]!];
    for (let i = 1; i < pts.length - 1; i++) {
      const arc = i >= from && i <= to ? filletCorner(pts[i - 1]!, pts[i]!, pts[i + 1]!, R) : [pts[i]!];
      if (arc.length === 1) {
        plain.push(arc[0]!); // near-straight — stays part of the plain run
        continue;
      }
      plain.push(arc[0]!); // tangent-in point closes this plain run
      runs.push(plain, arc);
      plain = [arc[arc.length - 1]!]; // tangent-out point opens the next one
    }
    plain.push(pts[pts.length - 1]!);
    runs.push(plain);

    const combined: THREE.Vector3[] = [];
    runs.forEach((run, idx) => {
      const chunk = idx % 2 === 1 ? run : chaikin(run, chaikinPasses);
      for (let i = combined.length ? 1 : 0; i < chunk.length; i++) combined.push(chunk[i]!);
    });
    return new THREE.CatmullRomCurve3(combined, false, 'catmullrom', 0.5);
  }

  /** World-space point where a rack lets cable out, per its datasheet. */
  function exitPoint(rack: RackEntry) {
    const kind = rack.spec.exit;
    // must agree with the aperture cut in buildRack (ex.x / ex.z)
    const chx = rack.x + rack.w / 2 - 0.13;
    if (kind === 'side') return { p: new THREE.Vector3(rack.x + rack.w / 2 + 0.016, rack.h * 0.86, rack.d / 2 - 0.22), kind, d: rack.d };
    if (kind === 'rear') return { p: new THREE.Vector3(chx, rack.h * 0.93, -rack.d / 2 + 0.1), kind, d: rack.d };
    return { p: new THREE.Vector3(chx, rack.h + 0.028, rack.d / 2 - 0.2), kind, d: rack.d };
  }

  /** NG-PH3D 3d: the two EIA rail uprights (buildRack's `railGeos`) sit at
   *  a fixed x per rack half (`MOUNT_W/2`) and each occupies a narrow,
   *  fixed z band (`d/2-0.09` and `-d/2+0.16`) running the rack's full
   *  height — a side-exit rack's cable lane sits close enough to that x
   *  that a z-only nudge meant to buy a filleted corner more shared-edge
   *  length can walk the STRAIGHT SEGMENT to its neighbour through one of
   *  those bands, even when the nudged point itself lands safely outside
   *  it (confirmed empirically: capping only the endpoint still left the
   *  segment from a neighbour on the band's far side clipping it).
   *  `from` is that neighbour's own z (assumed already safe, since it's
   *  never itself nudged) — the target is capped at the near edge of
   *  whichever band sits between the two, so the whole segment stays on
   *  `from`'s side of it. */
  function clampZAwayFromRail(from: number, to: number, d: number): number {
    // rail's own 20mm z-depth/2, plus enough margin for the Catmull-Rom
    // curve through this waypoint to overshoot past it before the next
    // control point pulls it back (measured empirically chasing this
    // invariant's violations — a margin sized to the rail's own physical
    // edge alone left the smoothed curve clipping it anyway).
    const half = 0.01 + 0.02;
    let z = to;
    for (const rz of [d / 2 - 0.09, -d / 2 + 0.16]) {
      const lo = rz - half, hi = rz + half;
      if (from <= lo) z = Math.min(z, lo);
      else if (from >= hi) z = Math.max(z, hi);
    }
    return z;
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

  /** Short front jumper between two panels in the same rack: a true
   *  two-point catenary hang from port to port (NG-PH3D 3a — patch cords
   *  are the one cable run in this scene that's actually free-hanging, not
   *  dressed into a channel; see catenarySpan's own doc for why the
   *  channel-routed cableCurve below doesn't get the same treatment).
   *
   *  No separate "straight out of the faceplate" lead-out stub: an early
   *  version added one for visual polish, but *any* stub whose direction
   *  doesn't exactly match the catenary's own tangent at that end creates a
   *  corner, and Chaikin/Catmull-Rom cannot be trusted to round an
   *  arbitrary corner back out to a specific minimum radius — verified
   *  empirically while chasing this invariant's violations (see git log for
   *  this file). catenarySpan's curvature is >= its own parameter `a`
   *  everywhere BY CONSTRUCTION (proof in its own doc comment), so feeding
   *  its samples straight into the curve, with nothing else, is what
   *  actually guarantees the invariant instead of hoping smoothing
   *  preserves it. The minor cost: the cable can leave a port at a slight
   *  angle instead of dead perpendicular to the faceplate. */
  function jumperCurve(a: THREE.Vector3, b: THREE.Vector3, minBend: number) {
    const hang = catenarySpan(a, b, minBend, 32);
    return new THREE.CatmullRomCurve3(hang, false, 'catmullrom', 0.5);
  }

  /** Collinear ladder: Chaikin preserves collinear runs exactly, so a straight
   *  descent survives smoothing where a single waypoint gets filleted away.
   *  NG-PH3D 3a: `steps` (still 5 by default) can end up dividing a SHORT
   *  climb into segments too short for routeCurve's fillet to reach a stiff
   *  cable's own minimum — filletCorner clamps its tangent length to 49% of
   *  the shorter adjacent edge, so it needs edge >= R/0.49 to hit the
   *  requested radius R exactly. Passing `minSeg` (from the cable's own
   *  minBendM) caps how many steps a short climb gets divided into, so its
   *  own segments stay long enough for that clamp to actually deliver. */
  const tzOf = (i: number) => ((i % 7) - 3) * 0.0115;
  function descend(pts: THREE.Vector3[], x: number, z: number, yFrom: number, yTo: number, steps = 5, minSeg = 0) {
    if (minSeg > 0) steps = Math.max(1, Math.min(steps, Math.floor(Math.abs(yTo - yFrom) / minSeg)));
    for (let i = 0; i <= steps; i++) {
      pts.push(new THREE.Vector3(x, yFrom + (yTo - yFrom) * (i / steps), z));
    }
  }

  type ExitRef = { p: THREE.Vector3; kind: ExitKind; d: number };

  // lane → 2-D grid: x from lane % WRAP, z from the wrap count, both steps
  // larger than the widest jacket so neighbours cannot interpenetrate.
  // Hoisted out of cableCurve (NG-PH3D 3e) so externalCableCurve below can
  // build the exact same port→lane→exit leg for a cable whose other end
  // isn't in this scene, instead of a second copy of this math.
  const lane = (ch: number, n: number) => ch - 0.004 + ((n % LANE_WRAP) - (LANE_WRAP - 1) / 2) * LANE_PITCH;
  // a run sags in proportion to how far it has to reach — real cable behaviour
  const dropSag = (from: number, to: number) => Math.min(0.012, Math.abs(to - from) * 0.045 + 0.002);
  // NG-PH3D 3a: the port->channel jog's own standoff/depth (OUT_Z/COMB_Z)
  // are physical rack dimensions, too small on their own to give a stiff
  // cable (Cat6A's 51mm) room to turn without violating its own minimum
  // bend radius — floor them at a fraction of minBend for this jog only,
  // not the shared constants (those also size the comb channel itself).
  function legIn(p: THREE.Vector3, l: number, tier: number, minBend: number) {
    const sag = dropSag(p.x, l);
    const oz = Math.max(OUT_Z, minBend * 0.6) + tier, cz = COMB_Z + tier;
    return [
      new THREE.Vector3(p.x, p.y, p.z + oz * 0.5),
      new THREE.Vector3(p.x + (l - p.x) * 0.2, p.y - sag * 0.4, p.z + oz * 1.5),
      new THREE.Vector3(p.x + (l - p.x) * 0.62, p.y - sag, p.z + cz * 0.95),
      new THREE.Vector3(l, p.y - sag * 0.3, p.z + cz),
    ];
  }

  /** Structured run: port → forward → droop → side comb → (tray) → back in. */
  function cableCurve(
    a: THREE.Vector3, b: THREE.Vector3, chanA: number, chanB: number, trayY: number,
    sameRack: boolean, laneA: number, laneB: number, exitA: ExitRef, exitB: ExitRef, runIx = 0,
    minBend = 0.02,
  ) {
    // lane → 2-D grid: x from lane % WRAP, z from the wrap count, both steps
    // larger than the widest jacket so neighbours cannot interpenetrate
    const tierA = (laneA % 7) * 0.012; // 7 is coprime with LANE_WRAP
    const tierB = (laneB % 7) * 0.012;
    const la = lane(chanA, laneA);
    const lb = lane(chanB, laneB);
    const pts = [a.clone(), ...legIn(a, la, tierA, minBend)];
    const prefixEnd = pts.length; // legIn(a)'s own joints are already fine — never fillet them
    if (sameRack) {
      const mid = (a.y + b.y) / 2;
      pts.push(new THREE.Vector3(chanA, mid, a.z + COMB_Z + tierA));
      pts.push(new THREE.Vector3(la, b.y - dropSag(b.x, lb) * 0.3, b.z + COMB_Z + tierB));
    } else {
      const ea = exitA.p, eb = exitB.p;
      // NG-PH3D 3a: floored like legIn's own oz/cz — a fixed 60mm drop is
      // too short a leg for routeCurve's fillet clamp to reach a stiff
      // cable's minimum bend radius (measured while chasing this
      // invariant's last violations).
      const preDrop = Math.max(0.06, minBend * 3);
      // NG-PH3D 3a: the P4 sweep below is two consecutive ~90° turns
      // sharing ONE edge (la → ea.x) — routeCurve's fillet clamps each to
      // 49% of it, so both together can claim at most ~98% of that edge's
      // OWN length no matter how much margin is requested. A short
      // lane-to-aperture gap (this rack's real ~100mm) is the actual
      // limit, not the fillet math — nudge the lane-side x outward for
      // this one sweep (not `la` itself, so the comb-channel merge point
      // used elsewhere is untouched) to give a stiff cable's two turns
      // enough shared edge to both reach their own minimum. top/rear widen
      // the lane-side x for this one sweep (not `la` itself, so the
      // comb-channel merge point used elsewhere is untouched). A side-exit
      // rack's mounting rail sits in that x band instead, and the cutout
      // it must thread through is only ~360mm tall — both a y nudge and an
      // x nudge each walked a sample into one of those (caught by P4's own
      // no-intersection invariant when tried; a y nudge also flips the
      // corner at the lane end into a much sharper reversal whenever
      // climbA is skipped, made worse not better). z is the one axis nudged
      // in this file, this test) — instead of sharing one z (their
      // midpoint) as top/rear's sweep does, pull each end toward the z its
      // OWN other neighbour already sits at: the lane end toward legIn's
      // own z, the exit end toward the exit aperture's own z. That's a
      // strictly gentler turn at both neighbouring joints too (each point
      // moves toward, not away from, its other neighbour) as well as a
      // longer shared edge.
      const laX = exitA.kind === 'side' ? la : la + Math.sign(ea.x - la || 1) * minBend * 1.2;
      const climbA = ea.y - 0.22;
      if (climbA > a.y + 0.03) pts.push(new THREE.Vector3(la, climbA, a.z + COMB_Z + tierA));
      const zBefore = a.z + COMB_Z + tierA;
      const preExitZA = (zBefore + ea.z) / 2;
      // NG-PH3D 3d: the two ends split this z-spread unevenly — the lane
      // end (deep inside the rack, nowhere near the cutout regardless of
      // z) can move freely, but the exit end sits right at the aperture,
      // so it's pulled almost all the way to ea.z specifically (not just
      // partway) since that's the exit's own already-verified-safe centre,
      // not merely "some direction away from the midpoint".
      const sideZFracLane = exitA.kind === 'side' ? 0.35 : 0;
      const sideZFracExit = exitA.kind === 'side' ? 0.85 : 0;
      pts.push(new THREE.Vector3(laX, ea.y - preDrop, preExitZA + (zBefore - preExitZA) * sideZFracLane));
      // NG-PH3D P4: sweep x from the lane to the exit point at a fixed z
      // first — going straight from (la, preExitZA) to ea.x/ea.clone() (x and
      // z changing together) could cut through the mounting rail sitting
      // between them for a rear/top-exit rack (same class of bug as the
      // PDU/tray-descent fixes above).
      pts.push(new THREE.Vector3(ea.x, ea.y - preDrop, preExitZA + (ea.z - preExitZA) * sideZFracExit));
      const sx = ((runIx % 5) - 2) * 0.016; // across the 210 mm aperture
      const sz = tzOf(runIx);
      if (exitA.kind === 'top') {
        pts.push(new THREE.Vector3(ea.x + sx, ea.y, ea.z + sz * 0.5));
        pts.push(new THREE.Vector3(ea.x + sx, trayY - 0.015, ea.z * 0.55 + sz));
      } else {
        // NG-PH3D 3a: no separate ea.clone() lead-in — it sat only 20mm from
        // descend's own first point, a segment too short for a stiff
        // cable's fillet clamp to reach its own minimum bend radius, and
        // redundant besides (descend already starts right at the exit).
        descend(pts, ea.x, ea.z + sz, ea.y, trayY - 0.02, 5, minBend * 2.8);
      }
      const tz = ((runIx % 9) - 4) * 0.0115; // spread the crossing across the tray
      pts.push(new THREE.Vector3(ea.x + (eb.x - ea.x) * 0.28, trayY + 0.012, tz * 0.6));
      pts.push(new THREE.Vector3((ea.x + eb.x) / 2, trayY + 0.012, tz));
      pts.push(new THREE.Vector3(eb.x - (eb.x - ea.x) * 0.28, trayY + 0.012, tz * 0.6));
      if (exitB.kind === 'top') {
        pts.push(new THREE.Vector3(eb.x + sx, trayY - 0.015, eb.z * 0.55 + sz));
        pts.push(new THREE.Vector3(eb.x + sx, eb.y, eb.z + sz * 0.5));
      } else {
        // NG-PH3D 3a: symmetric — no separate trailing eb.clone(), same
        // reason as the A-side entry above.
        descend(pts, eb.x, eb.z + sz, trayY - 0.02, eb.y, 5, minBend * 2.8);
      }
      // NG-PH3D P4: this used to jump straight from (eb.x, eb.z) to
      // (lb, avgZ) in one step — a diagonal that, for a rear/top-exit rack,
      // crosses exactly the x-z corner the mounting rail occupies (caught by
      // the no-intersection test). Sweep x to `lb` first at the
      // already-clear exit z, then move z to the average — same
      // one-axis-at-a-time fix as the PDU power-cord path above.
      pts.push(new THREE.Vector3(lb, eb.y - preDrop, eb.z + sz));
      pts.push(new THREE.Vector3(lb, eb.y - preDrop, (b.z + COMB_Z + tierB + eb.z) / 2));
      const climbB = eb.y - 0.22;
      if (climbB > b.y + 0.03) pts.push(new THREE.Vector3(lb, climbB, b.z + COMB_Z + tierB));
      // NG-PH3D 3d: this point stands in for legIn(b)'s own last point (the
      // reversed spread below drops the real one via slice(1)) so THIS end
      // of the run has a fillet-able corner symmetric with legIn(a)'s at
      // the top of this function — but unlike that one, it's exposed to
      // routeCurve's fillet (it's inside [prefixEnd, suffixStart]), and its
      // only neighbour on the far side is legIn(b)'s own 62%-of-gap point,
      // never moved. When a port already sits close to the lane in x, that
      // shared edge is too short for a stiff cable's own minimum bend
      // radius no matter how it's filleted (confirmed empirically chasing
      // this invariant's last violations, only reachable through this
      // slice's own side-exit scenario — top/rear's tested devices never
      // land close enough to the lane to trip it, so this floor is scoped
      // to `exitB.kind === 'side'` rather than risking their proven-fine
      // geometry). y looked free the same way the sweep's did, but this
      // point's OTHER neighbour (climbB, or the zmove point when climbB is
      // skipped) shares its exact y-formula-derived z already, and its
      // incoming edge is often a long straight drop — nudging y here
      // either shortens that drop into the same short-shared-edge problem
      // this is meant to fix, or, worse, reverses partway back up into it
      // (confirmed empirically). z moves this point off both neighbours'
      // near-identical z without touching either one's own position — but
      // this lane sits close enough to its own rail's x that the nudge can
      // land inside the rail's own z band, so it's run through the same
      // clamp the P4 fix above needed.
      const legInPt2X = b.x + (lb - b.x) * 0.62;
      const tailGap = Math.abs(lb - legInPt2X);
      const mergeMinTail = minBend * 2.4;
      const mergeExtraZ = exitB.kind === 'side' && tailGap < mergeMinTail
        ? Math.sqrt(mergeMinTail * mergeMinTail - tailGap * tailGap) : 0;
      const mergeZBase = b.z + COMB_Z + tierB;
      // the actual preceding point's own z — climbB's when it was pushed
      // (same formula as mergeZBase), otherwise the zmove point just
      // above, which can already sit clear on the OTHER side of a band
      // this shift would otherwise be needlessly clamped against.
      const mergePrevZ = climbB > b.y + 0.03 ? mergeZBase : (b.z + COMB_Z + tierB + eb.z) / 2;
      const mergeZ = clampZAwayFromRail(mergePrevZ, mergeZBase - mergeExtraZ, exitB.d);
      pts.push(new THREE.Vector3(lb, b.y - dropSag(b.x, lb) * 0.3, mergeZ));
    }
    const suffixStart = pts.length - 1; // index of the last "middle" point, before legIn(b)'s own joints
    pts.push(...legIn(b, lb, tierB, minBend).reverse().slice(1));
    pts.push(b.clone());
    // NG-PH3D 3a: 5 Chaikin passes for the whole route (unchanged from the
    // original tuning — legIn's own S-curve and the sameRack "full-height"
    // path are already comfortably within every verified minBendM this
    // way). The cross-rack "middle" section (tray-crossing + exit-aperture
    // jog) is a different story: it has NO term that scales with the
    // media's own stiffness, so a wide-enough cable eventually exceeds
    // whatever fixed radius that fixed-angle geometry happens to produce —
    // more Chaikin passes don't help there either (confirmed empirically:
    // 8 passes reproduced the same violating radius as 5, to the tenth of
    // a millimetre — Chaikin's corner-cutting converges to an angle-fixed
    // limit shape). routeCurve's true circular fillet (guaranteed radius
    // by construction, see its own + filletCorner's doc) is scoped to only
    // that middle span — legIn's own already-fine joints at both ends are
    // explicitly excluded so this doesn't disturb geometry that was never
    // the problem.
    if (!sameRack) return routeCurve(pts, minBend * 1.45, 5, prefixEnd, suffixStart);
    return smoothCurve(pts, 5);
  }

  /** NG-PH3D 3e: same run as `cableCurve`'s cross-rack A-side (port → lane →
   *  climb → exit aperture → down into the tray) for a link whose OTHER end
   *  isn't in this scene — a different site, or a wireless radio on a tower
   *  with no rack of its own. Surya: cables like this still have to "tetap
   *  melewati cable tray di atas dan harus natural serta rapih ... boleh
   *  terlihat terpotong ... tapi harus tetap rapih" — so it doesn't stop at
   *  the exit aperture, it continues a short way along the tray (toward
   *  whichever end of the centred row is nearer) and stops there, where
   *  `addCable`'s stub cap reads as "this run continues past the tray edge"
   *  instead of a cable dangling in mid-air. */
  function externalCableCurve(
    a: THREE.Vector3, chanA: number, trayY: number, laneA: number, exitA: ExitRef, rackA: RackEntry, runIx = 0,
    minBend = 0.02,
  ) {
    const tierA = (laneA % 7) * 0.012;
    const la = lane(chanA, laneA);
    const pts = [a.clone(), ...legIn(a, la, tierA, minBend)];
    // NG-PH3D 3e/D2: legIn's own last leg (its 62%-of-span point -> la) is a
    // fixed-mm z hop (oz/cz — see legIn's own comment — never scaled by
    // span) that a SHORT span (a device port close to its own rack's lane,
    // e.g. a side-exit rack's channel sitting near the rail) leaves
    // disproportionately tight: measured 45.8mm on a repro, under cat6a's
    // own 51mm minimum, entirely inside legIn's exempted "never fillet"
    // stretch. Pulling this point's own x earlier (40% of span instead of
    // 62%) trades length from the segment before it (never the tightest
    // one measured) to the one after — the one actually failing — without
    // touching legIn() itself or any of its other callers.
    pts[3] = pts[3]!.clone();
    pts[3].x = a.x + (la - a.x) * 0.4;
    const prefixEnd = pts.length - 1; // let routeCurve fillet the la corner too — see comment below
    const ea = exitA.p;
    const preDrop = Math.max(0.06, minBend * 3);
    const laX = exitA.kind === 'side' ? la : la + Math.sign(ea.x - la || 1) * minBend * 1.2;
    const climbA = ea.y - 0.22;
    if (climbA > a.y + 0.03) pts.push(new THREE.Vector3(la, climbA, a.z + COMB_Z + tierA));
    const zBefore = a.z + COMB_Z + tierA;
    const preExitZA = (zBefore + ea.z) / 2;
    const sideZFracLane = 0.35;
    const sideZFracExit = 0.85;
    // NG-PH3D 3e/D2: a top/rear exit's aperture sits directly above whatever
    // device occupies the rack's topmost U slots (confirmed via repro: the
    // exit x/z the sweep below lands on falls squarely inside a full-width
    // device's own footprint, e.g. a switch at U41 right under an apc's top
    // exit) — this preDrop dip is still below ea.y, so pulling z toward the
    // aperture here cuts straight through that device's chassis. `side`'s
    // exit sits well below the rack ceiling and has its own tested
    // rail-clearance fractions (NG-PH3D 3d, graduated pull below); top/rear
    // hold z at legIn's own comb z (already clear of every device's front
    // face, same margin legIn itself relies on) through this whole dip and
    // only pull toward ea.z once y has climbed past ea.y in the branch
    // below, where no device ever reaches (ea.y sits above the rack's own
    // usable U range by construction).
    const z4 = exitA.kind === 'side' ? preExitZA + (zBefore - preExitZA) * sideZFracLane : zBefore;
    const z5 = exitA.kind === 'side' ? preExitZA + (ea.z - preExitZA) * sideZFracExit : zBefore;
    // top/rear: laX's small offset from `la` only ever existed to buy the
    // side-exit fillet a longer shared edge (comment above) — with z held
    // flat through this dip, the laX->ea.x leg is short on every axis and
    // clamps its own corner instead. One point straight to ea.x removes
    // that corner rather than widening it.
    if (exitA.kind === 'side') pts.push(new THREE.Vector3(laX, ea.y - preDrop, z4));
    pts.push(new THREE.Vector3(ea.x, ea.y - preDrop, z5));
    const sx = ((runIx % 5) - 2) * 0.016;
    const sz = tzOf(runIx);
    if (exitA.kind === 'top') {
      pts.push(new THREE.Vector3(ea.x + sx, ea.y, ea.z + sz * 0.5));
      pts.push(new THREE.Vector3(ea.x + sx, trayY - 0.015, ea.z * 0.55 + sz));
    } else {
      descend(pts, ea.x, ea.z + sz, ea.y, trayY - 0.02, 5, minBend * 2.8);
    }
    // continue a short way into the tray, toward the nearer end of the row
    // (the row is centred on x=0 — see buildScene's rowOffset), then stop.
    // NG-PH3D 3e: the elbow this makes (vertical tray-entry -> horizontal
    // tray run) is exactly the two-turns-one-shared-edge problem cableCurve's
    // own comment already names for the lane->aperture sweep — routeCurve's
    // fillet clamps each turn to 49% of its adjacent edge, so `half` (below)
    // has to clear minBend*1.45/0.49 on its own for a stiff cable (cat6a,
    // 51mm) to hit its own minimum radius here; the first new edge is kept
    // purely horizontal (off the actual landing point, not a fixed height)
    // so its full length counts, not just its x-component.
    const land = pts[pts.length - 1]!;
    const dir = land.x < 0 ? -1 : 1;
    // NG-PH3D 3e/D2: `half` also has to clear whatever rack A itself is
    // wide, or the stub re-enters its OWN chassis/rails — caught by the
    // no-intersection test. This used to guess the widest RACK_SPECS enclosure
    // (600mm) rather than measure rackA itself; derive it instead from the
    // real distance between the landing point and this bay's own far edge
    // (rackA.x ± rackA.w/2, whichever side `dir` is heading), plus an
    // explicit clearance margin past that edge. z is held constant (no pull
    // toward the tray centreline) so both new edges stay a clean `half`
    // long, well past what the bend-radius floor below alone would need.
    const bayEdgeX = rackA.x + dir * (rackA.w / 2);
    const marginPastEdge = 0.05;
    const clearHalf = dir * (bayEdgeX - land.x) + marginPastEdge;
    const bendHalf = minBend * 3.3; // per the fillet-clamp floor above (minBend*1.45/0.49)
    const half = Math.max(bendHalf, clearHalf);
    pts.push(new THREE.Vector3(land.x + dir * half, land.y, land.z));
    pts.push(new THREE.Vector3(land.x + dir * half * 2, trayY + 0.012, land.z));
    return routeCurve(pts, minBend * 1.45, 5, prefixEnd);
  }

  /** `stubEnd`: the b-end (t=0.996) has no real device to plug into (NG-PH3D
   *  3e external cable) — cap it with a dark bushing instead of a connector
   *  boot, so it reads as "passes into a fitting and continues", not a plug
   *  floating in the tray. */
  function addCable(curve: THREE.Curve<THREE.Vector3>, mediaKey: string, meta: CableMeta, stubEnd = false) {
    const spec = MEDIA[mediaKey]!;
    const geo = track(new THREE.TubeGeometry(curve as THREE.Curve<THREE.Vector3> & { getPointAt: (t: number) => THREE.Vector3 }, 140, spec.r, 8, false));
    const pair = mats.media[mediaKey]!;
    const mesh = new THREE.Mesh(geo, pair.jk);
    mesh.name = 'cable-' + mediaKey + '-' + meta.name;
    mesh.userData.link = meta;
    root.add(mesh);
    if (stubEnd) {
      const tan = curve.getTangentAt(1).normalize();
      const q = new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 1, 0), tan);
      const cap = new THREE.Mesh(track(new THREE.CylinderGeometry(spec.r * 1.9, spec.r * 1.5, 0.026, 10)), mats.plugShell);
      cap.name = 'cable-stub-cap-' + meta.name;
      cap.quaternion.copy(q);
      cap.position.copy(curve.getPointAt(1)).addScaledVector(tan, -0.01);
      root.add(cap);
    }
    // connector: plug body + tapered strain relief, shaped per family
    const fam = ['cat6a', 'cat6a_xc', 'cat6a_oob'].includes(mediaKey) ? 'rj45'
      : ['dac', 'aoc'].includes(mediaKey) ? 'sfp'
      : ['pwrA', 'pwrB'].includes(mediaKey) ? 'iec' : 'lc';
    for (const t of [0.004, 0.996]) {
      if (stubEnd && t > 0.5) continue; // capped above, no connector boot at the open end
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
      // NG-PH3D 3a: rj45/lc are the two connector families with a Blender-
      // authored, dimension-verified asset (tools/blender/build_assets.py —
      // §2.a RJ45/LC, docs/design/24-DEVICE-PHYSICAL-SPEC.md). When that
      // geometry has been loaded (loadBootAssets(), a host-component
      // concern — buildScene() itself stays synchronous/network-free) an
      // instance transform is queued instead of building the old procedural
      // body+boot meshes, so every cable end of that family becomes one
      // InstancedMesh draw call instead of N per-cable meshes. Falls back to
      // the original procedural shapes (unchanged below) whenever the asset
      // isn't cached yet — first paint, or any test that never loads it.
      const bootGeo = (fam === 'rj45' || fam === 'lc') ? getBootGeometry(fam) : undefined;
      if (bootGeo) {
        const tip = curve.getPointAt(t < 0.5 ? 0 : 1);
        bootInstances[fam as BootFamily].push({ pos: tip, quat: q.clone(), color: pair.bt.color });
        continue;
      }
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
  // NG-PH3D P41: N real bays laid left-to-right (was a fixed A/B pair) —
  // each bay's centre-x is a running cursor over the row's real widths, then
  // the whole row is re-centred on x=0 so a 2-bay call produces the exact
  // same xA/xB the old fixed formula did (verified: for equal widths this
  // reduces to -(w+gap)/2 / (w+gap)/2).
  const bays = opts.racks;
  const specs = bays.map((b) => deriveSpec(b.enclosure, b.ruHeight));
  const gap = 0.1;
  let cursor = 0;
  const rawX = specs.map((s) => {
    const x = cursor + s.w / 2000;
    cursor += s.w / 1000 + gap;
    return x;
  });
  const rowWidth = cursor - gap;
  const rowOffset = rowWidth / 2;
  const xOf = new Map<string, number>();
  bays.forEach((b, i) => {
    const x = rawX[i]! - rowOffset;
    xOf.set(b.key, x);
    root.add(buildRack(b.key, b.enclosure, x, b.ruHeight));
  });
  for (const b of bays) {
    for (const def of b.devices) registry.racks[b.key]!.group.add(buildDevice(def, b.key));
  }

  root.updateMatrixWorld(true); // port anchors are read in world space below

  const trayY = Math.max(...specs.map((s) => s.h)) / 1000 + 0.11;
  const firstX = xOf.get(bays[0]!.key)!;
  const lastSpec = specs[specs.length - 1]!;
  const lastX = xOf.get(bays[bays.length - 1]!.key)!;
  // Same 0.1m overhang past the row on both ends (mirrors the left edge
  // below) — used to derive from the decorative CPI cabinet's position, but
  // that prop never belonged in the scene (Slice G bug 1: it drew a second,
  // user-never-added rack next to whatever the user actually built).
  root.add(buildTray(firstX - specs[0]!.w / 2000 - 0.1, lastX + lastSpec.w / 2000 + 0.1, trayY));

  // vertical manager channel per bay: between the 19" rail and the side panel
  const chanOf = new Map<string, number>();
  bays.forEach((b, i) => chanOf.set(b.key, xOf.get(b.key)! + specs[i]!.w / 2000 - 0.037));

  // Sort every run by the height of its endpoints first: the comb then reads as
  // a parallel fan (reference photos) instead of a cat's cradle of crossings.
  const rackOf = (id: string) => registry.devices[id]?.rackKey;
  const laneOrder = new Map<string, number>();
  for (const b of bays) {
    const key = b.key;
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
  const laneCount: Record<string, number> = {};
  for (const b of bays) laneCount[b.key] = 0;
  const nextLane = (key: string) => {
    const n = laneCount[key] ?? 0;
    laneCount[key] = n + 1;
    return n;
  };
  const bundleMedia: Record<string, string[]> = {};
  for (const b of bays) bundleMedia[b.key] = [];
  let crossIx = 0;
  for (const [i, l] of opts.links.entries()) {
    const aPresent = !!registry.devices[l.a[0]];
    const bPresent = !!registry.devices[l.b[0]];
    if (!aPresent && !bPresent) continue; // neither endpoint renders in this scene
    if (!aPresent || !bPresent) {
      // NG-PH3D 3e: exactly one endpoint is in this scene — plantAdapter.ts
      // now keeps a link like this (different site, or a wireless radio on
      // a tower with no rack of its own) instead of dropping it. Route the
      // real half up into the tray like any cross-rack run and stop there.
      const end = aPresent ? l.a : l.b;
      const p = worldPort(end[0], end[1]);
      if (!p) continue;
      const key = rackOf(end[0])!;
      const ln = laneOrder.get(key + ':' + i) ?? nextLane(key);
      const curve = externalCableCurve(p, chanOf.get(key)!, trayY - 0.03, ln,
        exitPoint(registry.racks[key]!), registry.racks[key]!, crossIx++, MEDIA[l.m]?.minBendM ?? 0.02);
      bundleMedia[key]?.push(l.m);
      const len = addCable(curve, l.m, {
        name: end[0] + ':' + end[1] + ' → luar scene',
        devs: [end[0]], live: l.live, media: l.m,
      }, true);
      const sprite = makeLabel(
        MEDIA[l.m]!.label.split(' · ')[0] + '  ' + stockLength(len) + ' m',
        curve.getPointAt(0.5).clone().add(new THREE.Vector3(0, 0.06, 0)),
      );
      sprite.userData.devs = [end[0]];
      sprite.userData.keyRun = true; // external runs are always worth naming
      continue;
    }
    const a = worldPort(l.a[0], l.a[1]);
    const b = worldPort(l.b[0], l.b[1]);
    if (!a || !b) continue;
    const ka = rackOf(l.a[0])!, kb = rackOf(l.b[0])!;
    const same = ka === kb;
    const cA = chanOf.get(ka)!;
    const cB = chanOf.get(kb)!;
    const kindOf = (id: string) => registry.devices[id]!.def.kind;
    const nearBy = Math.abs(a.y - b.y) <= U * 3.2;
    const panelHop = same && nearBy
      && (['patch', 'odf'].includes(kindOf(l.a[0])) || ['patch', 'odf'].includes(kindOf(l.b[0])));
    let curve: THREE.Curve<THREE.Vector3>;
    if (panelHop) {
      curve = jumperCurve(a, b, MEDIA[l.m]?.minBendM ?? 0.02);
    } else {
      const lnA = laneOrder.get(ka + ':' + i) ?? nextLane(ka);
      const lnB = same ? lnA : (laneOrder.get(kb + ':' + i) ?? nextLane(kb));
      curve = cableCurve(a, b, cA, cB, trayY - 0.03, same, lnA, lnB,
        exitPoint(registry.racks[ka]!), exitPoint(registry.racks[kb]!), same ? 0 : crossIx++,
        MEDIA[l.m]?.minBendM ?? 0.02);
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

  for (const b of bays) buildBundle(registry.racks[b.key]!, bundleMedia[b.key]!);

  // power cords: PDU → each server rear, alternating feed A (black) / feed B (red)
  for (const b of bays) {
    const rackKey = b.key;
    const rack = registry.racks[rackKey]!;
    const powered = b.devices.filter((d) => d.kind === 'server' || d.kind === 'switch');
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
      // NG-PH3D P4: the direct diagonal this used to take (rearZ straight to
      // outletZ while x also swept toward the PDU) let the Catmull-Rom curve
      // bulge into the accessory-channel/mounting-rail/PDU-body hazard band
      // that all share this rear corner — caught by the no-intersection
      // test. Route one axis at a time instead: drop to a z clear of every
      // hazard while x is still far from them (segment 1), sweep x to the
      // inlet while at that safe z (segment 2 — `inletX` is analytically
      // clear of the rail/channel/PDU x-ranges for every RACK_SPECS width),
      // then rise straight into the inlet at that already-safe x (segment 3).
      const sweepZ = -rack.d / 2 + 0.04;
      const pts = [
        new THREE.Vector3(rack.x + 0.09, y, rearZ),
        new THREE.Vector3(rack.x + 0.09, y - 0.01, sweepZ),
        new THREE.Vector3(inletX, y, sweepZ),
        new THREE.Vector3(inletX, y + 0.03, outletZ + 0.022),
        new THREE.Vector3(inletX, y + 0.038, outletZ + 0.008),
      ];
      addCable(new THREE.CatmullRomCurve3(pts, false, 'catmullrom', 0.4), feed,
        { name: 'power-' + def.id, devs: [def.id], live: false, media: feed });
    });
  }

  // NG-PH3D 3a: flush the queued rj45/lc boot instances — one InstancedMesh
  // draw call per family instead of the several per-cable-end meshes the
  // procedural fallback above builds. White base + per-instance colour
  // reproduces the doc's housing-colour convention (SFF-8432 Note 13 /
  // TIA-598-C) without a material per media.
  const ONE = new THREE.Vector3(1, 1, 1);
  for (const fam of ['rj45', 'lc'] as BootFamily[]) {
    const list = bootInstances[fam];
    if (!list.length) continue;
    const geo = getBootGeometry(fam)!;
    const bootMat = track(mat('boot-instanced-' + fam, 0xffffff, { roughness: 0.45, metalness: 0.12 }));
    const im = new THREE.InstancedMesh(geo, bootMat, list.length);
    im.name = 'boot-instanced-' + fam;
    const m4 = new THREE.Matrix4();
    list.forEach((it, i) => {
      m4.compose(it.pos, it.quat, ONE);
      im.setMatrixAt(i, m4);
      im.setColorAt(i, it.color);
    });
    im.instanceMatrix.needsUpdate = true;
    if (im.instanceColor) im.instanceColor.needsUpdate = true;
    root.add(im);
  }

  // NG-PH3D 3b: flush the queued SFP/QSFP cage instances the same way — one
  // InstancedMesh per family for the whole scene. Uniform EMI-shield colour
  // (no per-instance tint, unlike the media-coloured boots above), so no
  // setColorAt. `userData.portMap[instanceId]` lets the click-to-patch
  // raycaster (Rack3DElevationPanel) resolve a hit back to a real
  // dev/port pair even though there's no per-port mesh to carry it.
  // QA 2026-09-01 (Slice G bug 2): was roughness 0.4/metalness 0.55 — glossy
  // enough that a packed SFP/QSFP row caught the key light as one continuous
  // hot (near-white) band instead of reading as individual metal cages.
  const cageMat = track(mat('cage-instanced', 0x6a6d72, { roughness: 0.62, metalness: 0.35 }));
  for (const fam of ['cage-sfp', 'cage-qsfp'] as CageFamily[]) {
    const list = cageInstances[fam];
    if (!list.length) continue;
    const geo = getBootGeometry(fam)!;
    const im = new THREE.InstancedMesh(geo, cageMat, list.length);
    im.name = fam + '-instanced';
    const m4 = new THREE.Matrix4();
    list.forEach((it, i) => {
      m4.compose(it.pos, CAGE_QUAT, ONE);
      im.setMatrixAt(i, m4);
    });
    im.instanceMatrix.needsUpdate = true;
    im.userData.portMap = list.map((it) => ({ dev: it.dev, port: it.port }));
    root.add(im);
    registry.fineDetail.push(im);
  }

  // Sesi LOD tuning: flush the RJ45 cage/notch/keystone/LED + LC bore/
  // adapter queues (portDetailInstances, declared above) the same way — one
  // InstancedMesh per part, unit geometry with each instance's real box/
  // cylinder size baked into that instance's own scale (devices differ in
  // faceplate height by U count, so a shared fixed-size geometry like the
  // SFP/QSFP cages above can't be reused as-is). portMap on every one of
  // them, not just the ones most likely to be raycast-hit first (keystone
  // sits frontmost in front of the cage, matching pre-instancing z-order) —
  // cheap to carry, and keeps every layer resolvable if the visual stack
  // ever reorders.
  const UNIT_BOX = track(new THREE.BoxGeometry(1, 1, 1));
  const UNIT_CYL = track(new THREE.CylinderGeometry(1, 1, 1, 10));
  const portDetailMat: Record<keyof typeof portDetailInstances, THREE.Material> = {
    'rj45-cage': mats.port, 'rj45-notch': mats.port, keystone: mats.bezel,
    'port-led': mats.ledOn, 'lc-bore': mats.port, 'lc-adapter': mats.bezel,
  };
  for (const key of Object.keys(portDetailInstances) as (keyof typeof portDetailInstances)[]) {
    const list = portDetailInstances[key];
    if (!list.length) continue;
    const geo = key === 'lc-bore' ? UNIT_CYL : UNIT_BOX;
    const im = new THREE.InstancedMesh(geo, portDetailMat[key], list.length);
    im.name = key + '-instanced';
    const m4 = new THREE.Matrix4();
    list.forEach((it, i) => {
      m4.compose(it.pos, it.quat, it.scale);
      im.setMatrixAt(i, m4);
    });
    im.instanceMatrix.needsUpdate = true;
    im.userData.portMap = list.map((it) => ({ dev: it.dev, port: it.port }));
    root.add(im);
    registry.fineDetail.push(im);
  }

  // QA 2026-09-01: room ambient raised a touch so the near-black frame/rails
  // pick up some fill too — the faceplate-vs-rack imbalance wasn't just the
  // faceplate being too hot, the frame was also under-lit relative to it.
  const amb = new THREE.HemisphereLight(0xdce6ff, 0x1a1a18, 1.45);
  amb.name = 'room-ambient';
  root.add(amb);
  // Slice G bug 2: HemisphereLight still scales with a surface normal's
  // vertical component — a side panel or a swung-open door (normal roughly
  // horizontal) sits far enough from every DirectionalLight below that it
  // read as flat black regardless of how much its own material was lifted.
  // A small orientation-independent AmbientLight is the one light every
  // face picks up no matter which way it's turned.
  const flat = new THREE.AmbientLight(0xd8d4cc, 0.95);
  flat.name = 'omni-fill';
  root.add(flat);
  // was 2.1 — paired with the chassis lift() above, this alone was enough to
  // clip the faceplate toward white next to the frame's own near-black
  // albedo (which no amount of this light could brighten in kind).
  const fill = new THREE.DirectionalLight(0xfff6ec, 1.35);
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
  rim.position.set(chanOf.get(bays[0]!.key)! - 0.9, trayY - 0.7, 1.5);
  root.add(rim);

  return { root, registry, trayY, rowWidthM: rowWidth };
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

/** NG-PH3D 3b LOD threshold: the ortho camera's half-height (world metres —
 *  `spanFor()` in Rack3DElevationPanel) beyond which a full rack no longer
 *  needs per-port geometry. The panel's own default, unzoomed span for a
 *  ~2m/42U rack is ~1.6m (the whole rack already fits); 1.2m sits a bit
 *  below that so the out-of-the-box view is the simplified one, and zooming
 *  in (bigger `scale` in `spanFor`, smaller span) crosses it and reveals
 *  port-level detail. Not tuned against a device's own size — this is a
 *  scene-wide switch, not per-object distance culling (the camera sits at a
 *  fixed physical distance in this app; only its ortho frustum width
 *  changes with "zoom", so a real per-object THREE.LOD's distance-to-camera
 *  metric wouldn't track the user's actual zoom level). */
export const LOD_FAR_SPAN_M = 1.2;

/** Hide (`spanHeightM` beyond `LOD_FAR_SPAN_M`) or show every per-port fine
 *  detail object queued into `registry.fineDetail` during buildScene — the
 *  cage/keystone/notch/bore/LED meshes and the SFP/QSFP InstancedMeshes.
 *  Chassis and faceplate stay visible at every zoom; the faceplate texture
 *  already paints the port pattern at low res (faceTexture()'s decorative
 *  outlines), so dropping the 3D detail on top of it reads as one
 *  simplification, not a pop-in. */
export function applyLod(registry: Registry, spanHeightM: number) {
  const detailed = spanHeightM < LOD_FAR_SPAN_M;
  for (const o of registry.fineDetail) o.visible = detailed;
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
