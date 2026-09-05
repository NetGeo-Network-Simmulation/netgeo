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
  | 'drive-lff';

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
  color: 'green' | 'amber' | 'blue' | 'red' | 'white';
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
