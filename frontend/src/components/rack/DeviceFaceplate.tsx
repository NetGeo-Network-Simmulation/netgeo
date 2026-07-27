/**
 * DeviceFaceplate — 2D parametric SVG rack faceplate.
 *
 * Driven by `resolveDeviceType(nos, kind, interfaces)` from deviceTypes.ts.
 * Renders faithful per-brand front/back panels:
 *   Front: chassis body + rack ears + brand stripe + LEDs (optional LCD) + port zones
 *   Back:  PSU slots, fan trays, IEC inlet, ground lug, vent grille, rear port-zones
 *
 * Port renderers:
 *   rj45       → notched cage w/ 2 LED pips
 *   sfp/sfp+   → dark rect cage, accent-tinted stroke
 *   sfp28      → slightly taller dark rect
 *   qsfp28     → wider dark rect (double-wide feel)
 *   pon        → round SC/APC circle
 *   console-*  → small trapezoid
 *   mgmt-rj45  → small rect, labelled
 *   usb        → flat rect
 *   drive-sff  → bezel slot + handle bar + status LED (left pip)
 *   drive-lff  → taller bezel slot + handle + status LED
 *
 * ponytail: schematic-faithful, not pixel-exact vendor render. One parametric
 * engine covers every kind. Upgrade path: add NodeModel.model → look up
 * DEVICE_TYPES by slug → eventually swap SVG shapes for per-model art / 3D.
 */
import type { Interface, LinkStatus, NodeModel } from '@/api/types';
import { linkStatusColors } from '@/theme/tokens';
import { cn } from '@/lib/cn';
import { resolveDeviceType } from './deviceTypes';
import type { DeviceType, PortType, PortSpec, PortZone } from './deviceTypes';

// Rack#1: no data for a port (no matching interface, or an interface with no
// link) → neutral/off, never a random guess.
const EMPTY_LINK_STATUS = new Map<string, LinkStatus>();

export type Face = 'front' | 'back';

// ─── Viewport constants ───────────────────────────────────────────────────────
const W = 280;            // viewBox width (SVG units)
const RU_H = 32;          // height per 1U in SVG units
const EAR_W = 10;         // rack ear width
const PANEL_X0 = EAR_W + 2;  // usable panel left edge
const PANEL_X1 = W - EAR_W - 2; // usable panel right edge
const PANEL_W = PANEL_X1 - PANEL_X0;

// ─── Port shape helpers ───────────────────────────────────────────────────────

/** LED dot colours */
const LED_FILL: Record<'green' | 'amber' | 'blue' | 'red' | 'white', string> = {
  green: '#27C28B',
  amber: '#F5A623',
  blue:  '#4FA9F0',
  red:   '#F05050',
  white: '#E8E6E0',
};

interface PortRect { x: number; y: number; w: number; h: number }

/** Distribute `count` ports into `rows` even rows across [x0,x1] × [y0,y1]. */
function distributeRects(
  count: number,
  x0: number,
  x1: number,
  y0: number,
  y1: number,
  rows: 1 | 2,
  gap = 1.5,
): PortRect[] {
  if (count <= 0) return [];
  const perRow = Math.ceil(count / rows);
  const usedRows = Math.min(rows, Math.ceil(count / perRow));
  const cellW = (x1 - x0) / perRow;
  const cellH = (y1 - y0) / usedRows;
  const pw = Math.max(2, cellW - gap);
  const ph = Math.max(2, cellH - gap);
  return Array.from({ length: count }, (_, i) => ({
    x: x0 + (i % perRow) * cellW,
    y: y0 + Math.floor(i / perRow) * cellH,
    w: pw,
    h: ph,
  }));
}

/** Render a single port rectangle into SVG elements. */
function renderPort(
  pt: PortType,
  r: PortRect,
  idx: number,
  accent: string,
  key: string,
  poe?: boolean,
  status?: LinkStatus,
): React.ReactNode {
  const cx = r.x + r.w / 2;
  const cy = r.y + r.h / 2;

  if (pt === 'drive-sff' || pt === 'drive-lff') {
    // drive bay: outer slot + inner handle bar + status pip
    const lit = idx % 5 === 0; // every 5th bay glows
    return (
      <g key={key}>
        <rect x={r.x} y={r.y} width={r.w} height={r.h} rx="1" fill="#0e0e0e" stroke="#3d3d3a" strokeWidth="0.6" />
        <rect x={r.x + r.w * 0.08} y={r.y + r.h * 0.3} width={r.w * 0.55} height={r.h * 0.38} rx="0.5" fill="#252524" />
        <circle cx={r.x + r.w * 0.84} cy={cy} r={Math.min(r.h * 0.14, 1.5)} fill={lit ? '#27C28B' : '#2b2b28'} />
      </g>
    );
  }

  if (pt === 'pon') {
    // round SC/APC connector
    const rad = Math.min(r.w, r.h) * 0.4;
    return (
      <g key={key}>
        <circle cx={cx} cy={cy} r={rad} fill="#0d1b16" stroke="#2e7f52" strokeWidth="0.6" />
        <circle cx={cx} cy={cy} r={rad * 0.45} fill="#0a1510" />
      </g>
    );
  }

  if (pt === 'console-rj45' || pt === 'console-usb') {
    const isUsb = pt === 'console-usb';
    return (
      <g key={key}>
        <rect x={r.x} y={r.y + r.h * 0.1} width={r.w} height={r.h * 0.8} rx="0.8"
          fill={isUsb ? '#1a1a30' : '#111'} stroke={isUsb ? '#6060c0' : '#555'} strokeWidth="0.5" />
        {isUsb && (
          <rect x={r.x + r.w * 0.2} y={r.y + r.h * 0.3} width={r.w * 0.6} height={r.h * 0.4}
            rx="0.3" fill="#2a2a50" />
        )}
      </g>
    );
  }

  if (pt === 'mgmt-rj45') {
    return (
      <g key={key}>
        <rect x={r.x} y={r.y + r.h * 0.15} width={r.w} height={r.h * 0.7} rx="0.6"
          fill="#161616" stroke="#3a8a3a" strokeWidth="0.5" />
        <rect x={r.x + r.w * 0.25} y={r.y + r.h * 0.55} width={r.w * 0.5} height={r.h * 0.25}
          rx="0.3" fill="#1f401f" />
      </g>
    );
  }

  if (pt === 'usb') {
    return (
      <rect key={key} x={r.x} y={r.y + r.h * 0.2} width={r.w} height={r.h * 0.6}
        rx="0.4" fill="#1a1a30" stroke="#5050a0" strokeWidth="0.5" />
    );
  }

  // sfp / sfp+ / sfp28 / qsfp28
  if (pt === 'sfp' || pt === 'sfp+' || pt === 'sfp28' || pt === 'qsfp28') {
    const isQsfp = pt === 'qsfp28';
    // cage: dark rect with accent-tinted top edge
    return (
      <g key={key}>
        <rect x={r.x} y={r.y} width={r.w} height={r.h}
          rx="0.6" fill="#0c0c0c" stroke={isQsfp ? accent : '#383838'} strokeWidth={isQsfp ? '0.6' : '0.4'} />
        <rect x={r.x + r.w * 0.08} y={r.y + r.h * 0.08} width={r.w * 0.84} height={r.h * 0.28}
          fill={`${accent}22`} />
        {/* bail latch suggestion */}
        <rect x={r.x + r.w * 0.35} y={r.y + r.h * 0.72} width={r.w * 0.3} height={r.h * 0.16}
          rx="0.3" fill="#252525" />
      </g>
    );
  }

  // rj45 — notched cage with 2 status LEDs, driven by the real link status
  // (Rack#1). No status for this port (no interface / no link) -> off, not a
  // random guess.
  const linkColor = status ? linkStatusColors[status] : '#1a3d2a';
  const poeActive = poe && status === 'up';
  const secondaryColor = poeActive ? '#F5A623' : status === 'up' ? '#1BA0D7' : '#1a1a2a';
  return (
    <g key={key}>
      <rect x={r.x} y={r.y} width={r.w} height={r.h} rx="0.8" fill="#141414" stroke="#454542" strokeWidth="0.4" />
      {/* notch tab */}
      <rect x={r.x + r.w * 0.28} y={r.y + r.h * 0.62} width={r.w * 0.44} height={r.h * 0.3} rx="0.3" fill="#2c2c2a" />
      {/* link LED */}
      <circle cx={r.x + r.w * 0.2} cy={r.y + r.h * 0.25} r={Math.min(r.h * 0.12, 1.2)}
        fill={linkColor} />
      {/* speed / PoE LED */}
      <circle cx={r.x + r.w * 0.8} cy={r.y + r.h * 0.25} r={Math.min(r.h * 0.12, 1.2)}
        fill={secondaryColor} />
    </g>
  );
}

// ─── Zone layout engine ───────────────────────────────────────────────────────

/**
 * Lay out port zones left→right across [panelX0, panelX1].
 * fill zones share the remaining width after fixed/fractional zones are allocated.
 */
function layoutZones(
  zones: PortZone[],
  x0: number,
  x1: number,
  _y0: number,
  _y1: number,
): { zone: PortZone; zx0: number; zx1: number }[] {
  const totalW = x1 - x0;
  // First pass: measure fixed zones
  const fixedW = zones.reduce((s, z) => s + (z.widthFraction ? z.widthFraction * totalW : 0), 0);
  const fillCount = zones.filter((z) => z.align === 'fill' && !z.widthFraction).length;
  const fillW = fillCount > 0 ? (totalW - fixedW) / fillCount : 0;

  const result: { zone: PortZone; zx0: number; zx1: number }[] = [];
  let cursor = x0;
  // Sort: left-aligned first, fill second, right-aligned last
  const sorted = [...zones].sort((a, b) => {
    const order = { left: 0, fill: 1, right: 2 };
    return order[a.align] - order[b.align];
  });

  // But right-aligned zones get their space reserved from the right
  const rightZones = sorted.filter((z) => z.align === 'right');
  const otherZones = sorted.filter((z) => z.align !== 'right');

  // Reserve right zone widths
  const rightAllocations = rightZones.map((z) => ({
    zone: z,
    w: z.widthFraction ? z.widthFraction * totalW : fillW,
  }));
  let rightEdge = x1;
  const rightResults: typeof result = [];
  for (const ra of [...rightAllocations].reverse()) {
    const zx1 = rightEdge;
    const zx0 = rightEdge - ra.w;
    rightResults.unshift({ zone: ra.zone, zx0, zx1 });
    rightEdge -= ra.w;
  }

  // Fill left + fill zones up to rightEdge
  for (const z of otherZones) {
    const w = z.widthFraction ? z.widthFraction * totalW : fillW;
    result.push({ zone: z, zx0: cursor, zx1: cursor + w });
    cursor += w;
  }

  return [...result, ...rightResults];
}

// ─── Front panel renderer ─────────────────────────────────────────────────────

function renderFront(
  dt: DeviceType,
  H: number,
  accent: string,
  node: NodeModel,
  linkStatusByIface: Map<string, LinkStatus>,
  cableMode: boolean,
  pendingIface: string | null | undefined,
  onPortClick: ((ifaceId: string) => void) | undefined,
): React.ReactNode[] {
  const els: React.ReactNode[] = [];
  const panelY0 = H * 0.20;
  const panelY1 = H * 0.88;

  // LED column on left (inside panel, after brand stripe)
  const ledX = PANEL_X0 + 7;
  let ledY = panelY0 + 2;
  for (const led of dt.front.leds) {
    els.push(
      <g key={`led-${led.label}`}>
        <circle cx={ledX} cy={ledY} r={1.4} fill={LED_FILL[led.color]} />
        <text x={ledX + 3} y={ledY + 1} fontSize="3.5" fontFamily="monospace" fill="#6a6860">
          {led.label}
        </text>
      </g>
    );
    ledY += 6;
  }

  // LCD panel (Ubiquiti-style)
  const ledBlockW = dt.front.leds.length > 0 ? 24 : 0;
  const portX0 = PANEL_X0 + (dt.brand.badge === 'stripe' ? 6 : 2) + ledBlockW;

  if (dt.front.hasLcd) {
    const lcdW = 22;
    const lcdX = dt.front.lcdPos === 'center' ? (PANEL_X0 + PANEL_X1) / 2 - lcdW / 2 : portX0;
    els.push(
      <g key="lcd">
        <rect x={lcdX} y={H * 0.3} width={lcdW} height={H * 0.4} rx="1"
          fill="#0b140d" stroke="#2a6a2a" strokeWidth="0.5" />
        <rect x={lcdX + 2} y={H * 0.36} width={lcdW - 4} height={H * 0.12}
          rx="0.5" fill="#1a3a1a" />
      </g>
    );
  }

  // Server bezel: render drive bays instead of port zones
  if (dt.front.isServerBezel) {
    const count = dt.front.portZones.flatMap((z): PortSpec[] => z.ports).reduce((s, p) => s + p.count, 0);
    const driveType: PortType = dt.front.portZones[0]?.ports[0]?.type ?? 'drive-sff';
    const bays = distributeRects(count, portX0, PANEL_X1 - 2, H * 0.10, H * 0.90, dt.uHeight >= 2 ? 2 : 1, 2);
    bays.forEach((r, i) => els.push(renderPort(driveType, r, i, accent, `bay-${i}`)));
    return els;
  }

  // Port zones
  const lcdExtraX = dt.front.hasLcd ? 26 : 0;
  const zonesX0 = portX0 + lcdExtraX;
  const zonesX1 = PANEL_X1 - 2;

  const laidOut = layoutZones(dt.front.portZones, zonesX0, zonesX1, panelY0, panelY1);
  let portIdx = 0;

  for (const { zone, zx0, zx1 } of laidOut) {
    for (const spec of zone.ports) {
      const rects = distributeRects(spec.count, zx0, zx1, panelY0, panelY1, zone.rows, 1.5);
      for (let i = 0; i < rects.length; i++) {
        const r = rects[i]!;
        // Ordinal position of this port on the faceplate maps 1:1 to the
        // node's declared interfaces (Rack#1) — the only correlation the
        // schematic engine has, since ports aren't individually modelled.
        const portOrdinal = portIdx;
        const iface = node.interfaces?.[portOrdinal];
        const status = iface ? linkStatusByIface.get(iface.id) : undefined;
        portIdx++;
        const portEl = renderPort(spec.type, r, portOrdinal, accent, `port-${spec.type}-${portIdx}`, spec.poe, status);
        // Cable Mode (E1): wrap the port in a clickable <g> with a state ring.
        // Occupancy comes from linkStatusByIface (already derived from
        // links[].a_iface/b_iface upstream) — no new field, no new Set.
        if (cableMode && iface && onPortClick) {
          const occupied = linkStatusByIface.has(iface.id);
          const isPending = pendingIface === iface.id;
          els.push(
            <g
              key={`portwrap-${iface.id}`}
              className={cn('group', occupied ? 'cursor-not-allowed' : 'cursor-pointer')}
              onClick={() => onPortClick(iface.id)}
            >
              {portEl}
              {(occupied || isPending) && (
                <title>{occupied ? 'Port in use — unlink first' : 'Pending — click destination port'}</title>
              )}
              <rect
                x={r.x - 1}
                y={r.y - 1}
                width={r.w + 2}
                height={r.h + 2}
                rx="1.2"
                fill="none"
                stroke={occupied ? linkStatusColors.unknown : 'rgb(var(--ng-accent-rgb))'}
                strokeWidth={isPending ? 1.4 : 1}
                strokeDasharray={occupied ? '1.5 1.5' : undefined}
                className={cn(
                  'pointer-events-none',
                  isPending ? 'opacity-100 animate-pulse' : occupied ? 'opacity-70' : 'opacity-0 transition-opacity group-hover:opacity-90',
                )}
              />
            </g>,
          );
        } else {
          els.push(portEl);
        }
        // tiny label above first port in zone (WAN/DMZ etc)
        if (spec.label && i === 0) {
          els.push(
            <text
              key={`lbl-${spec.type}-${portIdx}`}
              x={r.x + r.w / 2}
              y={panelY0 - 1}
              textAnchor="middle"
              fontSize="3"
              fontFamily="monospace"
              fill="#6a6860"
            >
              {spec.label}
            </text>
          );
        }
      }
    }
  }

  return els;
}

// ─── Back panel renderer ──────────────────────────────────────────────────────

function renderBack(dt: DeviceType, H: number, accent: string): React.ReactNode[] {
  const els: React.ReactNode[] = [];
  const y0 = H * 0.15;
  const y1 = H * 0.88;
  const mid = (y0 + y1) / 2;
  let cursor = PANEL_X0 + 4;

  for (const block of dt.rear.blocks) {
    const count = block.count ?? 1;

    if (block.type === 'psu-slot') {
      for (let i = 0; i < count; i++) {
        const slotW = Math.min(44, (PANEL_W - 8) / (count * 1.2));
        els.push(
          <g key={`psu-${i}`}>
            <rect x={cursor} y={y0 + H * 0.05} width={slotW} height={y1 - y0 - H * 0.08}
              rx="2" fill="#1a1a18" stroke="#454542" strokeWidth="0.5" />
            <circle cx={cursor + slotW * 0.7} cy={mid} r={slotW * 0.15}
              fill="none" stroke="#3a3a38" strokeWidth="0.6" />
            <rect x={cursor + 2} y={y0 + H * 0.12} width={slotW * 0.4} height={(y1 - y0) * 0.08}
              rx="0.5" fill="#252523" />
          </g>
        );
        cursor += slotW + 3;
      }
    } else if (block.type === 'fan-tray') {
      const rad = Math.min((y1 - y0) * 0.35, 9);
      for (let i = 0; i < count; i++) {
        const cx = cursor + rad;
        els.push(
          <g key={`fan-${cursor}-${i}`}>
            <circle cx={cx} cy={mid} r={rad} fill="none" stroke="#3d3d3a" strokeWidth="0.8" />
            <circle cx={cx} cy={mid} r={rad * 0.35} fill="#1a1a18" />
            {[0, 90, 180, 270].map((deg) => {
              const angle = (deg * Math.PI) / 180;
              return (
                <line key={deg} x1={cx} y1={mid}
                  x2={cx + Math.cos(angle) * rad * 0.75}
                  y2={mid + Math.sin(angle) * rad * 0.75}
                  stroke="#3d3d3a" strokeWidth="0.6" />
              );
            })}
          </g>
        );
        cursor += rad * 2 + 4;
      }
    } else if (block.type === 'vent-grille') {
      const gw = 30;
      for (let row = 0; row < 3; row++) {
        els.push(
          <line key={`vent-${row}`}
            x1={cursor} y1={y0 + (y1 - y0) * (0.25 + row * 0.25)}
            x2={cursor + gw} y2={y0 + (y1 - y0) * (0.25 + row * 0.25)}
            stroke="#3d3d3a" strokeWidth="1" />
        );
      }
      cursor += gw + 4;
    } else if (block.type === 'iec-inlet') {
      const iw = 14, ih = (y1 - y0) * 0.5;
      els.push(
        <g key="iec">
          <rect x={cursor} y={mid - ih / 2} width={iw} height={ih}
            rx="2" fill="#181816" stroke="#3d3d3a" strokeWidth="0.6" />
          <path d={`M ${cursor + iw * 0.3} ${mid - ih * 0.25} l ${iw * 0.4} 0 l 0 ${ih * 0.5} l -${iw * 0.4} 0 Z`}
            fill="#252523" />
        </g>
      );
      cursor += iw + 4;
    } else if (block.type === 'ground-lug') {
      els.push(
        <g key="ground">
          <rect x={cursor} y={mid - 3} width={8} height={6}
            rx="0.5" fill="#b5952f" stroke="#8a6f22" strokeWidth="0.4" />
          <line x1={cursor + 4} y1={mid - 3} x2={cursor + 4} y2={mid - 7}
            stroke="#b5952f" strokeWidth="1.2" />
        </g>
      );
      cursor += 12;
    } else if (block.type === 'port-zone' && block.portZone) {
      const zw = 60;
      const rects = layoutZones(
        [block.portZone],
        cursor,
        cursor + zw,
        y0,
        y1,
      );
      let pi = 0;
      for (const { zone, zx0, zx1 } of rects) {
        for (const spec of zone.ports) {
          const pr = distributeRects(spec.count, zx0, zx1, y0, y1, zone.rows, 1.5);
          pr.forEach((r) => {
            els.push(renderPort(spec.type, r, pi++, accent, `rp-${spec.type}-${pi}`));
          });
        }
      }
      cursor += zw + 4;
    }
  }

  return els;
}

// ─── Port-position export (Rack#1 slice #4) ───────────────────────────────────

/**
 * Front-panel port center for each of the node's interfaces, as a fraction
 * (0..1) of the faceplate's own width/height. Mirrors the zone-layout walk in
 * `renderFront` (portX0/zonesX0/layoutZones/distributeRects) so cable
 * endpoints in RackElevationPanel can land on the real port instead of the
 * device's vertical mid-point. Only the front face has iface↔port ordinal
 * correlation (see renderFront) — an interface with no matching port (or a
 * server bezel, whose slots are drive bays, not network ports) is simply
 * absent from the map; callers fall back to the old center-of-block anchor.
 */
export function frontPortFractions(node: NodeModel, span: number): Map<string, { x: number; y: number }> {
  const dt = resolveDeviceType(node.nos, node.kind, node.interfaces);
  const out = new Map<string, { x: number; y: number }>();
  if (dt.front.isServerBezel) return out;

  const H = Math.max(RU_H, span * RU_H);
  const panelY0 = H * 0.20;
  const panelY1 = H * 0.88;
  const ledBlockW = dt.front.leds.length > 0 ? 24 : 0;
  const portX0 = PANEL_X0 + (dt.brand.badge === 'stripe' ? 6 : 2) + ledBlockW;
  const lcdExtraX = dt.front.hasLcd ? 26 : 0;
  const zonesX0 = portX0 + lcdExtraX;
  const zonesX1 = PANEL_X1 - 2;

  const laidOut = layoutZones(dt.front.portZones, zonesX0, zonesX1, panelY0, panelY1);
  let portIdx = 0;
  for (const { zone, zx0, zx1 } of laidOut) {
    for (const spec of zone.ports) {
      const rects = distributeRects(spec.count, zx0, zx1, panelY0, panelY1, zone.rows, 1.5);
      for (const r of rects) {
        const iface = node.interfaces?.[portIdx];
        portIdx++;
        if (!iface) continue;
        out.set(iface.id, { x: (r.x + r.w / 2) / W, y: (r.y + r.h / 2) / H });
      }
    }
  }
  return out;
}

/** One physical port slot in on-screen ordinal order, for the device console's
 *  compact port strip (docs/design/stitch-html/clay/device-console/NOTES.md
 *  point #2: same catalog data as the rack faceplate, not the pixel-precise
 *  SVG). `iface` is the node's own interface at that ordinal, or null for an
 *  unprovisioned slot (Rack#1: no data for a port → neutral, never guessed). */
export interface ConsolePort {
  ordinal: number;
  type: PortType;
  poeCapable: boolean;
  label?: string;
  iface: Interface | null;
}

/** Same zone/ordinal walk as `frontPortFractions`, minus the pixel geometry —
 *  reused so the console lists ports in the exact order the rack faceplate
 *  maps them, keeping the two views consistent. */
export function frontPortList(node: NodeModel): ConsolePort[] {
  const dt = resolveDeviceType(node.nos, node.kind, node.interfaces);
  if (dt.front.isServerBezel) return [];

  const zonesOrder = layoutZones(dt.front.portZones, 0, 1, 0, 1);
  const out: ConsolePort[] = [];
  let ordinal = 0;
  for (const { zone } of zonesOrder) {
    for (const spec of zone.ports) {
      for (let n = 0; n < spec.count; n++) {
        out.push({
          ordinal,
          type: spec.type,
          poeCapable: Boolean(spec.poe),
          label: spec.label,
          iface: node.interfaces[ordinal] ?? null,
        });
        ordinal++;
      }
    }
  }
  return out;
}

// ─── Main component ───────────────────────────────────────────────────────────

interface Props {
  node: NodeModel;
  span: number;
  face: Face;
  /** iface id -> link status (Rack#1). Omitted/empty -> every port renders neutral. */
  linkStatusByIface?: Map<string, LinkStatus>;
  /** E1: when true, front-face ports become clickable for link creation. */
  cableMode?: boolean;
  /** E1: the iface id currently locked as the link's first endpoint (any device). */
  pendingIface?: string | null;
  /** E1: fired with the clicked port's iface id when cableMode is on. */
  onPortClick?: (ifaceId: string) => void;
}

export function DeviceFaceplate({
  node,
  span,
  face,
  linkStatusByIface = EMPTY_LINK_STATUS,
  cableMode = false,
  pendingIface = null,
  onPortClick,
}: Props) {
  const dt = resolveDeviceType(node.nos, node.kind, node.interfaces);
  const H = Math.max(RU_H, span * RU_H);
  const { accent, chassis, label } = dt.brand;

  // Chassis gradient: top-lit; MikroTik white chassis gets a lighter gradient
  const isLight = chassis.toUpperCase() > '#AAAAAA'; // heuristic: light chassis
  const gradTop = isLight ? lighten(chassis, 0.15) : lighten(chassis, 0.12);
  const gradBot = isLight ? chassis : darken(chassis, 0.08);
  const gradId = `chs-${node.id}`;

  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      preserveAspectRatio="none"
      className="h-full w-full"
      role="img"
      aria-label={`${label} ${dt.model} ${face} view`}
    >
      <defs>
        <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor={gradTop} />
          <stop offset="0.45" stopColor={chassis} />
          <stop offset="1" stopColor={gradBot} />
        </linearGradient>
      </defs>

      {/* Chassis body */}
      <rect x="0" y="0" width={W} height={H} rx="2.5"
        fill={`url(#${gradId})`} stroke={darken(chassis, 0.2)} strokeWidth="0.5" />

      {/* Rack ears with screw holes */}
      {[EAR_W / 2, W - EAR_W / 2].map((cx) => (
        <g key={cx}>
          <rect x={cx - EAR_W / 2} y="0.5" width={EAR_W} height={H - 1}
            rx="1.5" fill={darken(chassis, 0.1)} />
          <circle cx={cx} cy={H * 0.22} r="1.8"
            fill="none" stroke={darken(chassis, 0.3)} strokeWidth="0.6" />
          <circle cx={cx} cy={H * 0.78} r="1.8"
            fill="none" stroke={darken(chassis, 0.3)} strokeWidth="0.6" />
        </g>
      ))}

      {/* Brand stripe */}
      <rect x={PANEL_X0 + 1} y={H * 0.14} width="3" height={H * 0.72}
        fill={accent} rx="1.5" />

      {/* Brand label + model name */}
      <text
        x={PANEL_X0 + 7}
        y={H * 0.32}
        fontSize={Math.min(7, H * 0.24)}
        fontFamily="monospace"
        fontWeight="600"
        fill={isLight ? '#1a1a18' : '#e8e6e0'}
      >
        {label}
      </text>
      <text
        x={PANEL_X0 + 7}
        y={H * 0.55}
        fontSize={Math.min(5.5, H * 0.18)}
        fontFamily="monospace"
        fill={isLight ? '#5a5a58' : '#8a8880'}
      >
        {dt.model}
      </text>
      <text
        x={PANEL_X0 + 7}
        y={H * 0.76}
        fontSize={Math.min(5, H * 0.16)}
        fontFamily="monospace"
        fill={isLight ? '#8a8880' : '#6a6860'}
      >
        {node.name}
      </text>

      {/* Port field / back blocks */}
      {face === 'front'
        ? renderFront(dt, H, accent, node, linkStatusByIface, cableMode, pendingIface, onPortClick)
        : renderBack(dt, H, accent)}
    </svg>
  );
}

// ─── Tiny colour helpers (no dependency needed) ───────────────────────────────

function hexToRgb(hex: string): [number, number, number] {
  const h = hex.replace('#', '');
  const n = parseInt(h.length === 3 ? h.split('').map((c) => c + c).join('') : h, 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}
function rgbToHex(r: number, g: number, b: number): string {
  return '#' + [r, g, b].map((v) => Math.max(0, Math.min(255, Math.round(v))).toString(16).padStart(2, '0')).join('');
}
function lighten(hex: string, amt: number): string {
  const [r, g, b] = hexToRgb(hex);
  return rgbToHex(r + 255 * amt, g + 255 * amt, b + 255 * amt);
}
function darken(hex: string, amt: number): string {
  const [r, g, b] = hexToRgb(hex);
  return rgbToHex(r - 255 * amt, g - 255 * amt, b - 255 * amt);
}
