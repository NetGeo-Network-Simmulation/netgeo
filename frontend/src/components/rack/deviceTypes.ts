/**
 * deviceTypes — parametric 2D SVG faceplate data for common rack devices.
 *
 * Data source: NetBox devicetype-library (CC0). Specs are faithful to real
 * hardware port counts/types per model; chassis hex values from vendor marketing
 * materials + measured screenshots.
 *
 * N4 (NG-DL-02 step 1+2 done): a node created from the device library carries
 * `device_type_id`, and `/api/device-types` now passes that entry's `ports`/
 * `physical`/`vendor` through from the pack JSON. `resolveDeviceType()` uses
 * that real data when present — DEVICE_TYPES below stays as the fallback
 * heuristic + style layer (brand colors) for nodes that don't have it. Step 3
 * (per-model 3D art) is still future work.
 */

import type { Nos, NodeKind, Interface } from '@/api/types';
import type { DeviceType as CatalogEntry } from '@/api/client';

// ─── Port / Zone types ────────────────────────────────────────────────────────

export type PortType =
  | 'rj45'
  | 'sfp'
  | 'sfp+'
  | 'sfp28'
  | 'qsfp28'
  | 'pon'
  | 'console-rj45'
  | 'console-usb'
  | 'mgmt-rj45'
  | 'usb'
  | 'drive-sff'
  | 'drive-lff'
  // RJ-11/RJ-14 (6P) analog voice/FXS jack — physically a 6-position modular
  // connector, NOT an 8-position RJ45 (research/3d-device-specs-onu.md
  // §gap-1, keputusan Surya 2026-09-06): must render + map distinctly, never
  // collapse into 'rj45'.
  | 'fxs';

export interface PortSpec {
  type: PortType;
  count: number;
  label?: string; // e.g. 'WAN', 'DMZ' — rendered as tiny text above the port
  poe?: boolean;
}

export interface PortZone {
  ports: PortSpec[];
  rows: 1 | 2;
  align: 'left' | 'right' | 'fill';
  widthFraction?: number; // 0–1; overrides auto-sizing when set
}

export interface RearBlock {
  type: 'psu-slot' | 'fan-tray' | 'ground-lug' | 'vent-grille' | 'iec-inlet' | 'port-zone' | 'led-group';
  count?: number; // iec-inlet: number of inlets rendered (default 1)
  portZone?: PortZone; // only when type === 'port-zone'
  leds?: Led[]; // only when type === 'led-group'
}

export interface Led {
  label: string;
  color: 'green' | 'amber' | 'blue' | 'red' | 'white' | 'purple' | 'yellow';
  position: 'left' | 'right' | 'above';
}

export interface DeviceType {
  slug: string;
  manufacturer: string;
  model: string;
  nos?: Nos;
  uHeight: number;
  isFullDepth?: boolean;
  /** Real chassis body width/depth in mm, ONLY for models with V or V(2nd)
   *  status in docs/design/24-DEVICE-PHYSICAL-SPEC.md §8.1. Omit when the
   *  doc's status is UNVERIFIED/low-confidence (e.g. arista-7050cx3-32s'
   *  width) — the 3D builder then falls back to the generic default body
   *  size rather than drawing a guessed number as real. */
  chassisMm?: { widthMm: number; depthMm: number };
  front: {
    portZones: PortZone[];
    leds: Led[];
    hasLcd?: boolean;
    lcdPos?: 'left' | 'center';
    isServerBezel?: boolean;
  };
  rear: {
    blocks: RearBlock[];
  };
  brand: {
    accent: string;   // accent stripe / SFP cage tint
    chassis: string;  // body fill colour
    label: string;    // brand name
    badge: 'stripe' | 'corner';
  };
}

// ─── Seed device library ──────────────────────────────────────────────────────

export const DEVICE_TYPES: DeviceType[] = [
  // ── MikroTik CRS317-1G-16S+RM ──────────────────────────────────────────────
  {
    slug: 'mikrotik-crs317-1g-16splus-rm',
    manufacturer: 'MikroTik',
    model: 'CRS317-1G-16S+RM',
    nos: 'routeros',
    uHeight: 1,
    // §8.1 V (mikrotik.com product page, dibuka langsung): half-depth chassis.
    chassisMm: { widthMm: 443, depthMm: 224 },
    front: {
      portZones: [
        {
          ports: [{ type: 'console-rj45', count: 1 }],
          rows: 1,
          align: 'left',
          widthFraction: 0.06,
        },
        {
          ports: [{ type: 'sfp+', count: 16 }],
          rows: 1,
          align: 'fill',
        },
        {
          ports: [{ type: 'mgmt-rj45', count: 1 }],
          rows: 1,
          align: 'right',
          widthFraction: 0.07,
        },
      ],
      leds: [
        { label: 'PWR', color: 'green', position: 'left' },
        { label: 'ACT', color: 'green', position: 'left' },
      ],
    },
    rear: {
      // docs/design/24-DEVICE-PHYSICAL-SPEC.md §9 (V, mikrotik.com opened directly):
      // passive cooling (no fan tray), no modular PSU — 2x IEC C14 redundant inlets
      // are the power slots themselves.
      blocks: [
        { type: 'iec-inlet', count: 2 },
      ],
    },
    brand: { accent: '#E4002B', chassis: '#EFEDE6', label: 'MikroTik', badge: 'stripe' },
  },

  // ── MikroTik CRS328-24P-4S+RM ──────────────────────────────────────────────
  {
    slug: 'mikrotik-crs328-24p-4splus-rm',
    manufacturer: 'MikroTik',
    model: 'CRS328-24P-4S+RM',
    nos: 'routeros',
    uHeight: 1,
    // §8.1 V (mikrotik.com product page, dibuka langsung): deeper than CRS317
    // because of the internal 500W PSU.
    chassisMm: { widthMm: 443, depthMm: 300 },
    front: {
      portZones: [
        {
          ports: [{ type: 'console-rj45', count: 1 }],
          rows: 1,
          align: 'left',
          widthFraction: 0.06,
        },
        {
          ports: [{ type: 'rj45', count: 24, poe: true }],
          rows: 1,
          align: 'fill',
        },
        {
          ports: [{ type: 'sfp+', count: 4 }],
          rows: 1,
          align: 'right',
          widthFraction: 0.14,
        },
      ],
      // docs/design/24-DEVICE-PHYSICAL-SPEC.md §10 (V, mikrotik.com manual opened
      // directly): manual lists PWR + FAN FAULT as the system LEDs, not a
      // generic ACT LED — per-port link/PoE LEDs are separate (port-level).
      leds: [
        { label: 'PWR', color: 'green', position: 'left' },
        { label: 'FAN FLT', color: 'amber', position: 'left' },
      ],
    },
    rear: {
      blocks: [
        { type: 'psu-slot', count: 1 },
        { type: 'fan-tray', count: 1 },
        { type: 'iec-inlet' },
      ],
    },
    brand: { accent: '#E4002B', chassis: '#EFEDE6', label: 'MikroTik', badge: 'stripe' },
  },

  // ── Cisco Catalyst 9300-48P ────────────────────────────────────────────────
  {
    slug: 'cisco-c9300-48p',
    manufacturer: 'Cisco',
    model: 'Catalyst 9300-48P',
    nos: 'ios',
    uHeight: 1,
    // §8.1 V(2nd) (cisco.com tech-specs + router-switch.com, cross-checked):
    // deep chassis — separate modular uplink bay adds length vs the 9500.
    chassisMm: { widthMm: 445, depthMm: 526 },
    front: {
      portZones: [
        {
          ports: [
            { type: 'console-usb', count: 1 },
            { type: 'console-rj45', count: 1 },
          ],
          rows: 1,
          align: 'left',
          widthFraction: 0.08,
        },
        {
          ports: [{ type: 'rj45', count: 48, poe: true }],
          rows: 2,
          align: 'fill',
        },
        {
          ports: [{ type: 'sfp+', count: 4, label: 'NM' }],
          rows: 1,
          align: 'right',
          widthFraction: 0.18,
        },
      ],
      leds: [
        { label: 'SYST', color: 'green', position: 'left' },
        { label: 'STAT', color: 'green', position: 'left' },
      ],
    },
    rear: {
      blocks: [
        { type: 'vent-grille' },
        { type: 'psu-slot', count: 2 },
        { type: 'fan-tray', count: 3 },
        { type: 'ground-lug' },
      ],
    },
    brand: { accent: '#1BA0D7', chassis: '#1A1A18', label: 'Cisco', badge: 'stripe' },
  },

  // ── Cisco Catalyst 9500-48Y ────────────────────────────────────────────────
  {
    slug: 'cisco-c9500-48y',
    manufacturer: 'Cisco',
    model: 'Catalyst 9500-48Y',
    nos: 'ios',
    uHeight: 1,
    // §8.1 V(2nd) (router-switch.com/serversupply.com, cross-checked):
    // shallower than the 9300 — all ports fixed at front, no uplink bay.
    chassisMm: { widthMm: 445, depthMm: 457 },
    front: {
      portZones: [
        {
          ports: [
            { type: 'mgmt-rj45', count: 1 },
            { type: 'console-rj45', count: 1 },
          ],
          rows: 1,
          align: 'left',
          widthFraction: 0.09,
        },
        {
          ports: [{ type: 'sfp28', count: 48 }],
          rows: 1,
          align: 'fill',
        },
        {
          ports: [{ type: 'qsfp28', count: 4, label: '100G' }],
          rows: 1,
          align: 'right',
          widthFraction: 0.13,
        },
      ],
      leds: [
        { label: 'SYST', color: 'green', position: 'left' },
        { label: 'STAT', color: 'green', position: 'left' },
      ],
    },
    rear: {
      blocks: [
        { type: 'psu-slot', count: 2 },
        { type: 'fan-tray', count: 4 },
        { type: 'ground-lug' },
      ],
    },
    brand: { accent: '#1BA0D7', chassis: '#1A1A18', label: 'Cisco', badge: 'stripe' },
  },

  // ── Juniper QFX5120-48Y ───────────────────────────────────────────────────
  {
    slug: 'juniper-qfx5120-48y',
    manufacturer: 'Juniper',
    model: 'QFX5120-48Y',
    nos: 'junos',
    uHeight: 1,
    // §8.1 V (apps.juniper.net/hct official spec tool, dibuka langsung) —
    // highest-quality source in the table; body-without-FRU figure used.
    chassisMm: { widthMm: 440.9, depthMm: 520.2 },
    front: {
      portZones: [
        {
          ports: [{ type: 'sfp28', count: 48 }],
          rows: 1,
          align: 'fill',
        },
        {
          ports: [{ type: 'qsfp28', count: 8, label: '100G' }],
          rows: 1,
          align: 'right',
          widthFraction: 0.18,
        },
      ],
      // docs/design/24-DEVICE-PHYSICAL-SPEC.md §10 (V, juniper.net documentation
      // opened directly): the 4 chassis LEDs (ALM/SYS/MST/ID) live on the REAR
      // panel next to the mgmt ports, not the front — see rear.blocks below.
      leds: [],
    },
    rear: {
      // §9 rear order (V, juniper.net): mgmt port + LED cluster → fan module → PSU.
      blocks: [
        {
          type: 'port-zone',
          portZone: {
            ports: [{ type: 'mgmt-rj45', count: 2 }],
            rows: 1,
            align: 'left',
          },
        },
        {
          type: 'led-group',
          leds: [
            { label: 'ALM', color: 'red', position: 'left' },
            { label: 'SYS', color: 'green', position: 'left' },
            { label: 'MST', color: 'green', position: 'left' },
            { label: 'ID', color: 'blue', position: 'left' },
          ],
        },
        { type: 'fan-tray', count: 3 },
        { type: 'psu-slot', count: 2 },
      ],
    },
    brand: { accent: '#84B135', chassis: '#1A1A18', label: 'Juniper', badge: 'stripe' },
  },

  // ── Arista 7050CX3-32S ────────────────────────────────────────────────────
  {
    slug: 'arista-7050cx3-32s',
    manufacturer: 'Arista',
    model: '7050CX3-32S',
    nos: 'eos',
    uHeight: 1,
    // §8.1: width is UNVERIFIED (reseller "19 inch" doesn't say body vs
    // faceplate; depth is only V(2nd)) — no chassisMm here on purpose, this
    // model keeps the generic default chassis size instead of a guess.
    front: {
      portZones: [
        {
          ports: [
            { type: 'mgmt-rj45', count: 1 },
            { type: 'console-rj45', count: 1 },
          ],
          rows: 1,
          align: 'left',
          widthFraction: 0.10,
        },
        {
          ports: [{ type: 'qsfp28', count: 32 }],
          rows: 2,
          align: 'fill',
        },
        {
          ports: [{ type: 'sfp+', count: 2, label: 'OOB' }],
          rows: 1,
          align: 'right',
          widthFraction: 0.09,
        },
      ],
      leds: [
        { label: 'SYS', color: 'green', position: 'left' },
        { label: 'PSU', color: 'green', position: 'left' },
      ],
    },
    rear: {
      blocks: [
        { type: 'psu-slot', count: 2 },
        { type: 'fan-tray', count: 4 },
      ],
    },
    brand: { accent: '#2A6EBB', chassis: '#141414', label: 'Arista', badge: 'stripe' },
  },

  // ── Ubiquiti USW-Pro-48 ───────────────────────────────────────────────────
  {
    slug: 'ubiquiti-usw-pro-48',
    manufacturer: 'Ubiquiti',
    model: 'USW-Pro-48',
    uHeight: 1,
    // §8.1 V(2nd) (techspecs.ui.com): half-depth, matches [[reference_verified_switch_specs]].
    chassisMm: { widthMm: 442.4, depthMm: 285.4 },
    front: {
      portZones: [
        {
          ports: [{ type: 'rj45', count: 48 }],
          rows: 2,
          align: 'fill',
        },
        {
          ports: [{ type: 'sfp+', count: 4, label: 'SFP+' }],
          rows: 1,
          align: 'right',
          widthFraction: 0.12,
        },
      ],
      leds: [
        { label: 'PWR', color: 'blue', position: 'left' },
      ],
      hasLcd: true,
      lcdPos: 'left',
    },
    rear: {
      blocks: [
        { type: 'psu-slot', count: 1 },
        { type: 'iec-inlet' },
      ],
    },
    brand: { accent: '#0559C7', chassis: '#101317', label: 'Ubiquiti', badge: 'stripe' },
  },

  // ── Fortinet FortiGate-100F ────────────────────────────────────────────────
  {
    slug: 'fortinet-fortigate-100f',
    manufacturer: 'Fortinet',
    model: 'FortiGate-100F',
    nos: undefined,
    uHeight: 1,
    // §8.1 V(2nd) (reseller aggregate, cross-checked): compact appliance,
    // shallowest chassis of the 9 curated models.
    chassisMm: { widthMm: 432, depthMm: 254 },
    front: {
      portZones: [
        {
          ports: [{ type: 'console-rj45', count: 1 }],
          rows: 1,
          align: 'left',
          widthFraction: 0.05,
        },
        {
          ports: [{ type: 'rj45', count: 14 }, { type: 'rj45', count: 2, label: 'WAN' }],
          rows: 1,
          align: 'fill',
        },
        {
          ports: [{ type: 'sfp', count: 4, label: 'DMZ' }],
          rows: 1,
          align: 'right',
          widthFraction: 0.13,
        },
        {
          ports: [{ type: 'sfp+', count: 2, label: 'HA' }],
          rows: 1,
          align: 'right',
          widthFraction: 0.08,
        },
      ],
      leds: [
        { label: 'STATUS', color: 'green', position: 'left' },
        { label: 'ALARM', color: 'red', position: 'left' },
      ],
    },
    rear: {
      // docs/design/24-DEVICE-PHYSICAL-SPEC.md §9 (V(2nd), community.fortinet.com):
      // dual PSU internal fixed (non-hot-swap) = 2x IEC C14, not 1 PSU + 1 inlet.
      blocks: [
        { type: 'iec-inlet', count: 2 },
      ],
    },
    brand: { accent: '#EE3124', chassis: '#2A2D33', label: 'Fortinet', badge: 'stripe' },
  },

  // ── Check Point Quantum Spark 1800 ────────────────────────────────────────
  {
    slug: 'checkpoint-quantum-spark-1800',
    manufacturer: 'Check Point',
    model: 'Quantum Spark 1800',
    nos: undefined,
    uHeight: 1,
    // §8.1 V (checkpoint.com/resources/datasheet-4532, dibuka langsung):
    // 430x300x44.2mm; sumber tak bedakan body/faceplate tapi jauh dari 19"
    // standar jadi diperlakukan sebagai body chassis (interpretasi derived).
    chassisMm: { widthMm: 430, depthMm: 300 },
    front: {
      portZones: [
        {
          ports: [
            { type: 'console-usb', count: 1 },
            { type: 'console-rj45', count: 1 },
          ],
          rows: 1,
          align: 'left',
          widthFraction: 0.09,
        },
        {
          // V: 18x rj45 LAN (16 GbE + LAN1-2 also 2.5GbE-capable — schema
          // doesn't distinguish per-port speed, noted in label instead).
          ports: [{ type: 'rj45', count: 18, label: 'LAN (2x port 1-2 = 2.5GbE)' }],
          rows: 2,
          align: 'fill',
        },
        {
          // V jumlah/tipe, derived representasi: 2x combo SFP/RJ45 WAN + 1x
          // dedicated EXT RJ45 diperlakukan sebagai 3x rj45 (schema tak
          // punya field combo) — lihat catatan combo di label.
          ports: [{ type: 'rj45', count: 3, label: 'WAN (2 combo SFP/RJ45)' }],
          rows: 1,
          align: 'right',
          widthFraction: 0.12,
        },
        {
          // V jumlah/tipe, derived representasi: 1x combo 10GbE SFP/RJ45
          // DMZ jadi sfp+ murni (fiber-capable terdekat) + 1x usb 3.0.
          ports: [
            { type: 'sfp+', count: 1, label: 'DMZ (combo w/ RJ45)' },
            { type: 'usb', count: 1 },
          ],
          rows: 1,
          align: 'right',
          widthFraction: 0.1,
        },
      ],
      // V (sc1.checkpoint.com Front-Panel.htm): Management/Internet/Power
      // status LEDs all report blue when healthy; simplified to 3 system
      // LEDs here (per-port link LEDs are rendered by the port cages, not
      // this system-level list).
      leds: [
        { label: 'PWR', color: 'blue', position: 'left' },
        { label: 'INTERNET', color: 'blue', position: 'left' },
        { label: 'MGMT', color: 'blue', position: 'left' },
      ],
    },
    rear: {
      // V ([[b03-firewall]] §1 via research note): dual 150W redundant PSU,
      // no fan-tray/psu-slot bays called out in the source diagram.
      blocks: [
        { type: 'iec-inlet', count: 2 },
      ],
    },
    brand: { accent: '#EE0A24', chassis: '#1A1A1A', label: 'Check Point', badge: 'stripe' },
  },

  // ── Sophos XGS 3300 ─────────────────────────────────────────────────────
  {
    slug: 'sophos-xgs-3300',
    manufacturer: 'Sophos',
    model: 'XGS 3300',
    nos: undefined,
    uHeight: 1,
    // §8.1 V (docs.sophos.com Operating Instructions PDF, dibuka & di-
    // pdftotext langsung): "438 x 405 x 44 mm Width x Depth x Height".
    chassisMm: { widthMm: 438, depthMm: 405 },
    front: {
      hasLcd: true,
      lcdPos: 'left',
      portZones: [
        {
          // V: console-usb (Micro-USB) + console-rj45 + 2x usb 3.0 + 1x
          // mgmt-rj45, all grouped in one "COM" zone per source diagram.
          ports: [
            { type: 'console-usb', count: 1 },
            { type: 'console-rj45', count: 1 },
            { type: 'usb', count: 2 },
            { type: 'mgmt-rj45', count: 1 },
          ],
          rows: 1,
          align: 'left',
          widthFraction: 0.2,
        },
        {
          ports: [{ type: 'rj45', count: 8, label: 'LAN 1-8 (1/2 bypass)' }],
          rows: 1,
          align: 'fill',
        },
        {
          ports: [{ type: 'sfp+', count: 2, label: 'F1-F2' }],
          rows: 1,
          align: 'right',
          widthFraction: 0.1,
        },
        {
          ports: [{ type: 'sfp', count: 2, label: 'F3-F4' }],
          rows: 1,
          align: 'right',
          widthFraction: 0.08,
        },
      ],
      // V (docs.sophos.com "LED Status" table): Power 1/2 + SSD are the
      // system-level LEDs; per-port ACT/LNK/Speed rendered by port cages.
      leds: [
        { label: 'PWR1', color: 'green', position: 'left' },
        { label: 'PWR2', color: 'green', position: 'left' },
        { label: 'SSD', color: 'blue', position: 'left' },
      ],
    },
    rear: {
      // V (docs.sophos.com): dual internal AC-DC, "Power 1 & Power 2" LEDs
      // imply 2 rear power inlets on this model (redundant internal).
      blocks: [
        { type: 'iec-inlet', count: 2 },
      ],
    },
    brand: { accent: '#DC271E', chassis: '#0D0D0D', label: 'Sophos', badge: 'stripe' },
  },

  // ── SonicWall NSa 2700 ──────────────────────────────────────────────────
  {
    slug: 'sonicwall-nsa-2700',
    manufacturer: 'SonicWall',
    model: 'NSa 2700',
    nos: undefined,
    uHeight: 1,
    // §8.1 V(2nd) (SonicWall datasheet via cdn.blueally.com mirror, dibuka &
    // di-pdftotext sesi ini): "43 x 32.5 x 4.5 (cm)".
    chassisMm: { widthMm: 430, depthMm: 325 },
    front: {
      portZones: [
        {
          ports: [{ type: 'console-rj45', count: 1 }],
          rows: 1,
          align: 'left',
          widthFraction: 0.05,
        },
        {
          ports: [{ type: 'rj45', count: 16, label: '16x 1-GbE' }],
          rows: 2,
          align: 'fill',
        },
        {
          ports: [
            { type: 'mgmt-rj45', count: 1 },
            { type: 'usb', count: 2 },
          ],
          rows: 1,
          align: 'right',
          widthFraction: 0.1,
        },
        {
          ports: [{ type: 'sfp+', count: 3, label: '10-GbE SFP+' }],
          rows: 1,
          align: 'right',
          widthFraction: 0.1,
        },
      ],
      // UNVERIFIED: datasheet has no LED color/label table — using the same
      // minimal PWR/ACT pattern as other entries lacking LED detail (e.g.
      // mikrotik-crs317-1g-16splus-rm) rather than inventing specifics.
      leds: [
        { label: 'PWR', color: 'green', position: 'left' },
        { label: 'ACT', color: 'green', position: 'left' },
      ],
    },
    rear: {
      // V(2nd): PSU rated 60W, redundant PSU is an optional add-on (default
      // config is single).
      blocks: [
        { type: 'iec-inlet', count: 1 },
      ],
    },
    brand: { accent: '#FF6600', chassis: '#111111', label: 'SonicWall', badge: 'stripe' },
  },

  // ── WatchGuard Firebox M370 ─────────────────────────────────────────────
  {
    slug: 'watchguard-firebox-m370',
    manufacturer: 'WatchGuard',
    model: 'Firebox M370',
    nos: undefined,
    uHeight: 1,
    // §8.1 V(2nd) (Firebox M270 & M370 datasheet via media.bechtle.com
    // mirror): "17” x 1.75” x 12.08” (431 x 44 x 307 mm)" for M370.
    chassisMm: { widthMm: 431, depthMm: 307 },
    front: {
      portZones: [
        {
          ports: [{ type: 'rj45', count: 8 }],
          rows: 1,
          align: 'fill',
        },
        {
          ports: [{ type: 'console-rj45', count: 1, label: 'SRL' }],
          rows: 1,
          align: 'right',
          widthFraction: 0.06,
        },
        {
          ports: [{ type: 'usb', count: 2 }],
          rows: 1,
          align: 'right',
          widthFraction: 0.06,
        },
      ],
      // V(2nd) keberadaan "LEDs" di panel depan, UNVERIFIED warna/label
      // per-LED individual — pola minimal seperti entri lain.
      leds: [
        { label: 'PWR', color: 'green', position: 'left' },
        { label: 'STATUS', color: 'amber', position: 'left' },
      ],
    },
    rear: {
      // Catatan implementasi: datasheet menempatkan PSU & fan di FRONT
      // panel appliance ini (bukan rear seperti device rackmount lain di
      // NetGeo) — V(2nd) untuk fakta itu, tapi tetap dimodelkan sebagai
      // iec-inlet di rear.blocks sesuai konvensi skema (derived placement,
      // bukan klaim vendor bahwa inlet ada di belakang).
      blocks: [
        { type: 'iec-inlet', count: 1 },
      ],
    },
    brand: { accent: '#CC0000', chassis: '#8B0000', label: 'WatchGuard', badge: 'stripe' },
  },

  // ── Barracuda CloudGen Firewall F400 ───────────────────────────────────
  {
    slug: 'barracuda-cloudgen-f400',
    manufacturer: 'Barracuda Networks',
    model: 'CloudGen Firewall F400',
    nos: undefined,
    uHeight: 1,
    // §8.1 chassisMm HILANGKAN — dua sumber sekunder kontradiktif (SHI Gov
    // "17.3x17.3x1.7in" ~439x439x43mm vs snippet lain "42.6x39.6x4.4cm"
    // 426x396x44mm, selisih terlalu besar untuk rounding); datasheet resmi
    // assets.barracuda.com tak memuat dimensi fisik. 3D builder pakai
    // fallback generic body size, bukan angka tebakan.
    front: {
      portZones: [
        {
          ports: [{ type: 'rj45', count: 8, label: 'LAN/WAN' }],
          rows: 1,
          align: 'fill',
        },
      ],
      // UNVERIFIED: tak ada tabel LED di datasheet performa yang terbaca —
      // pola minimal seperti entri UNVERIFIED lain.
      leds: [
        { label: 'PWR', color: 'green', position: 'left' },
        { label: 'STATUS', color: 'green', position: 'left' },
      ],
    },
    rear: {
      // Power watt UNVERIFIED, PSU single internal — V untuk single/internal.
      blocks: [
        { type: 'iec-inlet', count: 1 },
      ],
    },
    brand: { accent: '#EE2E24', chassis: '#141414', label: 'Barracuda', badge: 'stripe' },
  },

  // ── Hillstone SG-6000-A2600-IN ──────────────────────────────────────────
  {
    slug: 'hillstone-sg-6000-a2600-in',
    manufacturer: 'Hillstone Networks',
    model: 'SG-6000-A2600-IN',
    nos: undefined,
    uHeight: 1,
    // §8.1 V (hillstonenet.com datasheet, dibuka & di-pdftotext langsung):
    // "436x320x44 mm".
    chassisMm: { widthMm: 436, depthMm: 320 },
    front: {
      portZones: [
        {
          // V(2nd) (SG-6000 A-Series Hardware Reference Manual via
          // manualslib.com snippet, mengutip manual resmi Hillstone).
          ports: [
            { type: 'usb', count: 2 },
            { type: 'console-rj45', count: 1 },
          ],
          rows: 1,
          align: 'left',
          widthFraction: 0.14,
        },
        {
          ports: [{ type: 'mgmt-rj45', count: 1, label: 'MGT' }],
          rows: 1,
          align: 'left',
          widthFraction: 0.06,
        },
        {
          ports: [{ type: 'rj45', count: 8, label: 'GE 0-7' }],
          rows: 1,
          align: 'fill',
        },
      ],
      // V(2nd) keberadaan (Power, Status, Alarm, SSD, 2x Power supply) —
      // warna spesifik per-LED UNVERIFIED (snippet tak merinci), memakai
      // konvensi warna umum status/alarm dari entri lain.
      leds: [
        { label: 'PWR', color: 'green', position: 'left' },
        { label: 'STATUS', color: 'green', position: 'left' },
        { label: 'ALM', color: 'red', position: 'left' },
      ],
    },
    rear: {
      // V: dual AC redundant PSU tersedia untuk kelas A2000/A2600+.
      blocks: [
        { type: 'iec-inlet', count: 2 },
      ],
    },
    brand: { accent: '#0072BC', chassis: '#1C1C1C', label: 'Hillstone', badge: 'stripe' },
  },

  // ── Stormshield SN2100 ──────────────────────────────────────────────────
  {
    slug: 'stormshield-sn2100',
    manufacturer: 'Stormshield',
    model: 'SN2100',
    nos: undefined,
    uHeight: 1,
    // §8.1 V (stormshield.com/products/sn2100 halaman resmi, dibuka
    // langsung): "44,45x443x610 mm" tinggi x lebar x kedalaman — 1U
    // full-depth (kedalaman jauh melebihi device rackmount lain di batch).
    chassisMm: { widthMm: 443, depthMm: 610 },
    front: {
      portZones: [
        {
          ports: [{ type: 'rj45', count: 2, label: 'Fixed 1GbE' }],
          rows: 1,
          align: 'fill',
        },
        {
          // V(2nd) keberadaan/jumlah 3 slot ekspansi modular (RJ45 1G/2.5G/
          // 10G ATAU fiber 1G-40G, tergantung modul terpasang) — schema
          // PortSpec tak punya representasi "modul kosong beragam tipe";
          // dipetakan ke `sfp` x3 sebagai placeholder terdekat (fiber-
          // capable), label menandai ini slot generik kosong, bukan tipe
          // fisik pasti.
          ports: [{ type: 'sfp', count: 3, label: 'Expansion slot (modular, empty)' }],
          rows: 1,
          align: 'right',
          widthFraction: 0.18,
        },
        {
          ports: [{ type: 'usb', count: 2 }],
          rows: 1,
          align: 'right',
          widthFraction: 0.08,
        },
      ],
      // UNVERIFIED: tak ditemukan tabel LED resmi di sumber manapun —
      // pola minimal seperti entri UNVERIFIED lain.
      leds: [
        { label: 'PWR', color: 'green', position: 'left' },
        { label: 'STATUS', color: 'green', position: 'left' },
      ],
    },
    rear: {
      // V(2nd): 1x power port IEC-60320-C14, single PSU.
      blocks: [
        { type: 'iec-inlet', count: 1 },
      ],
    },
    brand: { accent: '#FF6A13', chassis: '#1A1A1A', label: 'Stormshield', badge: 'stripe' },
  },

  // ── Netgate 8200 MAX ────────────────────────────────────────────────────
  {
    slug: 'netgate-8200-max',
    manufacturer: 'Netgate',
    model: '8200 MAX',
    nos: undefined,
    uHeight: 1,
    // §8.1 V(2nd)/derived widthMm, V depthMm (shop.netgate.com "19 x 10 x
    // 1.75 in" — 482.6mm is faceplate/rack-ears per §1.2.1 convention, not
    // chassis body; body estimated ~437mm per research note's own explicit
    // recommendation when a body-only number is needed). depthMm 254mm
    // taken as-is (depth rarely differs body vs faceplate).
    chassisMm: { widthMm: 437, depthMm: 254 },
    front: {
      portZones: [
        {
          // V jenis/jumlah, UNVERIFIED urutan X-Y presisi (deskripsi teks,
          // bukan diagram bergambar) — combo RJ45/Micro-B auto-detect
          // direpresentasikan console-rj45 saja (schema tak punya combo).
          ports: [{ type: 'console-rj45', count: 1 }],
          rows: 1,
          align: 'left',
          widthFraction: 0.07,
        },
        {
          ports: [{ type: 'sfp+', count: 2, label: '10G WAN' }],
          rows: 1,
          align: 'left',
          widthFraction: 0.1,
        },
        {
          // V jenis/jumlah, derived representasi: combo 1G WAN diwakili
          // sfp murni (schema tak punya combo).
          ports: [{ type: 'sfp', count: 2, label: '1G WAN (combo)' }],
          rows: 1,
          align: 'left',
          widthFraction: 0.1,
        },
        {
          ports: [{ type: 'rj45', count: 4, label: '2.5G LAN' }],
          rows: 1,
          align: 'fill',
        },
        {
          ports: [{ type: 'usb', count: 2 }],
          rows: 1,
          align: 'right',
          widthFraction: 0.06,
        },
      ],
      // V keberadaan (3x LED 4-warna RGB+amber, software-controlled),
      // UNVERIFIED posisi/makna per-LED individual — disederhanakan jadi
      // satu LED sistem representatif alih-alih menebak 3 label/posisi.
      leds: [
        { label: 'STATUS', color: 'blue', position: 'left' },
      ],
    },
    rear: {
      // V: PSU eksternal 12V 5A (60W), konektor barrel berulir (locking) —
      // bukan IEC C14 AC seperti device lain; `iec-inlet` dipakai sebagai
      // representasi rear-power-connector terdekat yang ada di schema
      // (tidak ada tipe "dc-barrel"), TIDAK menyiratkan klaim AC C14 nyata.
      blocks: [
        { type: 'iec-inlet', count: 1 },
      ],
    },
    brand: { accent: '#F58220', chassis: '#101010', label: 'Netgate', badge: 'stripe' },
  },

  // ── Dell PowerEdge R740 (2U server) ───────────────────────────────────────
  {
    slug: 'dell-poweredge-r740',
    manufacturer: 'Dell',
    model: 'PowerEdge R740',
    uHeight: 2,
    isFullDepth: true,
    // §8.1 V (i.dell.com spec sheet + dell.com manual, dibuka langsung):
    // 2U full-depth server — the deepest chassis of the 9 by a wide margin.
    chassisMm: { widthMm: 434.0, depthMm: 737.5 },
    front: {
      portZones: [
        {
          ports: [{ type: 'drive-lff', count: 8 }],
          rows: 1,
          align: 'fill',
        },
      ],
      leds: [
        { label: 'PWR', color: 'green', position: 'left' },
      ],
      isServerBezel: true,
    },
    rear: {
      blocks: [
        { type: 'psu-slot', count: 2 },
        {
          type: 'port-zone',
          portZone: {
            ports: [
              { type: 'mgmt-rj45', count: 1, label: 'iDRAC' },
              { type: 'rj45', count: 4 },
            ],
            rows: 1,
            align: 'left',
          },
        },
        { type: 'vent-grille' },
      ],
    },
    brand: { accent: '#007DB8', chassis: '#17171A', label: 'Dell', badge: 'stripe' },
  },

  // ── Lenovo ThinkSystem SR650 V4 (2U server) ──────────────────────────────
  {
    slug: 'lenovo-thinksystem-sr650-v4',
    manufacturer: 'Lenovo',
    model: 'ThinkSystem SR650 V4',
    uHeight: 2,
    isFullDepth: true,
    // §8.1 V (research/3d-device-specs-server.md §1, pubs.lenovo.com
    // /sr650-v4/server_specifications_mechanical, dibuka langsung): body
    // width 445mm eksplisit dibedakan vendor dari lebar-dengan-rail-latch
    // 482mm (19.0") — dipakai 445mm (body), bukan 482mm.
    chassisMm: { widthMm: 445, depthMm: 796 },
    front: {
      portZones: [
        {
          // V jenis/jumlah (pubs.lenovo.com/sr650-v4/server_front_view).
          // Konfigurasi representatif: 24x SFF hot-swap. Varian backplane
          // lain yang ADA tapi TIDAK dimodelkan: 12x LFF 3.5", 32x E3.S NVMe.
          ports: [{ type: 'drive-sff', count: 24 }],
          rows: 1,
          align: 'fill',
        },
      ],
      // V, satu-satunya SKU batch ini dengan daftar LED lengkap + warna
      // (pubs.lenovo.com/sr650-v4/server_front_operator_panel).
      leds: [
        { label: 'PWR', color: 'green', position: 'left' },
        { label: 'NET', color: 'green', position: 'left' },
        { label: 'ERR', color: 'amber', position: 'left' },
        { label: 'ID', color: 'blue', position: 'left' },
      ],
      isServerBezel: true,
    },
    rear: {
      blocks: [
        { type: 'psu-slot', count: 2 },
        {
          type: 'port-zone',
          portZone: {
            // mgmt-rj45: XCC dedicated 1GbE, V. 2x OCP 3.0 NIC slot bersifat
            // MODULAR (1/10/25/40G tergantung kartu terpasang) — TIDAK
            // digambar sebagai PortSpec tetap (research §gap-2).
            ports: [{ type: 'mgmt-rj45', count: 1, label: 'XCC' }],
            rows: 1,
            align: 'left',
          },
        },
        { type: 'vent-grille' },
      ],
    },
    // brand: derived, low-confidence — belum dikonfirmasi ke foto produk
    // aktual (research §1); merah aksen dari identitas korporat Lenovo.
    brand: { accent: '#E2231A', chassis: '#1A1A1A', label: 'Lenovo', badge: 'stripe' },
  },

  // ── Inspur NF5280M5 (2U server) ───────────────────────────────────────────
  {
    slug: 'inspur-nf5280m5',
    manufacturer: 'Inspur',
    model: 'NF5280M5',
    uHeight: 2,
    isFullDepth: true,
    // §8.1 V (research/3d-device-specs-server.md §2, PDF resmi inspur.com):
    // "Dimension（W×H×D）: 435mm×87mm×779.5mm".
    chassisMm: { widthMm: 435, depthMm: 779.5 },
    front: {
      portZones: [
        {
          // V (PDF resmi, tabel teks — UNVERIFIED urutan X-Y, PDF tanpa
          // diagram bergambar). Konfigurasi representatif: 25x SFF. Varian
          // lain yang ADA tapi TIDAK dimodelkan: 12x LFF 3.5".
          ports: [{ type: 'drive-sff', count: 25 }],
          rows: 1,
          align: 'fill',
        },
        {
          ports: [{ type: 'usb', count: 2 }],
          rows: 1,
          align: 'right',
          widthFraction: 0.08,
        },
      ],
      // V untuk keberadaannya (PDF resmi, tabel "I/O Interface"), TAPI
      // warna TIDAK disebutkan di datasheet ini — placeholder putih netral,
      // bukan tebakan warna asli.
      leds: [{ label: 'UID', color: 'white', position: 'left' }],
      isServerBezel: true,
    },
    rear: {
      blocks: [
        { type: 'psu-slot', count: 2 },
        {
          type: 'port-zone',
          portZone: {
            // mgmt-rj45: BMC dedicated 1GbE, V. OCP 25G + PHY 1G/10G slot
            // bersifat MODULAR (jumlah/jenis port aktual baru diketahui
            // setelah kartu terpasang) — TIDAK digambar sebagai PortSpec
            // tetap (research §gap-2).
            ports: [{ type: 'mgmt-rj45', count: 1 }],
            rows: 1,
            align: 'left',
          },
        },
        { type: 'vent-grille' },
      ],
    },
    // brand: derived, low-confidence — tidak ada inspeksi foto produk;
    // aksen korporat TIDAK ditentukan (research §2), dipakai netral = chassis.
    brand: { accent: '#1C1C1E', chassis: '#1C1C1E', label: 'Inspur', badge: 'stripe' },
  },

  // ── QCT QuantaGrid D52BQ-2U (2U server) ───────────────────────────────────
  {
    slug: 'qct-quantagrid-d52bq-2u',
    manufacturer: 'QCT',
    model: 'QuantaGrid D52BQ-2U',
    uHeight: 2,
    isFullDepth: true,
    // §8.1 V (research/3d-device-specs-server.md §3, dikonfirmasi identik
    // di qct.io product page DAN PDF datasheet resmi mirror hyperscalers.com):
    // "W x H x D (mm): 440 x 87.5 x 780".
    chassisMm: { widthMm: 440, depthMm: 780 },
    front: {
      portZones: [
        {
          // V — 5 varian SKU backplane berbeda dalam satu model, dipilih
          // SKU#4 sebagai representasi (paling padat/umum): 24x SFF depan.
          ports: [{ type: 'drive-sff', count: 24 }],
          rows: 1,
          align: 'fill',
        },
        {
          ports: [{ type: 'usb', count: 2 }],
          rows: 1,
          align: 'right',
          widthFraction: 0.08,
        },
      ],
      // V untuk keberadaannya ("Front I/O: Power/ID/Status LEDs", generik
      // 3 LED tanpa breakdown warna per-fungsi) — warna UNVERIFIED,
      // placeholder putih netral, bukan tebakan warna asli.
      leds: [{ label: 'STATUS', color: 'white', position: 'left' }],
      isServerBezel: true,
    },
    rear: {
      blocks: [
        { type: 'psu-slot', count: 2 },
        {
          type: 'port-zone',
          portZone: {
            // mgmt-rj45: dedicated LOM GbE mgmt, V. NIC 10G RJ45 mezzanine
            // OCP bersifat OPSIONAL/MODULAR — TIDAK digambar sebagai
            // PortSpec tetap (research §gap-2, bukan bawaan wajib).
            ports: [{ type: 'mgmt-rj45', count: 1 }],
            rows: 1,
            align: 'left',
          },
        },
        // rear juga punya 2x drive-sff NVMe/SATA opsional per datasheet —
        // TIDAK digambar (opsional, tidak selalu terpasang).
        { type: 'vent-grille' },
      ],
    },
    // brand: derived, low-confidence — tidak ada inspeksi foto produk;
    // aksen korporat TIDAK ditentukan (research §3), dipakai netral = chassis.
    brand: { accent: '#1D1D1F', chassis: '#1D1D1F', label: 'QCT', badge: 'stripe' },
  },

  // ── Wiwynn SV300G3 (1U server, 19-inch konvensional) ──────────────────────
  {
    slug: 'wiwynn-sv300g3',
    manufacturer: 'Wiwynn',
    model: 'SV300G3',
    uHeight: 1,
    // §8.1 V (research/3d-device-specs-server.md §4, wiwynn.com/products
    // /19-inch/sv300g3, dibuka langsung): "43.5 (H) × 436 (W) × 710 (D) (mm)".
    chassisMm: { widthMm: 436, depthMm: 710 },
    front: {
      portZones: [
        {
          // V: "Ten 2.5″ hot-plug drive bays".
          ports: [{ type: 'drive-sff', count: 10 }],
          rows: 1,
          align: 'fill',
        },
        {
          ports: [{ type: 'usb', count: 1 }],
          rows: 1,
          align: 'right',
          widthFraction: 0.06,
        },
      ],
      // LED UNVERIFIED total — halaman resmi hanya menyebut 3 tombol
      // PWR/UID/Reset, dikonfirmasi ulang eksplisit TIDAK ada LED status/
      // HDD/network terpisah untuk model ini. Pola minimal generik dipakai
      // sebagai gantinya (preseden askey-rtf6105vw).
      leds: [{ label: 'PWR', color: 'green', position: 'left' }],
      isServerBezel: true,
    },
    rear: {
      blocks: [
        { type: 'psu-slot', count: 2 },
        {
          type: 'port-zone',
          portZone: {
            // mgmt-rj45: dedicated GbE BMC port, V. rj45: rear I/O standar
            // 1x, V (tetap, bukan modular — beda dari slot OCP v2.0 opsional
            // yang juga ada di model ini tapi tidak digambar).
            ports: [
              { type: 'mgmt-rj45', count: 1 },
              { type: 'rj45', count: 1 },
            ],
            rows: 1,
            align: 'left',
          },
        },
        { type: 'vent-grille' },
      ],
    },
    // brand: derived, low-confidence — tidak ada inspeksi foto produk;
    // aksen korporat TIDAK ditentukan (research §4, dijual white-box ke
    // hyperscaler), dipakai netral = chassis.
    brand: { accent: '#151517', chassis: '#151517', label: 'Wiwynn', badge: 'stripe' },
  },

  // ── Pure Storage FlashArray//X70 R5 (3U all-flash SAN) ────────────────────
  {
    slug: 'pure-flasharray-x70-r5',
    manufacturer: 'Pure Storage',
    model: 'FlashArray//X70 R5',
    uHeight: 3,
    isFullDepth: true,
    // §8.1 V (research/3d-device-specs-server.md §5, PDF resmi
    // everpuredata.com/content/dam/pdf/en/datasheets/ds-flasharray-x.pdf):
    // "5.12” x 18.94” x 29.72” chassis" (H×W×D). Lebar 18.94" (481,1mm)
    // dipertahankan sebagai body chassis nyata (BUKAN dihilangkan meski
    // dekat 19"/482,6mm) karena datasheet yang sama mencantumkan //X50 R5
    // dengan lebar BERBEDA (400mm) untuk chassis yang sama-sama masuk rak
    // 19" — variasi antar-model adalah bukti kuat ini angka body asli.
    chassisMm: { widthMm: 481, depthMm: 755 },
    front: {
      portZones: [
        {
          // UNVERIFIED urutan pasti — V(2nd) (manualslib.com mirror
          // instalasi resmi, dikorroborasi WebSearch): 10 atau 20
          // DirectFlash Module (DFM) bay. Direpresentasikan sebagai
          // drive-sff (proksi terdekat) — DFM adalah modul NVMe proprietary
          // Pure, BUKAN drive 2.5" standar SFF/LFF industri; catat perbedaan
          // fisik ini untuk builder 3D.
          ports: [{ type: 'drive-sff', count: 20, label: 'DFM' }],
          rows: 1,
          align: 'fill',
        },
      ],
      // LED UNVERIFIED total — nol daftar LED front panel ditemukan di
      // datasheet marketing maupun mirror instalasi. Pola minimal generik
      // dipakai sebagai gantinya (preseden askey-rtf6105vw).
      leds: [{ label: 'PWR', color: 'green', position: 'left' }],
      isServerBezel: true,
    },
    rear: {
      blocks: [
        { type: 'iec-inlet', count: 2 },
        {
          type: 'port-zone',
          portZone: {
            // mgmt-rj45 x2 (mgmt0/mgmt1, 1000base-t) + rj45 x4 (ct0/ct1
            // eth0/eth1, 10gbase-t — konektor fisik tetap RJ45 meski 10G) —
            // V(2nd), NetBox Data Exchange + korroborasi PDF resmi. Host
            // port FC/Ethernet 10-100G untuk data path utama bersifat
            // MODULAR (kartu HBA tambahan) — TIDAK dienumerasi.
            ports: [
              { type: 'mgmt-rj45', count: 2 },
              { type: 'rj45', count: 4 },
            ],
            rows: 1,
            align: 'left',
          },
        },
        { type: 'vent-grille' },
      ],
    },
    // brand: derived, low-confidence — bezel oranye khas "Pure Storage"
    // (research §5), TAPI rebrand korporat ke "Everpure" efektif 23 Feb
    // 2026 berpotensi mengubah skema warna — hex oranye lama dicatat
    // dengan peringatan eksplisit mungkin sudah usang, belum ada foto
    // produk era Everpure diperiksa.
    brand: { accent: '#FA4B2A', chassis: '#17171A', label: 'Pure Storage', badge: 'stripe' },
  },

  // ── Hitachi Vantara VSP One Block 24/26/28 (2U all-flash SAN) ─────────────
  {
    slug: 'hitachi-vsp-one-block',
    manufacturer: 'Hitachi Vantara',
    model: 'VSP One Block 24/26/28',
    uHeight: 2,
    // §8.1 chassisMm DIHILANGKAN (UNVERIFIED, keputusan leader) — PDF resmi
    // matrix-specifications menyebut "Width: 19.0” (482mm W)": angka genap
    // bulat TANPA variasi antar-kelas node (beda dari kasus Pure/Everpure di
    // atas, yang justru punya lebar berbeda per varian sebagai bukti body
    // asli) — pola klasik restated lebar-rak-nominal, BUKAN pengukuran body
    // chassis aktual. Depth 852mm (33.6") tidak ditebak jadi body sendirian
    // karena skema chassisMm mewajibkan width+depth sepasang.
    front: {
      // portZones DIHILANGKAN (UNVERIFIED, keputusan leader) — PDF resmi
      // hanya menyebut kapasitas maksimum agregat "72 SFF NVMe SSD"
      // (mencakup node dasar + hingga 2x expansion tray 24-SFF); jumlah bay
      // FISIK pada node controller dasar sendiri tidak dinyatakan eksplisit
      // di kedua PDF resmi yang dibuka riset — tidak ditebak dari angka
      // tray ekspansi sebagai proksi node dasar (dua komponen berbeda).
      portZones: [],
      // LED UNVERIFIED total — kedua PDF resmi (datasheet + matrix-specs)
      // adalah dokumen marketing/spec-comparison, tidak membahas front
      // panel/LED sama sekali. Pola minimal generik dipakai sebagai
      // gantinya (preseden askey-rtf6105vw).
      leds: [{ label: 'PWR', color: 'green', position: 'left' }],
      isServerBezel: true,
    },
    rear: {
      // Host port Fibre Channel (hingga 32x FC, Gen 7) SENGAJA DIHILANGKAN
      // (keputusan leader): TIDAK ADA tipe port "fc" di kosakata PortType —
      // aturan Surya melarang memetakan FC ke tipe lain (mis. sfp+) karena
      // protokol dan optik FC berbeda dari Ethernet meski konektor fisik
      // SFP+ mirip. Menunggu tipe port dedicated di skema (research §gap-1).
      blocks: [
        { type: 'psu-slot', count: 2 },
        { type: 'vent-grille' },
      ],
    },
    // brand: derived, low-confidence — belum dikonfirmasi ke foto produk
    // aktual (research §6); merah aksen dari identitas korporat Hitachi.
    brand: { accent: '#E60012', chassis: '#17171A', label: 'Hitachi Vantara', badge: 'stripe' },
  },

  // ── VSOL V5600X7 (OLT chassis) ──────────────────────────────────────────
  {
    slug: 'vsol-v5600x7',
    manufacturer: 'VSOL',
    model: 'V5600X7',
    uHeight: 6,
    // §8.1 V (vsolcn.com V5600X-Series-Datasheet-V1.0-EN, dikutip via
    // research/3d-device-specs-olt.md §1): "442x299x266.7mm" tanpa telinga
    // mounting. uHeight 6 derived (266.7 / 44.45 ≈ 6, vendor tak sebut "6U"
    // literal).
    chassisMm: { widthMm: 442, depthMm: 299 },
    front: {
      portZones: [
        {
          // V jenis/jumlah kartu; konfigurasi representatif (1x kartu
          // CBG1601 16-port GPON di slot 1 dari 7 slot servis) — chassis
          // punya 6 slot lain yang dibiarkan kosong di representasi ini,
          // bukan konfigurasi maksimum 112 port.
          ports: [{ type: 'pon', count: 16, label: 'GPON slot 1 (kartu CBG1601)' }],
          rows: 1,
          align: 'fill',
        },
        {
          // V: kartu CSMUX701 (kontrol/uplink) di slot 5 — AUX out-band.
          ports: [
            { type: 'console-rj45', count: 1 },
            { type: 'mgmt-rj45', count: 1 },
          ],
          rows: 1,
          align: 'right',
          widthFraction: 0.09,
        },
        {
          ports: [{ type: 'usb', count: 2 }],
          rows: 1,
          align: 'right',
          widthFraction: 0.06,
        },
        {
          // V jumlah/jenis, derived representasi: SFP(GE)/SFP+(10GE)
          // dual-rate diwakili `sfp` murni (schema tak punya combo).
          ports: [
            { type: 'sfp', count: 8, label: 'Uplink CSMU (SFP/SFP+ dual-rate)' },
            { type: 'qsfp28', count: 1, label: 'Uplink 40/50/100GE' },
          ],
          rows: 1,
          align: 'right',
          widthFraction: 0.18,
        },
      ],
      // UNVERIFIED: tak ada tabel LED di sumber resmi.
      leds: [
        { label: 'PWR', color: 'green', position: 'left' },
        { label: 'ACT', color: 'green', position: 'left' },
      ],
    },
    rear: {
      // V feed (DC only, -48V, no AC option per ordering info); jumlah slot
      // PSU UNVERIFIED — pakai 1 sebagai default minimal alih-alih menebak
      // redundansi.
      blocks: [
        { type: 'psu-slot', count: 1 },
      ],
    },
    brand: { accent: '#0072BC', chassis: '#1C1C1C', label: 'VSOL', badge: 'stripe' },
  },

  // ── C-DATA FD1700S (compact modular OLT) ────────────────────────────────
  {
    slug: 'cdata-fd1700s',
    manufacturer: 'C-DATA',
    model: 'FD1700S',
    uHeight: 1,
    // §8.1 V (cdatatec.com halaman resmi FD1700S): "1U 19-inch standard
    // box", "440x375x44mm".
    chassisMm: { widthMm: 440, depthMm: 375 },
    front: {
      portZones: [
        {
          // V jenis/jumlah; konfigurasi representatif = 1x kartu 16-port
          // GPON (opsi 8/24/32-port juga tersedia, tidak dimodelkan di sini).
          ports: [{ type: 'pon', count: 16, label: 'GPON slot (kartu 16-port, opsi 8/24/32 juga tersedia)' }],
          rows: 1,
          align: 'fill',
        },
        {
          // V jumlah, derived representasi: opsi "4x1G(SFP)/10G(SFP+)"
          // diwakili sfp+ murni; opsi kedua "4x10G(SFP+)/25G(SFP28)" tidak
          // dipakai di konfigurasi representatif ini.
          ports: [{ type: 'sfp+', count: 4, label: 'Uplink (opsi 4x10G SFP+, varian 25G tidak dimodelkan)' }],
          rows: 1,
          align: 'right',
          widthFraction: 0.14,
        },
      ],
      // UNVERIFIED: tak ada tabel LED di sumber resmi.
      leds: [
        { label: 'PWR', color: 'green', position: 'left' },
        { label: 'ACT', color: 'green', position: 'left' },
      ],
    },
    rear: {
      // V: dual redundant PSU module (AC 100-240V atau DC -40~-72V).
      blocks: [
        { type: 'psu-slot', count: 2 },
      ],
    },
    brand: { accent: '#0A3D62', chassis: '#141414', label: 'C-DATA', badge: 'stripe' },
  },

  // ── BDCOM GP3600-8CGP (1U fixed-port OLT) ───────────────────────────────
  {
    slug: 'bdcom-gp3600-8cgp',
    manufacturer: 'BDCOM',
    model: 'GP3600-8CGP',
    uHeight: 1,
    // §8.1 V (bdcom.cn PDF resmi): "440x270x44mm". uHeight 1 derived
    // (44 / 44.45 ≈ 1, vendor tak sebut "1U" literal).
    chassisMm: { widthMm: 440, depthMm: 270 },
    front: {
      portZones: [
        {
          // V jenis/jumlah — port fixed (bukan slot kartu), combo
          // GPON/XG-PON/XGS-PON pada port fisik yang sama, modul PON dijual
          // terpisah.
          ports: [{ type: 'pon', count: 8, label: 'Combo GPON/XG-PON/XGS-PON (modul PON dijual terpisah)' }],
          rows: 1,
          align: 'fill',
        },
        {
          ports: [{ type: 'sfp+', count: 8, label: 'Uplink 10GE' }],
          rows: 1,
          align: 'right',
          widthFraction: 0.16,
        },
        {
          ports: [{ type: 'qsfp28', count: 2, label: 'Uplink 100GE' }],
          rows: 1,
          align: 'right',
          widthFraction: 0.08,
        },
      ],
      // UNVERIFIED: tak ada tabel LED di sumber resmi.
      leds: [
        { label: 'PWR', color: 'green', position: 'left' },
        { label: 'ACT', color: 'green', position: 'left' },
      ],
    },
    rear: {
      // V: 2x "power slot" hot-swap (SKU AC 100-240V atau SKU DC 36-72V
      // terpisah, bukan dual-input otomatis); max consumption 100W eksplisit
      // (bukan typical).
      blocks: [
        { type: 'psu-slot', count: 2 },
      ],
    },
    // brand accent tidak cukup dikenal untuk ditebak dengan keyakinan
    // (research/3d-device-specs-olt.md §3) — pakai abu-abu netral alih-alih
    // menebak warna logo.
    brand: { accent: '#4A4A4A', chassis: '#101010', label: 'BDCOM', badge: 'stripe' },
  },

  // ── DZS Velocity V14 (large chassis OLT) ────────────────────────────────
  {
    slug: 'dzs-velocity-v14',
    manufacturer: 'DZS',
    model: 'Velocity V14',
    uHeight: 14,
    // §8.1 chassisMm HILANGKAN — label vendor "622x441x280mm (W×H×D)" tidak
    // konsisten matematis: 622mm = 14x44.45mm (tinggi 14U sebenarnya), BUKAN
    // lebar seperti urutan label menyiratkan (digicomm.com PDF resmi DZS,
    // dikutip via research/3d-device-specs-olt.md §4). Mana dari 441/280 itu
    // width vs depth jadi simpulan, bukan kutipan langsung — tidak ditebak
    // sebagai chassisMm nyata, 3D builder pakai fallback generic body size.
    front: {
      portZones: [
        {
          // V jenis+jumlah kartu; konfigurasi representatif = mode
          // single-central-blade (1 slot switch pusat + 13 slot subscriber),
          // 1x kartu 16-port GPON OLT service card di salah satu dari 13
          // slot — bukan satu-satunya konfigurasi yang mungkin.
          ports: [{ type: 'pon', count: 16, label: 'Kartu GPON OLT 16-port, 1 dari 13 slot subscriber (mode single-central-blade)' }],
          rows: 1,
          align: 'fill',
        },
        {
          // derived: agregat central switch "2x100GE + 4x10G/25GE + 2xGE"
          // dipetakan per-jenis; medium "GE" polos diasumsikan optik SFP,
          // UNVERIFIED apakah RJ45 elektrik atau SFP di sumber.
          ports: [
            { type: 'qsfp28', count: 2, label: 'Central switch uplink 100GE' },
            { type: 'sfp28', count: 4, label: 'Central switch uplink 10/25GE' },
            { type: 'sfp', count: 2, label: 'Central switch uplink GE' },
          ],
          rows: 1,
          align: 'right',
          widthFraction: 0.22,
        },
      ],
      // UNVERIFIED: tak ada tabel LED di sumber resmi.
      leds: [
        { label: 'PWR', color: 'green', position: 'left' },
        { label: 'ACT', color: 'green', position: 'left' },
      ],
    },
    rear: {
      // V: dual A/B redundant feeds, DC only -43.75 to -59.9VDC.
      blocks: [
        { type: 'psu-slot', count: 2 },
      ],
    },
    brand: { accent: '#6E3FA3', chassis: '#1A1A1A', label: 'DZS', badge: 'stripe' },
  },

  // ── Adtran SDX 6320 (disaggregated OLT) ─────────────────────────────────
  {
    slug: 'adtran-sdx-6320',
    manufacturer: 'Adtran',
    model: 'SDX 6320',
    // uHeight 2, BUKAN 1.5 walau vendor menyatakan "1.5RU" (adtran.com PDF
    // resmi, tabel dimensi "225x387x66mm" konsisten 66/44.45≈1.5) — uHeight
    // dipakai sebagai span slot RU bilangan bulat di canPlaceDevice()/
    // dropDecision() (frontend/src/lib/three/plantAdapter.ts), pecahan akan
    // merusak aritmetika slot rak. Dibulatkan ke atas (keputusan leader).
    uHeight: 2,
    // §8.1 V (adtran.com PDF resmi, tabel dimensi D×W×H mm).
    chassisMm: { widthMm: 387, depthMm: 225 },
    front: {
      portZones: [
        {
          ports: [{ type: 'pon', count: 16, label: 'Combo PON (GPON+XGS-PON simultan per port)' }],
          rows: 1,
          align: 'fill',
        },
        {
          ports: [{ type: 'qsfp28', count: 4, label: 'Uplink 100GbE' }],
          rows: 1,
          align: 'right',
          widthFraction: 0.16,
        },
        {
          ports: [{ type: 'sfp+', count: 4, label: 'Uplink 10GbE' }],
          rows: 1,
          align: 'right',
          widthFraction: 0.12,
        },
      ],
      // UNVERIFIED: tak ada tabel LED di sumber resmi.
      leds: [
        { label: 'PWR', color: 'green', position: 'left' },
        { label: 'ACT', color: 'green', position: 'left' },
      ],
    },
    rear: {
      // V: -48VDC redundant.
      blocks: [
        { type: 'psu-slot', count: 2 },
      ],
    },
    brand: { accent: '#0072CE', chassis: '#1B1B1B', label: 'Adtran', badge: 'stripe' },
  },

  // ── Calix AXOS E7-2 + XG801 (small modular OLT) ─────────────────────────
  {
    slug: 'calix-axos-e7-2',
    manufacturer: 'Calix',
    model: 'AXOS E7-2 + XG801',
    // V: chassis "scalable from 1RU to 10RU", 1RU dipilih sebagai
    // konfigurasi dasar representatif, bukan satu-satunya ukuran.
    uHeight: 1,
    // §8.1 chassisMm HILANGKAN — halaman produk resmi calix.com tak
    // mencantumkan dimensi fisik chassis, datasheet lengkap terkunci login
    // (research/3d-device-specs-olt.md §6, preseden Barracuda). 3D builder
    // pakai fallback generic body size, bukan angka tebakan.
    front: {
      portZones: [
        {
          // V jenis/jumlah per kartu XG801; konfigurasi representatif = 2
          // slot chassis dasar, kedua slot diisi kartu XG801 (port
          // selectable GPON/XGS-PON/P2P Ethernet per-port) — bukan
          // satu-satunya konfigurasi (chassis skalabel hingga 10RU/20 slot).
          ports: [{ type: 'pon', count: 16, label: 'Kartu XG801 slot 1+2 (16x port selectable XGS-PON/GPON/P2P Ethernet)' }],
          rows: 1,
          align: 'fill',
        },
        {
          ports: [{ type: 'sfp+', count: 8, label: 'Uplink slot 1+2 (10GE/2.5GE/GE multi-rate)' }],
          rows: 1,
          align: 'right',
          widthFraction: 0.18,
        },
        {
          // derived/approximate: datasheet asli QSFP-DD (100GE/40GE atau DAC
          // P2P/P2MP), schema tak punya tipe QSFP-DD terpisah — qsfp28
          // dipakai sebagai representasi terdekat, bukan kecocokan
          // form-factor persis.
          ports: [{ type: 'qsfp28', count: 4, label: 'Uplink QSFP-DD (direpresentasikan qsfp28, approximate)' }],
          rows: 1,
          align: 'right',
          widthFraction: 0.14,
        },
      ],
      // UNVERIFIED: tak ada tabel LED di sumber resmi.
      leds: [
        { label: 'PWR', color: 'green', position: 'left' },
        { label: 'ACT', color: 'green', position: 'left' },
      ],
    },
    rear: {
      // Power chassis (PSU/RU lengkap) UNVERIFIED — hanya power per-kartu
      // yang diketahui (max 130W, typical 90W per XG801). Pakai 1 slot
      // sebagai default minimal alih-alih menebak redundansi.
      blocks: [
        { type: 'psu-slot', count: 1 },
      ],
    },
    // brand accent tidak cukup dikenal untuk ditebak dengan keyakinan —
    // pakai abu-abu netral sama seperti BDCOM alih-alih menebak warna logo.
    brand: { accent: '#4A4A4A', chassis: '#161616', label: 'Calix', badge: 'stripe' },
  },

  // ── Raisecom ISCOM6860 (large chassis OLT) ──────────────────────────────
  {
    slug: 'raisecom-iscom6860',
    manufacturer: 'Raisecom',
    model: 'ISCOM6860',
    uHeight: 6,
    // §8.1 V (PDF resmi Raisecom via mirror unicorsa.com.ar): "443x237x266mm",
    // RU dinyatakan eksplisit oleh vendor sendiri.
    chassisMm: { widthMm: 443, depthMm: 237 },
    front: {
      portZones: [
        {
          // V jenis kartu, derived density (16 port/kartu = 112 interface
          // maksimum / 7 slot servis); konfigurasi representatif = 1x kartu
          // GPON 16-port di salah satu dari 7 slot servis, bukan
          // satu-satunya konfigurasi. Slot SMC (Switch/Main Control) x2 tak
          // dirinci portnya di sumber — UNVERIFIED, tidak dimasukkan sebagai
          // PortSpec.
          ports: [{ type: 'pon', count: 16, label: 'Kartu GPON 16-port, 1 dari 7 slot servis' }],
          rows: 1,
          align: 'fill',
        },
        {
          ports: [{ type: 'sfp+', count: 8, label: 'Uplink dedicated 10GE' }],
          rows: 1,
          align: 'right',
          widthFraction: 0.16,
        },
      ],
      // UNVERIFIED: tak ada tabel LED di sumber resmi.
      leds: [
        { label: 'PWR', color: 'green', position: 'left' },
        { label: 'ACT', color: 'green', position: 'left' },
      ],
    },
    rear: {
      // V: 2 slot PSU, 1+1 redundant, DC -48V (-38.4 to -57.6VDC).
      blocks: [
        { type: 'psu-slot', count: 2 },
      ],
    },
    // brand accent tidak cukup dikenal untuk ditebak dengan keyakinan —
    // pakai abu-abu netral sama seperti BDCOM alih-alih menebak warna logo.
    brand: { accent: '#4A4A4A', chassis: '#181818', label: 'Raisecom', badge: 'stripe' },
  },

  // ── Iskratel Lumia T14 (large chassis OLT, EU) ──────────────────────────
  {
    slug: 'iskratel-lumia-t14',
    manufacturer: 'Iskratel',
    model: 'Lumia T14',
    // V ("S&T Iskratel" via mirror hfctechnics.hu PDF resmi): "14 slots,
    // 14U", termasuk 1U dicadangkan untuk pendinginan. Brand kini bagian
    // Kontron d.o.o. sejak rebrand 2023/2024.
    uHeight: 14,
    // §8.1 chassisMm HILANGKAN — dua red flag di sumber (research/
    // 3d-device-specs-olt.md §8): (1) lebar mentah 482.6mm = persis 19 inci,
    // kemungkinan besar faceplate+rack-ears, bukan chassis body murni; (2)
    // tinggi mentah 572mm tak cocok matematis dengan "14U" yang dinyatakan
    // vendor sendiri (572/44.45≈12.87). Tidak diselesaikan/ditebak — 3D
    // builder pakai fallback generic body size.
    front: {
      portZones: [
        {
          // V jenis kartu & split ratio; konfigurasi representatif = mode
          // single-central-blade (1 slot switch pusat + 13 slot subscriber),
          // 1x kartu Lumia C16T combo di salah satu slot subscriber, bukan
          // satu-satunya konfigurasi.
          ports: [{ type: 'pon', count: 16, label: 'Kartu Lumia C16T combo GPON/XGS-PON/Combo, 1 dari 13 slot subscriber (mode single-central-blade)' }],
          rows: 1,
          align: 'fill',
        },
        {
          // derived: agregat central switch "2x100GE + 4x10G/25GE + 2xGE"
          // dipetakan per-jenis; medium "GE" polos diasumsikan optik,
          // UNVERIFIED medium fisik persis.
          ports: [
            { type: 'qsfp28', count: 2, label: 'Central switch uplink 100GE' },
            { type: 'sfp28', count: 4, label: 'Central switch uplink 10/25GE' },
            { type: 'sfp', count: 2, label: 'Central switch uplink GE' },
          ],
          rows: 1,
          align: 'right',
          widthFraction: 0.22,
        },
      ],
      // UNVERIFIED: tak ada tabel LED di sumber resmi.
      leds: [
        { label: 'PWR', color: 'green', position: 'left' },
        { label: 'ACT', color: 'green', position: 'left' },
      ],
    },
    rear: {
      // V: dual-rail DC redundant, -42 to -60VDC.
      blocks: [
        { type: 'psu-slot', count: 2 },
      ],
    },
    // brand accent tidak cukup dikenal untuk ditebak dengan keyakinan
    // (identitas visual sedang transisi ke Kontron) — pakai abu-abu netral
    // sama seperti BDCOM alih-alih menebak warna logo.
    brand: { accent: '#4A4A4A', chassis: '#1A1A1A', label: 'Iskratel', badge: 'stripe' },
  },

  // ── Generic OLT (forgeos/olt) ─────────────────────────────────────────────
  {
    slug: 'generic-olt',
    manufacturer: 'NetGeo',
    model: 'Generic OLT',
    nos: 'forgeos',
    uHeight: 1,
    front: {
      portZones: [
        {
          ports: [{ type: 'console-rj45', count: 1 }],
          rows: 1,
          align: 'left',
          widthFraction: 0.07,
        },
        {
          ports: [{ type: 'pon', count: 8 }],
          rows: 1,
          align: 'fill',
        },
        {
          ports: [{ type: 'sfp+', count: 2, label: 'UPL' }],
          rows: 1,
          align: 'right',
          widthFraction: 0.12,
        },
      ],
      leds: [
        { label: 'PWR', color: 'green', position: 'left' },
        { label: 'ALM', color: 'amber', position: 'left' },
      ],
    },
    rear: {
      blocks: [
        { type: 'psu-slot', count: 2 },
        { type: 'fan-tray', count: 1 },
      ],
    },
    brand: { accent: '#F5A623', chassis: '#1F1E1D', label: 'NetGeo', badge: 'stripe' },
  },

  // ── Skyworth Digital GN630V (desktop ONU) ───────────────────────────────
  {
    slug: 'skyworth-gn630v',
    manufacturer: 'Skyworth Digital',
    model: 'GN630V',
    uHeight: 1, // shelf/desktop placement di rak NetGeo, BUKAN RU vendor — ONU ini tabletop, tidak pernah diklaim rackmount.
    // §8.1 V(2nd), sumber tunggal non-domain-vendor (agregator, mengutip QIG
    // Skyworth), belum re-verifiable (halaman resmi en.skyworthdigital.com
    // dikonfirmasi TIDAK mencantumkan dimensi; re-fetch sumber sekunder 403).
    // Confidence lebih rendah dari V(2nd) biasa — lihat research/3d-device-specs-onu.md §1.
    chassisMm: { widthMm: 210, depthMm: 150 },
    front: {
      portZones: [
        {
          ports: [{ type: 'pon', count: 1, label: 'GPON' }],
          rows: 1,
          align: 'left',
          widthFraction: 0.16,
        },
        {
          ports: [{ type: 'rj45', count: 4, label: 'LAN' }],
          rows: 1,
          align: 'fill',
        },
        // 2x FXS RJ-11 (voice) — research/3d-device-specs-onu.md §1/§gap-1,
        // en.skyworthdigital.com (V).
        {
          ports: [{ type: 'fxs', count: 2, label: 'FXS' }],
          rows: 1,
          align: 'right',
          widthFraction: 0.1,
        },
        {
          ports: [{ type: 'usb', count: 1 }],
          rows: 1,
          align: 'right',
          widthFraction: 0.08,
        },
      ],
      // V (en.skyworthdigital.com, label eksplisit); warna derived (halaman
      // resmi tak punya kolom warna, klaim warna agregator TIDAK dipakai).
      leds: [
        { label: 'Power', color: 'green', position: 'left' },
        { label: 'PON', color: 'green', position: 'left' },
        { label: 'LOS', color: 'red', position: 'left' },
        { label: 'Internet', color: 'green', position: 'left' },
        { label: 'LAN1-4', color: 'green', position: 'left' },
        { label: '2.4G', color: 'blue', position: 'left' },
        { label: '5G', color: 'blue', position: 'left' },
        { label: 'WPS', color: 'amber', position: 'left' },
      ],
    },
    rear: {
      // Adaptor eksternal (bukan AC inlet internal) — dimodelkan sbg
      // psu-slot generik, schema tak punya tipe rear-block "DC jack".
      blocks: [{ type: 'psu-slot', count: 1 }],
    },
    // brand.chassis: derived dari saran research (chassis putih/abu umum
    // consumer CPE, tak ada foto resmi diperiksa). accent: tidak
    // ditentukan di research — netral abu, bukan tebakan logo brand.
    brand: { accent: '#9A9A96', chassis: '#F2F2F0', label: 'Skyworth', badge: 'stripe' },
  },

  // ── GL-COM GL-X822U-MTK (desktop ONU) ───────────────────────────────────
  {
    slug: 'glcom-gl-x822u-mtk',
    manufacturer: 'GL-COM',
    model: 'GL-X822U-MTK',
    uHeight: 1, // shelf/desktop di rak NetGeo, bukan RU vendor.
    // §8.1 V (gl-com.com/Products_Details/GL-X822U-MTK.html, dibuka
    // langsung): "205mm x155mm x30mm (W x D x H)".
    chassisMm: { widthMm: 205, depthMm: 155 },
    front: {
      portZones: [
        {
          ports: [{ type: 'pon', count: 1, label: 'GPON/EPON' }],
          rows: 1,
          align: 'left',
          widthFraction: 0.16,
        },
        {
          ports: [{ type: 'rj45', count: 4, label: 'LAN' }],
          rows: 1,
          align: 'fill',
        },
        // 1x FXS RJ-11 — research/3d-device-specs-onu.md §2/§gap-1,
        // gl-com.com (V).
        {
          ports: [{ type: 'fxs', count: 1, label: 'FXS' }],
          rows: 1,
          align: 'right',
          widthFraction: 0.06,
        },
        {
          ports: [{ type: 'usb', count: 2 }],
          rows: 1,
          align: 'right',
          widthFraction: 0.1,
        },
      ],
      // V (halaman resmi gl-com.com, label eksplisit); warna derived —
      // halaman produk dikonfirmasi eksplisit tak punya kolom warna sama sekali.
      leds: [
        { label: 'POWER', color: 'green', position: 'left' },
        { label: 'PON', color: 'green', position: 'left' },
        { label: 'LOS', color: 'red', position: 'left' },
        { label: 'LAN1', color: 'green', position: 'left' },
        { label: 'LAN2', color: 'green', position: 'left' },
        { label: 'LAN3', color: 'green', position: 'left' },
        { label: 'LAN4', color: 'green', position: 'left' },
        { label: 'FXS1', color: 'amber', position: 'left' },
        { label: '2.4G', color: 'blue', position: 'left' },
        { label: '5G', color: 'blue', position: 'left' },
      ],
    },
    rear: {
      blocks: [{ type: 'psu-slot', count: 1 }], // adaptor eksternal <18W
    },
    // brand.chassis derived (research: putih/abu terang umum, tak ada foto
    // resmi); accent tidak ditentukan — netral abu.
    brand: { accent: '#8F8F8B', chassis: '#EDEDED', label: 'GL-COM', badge: 'stripe' },
  },

  // ── Comtrend GRG-4361 (desktop ONU) ─────────────────────────────────────
  {
    slug: 'comtrend-grg-4361',
    manufacturer: 'Comtrend',
    model: 'GRG-4361',
    uHeight: 1, // shelf/desktop di rak NetGeo, bukan RU vendor.
    // §8.1 V (us.comtrend.com PDF resmi Datasheet-GRG-4361_V1.1, diunduh +
    // pdftotext langsung): "148mm(L) x 98mm(W) x46mm(H)".
    chassisMm: { widthMm: 148, depthMm: 98 },
    front: {
      portZones: [
        {
          ports: [{ type: 'pon', count: 1, label: 'XGS-PON' }],
          rows: 1,
          align: 'left',
          widthFraction: 0.3,
        },
        {
          ports: [{ type: 'rj45', count: 1, label: '10GE LAN' }],
          rows: 1,
          align: 'fill',
        },
        // 1x FXS RJ-11 — research/3d-device-specs-onu.md §3/§gap-1,
        // us.comtrend.com PDF resmi (V).
        {
          ports: [{ type: 'fxs', count: 1, label: 'FXS' }],
          rows: 1,
          align: 'right',
          widthFraction: 0.15,
        },
      ],
      // V (dua versi datasheet, V1.1 & V3.0, cocok identik); warna
      // UNVERIFIED di kedua versi — derived. Label "GPON" dipertahankan apa
      // adanya walau device ini XGS-PON — anomali penamaan vendor sendiri,
      // tidak dikoreksi (lihat research/3d-device-specs-onu.md §3).
      leds: [
        { label: 'Power', color: 'green', position: 'left' },
        { label: 'GPON', color: 'green', position: 'left' },
        { label: '10GLAN', color: 'green', position: 'left' },
        { label: 'Phone', color: 'amber', position: 'left' },
      ],
    },
    rear: {
      blocks: [{ type: 'psu-slot', count: 1 }], // adaptor eksternal 12VDC/1.0A
    },
    brand: { accent: '#8C8C88', chassis: '#F0F0EE', label: 'Comtrend', badge: 'stripe' },
  },

  // ── Hitron Technologies NOVA2208 (MDU ONU, rack-or-wall shelf) ──────────
  {
    slug: 'hitron-nova2208',
    manufacturer: 'Hitron Technologies',
    model: 'NOVA2208',
    // uHeight 1 = penempatan shelf/rak-parsial di NetGeo, BUKAN klaim RU
    // literal vendor (vendor tak pernah sebut "1U", tinggi 44.5mm≈1U
    // kebetulan matematis). PERINGATAN: lebar chassis 250mm — device ini
    // TIDAK mengisi lebar rak 19" penuh (482.6mm), hanya ~52%. Vendor
    // menyatakan "Rack- and wall-mounting options" (V) tapi lewat
    // bracket/shelf parsial, bukan chassis 19" full-width seperti device
    // OLT rackmount lain di pack ini.
    uHeight: 1,
    // §8.1 V (us.hitrontech.com/wp-content/uploads/2023/10/DS-NOVA2208.pdf,
    // diunduh + pdftotext langsung): "Dimensions: 250mm(W)x44.5mm(H)x300mm(D)".
    chassisMm: { widthMm: 250, depthMm: 300 },
    front: {
      portZones: [
        {
          ports: [{ type: 'pon', count: 1, label: '10G-EPON/XG-PON/XGS-PON selectable' }],
          rows: 1,
          align: 'left',
          widthFraction: 0.14,
        },
        {
          ports: [{ type: 'rj45', count: 1, label: 'Uplink 1/2.5/5/10GBASE-T' }],
          rows: 1,
          align: 'right',
          widthFraction: 0.1,
        },
        {
          ports: [{ type: 'rj45', count: 8, label: 'LAN 100M/1G/2.5GBASE-T' }],
          rows: 2,
          align: 'fill',
        },
        // 8x FXS RJ-11 — jumlah terbanyak di batch ini — research/
        // 3d-device-specs-onu.md §4/§gap-1, us.hitrontech.com PDF resmi (V).
        {
          ports: [{ type: 'fxs', count: 8, label: 'FXS' }],
          rows: 2,
          align: 'right',
          widthFraction: 0.22,
        },
      ],
      // V (PDF resmi, bagian "Mechanical") — 4 LED generik tanpa breakdown
      // per-port. Warna derived (tak ada kolom warna di datasheet).
      leds: [
        { label: 'Power', color: 'green', position: 'left' },
        { label: 'Alarm', color: 'red', position: 'left' },
        { label: 'Link', color: 'green', position: 'left' },
        { label: 'Activity', color: 'amber', position: 'left' },
      ],
    },
    rear: {
      blocks: [{ type: 'iec-inlet', count: 1 }], // V: PSU internal IEC320-C14, 36W
    },
    brand: { accent: '#8A8A86', chassis: '#E8E8E6', label: 'Hitron', badge: 'stripe' },
  },

  // ── Actiontec XG-99M (desktop/wall ONU) ─────────────────────────────────
  {
    slug: 'actiontec-xg-99m',
    manufacturer: 'Actiontec Electronics',
    model: 'XG-99M',
    uHeight: 1, // shelf/desktop-or-wall di rak NetGeo, bukan RU vendor ("Desktop mounting & wall mounting" eksplisit, V).
    // §8.1 V (PDF resmi via Wayback Machine snapshot 2024-12-03 — URL live
    // newserv.actiontec.com connection-refused saat diakses langsung, bukan
    // bukti dokumen hilang): "208mm x 150mm x 35mm(W x D x H)".
    chassisMm: { widthMm: 208, depthMm: 150 },
    front: {
      portZones: [
        {
          ports: [{ type: 'pon', count: 1, label: 'XGS-PON' }],
          rows: 1,
          align: 'left',
          widthFraction: 0.2,
        },
        {
          ports: [
            { type: 'rj45', count: 1, label: '10G Base-T' },
            { type: 'rj45', count: 1, label: '1G Base-T' },
          ],
          rows: 1,
          align: 'fill',
        },
        // Hingga 2x FXS RJ-11 — research/3d-device-specs-onu.md §6/§gap-1,
        // PDF resmi via Wayback Machine (V). 1x interface MoCA masih tidak
        // direpresentasikan — tidak ada PortType yang cocok (§gap-3).
        {
          ports: [{ type: 'fxs', count: 2, label: 'FXS' }],
          rows: 1,
          align: 'right',
          widthFraction: 0.12,
        },
      ],
      // V, baru ditemukan sesi riset ini (tidak ada di pass sebelumnya) —
      // 7 label terbanyak di batch ini. Warna derived (datasheet hanya label).
      leds: [
        { label: 'POWER', color: 'green', position: 'left' },
        { label: 'OPTICAL', color: 'green', position: 'left' },
        { label: 'LAN', color: 'green', position: 'left' },
        { label: 'UPDATE', color: 'amber', position: 'left' },
        { label: 'ALARM', color: 'red', position: 'left' },
        { label: 'POTS', color: 'green', position: 'left' },
        { label: 'MOCA', color: 'blue', position: 'left' },
      ],
    },
    rear: {
      blocks: [{ type: 'psu-slot', count: 1 }], // adaptor eksternal +16V, <18W
    },
    brand: { accent: '#87877F', chassis: '#EAEAE8', label: 'Actiontec', badge: 'stripe' },
  },

  // ── Vantiva FXA530Z / platform N670 (desktop/wall ONU) ──────────────────
  {
    slug: 'vantiva-fxa530z',
    manufacturer: 'Vantiva',
    model: 'FXA530Z (N670)',
    uHeight: 1, // shelf/desktop-or-wall di rak NetGeo, bukan RU vendor.
    // §8.1 V(2nd) — konten jelas terbitan Vantiva (footer platform "N670")
    // tapi hosting mirror distributor amt.com, bukan domain vantiva.com
    // langsung (pola sama [[3d-device-specs-firewall]] §3/§4): "148mm W x
    // 105mm D x 40.75mm H".
    chassisMm: { widthMm: 148, depthMm: 105 },
    front: {
      portZones: [
        {
          // Data-only, sengaja tanpa WiFi (arsitektur access+WiFi terpisah).
          ports: [{ type: 'pon', count: 1, label: 'XGS-PON' }],
          rows: 1,
          align: 'left',
          widthFraction: 0.3,
        },
        {
          ports: [{ type: 'rj45', count: 1, label: '10G RJ45 auto-sensing' }],
          rows: 1,
          align: 'fill',
        },
      ],
      // V(2nd) — datasheet: "Front: Power, PON, LAN. Rear: Ethernet
      // Link/Speed" — device ini punya LED di dua panel berbeda; LED rear
      // dimodelkan di rear.blocks led-group (lihat pola juniper-qfx5120-48y
      // di atas). Warna derived (tak ada kolom warna).
      leds: [
        { label: 'Power', color: 'green', position: 'left' },
        { label: 'PON', color: 'green', position: 'left' },
        { label: 'LAN', color: 'green', position: 'left' },
      ],
    },
    rear: {
      blocks: [
        { type: 'psu-slot', count: 1 }, // adaptor eksternal 12VDC/1A
        {
          type: 'led-group',
          leds: [{ label: 'Ethernet Link/Speed', color: 'green', position: 'left' }],
        },
      ],
    },
    brand: { accent: '#84847C', chassis: '#F5F5F3', label: 'Vantiva', badge: 'stripe' },
  },

  // ── Humax BGW320-500 (AT&T-branded, desktop tower ONU) ──────────────────
  {
    slug: 'humax-bgw320-500',
    // Manufacturer = Humax (dibuat/di-file Humax per FCC grantee O6Z) —
    // device ini dijual/dipasarkan berbranding AT&T, TIDAK disembunyikan:
    // lihat datasheet_note pack JSON untuk detail branding AT&T.
    manufacturer: 'Humax',
    model: 'BGW320-500',
    uHeight: 1, // shelf/desktop tower vertikal di rak NetGeo, bukan RU vendor.
    // §8.1 V(2nd) via FCC filing (dokumen asli Humax dari FCC ID O6ZBGW320,
    // bukan paraphrase pihak ketiga — didownload via redirect fccid.io ke
    // mirror PDF manual 30 halaman): lebar 200mm x kedalaman 100mm (tinggi
    // 188/191mm terpisah).
    chassisMm: { widthMm: 200, depthMm: 100 },
    front: {
      portZones: [
        {
          // V: 1x PON (cage SFP/SFP+) + 1x RJ45 WAN — device ini punya dua
          // opsi uplink fisik berbeda (optik dan tembaga).
          ports: [
            { type: 'pon', count: 1, label: 'WAN optical (SFP/SFP+ cage)' },
            { type: 'rj45', count: 1, label: 'WAN copper' },
          ],
          rows: 1,
          align: 'left',
          widthFraction: 0.3,
        },
        {
          ports: [
            { type: 'rj45', count: 3, label: 'LAN 1G' },
            { type: 'rj45', count: 1, label: 'LAN 2.5/5G' },
          ],
          rows: 1,
          align: 'fill',
        },
        // 1x FXS RJ-11 — research/3d-device-specs-onu.md §7/§gap-1, FCC
        // filing O6ZBGW320 (V(2nd)).
        {
          ports: [{ type: 'fxs', count: 1, label: 'FXS' }],
          rows: 1,
          align: 'right',
          widthFraction: 0.06,
        },
        {
          ports: [{ type: 'usb', count: 1 }],
          rows: 1,
          align: 'right',
          widthFraction: 0.08,
        },
      ],
      // CATATAN JUJUR: dokumen riset (research/3d-device-specs-onu.md) hanya
      // menyisakan baris ringkasan untuk SKU ini ("Service/WPS/PowerJack/
      // Ethernet, warna+state penuh V") — bagian riset detail per-SKU untuk
      // Humax hilang dari catatan sumber (gap di dokumen sumber itu sendiri,
      // BUKAN dikarang di sini). 4 label kategori dipakai apa adanya
      // ("PowerJack" ditafsirkan sbg LED Power di dekat jack) tapi
      // warna/state SEBENARNYA tidak tersimpan di catatan — ditandai derived
      // di sini meski catatan sumber mengklaim "penuh V" untuk warna,
      // karena nilai aktualnya tidak bisa dikutip ulang dari ringkasan saja.
      leds: [
        { label: 'Service', color: 'green', position: 'left' },
        { label: 'WPS', color: 'amber', position: 'left' },
        { label: 'Power', color: 'green', position: 'left' },
        { label: 'Ethernet', color: 'green', position: 'left' },
      ],
    },
    rear: {
      blocks: [{ type: 'psu-slot', count: 1 }],
    },
    // brand.chassis derived — tidak ada foto produk diverifikasi sesi ini
    // (bagian riset Humax hilang, lihat catatan di atas); netral abu umum.
    brand: { accent: '#7D7D77', chassis: '#EFEFEC', label: 'Humax', badge: 'stripe' },
  },

  // ── Askey RTF6105VW (desktop ONU, data paling parsial di batch) ─────────
  {
    slug: 'askey-rtf6105vw',
    manufacturer: 'Askey Computer Corp.',
    model: 'RTF6105VW',
    uHeight: 1, // shelf/desktop vertikal (klaim forum komunitas Movistar, V(2nd)/low-confidence) di rak NetGeo — vendor tak sebut RU sama sekali.
    // §8.1 chassisMm DIHILANGKAN (UNVERIFIED) — 4 jalur pencarian buntu
    // sesi riset: (1) PDF datasheet resmi askey.com.tw tak punya tabel
    // dimensi, (2) probe FCC ID grantee H8N pola H8NRTF6105VW 404, (3)
    // database ANATEL Brazil tak punya entri RTF6105VW spesifik (model
    // Askey lain ada), (4) forum Movistar hanya deskripsi kualitatif tanpa
    // angka. Tidak ditebak — preseden barracuda-cloudgen-f400 &
    // calix-axos-e7-2 di atas. 3D builder pakai fallback generic body size.
    front: {
      portZones: [
        {
          ports: [{ type: 'pon', count: 1, label: '10G EPON atau XGS-PON (selectable UNVERIFIED)' }],
          rows: 1,
          align: 'left',
          widthFraction: 0.2,
        },
        {
          ports: [
            { type: 'rj45', count: 4, label: '1000 Base-T' },
            { type: 'rj45', count: 4, label: '2.5G Base-T' },
          ],
          rows: 2,
          align: 'fill',
        },
        // 2x FXS RJ-11 — research/3d-device-specs-onu.md §8/§gap-1,
        // askey.com.tw PDF resmi (V).
        {
          ports: [{ type: 'fxs', count: 2, label: 'FXS' }],
          rows: 1,
          align: 'right',
          widthFraction: 0.12,
        },
      ],
      // LED UNVERIFIED total — datasheet resmi vendor sama sekali tidak
      // menyebut LED. Satu-satunya isyarat forum Movistar ("4 LED di
      // depan", tanpa label/warna) TIDAK dipakai sebagai dasar array ini —
      // pola minimal generik dipakai sebagai gantinya (keputusan leader).
      leds: [{ label: 'PWR', color: 'green', position: 'left' }],
    },
    rear: {
      blocks: [{ type: 'psu-slot', count: 1 }], // adaptor eksternal 12VDC/3.5A
    },
    // brand: UNVERIFIED total — nol foto produk maupun deskripsi warna dari
    // sumber manapun (beda dari 7 SKU lain yang setidaknya punya asumsi
    // "putih/abu consumer CPE umum"). Abu netral placeholder, BUKAN tebakan
    // warna produk asli — tunggu foto produk untuk final art.
    brand: { accent: '#707070', chassis: '#707070', label: 'Askey', badge: 'stripe' },
  },

  // ── Padtec Combiner 10Gb/s ODU-XC 8x2 G.709 (1U standalone OTN muxponder) ──
  {
    slug: 'padtec-combiner-10g-odu-xc',
    manufacturer: 'Padtec',
    model: 'Combiner 10Gb/s ODU-XC 8x2 G.709',
    uHeight: 1,
    // §8.1 V (manual teknis resmi Padtec REV 8, via filing regulator ANATEL
    // Brasil): "Largura [mm] 1U: 440", "Profundidade [mm] 1U: 242,4" — bukan
    // 482.6mm (19in penuh), jadi kemungkinan besar lebar body chassis asli.
    chassisMm: { widthMm: 440, depthMm: 242.4 },
    front: {
      portZones: [
        {
          // V jenis/jumlah (§4.2.7/4.2.8 manual); urutan kiri-kanan
          // UNVERIFIED (diagram panel depan tak ter-parse teks).
          ports: [{ type: 'sfp', count: 8, label: 'Client 1-8' }],
          rows: 1,
          align: 'fill',
        },
        {
          // V: 2x network OTU2 tunable C/L-band. Aproksimasi eksplisit ke
          // sfp+ — manual sebut modul bisa SFP atau XFP, kosakata target tak
          // punya XFP terpisah.
          ports: [{ type: 'sfp+', count: 2, label: 'OTU2 network' }],
          rows: 1,
          align: 'right',
          widthFraction: 0.16,
        },
        {
          // interpretasi kami: port "GL" (Gerencia Local) -> console, BUKAN
          // label vendor eksplisit "console".
          ports: [{ type: 'console-rj45', count: 1, label: 'GL' }],
          rows: 1,
          align: 'right',
          widthFraction: 0.07,
        },
        {
          // interpretasi kami: port "DCN" (remote mgmt network) -> mgmt,
          // BUKAN label vendor eksplisit "mgmt".
          ports: [{ type: 'mgmt-rj45', count: 1, label: 'DCN' }],
          rows: 1,
          align: 'right',
          widthFraction: 0.07,
        },
      ],
      // V label+warna eksplisit (§4.2.11 manual) — satu-satunya SKU batch
      // ini dgn LED nama asli lengkap. Jumlah presisi LOS/LASEROFF OTU2
      // UNVERIFIED (1 gabungan atau 2 terpisah per port network) — grup jadi
      // satu label range spt konvensi LAN1-4 di entri lain.
      leds: [
        { label: 'POWER', color: 'green', position: 'left' },
        { label: 'LOS OTU2 1-2', color: 'red', position: 'left' },
        { label: 'LASER OFF OTU2 1-2', color: 'red', position: 'left' },
        { label: 'LOS Client 1-8', color: 'red', position: 'left' },
        { label: 'LASEROFF Client 1-8', color: 'red', position: 'left' },
      ],
    },
    rear: {
      // V (§4.2.9): -48VDC nominal (min -60V, maks -36V), single DC
      // terminal feed di belakang — tak ada redundansi disebutkan.
      blocks: [{ type: 'psu-slot', count: 1 }],
    },
    // brand: UNVERIFIED total — manual teknis berisi diagram/teks, tanpa foto
    // produk berwarna yang berhasil diperiksa. Abu netral placeholder.
    brand: { accent: '#707070', chassis: '#707070', label: 'Padtec', badge: 'stripe' },
  },

  // ── Ekinops EKINOPS360 C200HC (modular DWDM/OTN chassis, 2RU) ───────────
  {
    slug: 'ekinops-ekinops360-c200hc',
    manufacturer: 'Ekinops',
    model: 'EKINOPS360 C200HC',
    uHeight: 2,
    // §8.1 V (datasheet resmi V.12, 10/2025, via Wayback Machine — ekinops.com
    // 403 semua path langsung): "Width 442mm", "Depth 269mm (DC version)".
    // Bukan 482.6mm, jadi kemungkinan besar lebar body asli. Varian AC punya
    // depth beda (442mm) — tidak dimodelkan, SKU ini pakai versi DC.
    chassisMm: { widthMm: 442, depthMm: 269 },
    front: {
      // UNVERIFIED total, sengaja dibiarkan minimal — chassis modular murni
      // (6 slot service/photonic + 1 slot manajemen "any card-any slot"),
      // datasheet kartu manajemen PM_MNGT4-2 tak ditemukan (nol snapshot
      // Wayback). Foto produk resmi kualitatif menunjukkan kemungkinan
      // RJ45+USB di kartu manajemen TAPI riset eksplisit menandai itu TIDAK
      // dipakai sbg dasar PortSpec presisi — jangan mengarang jumlah/jenis.
      portZones: [
        {
          ports: [{ type: 'mgmt-rj45', count: 1, label: 'MGMT (kartu PM_MNGT4-2, UNVERIFIED)' }],
          rows: 1,
          align: 'left',
          widthFraction: 0.12,
        },
      ],
      // UNVERIFIED — foto hanya beri indikator hijau tanpa label terbaca;
      // pola minimal generik dipakai (keputusan leader).
      leds: [{ label: 'PWR', color: 'green', position: 'left' }],
    },
    rear: {
      // V (tabel FEATURES datasheet): -48VDC atau opsi AC, 60W/72W kosong,
      // maks 500W. Redundansi PSU UNVERIFIED — pakai 1 sbg default minimal
      // alih-alih menebak.
      blocks: [{ type: 'psu-slot', count: 1 }],
    },
    // brand: derived dari foto produk resmi (Wayback) — body silver/abu
    // metalik anodized, aksen biru logo "EKINOPS" pada handle ejector kartu.
    brand: { accent: '#1B5FA8', chassis: '#B7BBC0', label: 'Ekinops', badge: 'stripe' },
  },

  // ── Fujitsu / 1Finity Americas 1FINITY T310 (transport blade, 1U) ───────
  {
    slug: 'fujitsu-1finity-t310',
    manufacturer: 'Fujitsu',
    model: '1FINITY T310',
    uHeight: 1,
    // §8.1 chassisMm DIHILANGKAN — satu-satunya angka lebar (483mm) adalah
    // "Dimensions H×W×D: 1.75 × 19 × 17.72in (44.4×483×450mm)", TAPI datasheet
    // resmi (via Wayback, snapshot 2021-02-28) SENDIRI eksplisit menyatakan
    // "W = 19in or 23in with mounting rails" — itu faceplate/rail, bukan body
    // (preseden Barracuda/Calix/DZS/Iskratel/Askey: field kosong lebih baik
    // drpd angka yang dibantah sumbernya sendiri). Depth 450mm juga
    // bersyarat ("<23.6in/600mm with fiber management"). 3D builder pakai
    // fallback generic body size.
    front: {
      portZones: [
        {
          // V jumlah/jenis (tabel "Line Optics"); form factor asli 2x
          // CFP2-ACO coherent — dipetakan qsfp28 sbg aproksimasi eksplisit,
          // CFP2 tak ada di union PortType. Urutan kiri-kanan UNVERIFIED.
          ports: [{ type: 'qsfp28', count: 2, label: 'Network (CFP2-ACO, aproksimasi)' }],
          rows: 1,
          align: 'right',
          widthFraction: 0.14,
        },
        {
          // V: 20x client 10GbE/OC-192/OTU2/OTU2e, SFP+ SR/LR/ER.
          ports: [{ type: 'sfp+', count: 20, label: 'Client' }],
          rows: 2,
          align: 'fill',
        },
        {
          // V: "Local Management Port (LMP): 1x 10/100Mbps Ethernet RJ-45".
          ports: [{ type: 'mgmt-rj45', count: 1, label: 'LMP' }],
          rows: 1,
          align: 'left',
          widthFraction: 0.07,
        },
        {
          // V: "Management Port (LCN): 2x GbE SFP" — dipetakan sfp generik
          // meski fungsinya manajemen (bentuk fisik SFP).
          ports: [{ type: 'sfp', count: 2, label: 'LCN' }],
          rows: 1,
          align: 'left',
          widthFraction: 0.09,
        },
      ],
      // V untuk kategori nama (tabel "Base System": "Front LEDs: System
      // Status, Alarm Severity, and Port"), UNVERIFIED untuk rincian
      // jumlah/warna per kategori.
      leds: [
        { label: 'System Status', color: 'green', position: 'left' },
        { label: 'Alarm Severity', color: 'red', position: 'left' },
        { label: 'Port', color: 'green', position: 'left' },
      ],
    },
    rear: {
      // V (tabel Power): -48VDC nominal (-40V..-57V), dual-feed fixed DC PSU,
      // konsumsi 224W typical (satu-satunya SKU batch ini dgn angka typical
      // eksplisit terpisah dari maks).
      blocks: [{ type: 'psu-slot', count: 2 }],
    },
    // brand: UNVERIFIED total — datasheet PDF hanya diagram garis/ilustrasi
    // hitam-putih, tanpa foto produk berwarna.
    brand: { accent: '#707070', chassis: '#707070', label: 'Fujitsu', badge: 'stripe' },
  },

  // ── Adtran (eks-ADVA) FSP 3000 CloudConnect SH1R (modular OTN shelf, 1U) ─
  {
    slug: 'adtran-fsp3000-cloudconnect-sh1r',
    manufacturer: 'Adtran',
    model: 'FSP 3000 CloudConnect SH1R',
    uHeight: 1,
    // §8.1 V, DENGAN catatan resmi vendor: tabel "Dimensions (W x D x H):
    // 430mm x 540mm* x 1RU" — tanda bintang (*) di datasheet berarti "With
    // front cover" (footnote eksplisit halaman sama), jadi 540mm TERMASUK
    // front cover, bukan body polos-tanpa-cover. Lebar 430mm bukan 482.6mm,
    // jadi kemungkinan besar lebar body asli. Sumber: techgardens.com mirror
    // datasheet resmi ADVA 2017 (V(2nd), kop surat ADVA verbatim).
    chassisMm: { widthMm: 430, depthMm: 540 },
    front: {
      portZones: [
        {
          // V: kartu "Software-defined 400G transponder" (representatif, 1
          // dari 2 slot traffic) — 4x client 100GbE/OTU4 QSFP28.
          ports: [{ type: 'qsfp28', count: 4, label: 'Client (kartu 400G transponder)' }],
          rows: 1,
          align: 'left',
          widthFraction: 0.24,
        },
        {
          // V jumlah; form factor pluggable UNVERIFIED utk sisi network kartu
          // ini (beda dari kartu lain yg eksplisit sebut QSFP28) — dipetakan
          // qsfp28 sbg estimasi konsisten kelas kecepatan.
          ports: [{ type: 'qsfp28', count: 2, label: 'Network DWDM (form factor UNVERIFIED)' }],
          rows: 1,
          align: 'left',
          widthFraction: 0.13,
        },
        {
          // V level platform; jumlah presisi per shelf UNVERIFIED (2 slot
          // manajemen redundan bisa berarti port ini 1x atau 2x).
          ports: [{ type: 'mgmt-rj45', count: 1, label: 'Mgmt (GUI/CLI/SNMP)' }],
          rows: 1,
          align: 'right',
          widthFraction: 0.08,
        },
        {
          ports: [{ type: 'console-rj45', count: 1, label: 'Serial CLI' }],
          rows: 1,
          align: 'right',
          widthFraction: 0.07,
        },
      ],
      // UNVERIFIED total — datasheet 5 halaman (teknis+platform) sama sekali
      // tak sebut kata "LED"/"indicator". Pola minimal generik (keputusan
      // leader).
      leds: [{ label: 'PWR', color: 'green', position: 'left' }],
    },
    rear: {
      // V (shelf SH1R spesifik): "Typical/maximum power: 380W/460W", AC/DC
      // redundant PSU.
      blocks: [{ type: 'psu-slot', count: 2 }],
    },
    // brand: UNVERIFIED total — tak ada foto produk berwarna diperiksa sesi
    // ini (produk legacy ADVA, warna korporat aktual belum dipastikan sama
    // dgn brand Adtran router lain di pack OLT).
    brand: { accent: '#707070', chassis: '#707070', label: 'Adtran', badge: 'stripe' },
  },

  // ── Batch wireless-AP (Sesi riset 2026-09-06, research/3d-device-specs-wireless-ap.md) ──
  // Semua 7 SKU: ceiling/wall-mount, BUKAN rackmount. uHeight: 1 adalah
  // penempatan shelf di rak NetGeo (keputusan leader), bukan RU vendor.
  // Tak satu pun dari 7 SKU ini punya konektor antena eksternal (semua
  // internal built-in) — gap kosakata RP-SMA/N-type dari research tidak
  // terpakai batch ini.

  // ── Grandstream Networks GWN7660 ─────────────────────────────────────────
  {
    slug: 'grandstream-gwn7660',
    manufacturer: 'Grandstream Networks',
    model: 'GWN7660',
    uHeight: 1,
    // §8.1 V (grandstream.com datasheet resmi GWN7660 rev 3.2023.05,
    // pdftotext): "Unit Dimension: 180.4mmx180.4mmx40.8mm" — device bundar,
    // vendor memberi W=D identik (bukan diameter tunggal eksplisit, tapi
    // bentuk bundar dikonfirmasi dari foto produk resmi).
    chassisMm: { widthMm: 180.4, depthMm: 180.4 },
    front: {
      portZones: [
        {
          // V, user manual resmi Table 14: "NET/PoE" 10/100/1000Mbps,
          // menerima PoE/PoE+ 802.3af/at.
          ports: [{ type: 'rj45', count: 1, label: 'NET/PoE', poe: true }],
          rows: 1,
          align: 'left',
          widthFraction: 0.4,
        },
        {
          // V: "NET" 10/100/1000Mbps ke router/AP lain, bukan PoE-in.
          ports: [{ type: 'rj45', count: 1, label: 'NET' }],
          rows: 1,
          align: 'fill',
        },
      ],
      // V (user manual Table 15 "LED Patterns", family-wide GWN76XX): 3
      // tri-color LED (device tracking/status), pattern lengkap: OFF ·
      // blinking green (FW update) · solid green (update sukses) ·
      // blinking red (factory reset) · solid red (update gagal) · solid
      // purple (belum diprovisioning) · blinking blue (provisioning) ·
      // solid blue (provisioned) · blinking white (locate AP) · yellow
      // (mesh disconnect). `Led.color` sekarang punya 'purple'/'yellow'
      // (ditambahkan batch router 2026-09-06) — 3 entri di bawah dikoreksi
      // ke warna asli vendor: not-provisioned (purple), provisioned (blue),
      // mesh-disconnect (yellow).
      leds: [
        { label: 'Status (not-provisioned)', color: 'purple', position: 'left' },
        { label: 'Status (provisioned)', color: 'blue', position: 'left' },
        { label: 'Status (mesh disconnect)', color: 'yellow', position: 'left' },
      ],
    },
    rear: {
      blocks: [{ type: 'psu-slot', count: 1 }], // PoE-in via NET/PoE, tanpa PSU/AC input terpisah
    },
    // brand: derived, low-confidence — tak ada inspeksi foto produk detail
    // sesi riset ini; AP ceiling putih polos khas kelas ini.
    brand: { accent: '#C7C7C5', chassis: '#F2F2F0', label: 'Grandstream', badge: 'stripe' },
  },

  // ── EnGenius Technologies ECW230 ─────────────────────────────────────────
  {
    slug: 'engenius-ecw230',
    manufacturer: 'EnGenius',
    model: 'ECW230',
    uHeight: 1,
    // §8.1 V, dari PDF resmi engeniustech.com/.../ECW230-Datasheet.pdf (doc
    // v1.1 09212023): "Dimensions: 205 x 205 x 33.2 mm". Catatan diskrepansi
    // jujur: halaman toko resmi store.engeniustech.com menyebut 210x210mm
    // (height identik 33.2mm) — PDF datasheet dipakai sbg acuan (dokumen
    // teknis versi-terkontrol vs halaman marketing), diskrepansi 5mm dicatat
    // apa adanya, bukan dirujuk ganda.
    chassisMm: { widthMm: 205, depthMm: 205 },
    front: {
      portZones: [
        {
          // V, PDF resmi "Physical Interfaces": satu-satunya port jaringan,
          // 2.5GbE, menerima PoE+ 802.3at.
          ports: [{ type: 'rj45', count: 1, label: '2.5GE (PoE+)', poe: true }],
          rows: 1,
          align: 'fill',
        },
      ],
      // V nama LED (PDF resmi daftar "LED indicators": Power/LAN/2.4GHz/
      // 5GHz, 4 LED terpisah) — warna/pola nyala per-LED TIDAK dirinci di
      // datasheet ini (gap, bukan ditebak). Warna 'white' di bawah adalah
      // placeholder UNVERIFIED, konsisten pola batch server (Inspur/QCT UID).
      leds: [
        { label: 'Power', color: 'white', position: 'left' },
        { label: 'LAN', color: 'white', position: 'left' },
        { label: '2.4GHz', color: 'white', position: 'left' },
        { label: '5GHz', color: 'white', position: 'left' },
      ],
    },
    rear: {
      blocks: [{ type: 'psu-slot', count: 1 }], // PoE-in atau adapter 12VDC/2A opsional
    },
    // brand: derived, low-confidence — tak ada inspeksi foto produk detail
    // sesi riset ini.
    brand: { accent: '#C7C7C5', chassis: '#F5F5F3', label: 'EnGenius', badge: 'stripe' },
  },

  // ── Araknis Networks (Snap One) AN-520-AP-I ──────────────────────────────
  {
    slug: 'araknis-an-520-ap-i',
    manufacturer: 'Araknis Networks',
    model: 'AN-520-AP-I',
    uHeight: 1,
    // §8.1 V, datasheet resmi snapav.com AN-520-AP-I_Datasheet_Final.pdf:
    // "Physical Size: 7.33in x 7.33in x 1.56in — 18.61cm x 18.61cm x 3.95cm"
    // (device bundar, diameter). Mapping W=D=186.1mm apa adanya.
    chassisMm: { widthMm: 186.1, depthMm: 186.1 },
    front: {
      portZones: [
        {
          // V, datasheet: "2.5G BASE-T Ethernet". PoE 802.3af/at diterima
          // pada "salah satu" RJ45 — datasheet tak merinci port persis mana,
          // dipetakan ke port 2.5G ini sbg asumsi paling umum (uplink lebih
          // cepat = PoE-in), bukan klaim vendor eksplisit per-port.
          ports: [{ type: 'rj45', count: 1, label: '2.5G BASE-T', poe: true }],
          rows: 1,
          align: 'left',
          widthFraction: 0.5,
        },
        {
          ports: [{ type: 'rj45', count: 1, label: '1G BASE-T' }],
          rows: 1,
          align: 'fill',
        },
      ],
      // LED — V UNTUK MODEL SIBLING OUTDOOR AN-520-AP-O (installation guide
      // resmi help.snapone.com), TIDAK dikonfirmasi identik untuk AN-520-AP-I
      // (indoor) karena installation guide -I sendiri gagal diakses sesi
      // riset. Dicatat apa adanya, bukan diklaim identik. State "heater
      // aktif" pada sumber -O sengaja dihilangkan (spesifik outdoor, -I tak
      // punya heater).
      leds: [
        { label: 'Power (dari sibling -O, lihat komentar)', color: 'blue', position: 'left' },
        { label: 'Radio 2.4/5GHz (dari sibling -O, lihat komentar)', color: 'blue', position: 'left' },
        { label: 'LAN1/LAN2 (dari sibling -O, lihat komentar)', color: 'blue', position: 'left' },
      ],
    },
    rear: {
      blocks: [{ type: 'psu-slot', count: 1 }], // PoE 802.3af/at atau DC 12V/3A adapter
    },
    // brand: derived, low-confidence — tak ada inspeksi foto produk detail
    // sesi riset ini.
    brand: { accent: '#C7C7C5', chassis: '#F0F0EE', label: 'Araknis Networks', badge: 'stripe' },
  },

  // ── Cambium Networks XV3-8 ────────────────────────────────────────────────
  {
    slug: 'cambium-xv3-8',
    manufacturer: 'Cambium Networks',
    model: 'XV3-8',
    uHeight: 1,
    // §8.1 V, PDF resmi brandcentral.cambiumnetworks.com XV3-8 Data Sheet:
    // "Dimensions: 235 mm x 235 mm x 42 mm". Dua angka identik (235x235) —
    // kemungkinan body persegi (bukan bundar spt Grandstream/EnGenius/
    // Araknis di atas), bentuk fisik pasti belum dikonfirmasi lewat foto.
    chassisMm: { widthMm: 235, depthMm: 235 },
    front: {
      portZones: [
        {
          // V, PDF resmi: "IEEE 802.3bz 100/1000/2500/5000 Mbps" — dicatat
          // apa adanya dari sumber (802.3bz standar resminya mentok 2.5/5G,
          // tapi PDF menyebut rentang hingga 5000Mbps).
          ports: [{ type: 'rj45', count: 1, label: '802.3bz multi-gig', poe: true }],
          rows: 1,
          align: 'left',
          widthFraction: 0.4,
        },
        {
          ports: [{ type: 'rj45', count: 1, label: '802.3 GbE' }],
          rows: 1,
          align: 'left',
          widthFraction: 0.35,
        },
        {
          ports: [{ type: 'usb', count: 1, label: 'USB 3.0' }],
          rows: 1,
          align: 'fill',
        },
      ],
      // V nama saja ("Multi-color status LEDs") — jumlah/pola warna per-
      // status TIDAK dirinci, gap sama dgn QCT/Inspur di batch server. Warna
      // 'white' = placeholder UNVERIFIED.
      leds: [{ label: 'Status (multi-color, nama saja)', color: 'white', position: 'left' }],
    },
    rear: {
      blocks: [{ type: 'psu-slot', count: 1 }], // PoE 802.3bt (35W maks) / 802.3at / 12VDC adapter
    },
    // brand: derived, low-confidence — aksen teal khas logo Cambium
    // (#00A99D), body putih polos, belum dikonfirmasi ke foto produk aktual.
    brand: { accent: '#00A99D', chassis: '#F4F4F2', label: 'Cambium Networks', badge: 'stripe' },
  },

  // ── Hikvision DS-3WAP622E-SI ──────────────────────────────────────────────
  {
    slug: 'hikvision-ds-3wap622e-si',
    manufacturer: 'Hikvision',
    model: 'DS-3WAP622E-SI',
    uHeight: 1,
    // §8.1 V, datasheet resmi via arsip Wayback Machine (assets.hikvision.com,
    // akses langsung 403/S3 AccessDenied — konten tetap terbitan Hikvision):
    // "Product Dimensions (W x H x D): 220 mm x 220 mm x 39 mm".
    chassisMm: { widthMm: 220, depthMm: 220 },
    front: {
      portZones: [
        {
          // V, datasheet resmi: satu-satunya port jaringan, 2.5GbE, PoE
          // 802.3af/at input (atau DC 12V/1.5A alternatif).
          ports: [{ type: 'rj45', count: 1, label: '2.5GE uplink', poe: true }],
          rows: 1,
          align: 'fill',
        },
      ],
      // V palet warna saja: "LED Indicator: Yellow/green/blue for different
      // working states, flashing mode" — jumlah LED fisik & pemetaan warna-
      // ke-state TIDAK dirinci (lebih generik dari tabel Grandstream/Araknis
      // di atas). `Led.color` sekarang punya 'yellow' (ditambahkan batch
      // router 2026-09-06) — dikoreksi ke warna asli vendor; representasi
      // dipilih 'yellow' sebagai LED status utama, palet lengkap (kuning/
      // hijau/biru) tetap dicatat di komentar ini.
      leds: [{ label: 'Status (palet kuning/hijau/biru, pola tak dirinci)', color: 'yellow', position: 'left' }],
    },
    rear: {
      blocks: [{ type: 'psu-slot', count: 1 }], // PoE 802.3af/at atau DC 12V/1.5A adapter
    },
    // brand: derived, low-confidence — tak ada inspeksi foto produk detail
    // sesi riset ini.
    brand: { accent: '#C7C7C5', chassis: '#F3F3F1', label: 'Hikvision', badge: 'stripe' },
  },

  // ── Peplink (Pepwave) AP One AX ───────────────────────────────────────────
  {
    slug: 'peplink-ap-one-ax',
    manufacturer: 'Peplink',
    model: 'AP One AX',
    uHeight: 1,
    // §8.1 V, datasheet resmi download.peplink.com/resources/
    // pepwave_ap_one_ax_datasheet.pdf: "Dimensions: 8.1 x 8.1 x 1.3 inches —
    // 205 x 205 x 32 mm".
    chassisMm: { widthMm: 205, depthMm: 205 },
    front: {
      portZones: [
        {
          // V, datasheet resmi: satu-satunya port jaringan, 2.5GbE,
          // menerima PoE 802.3at (injector dijual terpisah) atau DC jack
          // 12V alternatif.
          ports: [{ type: 'rj45', count: 1, label: '2.5GE (PoE 802.3at via injector)', poe: true }],
          rows: 1,
          align: 'fill',
        },
      ],
      // V label+warna lengkap per-LED, user manual resmi v3.9.0 bagian
      // "4.3 AP One AX" (tabel spesifik model ini, bukan digeneralisasi dari
      // model AP One lain di dokumen yang sama) — satu-satunya LED table
      // "full V" di batch ini selain Grandstream.
      leds: [
        { label: 'Power', color: 'blue', position: 'left' },
        { label: 'Status', color: 'blue', position: 'left' },
        { label: 'Ethernet Port', color: 'blue', position: 'left' },
        { label: 'Wi-Fi', color: 'blue', position: 'left' },
      ],
    },
    rear: {
      blocks: [{ type: 'psu-slot', count: 1 }], // PoE 802.3at (injector terpisah) atau AC adapter 12V/2.5A
    },
    // brand: derived, low-confidence — tak ada inspeksi foto produk detail
    // sesi riset ini.
    brand: { accent: '#C7C7C5', chassis: '#F2F2F0', label: 'Peplink', badge: 'stripe' },
  },

  // ── Tenda i27 ─────────────────────────────────────────────────────────────
  {
    slug: 'tenda-i27',
    manufacturer: 'Tenda',
    model: 'i27',
    uHeight: 1,
    // §8.1 V, datasheet resmi static.tenda.com.cn/.../i27V1.0_Datasheet.pdf:
    // "Dimension: φ176*43.5mm" — vendor memberi DIAMETER eksplisit (φ),
    // bukan width x depth terpisah. widthMm=depthMm=176 di bawah adalah
    // pemetaan `derived` kita ke skema chassisMm persegi, BUKAN pengukuran
    // vendor dua-sumbu terpisah (beda dari Grandstream/EnGenius/Araknis/
    // Cambium/Hikvision di atas yang semuanya sudah W×D dua-angka vendor).
    chassisMm: { widthMm: 176, depthMm: 176 },
    front: {
      portZones: [
        {
          // V, datasheet resmi "Hardware Specifications": satu-satunya port
          // jaringan, GbE, menerima PoE.
          ports: [{ type: 'rj45', count: 1, label: 'Ethernet (PoE)', poe: true }],
          rows: 1,
          align: 'fill',
        },
      ],
      // V nama saja: "LED indicator: 1*SYS" — satu LED sistem tunggal,
      // warna/pola nyala TIDAK dirinci. 'white' = placeholder UNVERIFIED.
      leds: [{ label: 'SYS', color: 'white', position: 'left' }],
    },
    rear: {
      blocks: [{ type: 'psu-slot', count: 1 }], // PoE 802.3af atau 48V/0.8A Passive PoE
    },
    // brand: derived, low-confidence — tak ada inspeksi foto produk detail
    // sesi riset ini.
    brand: { accent: '#C7C7C5', chassis: '#F4F4F2', label: 'Tenda', badge: 'stripe' },
  },

  // ── Batch router (Sesi riset 2026-09-06, research/3d-device-specs-router.md) ──
  // 5 SKU chassisMm V dari sumber primer vendor; H3C dan Ruijie sengaja
  // dikecualikan (masih V(2nd)/belum layak, keputusan leader). Edgecore
  // AS7726-32X JUGA dikecualikan dari batch ini (koreksi leader
  // 2026-09-06): SKU sama dipilih batch switch (data-center ToR 32x100G),
  // dikonversi ke pack `switches`, bukan `routers`, untuk hindari duplikat
  // lintas-kategori.

  // ── DrayTek Vigor 3910 ────────────────────────────────────────────────────
  {
    slug: 'draytek-vigor-3910',
    manufacturer: 'DrayTek',
    model: 'Vigor 3910',
    uHeight: 1,
    // §8.1 V (datasheet resmi DrayTek): 443x285x45mm, chassis body (BUKAN
    // 482,6mm lebar rail).
    chassisMm: { widthMm: 443, depthMm: 285 },
    front: {
      portZones: [
        {
          ports: [
            { type: 'usb', count: 2 },
            // V isi/jumlah dari Quick Start Guide resmi; dokumen menyebut
            // "Console Connector / Console Flat Cable" — bentuk fisik kabel
            // pipih mirip RJ45 tapi pinout tak dikonfirmasi eksplisit di
            // teks. DIASUMSIKAN console-rj45, bukan V penuh untuk detail
            // konektor (research §1.A).
            { type: 'console-rj45', count: 1 },
          ],
          rows: 1,
          align: 'left',
          widthFraction: 0.16,
        },
        {
          // P1-P2, switchable WAN/LAN 10G/1G.
          ports: [{ type: 'sfp+', count: 2, label: 'P1-P2' }],
          rows: 1,
          align: 'fill',
        },
        {
          // P3-P12: 2x 2.5GBase-T + 8x GbE, semua RJ45, switchable WAN/LAN.
          ports: [{ type: 'rj45', count: 10, label: 'P3-P12' }],
          rows: 1,
          align: 'right',
          widthFraction: 0.5,
        },
      ],
      // V, Quick Start Guide resmi.
      leds: [
        { label: 'PWR', color: 'green', position: 'left' },
        { label: 'ACT', color: 'green', position: 'left' },
      ],
    },
    rear: {
      blocks: [{ type: 'iec-inlet' }], // internal fixed AC, non-redundant
    },
    // brand: derived — DrayTek belum ada foto produk detail diperiksa pass ini.
    brand: { accent: '#D0021B', chassis: '#E8E8E8', label: 'DrayTek', badge: 'stripe' },
  },

  // ── TP-Link (Omada) ER8411 ────────────────────────────────────────────────
  {
    slug: 'tplink-omada-er8411',
    manufacturer: 'TP-Link',
    model: 'Omada ER8411',
    uHeight: 1,
    // §8.1 V (datasheet resmi TP-Link): 440x220x44mm, chassis body.
    chassisMm: { widthMm: 440, depthMm: 220 },
    front: {
      portZones: [
        {
          ports: [
            { type: 'usb', count: 2 }, // USB1 (LTE) + USB2 (storage)
            // UNVERIFIED tipe fisik (RJ45 vs USB-C) — Installation Guide
            // resmi hanya menyebut label "Console" tanpa spesifikasi bentuk
            // (research §2.A). console-rj45 dipilih sebagai asumsi paling
            // masuk akal (mayoritas gateway rackmount kelas ini pakai RJ45
            // console), BUKAN V.
            { type: 'console-rj45', count: 1 },
          ],
          rows: 1,
          align: 'left',
          widthFraction: 0.18,
        },
        {
          // Port1-2 SFP+ WAN/WAN-LAN 10G, Port3 SFP WAN/LAN 1G.
          ports: [
            { type: 'sfp+', count: 2, label: 'Port1-2' },
            { type: 'sfp', count: 1, label: 'Port3' },
          ],
          rows: 1,
          align: 'fill',
        },
        {
          ports: [{ type: 'rj45', count: 8, label: 'Port4-11' }],
          rows: 1,
          align: 'right',
          widthFraction: 0.4,
        },
      ],
      // V, Installation Guide resmi.
      leds: [
        { label: 'PWR1', color: 'green', position: 'left' },
        { label: 'PWR2', color: 'green', position: 'left' },
        { label: 'SYS', color: 'green', position: 'left' },
        { label: 'FAN', color: 'green', position: 'left' },
      ],
    },
    rear: {
      blocks: [{ type: 'iec-inlet', count: 2 }], // 2x fixed AC redundant, non hot-swap
    },
    // brand: derived — chassis hitam/abu gelap khas lini Omada.
    brand: { accent: '#2681FF', chassis: '#1A1A1A', label: 'TP-Link', badge: 'stripe' },
  },

  // ── UfiSpace S9600-72XC ───────────────────────────────────────────────────
  {
    slug: 'ufispace-s9600-72xc',
    manufacturer: 'UfiSpace',
    model: 'S9600-72XC',
    uHeight: 2,
    isFullDepth: true,
    // §8.1 V (datasheet resmi + Hardware Installation Guide resmi, dua
    // sumber angka identik): 436x609.6x87.7mm. CATATAN: tabel dimensi di
    // Installation Guide melabeli baris ini "S9701-82DC" (nama part
    // internal/ODM), bukan salah tempel — angka numerik identik dengan
    // datasheet marketing S9600-72XC (research §3.A).
    chassisMm: { widthMm: 436, depthMm: 609.6 },
    front: {
      portZones: [
        {
          // Isi & jumlah V; urutan visual kiri-kanan TIDAK terkonfirmasi
          // (diagram front panel adalah gambar, tidak ter-ekstrak sebagai
          // teks — research §3.A). Susunan di bawah = `derived`: blok
          // SFP28 besar di kiri, QSFP28 di kanan, klaster mgmt/console
          // terpisah.
          ports: [{ type: 'sfp28', count: 64, label: 'derived: order' }],
          rows: 2,
          align: 'fill',
        },
        {
          ports: [{ type: 'qsfp28', count: 8, label: 'derived: order' }],
          rows: 1,
          align: 'right',
          widthFraction: 0.14,
        },
        {
          ports: [
            { type: 'sfp+', count: 2, label: 'MGMT' },
            { type: 'console-rj45', count: 1 },
            { type: 'console-usb', count: 1 },
            { type: 'mgmt-rj45', count: 1 },
            { type: 'usb', count: 1 },
          ],
          rows: 2,
          align: 'left',
          widthFraction: 0.14,
        },
      ],
      // V, Hardware Installation Guide resmi §Front Panel LED. Timing
      // SMB "1PPS"/"10MHz" (bukan LED) tak ada padanan union type — utang
      // kosakata, tidak direpresentasikan visual (research §3.A/gap).
      leds: [
        { label: 'SYNC', color: 'green', position: 'left' },
        { label: 'SYS', color: 'green', position: 'left' },
        { label: 'FAN', color: 'green', position: 'left' },
        { label: 'PS0', color: 'green', position: 'left' },
        { label: 'PS1', color: 'green', position: 'left' },
      ],
    },
    rear: {
      blocks: [
        { type: 'psu-slot', count: 2 }, // 1+1 hot-swap, 1300W AC atau DC
        { type: 'fan-tray', count: 1 },
      ],
    },
    // brand: derived — UfiSpace produk B2B ODM tanpa identitas warna
    // konsumen; chassis abu-abu gelap standar rak telco 2U.
    brand: { accent: '#5A5A5A', chassis: '#2B2B2B', label: 'UfiSpace', badge: 'stripe' },
  },

  // ── Extreme Networks SLX 9740-40C ─────────────────────────────────────────
  {
    slug: 'extreme-slx-9740-40c',
    manufacturer: 'Extreme',
    model: 'SLX 9740-40C',
    // NOS: SLX-OS (proprietary Extreme) — tidak ada di union `Nos` (routers.ts
    // API types), diomit sebagaimana vendor proprietary lain di batch ini.
    uHeight: 1,
    isFullDepth: true,
    // §8.1 V (Hardware Installation Guide resmi §Weights and Physical
    // Dimensions): 450x640x43.1mm. 40C (1U) dipilih atas 80C (2U) sebagai
    // konfigurasi acuan (research §5).
    chassisMm: { widthMm: 450, depthMm: 640 },
    front: {
      portZones: [
        {
          // Item #2 Figure 1 resmi: 40x QSFP28 100GE/40GE. Pola
          // warna/behavior LED per-port QSFP28 detail UNVERIFIED (research §5).
          ports: [{ type: 'qsfp28', count: 40 }],
          rows: 2,
          align: 'fill',
        },
        {
          ports: [
            { type: 'mgmt-rj45', count: 1 },
            { type: 'usb', count: 1 },
            { type: 'console-rj45', count: 1 },
          ],
          rows: 1,
          align: 'right',
          widthFraction: 0.12,
        },
      ],
      // V, Hardware Installation Guide resmi §LED activity interpretation.
      leds: [
        { label: 'Power', color: 'green', position: 'left' },
        { label: 'Status', color: 'green', position: 'left' },
      ],
    },
    rear: {
      blocks: [
        { type: 'psu-slot', count: 2 },
        { type: 'fan-tray', count: 6 },
      ],
    },
    // brand: derived — Extreme corporate identity ungu, chassis hitam standar.
    brand: { accent: '#66218A', chassis: '#1C1C1C', label: 'Extreme', badge: 'stripe' },
  },

  // ── Ubiquiti UniFi Dream Machine Pro (UDM-Pro) ────────────────────────────
  {
    slug: 'ubiquiti-udm-pro',
    manufacturer: 'Ubiquiti',
    model: 'UDM-Pro',
    uHeight: 1,
    // §8.1 V (Datasheet resmi Ubiquiti dl.ubnt.com/ds/udm-pro.pdf):
    // 442.4x285.6x43.7mm, chassis body (BUKAN 482,6mm lebar rail).
    chassisMm: { widthMm: 442.4, depthMm: 285.6 },
    front: {
      portZones: [
        {
          // Bay HDD 3,5" internal untuk UniFi Protect NVR — router dengan
          // drive bay, drive-lff dipakai sesuai keputusan leader.
          ports: [{ type: 'drive-lff', count: 1 }],
          rows: 1,
          align: 'left',
          widthFraction: 0.16,
        },
        {
          // LAN 8x GbE RJ45 + WAN 1x GbE RJ45; urutan pasti klaster kanan
          // tak terkonfirmasi ke sumber (research §6.A), isi/jumlah V.
          ports: [
            { type: 'rj45', count: 8, label: 'LAN' },
            { type: 'rj45', count: 1, label: 'WAN' },
          ],
          rows: 1,
          align: 'fill',
        },
        {
          ports: [
            { type: 'sfp+', count: 1, label: 'LAN' },
            { type: 'sfp+', count: 1, label: 'WAN' },
          ],
          rows: 1,
          align: 'right',
          widthFraction: 0.14,
        },
      ],
      // V, Datasheet resmi §LEDs.
      leds: [
        { label: 'Ethernet', color: 'green', position: 'left' },
        { label: 'SFP+', color: 'white', position: 'left' },
        { label: 'HDD', color: 'white', position: 'left' },
      ],
      hasLcd: true,
      lcdPos: 'left', // layar LCM touchscreen 1.3" — V, datasheet resmi
    },
    rear: {
      blocks: [{ type: 'iec-inlet' }], // internal AC/DC 50W, non-redundant fixed
    },
    // brand: derived — enclosure Aluminium CNC putih/abu terang khas UniFi
    // (bahan V dari datasheet), hex pasti derived.
    brand: { accent: '#0559C9', chassis: '#F5F5F5', label: 'Ubiquiti', badge: 'stripe' },
  },

  // ── D-Link DGS-3630-52PC ───────────────────────────────────────────────────
  {
    slug: 'dlink-dgs-3630-52pc',
    manufacturer: 'D-Link',
    model: 'DGS-3630-52PC',
    uHeight: 1,
    // §8.1 V (Hardware Installation Guide resmi media.dlink.eu): "441 mm (W)
    // x 380 mm (D) x 44 mm (H), 19-inch, 1 U" — chassis body, bukan lebar rail.
    chassisMm: { widthMm: 441, depthMm: 380 },
    front: {
      portZones: [
        {
          ports: [{ type: 'rj45', count: 44, poe: true }],
          // rows: 2 diasumsikan (pola umum 48-port dua baris) — jumlah/PoE V,
          // urutan piksel presisi UNVERIFIED (figure PDF tak terekstrak teks).
          rows: 2,
          align: 'fill',
        },
        {
          // 4 port combo RJ45/SFP (salah satu aktif per port) — TIDAK ada
          // tipe "combo" di kosakata PortType, direpresentasikan sebagai satu
          // port fisik sisi RJ45 dengan label COMBO (pola sama dgn Blok B
          // pack research), bukan digambar dua kali.
          ports: [{ type: 'rj45', count: 4, poe: true, label: 'COMBO' }],
          rows: 1,
          align: 'right',
          widthFraction: 0.1,
        },
        {
          ports: [{ type: 'sfp+', count: 4 }],
          rows: 1,
          align: 'right',
          widthFraction: 0.14,
        },
      ],
      // V lengkap warna, manual resmi §LED Indicators (depan).
      leds: [
        { label: 'PWR', color: 'green', position: 'left' },
        { label: 'CONSOLE', color: 'green', position: 'left' },
        { label: 'RPS', color: 'green', position: 'left' },
        { label: 'FAN ERR', color: 'red', position: 'left' },
      ],
      // Stack ID = 7-segment digit display (bukan LED tunggal), tidak ada
      // padanan di skema Led — diomit sesuai keputusan pola batch sebelumnya.
    },
    rear: {
      blocks: [
        {
          // Keputusan leader: mgmt/console DGS-3630 ada di REAR panel, V
          // eksplisit manual ("Rear Panel Components"): MGMT RJ45, 2x
          // console (RJ45 + mini-USB), alarm RJ45, USB 2.0. Alarm RJ45 tidak
          // punya padanan PortType, diomit (bukan port data).
          type: 'port-zone',
          portZone: {
            ports: [
              { type: 'mgmt-rj45', count: 1 },
              { type: 'console-rj45', count: 1 },
              { type: 'console-usb', count: 1 },
              { type: 'usb', count: 1 },
            ],
            rows: 1,
            align: 'left',
          },
        },
        { type: 'psu-slot', count: 1 }, // internal; RPS eksternal (DPS-700) opsional, tidak digambar
        { type: 'vent-grille' },
      ],
    },
    // brand: derived, low-confidence — belum ada inspeksi foto produk sesi
    // riset; D-Link umumnya hitam dgn aksen merah korporat.
    brand: { accent: '#CE181E', chassis: '#1B1B1B', label: 'D-Link', badge: 'stripe' },
  },

  // ── Netgear M4300-52G-PoE+ ─────────────────────────────────────────────────
  {
    slug: 'netgear-m4300-52g-poe-plus',
    manufacturer: 'Netgear',
    model: 'M4300-52G-PoE+',
    uHeight: 1,
    // §8.1 V (Data Sheet resmi downloads.netgear.com, tabel Dimensions):
    // 440x388x44mm — varian non-PoE seri sama punya depth beda (31cm vs
    // 38,8cm) membuktikan ini pengukuran body per-model, bukan lebar rak
    // generik dibulatkan.
    chassisMm: { widthMm: 440, depthMm: 388 },
    front: {
      portZones: [
        {
          ports: [{ type: 'console-usb', count: 1 }],
          rows: 1,
          align: 'left',
          widthFraction: 0.06,
        },
        {
          ports: [{ type: 'rj45', count: 48, poe: true }],
          // rows: 2 diasumsikan (pola umum), jumlah/PoE V — Manual Instalasi
          // resmi tidak menggambar baris fisik secara tekstual.
          rows: 2,
          align: 'fill',
        },
        {
          ports: [{ type: 'rj45', count: 2, label: '10G' }],
          rows: 1,
          align: 'right',
          widthFraction: 0.08,
        },
        {
          ports: [{ type: 'sfp+', count: 2 }],
          rows: 1,
          align: 'right',
          widthFraction: 0.08,
        },
        {
          // OOB 1G mgmt + USB 2.0 — V, ada di FRONT (bukan rear), beda dari
          // RS232 console yang ada di rear (lihat blok rear).
          ports: [
            { type: 'mgmt-rj45', count: 1 },
            { type: 'usb', count: 1 },
          ],
          rows: 1,
          align: 'right',
          widthFraction: 0.1,
        },
      ],
      // V lengkap warna, Hardware Installation Guide resmi Tabel 2.
      leds: [
        { label: 'PWR1', color: 'green', position: 'left' },
        { label: 'PWR2', color: 'green', position: 'left' },
        { label: 'FAN', color: 'green', position: 'left' },
        { label: 'MASTER', color: 'green', position: 'left' },
      ],
    },
    rear: {
      blocks: [
        {
          // RS232 console — V eksplisit "Back panel model": beda dari
          // console-usb depan.
          type: 'port-zone',
          portZone: {
            ports: [{ type: 'console-rj45', count: 1 }],
            rows: 1,
            align: 'left',
          },
        },
        { type: 'psu-slot', count: 2 }, // modular bay 1+2 (APS550W/1000W) + RPS interface
      ],
    },
    // brand: derived, low-confidence — tidak ada inspeksi foto produk sesi ini.
    brand: { accent: '#8DC63F', chassis: '#1A1A1A', label: 'Netgear', badge: 'stripe' },
  },

  // ── Zyxel XGS4600-52F ──────────────────────────────────────────────────────
  {
    slug: 'zyxel-xgs4600-52f',
    manufacturer: 'Zyxel',
    model: 'XGS4600-52F',
    uHeight: 1,
    // §8.1 V (halaman spesifikasi resmi zyxel.com via arsip Wayback):
    // "441 x 360 x 44" mm. Varian sekeluarga (32/32F: depth 270mm) beda
    // depth meski width sama — bukti width 441mm body asli.
    chassisMm: { widthMm: 441, depthMm: 360 },
    front: {
      portZones: [
        {
          // 48x SFP GIGABIT MURNI FIBER — BUKAN RJ45 (beda dari varian
          // -32/-32F combo di seri sama), V user guide resmi §3.1.
          ports: [{ type: 'sfp', count: 48 }],
          rows: 2,
          align: 'fill',
        },
        {
          ports: [{ type: 'sfp+', count: 4 }],
          rows: 1,
          align: 'right',
          widthFraction: 0.14,
        },
        {
          // mgmt out-of-band RJ45 — V eksplisit ADA DI FRONT untuk model
          // -52F (beda dari console yang ada di rear untuk varian ini).
          ports: [{ type: 'mgmt-rj45', count: 1 }],
          rows: 1,
          align: 'left',
          widthFraction: 0.06,
        },
      ],
      // V lengkap warna, user guide resmi §3.3 Tabel 3.
      leds: [
        { label: 'PWR', color: 'green', position: 'left' },
        { label: 'PWR2', color: 'green', position: 'left' },
        { label: 'SYS', color: 'green', position: 'left' },
        { label: 'FAN', color: 'green', position: 'left' },
        { label: 'LOCATOR', color: 'blue', position: 'left' },
        { label: 'MASTER', color: 'green', position: 'left' },
      ],
    },
    rear: {
      blocks: [
        {
          // Console — V eksplisit ADA DI REAR untuk -52F (beda dari
          // -32/-32F yang consolenya di front — dicatat di research, jangan
          // disamakan antar-varian).
          type: 'port-zone',
          portZone: {
            ports: [{ type: 'console-rj45', count: 1 }],
            rows: 1,
            align: 'left',
          },
        },
        { type: 'psu-slot', count: 2 }, // dual PWR/PWR2 bay; posisi rear derived (konvensi umum, tak dinyatakan eksplisit)
      ],
    },
    // brand: derived, low-confidence — tidak ada inspeksi foto produk sesi ini.
    brand: { accent: '#EE7623', chassis: '#1C1C1E', label: 'Zyxel', badge: 'stripe' },
  },

  // ── Alcatel-Lucent Enterprise OmniSwitch OS6560-P48X4 ─────────────────────
  {
    slug: 'ale-os6560-p48x4',
    manufacturer: 'Alcatel-Lucent Enterprise',
    model: 'OmniSwitch OS6560-P48X4',
    uHeight: 1,
    // §8.1 V (Datasheet resmi al-enterprise.com, tabel "Gigabit product
    // matrix"): "Height 4.4cm, Width 44cm, Depth 35cm" — sama utk 5 varian
    // Gigabit seri ini.
    chassisMm: { widthMm: 440, depthMm: 350 },
    front: {
      portZones: [
        {
          ports: [{ type: 'rj45', count: 48, poe: true }],
          rows: 2,
          align: 'fill',
        },
        {
          ports: [{ type: 'sfp', count: 2 }],
          rows: 1,
          align: 'right',
          widthFraction: 0.08,
        },
        {
          ports: [{ type: 'sfp+', count: 4 }],
          rows: 1,
          align: 'right',
          widthFraction: 0.14,
        },
        {
          ports: [
            { type: 'usb', count: 1 },
            { type: 'console-rj45', count: 1 },
          ],
          rows: 1,
          align: 'left',
          widthFraction: 0.08,
        },
      ],
      // Jenis LED V (datasheet resmi), WARNA tidak dicantumkan (datasheet
      // marketing-tier, bukan Hardware User Guide) — hijau di sini derived,
      // bukan V, ditandai eksplisit sesuai keputusan leader.
      leds: [
        { label: 'System', color: 'green', position: 'left' }, // derived
        { label: 'PWR', color: 'green', position: 'left' }, // derived
        { label: 'VC', color: 'green', position: 'left' }, // derived
      ],
    },
    rear: {
      blocks: [
        { type: 'psu-slot', count: 2 }, // 1 primer + 1 backup slide-in; posisi rear derived (konvensi umum PSU modular)
        { type: 'vent-grille' },
      ],
    },
    // brand: derived, low-confidence — tidak ada inspeksi foto produk sesi ini.
    brand: { accent: '#5C2D91', chassis: '#1B1B1D', label: 'Alcatel-Lucent Enterprise', badge: 'stripe' },
  },

  // ── RUCKUS (Belden) ICX 7150-48P ───────────────────────────────────────────
  {
    slug: 'ruckus-icx7150-48p',
    manufacturer: 'RUCKUS',
    model: 'ICX 7150-48P',
    uHeight: 1,
    // EOL — dicatat eksplisit, tidak disembunyikan: seri ICX 7150
    // discontinued 15 Januari 2026 per halaman produk resmi
    // ruckusnetworks.com (V). Dipilih tetap karena data teknis paling
    // lengkap/terverifikasi di antara kandidat Ruckus lain.
    //
    // §8.1 V (Datasheet resmi ruckusnetworks.com, tabel Dimensions):
    // "440 (W), 370 (D), 43.65 (H)" mm.
    chassisMm: { widthMm: 440, depthMm: 370 },
    front: {
      portZones: [
        {
          ports: [{ type: 'rj45', count: 48, poe: true }],
          rows: 2,
          align: 'fill',
        },
        {
          ports: [{ type: 'rj45', count: 2, label: 'UPLINK' }],
          rows: 1,
          align: 'right',
          widthFraction: 0.08,
        },
        {
          ports: [{ type: 'sfp+', count: 4 }],
          rows: 1,
          align: 'right',
          widthFraction: 0.14,
        },
        {
          ports: [
            { type: 'mgmt-rj45', count: 1 },
            { type: 'console-usb', count: 1 }, // USB Type-C console
            { type: 'console-rj45', count: 1 },
            { type: 'usb', count: 1 },
          ],
          rows: 1,
          align: 'left',
          widthFraction: 0.14,
        },
      ],
      // V lengkap warna (System/Power), Hardware Installation Guide resmi
      // docs.ruckuswireless.com. Warna detail per-mode port-side (5-mode
      // status button) UNVERIFIED presisi, tidak digambar sebagai LED
      // terpisah di sini.
      leds: [
        { label: 'SYSTEM', color: 'green', position: 'left' },
        { label: 'POWER', color: 'green', position: 'left' },
      ],
    },
    rear: {
      blocks: [
        { type: 'psu-slot', count: 1 }, // single integrated PSU 525W AC (C14 inlet)
        { type: 'vent-grille' },
      ],
    },
    // brand: derived, low-confidence — tidak ada inspeksi foto produk sesi ini.
    brand: { accent: '#8DC63F', chassis: '#1D1D1D', label: 'RUCKUS', badge: 'stripe' },
  },

  // ── Netberg Aurora 610 ─────────────────────────────────────────────────────
  {
    slug: 'netberg-aurora-610',
    manufacturer: 'Netberg',
    model: 'Aurora 610',
    // NOS: Open Network Linux / SONiC (ONIE) — tidak ada di union `Nos`
    // (routers.ts API types), diomit sebagaimana NOS proprietary/white-box
    // lain di batch ini (lihat extreme-slx-9740-40c).
    uHeight: 1,
    isFullDepth: true,
    // §8.1 V (Manual instalasi resmi netbergtw.com, tabel Specification):
    // "Dimensions (DxWxH): 482 x 440 x 44 mm" — 482mm di sini adalah DEPTH
    // (bukan width/lebar rail 19"), width tetap 440mm konsisten pola batch.
    chassisMm: { widthMm: 440, depthMm: 482 },
    front: {
      // Data port di FRONT (konvensi DC ToR standar) — V eksplisit figure
      // manual §3.2/3.3. Mgmt/console/USB ada di REAR (lihat blok rear),
      // BEDA dari kelima SKU access/aggregation lain di batch ini.
      portZones: [
        {
          ports: [{ type: 'sfp28', count: 48 }],
          rows: 2,
          align: 'fill',
        },
        {
          ports: [{ type: 'qsfp28', count: 8 }],
          rows: 1,
          align: 'right',
          widthFraction: 0.18,
        },
      ],
      // V lengkap warna, manual resmi Tabel 3.5. Health LED bi-color
      // hijau/merah — hijau (ready) dipakai sbg warna dominan di sini.
      leds: [{ label: 'HEALTH', color: 'green', position: 'left' }],
    },
    rear: {
      blocks: [
        { type: 'psu-slot', count: 2 }, // 1+1 redundant hot-swap, 800W 80+ Platinum
        { type: 'fan-tray', count: 4 }, // N+1
        {
          // Keputusan leader: mgmt/console REAR (konvensi DC ToR) — V
          // eksplisit manual §3.4 Figure "Rear Panel".
          type: 'port-zone',
          portZone: {
            ports: [
              { type: 'mgmt-rj45', count: 1 },
              { type: 'console-rj45', count: 1 },
              { type: 'usb', count: 1 },
            ],
            rows: 1,
            align: 'left',
          },
        },
      ],
    },
    // brand: derived, low-confidence — white-box ODM Taiwan, tidak ada
    // inspeksi foto produk sesi ini.
    brand: { accent: '#3AA0FF', chassis: '#1C1C1E', label: 'Netberg', badge: 'stripe' },
  },

  // ── Edgecore AS7726-32X (DCS204) ───────────────────────────────────────────
  {
    slug: 'edgecore-as7726-32x',
    manufacturer: 'Edgecore',
    model: 'AS7726-32X',
    // NOS: ONIE-preloaded, kompatibel SONiC/open-source & komersial — tidak
    // ada di union `Nos`, diomit (pola sama dgn Netberg §di atas).
    uHeight: 1,
    isFullDepth: true,
    // §8.1 V (Datasheet resmi edge-core.com DCS204_AS7726-32X_DS_R07):
    // "Dimensions (WxHxD): 43.84 x 4.35 x 51.5 cm" → 438.4 x 515mm.
    // Batch ROUTER sempat memilih SKU sama, DIKELUARKAN dari situ atas
    // instruksi leader (ini switch DC ToR 32x100G, bukan router) — masuk
    // di sini, tidak duplikat lintas-kategori.
    chassisMm: { widthMm: 438.4, depthMm: 515 },
    front: {
      // V eksplisit, datasheet resmi edge-core.com: "All ports on front;
      // PSUs and fans accessible from rear" — mgmt-rj45/sfp+-mgmt/console/
      // usb SEMUA ada di FRONT bersama data port, hanya PSU+fan yang rear.
      portZones: [
        {
          ports: [{ type: 'qsfp28', count: 32 }],
          rows: 2,
          align: 'fill',
        },
        {
          ports: [
            { type: 'mgmt-rj45', count: 1 },
            { type: 'sfp+', count: 2, label: 'MGMT' }, // "10G management port", BUKAN data uplink
            { type: 'usb', count: 1 },
            { type: 'console-rj45', count: 1 },
          ],
          rows: 1,
          align: 'right',
          widthFraction: 0.16,
        },
      ],
      // Jenis LED V (Diagnostic/Locator/PSU/Fan), warna TIDAK dicantumkan
      // datasheet (marketing-tier) — hijau di sini derived, bukan V.
      leds: [
        { label: 'DIAG', color: 'green', position: 'left' }, // derived
        { label: 'LOCATOR', color: 'green', position: 'left' }, // derived
      ],
    },
    rear: {
      // V eksplisit, datasheet resmi: PSU + fan accessible from rear.
      blocks: [
        { type: 'psu-slot', count: 2 }, // redundant, load-sharing, hot-swap
        { type: 'fan-tray', count: 6 }, // 5+1 redundant hot-swap
      ],
    },
    // brand: derived, low-confidence — white-box ODM Taiwan (Accton), tidak
    // ada inspeksi foto produk sesi ini.
    brand: { accent: '#00A19A', chassis: '#1A1A1C', label: 'Edgecore', badge: 'stripe' },
  },
];

// ─── Resolve helpers ──────────────────────────────────────────────────────────

/** Score a DeviceType candidate against the node's real interface list. */
function scoreMatch(dt: DeviceType, nos: Nos, kind: NodeKind, ifaces?: Interface[]): number {
  let score = 0;
  if (dt.nos && dt.nos === nos) score += 10;
  // kind heuristic
  const k = kind;
  if (k === 'olt' && dt.slug.includes('olt')) score += 5;
  if (k === 'server' && dt.uHeight >= 2) score += 4;
  if (k === 'firewall' && dt.manufacturer === 'Fortinet') score += 4;
  if (!ifaces?.length) return score;

  // port-count match (rough):
  const eth = ifaces.filter((i) => i.type === 'eth').length;
  const sfp = ifaces.filter((i) => i.type === 'sfp' || i.type === 'sfp28').length;
  const qsfp = ifaces.filter((i) => i.type === 'qsfp').length;
  const pon = ifaces.filter((i) => i.type === 'gpon').length;

  // count total ports of each broad type from all zones
  let dtEth = 0, dtSfp = 0, dtQsfp = 0, dtPon = 0;
  for (const z of dt.front.portZones) {
    for (const p of z.ports) {
      if (p.type === 'rj45' || p.type === 'mgmt-rj45') dtEth += p.count;
      else if (p.type === 'sfp' || p.type === 'sfp+' || p.type === 'sfp28') dtSfp += p.count;
      else if (p.type === 'qsfp28') dtQsfp += p.count;
      else if (p.type === 'pon') dtPon += p.count;
    }
  }

  const delta = Math.abs(dtEth - eth) + Math.abs(dtSfp - sfp) + Math.abs(dtQsfp - qsfp) + Math.abs(dtPon - pon);
  score += Math.max(0, 8 - delta);
  return score;
}

/** Build a generic DeviceType for unknown kinds — NetGeo coral theme. */
function genericFor(nos: Nos, kind: NodeKind): DeviceType {
  const base = {
    brand: { accent: '#D97757', chassis: '#1F1E1D', label: 'NetGeo', badge: 'stripe' as const },
    rear: {
      blocks: [
        { type: 'psu-slot' as const, count: 1 },
        { type: 'fan-tray' as const, count: 1 },
      ],
    },
    isFullDepth: false,
  };

  // ── per-kind front panel ──
  if (kind === 'switch') {
    return {
      ...base,
      slug: `generic-switch`,
      manufacturer: 'NetGeo',
      model: 'Generic Switch',
      nos,
      uHeight: 1,
      front: {
        portZones: [
          { ports: [{ type: 'rj45', count: 24 }], rows: 1, align: 'fill' },
          { ports: [{ type: 'sfp+', count: 4 }], rows: 1, align: 'right', widthFraction: 0.16 },
        ],
        leds: [{ label: 'PWR', color: 'green', position: 'left' }],
      },
    };
  }
  if (kind === 'router') {
    return {
      ...base,
      slug: `generic-router`,
      manufacturer: 'NetGeo',
      model: 'Generic Router',
      nos,
      uHeight: 1,
      front: {
        portZones: [
          { ports: [{ type: 'rj45', count: 5 }], rows: 1, align: 'fill' },
          { ports: [{ type: 'sfp+', count: 2 }], rows: 1, align: 'right', widthFraction: 0.18 },
        ],
        leds: [{ label: 'PWR', color: 'green', position: 'left' }],
      },
    };
  }
  if (kind === 'olt') {
    return {
      ...base,
      slug: `generic-olt-fallback`,
      manufacturer: 'NetGeo',
      model: 'Generic OLT',
      nos,
      uHeight: 1,
      rear: {
        blocks: [
          { type: 'psu-slot' as const, count: 2 },
          { type: 'fan-tray' as const, count: 1 },
        ],
      },
      front: {
        portZones: [
          { ports: [{ type: 'pon', count: 8 }], rows: 1, align: 'fill' },
          { ports: [{ type: 'sfp+', count: 2 }], rows: 1, align: 'right', widthFraction: 0.14 },
        ],
        leds: [{ label: 'PWR', color: 'green', position: 'left' }],
      },
    };
  }
  if (kind === 'firewall') {
    return {
      ...base,
      slug: `generic-firewall`,
      manufacturer: 'NetGeo',
      model: 'Generic Firewall',
      nos,
      uHeight: 1,
      front: {
        portZones: [
          { ports: [{ type: 'rj45', count: 4, label: 'LAN' }, { type: 'rj45', count: 2, label: 'WAN' }, { type: 'rj45', count: 2, label: 'DMZ' }], rows: 1, align: 'fill' },
        ],
        leds: [
          { label: 'SYS', color: 'green', position: 'left' },
          { label: 'ALM', color: 'red', position: 'left' },
        ],
      },
    };
  }
  if (kind === 'server') {
    return {
      ...base,
      slug: `generic-server`,
      manufacturer: 'NetGeo',
      model: 'Generic Server',
      nos,
      uHeight: 2,
      isFullDepth: true,
      front: {
        portZones: [{ ports: [{ type: 'drive-sff', count: 8 }], rows: 1, align: 'fill' }],
        leds: [{ label: 'PWR', color: 'green', position: 'left' }],
        isServerBezel: true,
      },
    };
  }
  if (kind === 'ap' || kind === 'cpe') {
    return {
      ...base,
      slug: `generic-${kind}`,
      manufacturer: 'NetGeo',
      model: kind === 'ap' ? 'Generic AP' : 'Generic CPE',
      nos,
      uHeight: 1,
      rear: { blocks: [{ type: 'iec-inlet' as const }] },
      front: {
        portZones: [{ ports: [{ type: 'rj45', count: 1 }], rows: 1, align: 'fill' }],
        leds: [{ label: 'PWR', color: 'green', position: 'left' }],
      },
    };
  }
  // host / cloud / fallback
  return {
    ...base,
    slug: `generic-${kind}`,
    manufacturer: 'NetGeo',
    model: `Generic ${kind.charAt(0).toUpperCase() + kind.slice(1)}`,
    nos,
    uHeight: 1,
    front: {
      portZones: [
        { ports: [{ type: 'rj45', count: 2 }], rows: 1, align: 'fill' },
      ],
      leds: [{ label: 'PWR', color: 'green', position: 'left' }],
    },
  };
}

/** Pack `ports[].type` (backend `IfaceType`) -> rack faceplate `PortType`.
 *  `wifi` (radio sectors on cell-site/wireless-ap packs) has no physical
 *  rack connector to draw, so it's simply omitted from the faceplate. */
const PACK_PORT_TYPE_MAP: Partial<Record<string, PortType>> = {
  eth: 'rj45',
  sfp: 'sfp',
  sfp28: 'sfp28',
  qsfp: 'qsfp28',
  gpon: 'pon',
  voice: 'fxs', // RJ-11 FXS/POTS line, added for the ONU voice-port batch
};

/** Build a real DeviceType from a pack-sourced /api/device-types entry
 *  (vendor/ports/physical, passed through by the backend as-is). One
 *  PortZone per `ports[]` entry, in the pack's own order. Returns null when
 *  every port entry is unmappable (e.g. a radio-only device) — the caller
 *  falls back to the existing heuristic rather than drawing an empty shell. */
function buildFromPack(pack: CatalogEntry, kind: NodeKind): DeviceType | null {
  const portZones: PortZone[] = [];
  for (const p of pack.ports ?? []) {
    const type = PACK_PORT_TYPE_MAP[p.type];
    if (!type) continue;
    portZones.push({
      ports: [{ type, count: p.count, label: p.role, poe: Boolean(p.poe) }],
      rows: 1,
      align: 'fill',
    });
  }
  if (portZones.length === 0) return null;

  const vendorStyle = pack.vendor
    ? DEVICE_TYPES.find((dt) => dt.manufacturer.toLowerCase() === pack.vendor!.toLowerCase())?.brand
    : undefined;

  return {
    slug: pack.id,
    manufacturer: pack.vendor ?? 'Unknown',
    model: pack.name,
    uHeight: pack.physical?.ru ?? 1,
    front: {
      portZones,
      leds: [{ label: 'PWR', color: 'green', position: 'left' }],
    },
    rear: { blocks: [{ type: 'psu-slot', count: 1 }] },
    brand: vendorStyle ?? genericFor('forgeos', kind).brand,
  };
}

/**
 * Resolve the best-matching DeviceType for a node.
 *
 * Priority:
 *  1. Real pack data (`packDeviceType.ports`) — the node's own `device_type_id`
 *     resolved to a /api/device-types entry that carries port/physical data
 *  2. Seed with exact NOS match + best port-count score
 *  3. Seed with NOS match (no interfaces)
 *  4. Accurate generic per kind (NetGeo coral theme)
 *
 * ponytail: DEVICE_TYPES below is the fallback for nodes with no
 * `device_type_id`, or whose pack entry never got ports data.
 */
export function resolveDeviceType(
  nos: Nos,
  kind: NodeKind,
  ifaces?: Interface[],
  packDeviceType?: CatalogEntry | null,
): DeviceType {
  if (packDeviceType?.ports?.length) {
    const built = buildFromPack(packDeviceType, kind);
    if (built) return built;
  }

  // Filter to candidates that match NOS (seeded) or kind heuristic
  const candidates = DEVICE_TYPES.filter(
    (dt) =>
      dt.nos === nos ||
      (kind === 'olt' && dt.slug.includes('olt')) ||
      (kind === 'server' && dt.uHeight >= 2 && dt.front.isServerBezel) ||
      (kind === 'firewall' && dt.slug.includes('fortigate')),
  );

  if (candidates.length === 0) return genericFor(nos, kind);

  // Score and pick best
  let best: DeviceType = candidates[0]!;
  let bestScore = scoreMatch(best, nos, kind, ifaces);
  for (const c of candidates.slice(1)) {
    const s = scoreMatch(c, nos, kind, ifaces);
    if (s > bestScore) { best = c; bestScore = s; }
  }
  return best;
}
