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

  // ── Dell PowerEdge R740 (2U server) ───────────────────────────────────────
  {
    slug: 'dell-poweredge-r740',
    manufacturer: 'Dell',
    model: 'PowerEdge R740',
    uHeight: 2,
    isFullDepth: true,
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
