/**
 * ProfileChart — self-contained inline-SVG terrain/LoS profile (no chart dep).
 *
 * Presentational only: a filled terrain silhouette with a straight TX→RX sight
 * line coloured by the backend verdict. Shared by the map's ElevationProfilePanel
 * and the RF Planning workspace so the SVG geometry lives in exactly one place.
 * All LoS/Fresnel *verdicts* come from the server; the optional Fresnel-clearance
 * band drawn here is a visualisation of the first-zone geometry, not a new check.
 */
import { Check, Ban, AlertTriangle } from 'lucide-react';

const VB_W = 760;
const VB_H = 200;
const M = { top: 14, right: 14, bottom: 24, left: 46 };
const PLOT_W = VB_W - M.left - M.right;
const PLOT_H = VB_H - M.top - M.bottom;

export interface ChartPoint {
  distance_m: number;
  elevation_m: number;
}

interface Verdict {
  color: string; // status token hex (theme-independent, AA on both surfaces)
  dashed: boolean;
  label: string;
  icon: typeof Check;
}

export function verdictOf(d: { losClear: boolean; fresnelClear: boolean }): Verdict {
  if (!d.losClear) return { color: '#FF4D4F', dashed: true, label: 'LoS blocked', icon: Ban };
  if (!d.fresnelClear)
    return { color: '#F5A623', dashed: true, label: 'Fresnel partially obstructed', icon: AlertTriangle };
  return { color: '#27C28B', dashed: false, label: 'Line of sight clear', icon: Check };
}

export function fmtKm(m: number): string {
  const km = m / 1000;
  return km >= 10 ? `${km.toFixed(1)} km` : `${km.toFixed(2)} km`;
}

/**
 * F67 — honest terrain provenance. The backend degrades to a flat-terrain
 * profile (elevation_m=0 everywhere) when its DEM provider is unreachable, so
 * offline/homelab installs keep working — but that result must never look
 * like a real terrain measurement. A visible (not shouty) reliability notice,
 * shown wherever a `terrain_source` profile is rendered.
 */
export function TerrainSourceNotice({ source }: { source: 'dem' | 'flat_fallback' }) {
  if (source !== 'flat_fallback') return null;
  return (
    <div className="flex items-start gap-1.5 rounded-lg border border-warning/25 bg-warning/10 px-2.5 py-1.5 text-[11px] text-fg/70">
      <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-warning" />
      <span>
        Flat-terrain estimate — the elevation provider is unreachable, so this result assumes flat ground instead of real terrain.
      </span>
    </div>
  );
}

export function ProfileChart({
  points,
  totalDistanceM,
  losClear,
  fresnelClear,
  txH,
  rxH,
  freqGhz,
  fresnelBand = false,
}: {
  points: ChartPoint[];
  totalDistanceM: number;
  losClear: boolean;
  fresnelClear: boolean;
  txH: number;
  rxH: number;
  /** Link frequency (GHz) — required to draw the Fresnel band. */
  freqGhz?: number;
  /** Draw the 60% first-Fresnel-zone clearance band around the sight line. */
  fresnelBand?: boolean;
}) {
  const pts = points;
  const total = totalDistanceM || pts[pts.length - 1]?.distance_m || 1;

  // Antenna tops (metres ASL) anchor the sight line.
  const h1 = (pts[0]?.elevation_m ?? 0) + txH;
  const h2 = (pts[pts.length - 1]?.elevation_m ?? 0) + rxH;

  const terrainVals = pts.map((p) => p.elevation_m);
  let yMin = Math.min(...terrainVals);
  let yMax = Math.max(...terrainVals, h1, h2);
  if (yMax - yMin < 1) yMax = yMin + 1; // avoid a zero range on flat terrain
  const pad = (yMax - yMin) * 0.08;
  yMin -= pad;
  yMax += pad;

  const xOf = (d: number) => M.left + (d / total) * PLOT_W;
  const yOf = (v: number) => M.top + (1 - (v - yMin) / (yMax - yMin)) * PLOT_H;
  const baseline = M.top + PLOT_H;

  const terrainLine = pts.map((p) => `${xOf(p.distance_m).toFixed(1)},${yOf(p.elevation_m).toFixed(1)}`).join(' ');
  const terrainArea =
    `M ${xOf(0).toFixed(1)},${baseline.toFixed(1)} ` +
    `L ${terrainLine.split(' ').join(' L ')} ` +
    `L ${xOf(total).toFixed(1)},${baseline.toFixed(1)} Z`;

  const v = verdictOf({ losClear, fresnelClear });

  // Optional 60% first-Fresnel-zone clearance band around the LOS line.
  // r1(d) = sqrt(λ·d1·d2 / D); λ = c/f (metres). Offset the sight-line elevation
  // by ±0.6·r1 to bound the "must stay clear" region — a drawing, not a verdict.
  let fresnelPath: string | null = null;
  let fresnelLowerLine: string | null = null;
  if (fresnelBand && freqGhz && freqGhz > 0) {
    const lambda = 0.299792458 / freqGhz; // c (m/ns) / GHz → metres
    const losAt = (d: number) => h1 + (h2 - h1) * (d / total);
    const r1 = (d: number) => {
      const d1 = d;
      const d2 = total - d;
      return d1 <= 0 || d2 <= 0 ? 0 : Math.sqrt((lambda * d1 * d2) / total);
    };
    const upper = pts.map((p) => `${xOf(p.distance_m).toFixed(1)},${yOf(losAt(p.distance_m) + 0.6 * r1(p.distance_m)).toFixed(1)}`);
    const lower = pts.map((p) => `${xOf(p.distance_m).toFixed(1)},${yOf(losAt(p.distance_m) - 0.6 * r1(p.distance_m)).toFixed(1)}`);
    fresnelPath = `M ${upper.join(' L ')} L ${[...lower].reverse().join(' L ')} Z`;
    fresnelLowerLine = `M ${lower.join(' L ')}`;
  }

  const yTicks = [yMin + pad, (yMin + yMax) / 2, yMax - pad];
  const xTicks = [0, total / 2, total];

  return (
    <svg
      viewBox={`0 0 ${VB_W} ${VB_H}`}
      className="h-auto w-full"
      preserveAspectRatio="xMidYMid meet"
      role="img"
      aria-label={`Elevation profile over ${fmtKm(total)}. ${v.label}.`}
    >
      {/* Y grid + labels */}
      {yTicks.map((t) => (
        <g key={`y${t}`}>
          <line x1={M.left} x2={VB_W - M.right} y1={yOf(t)} y2={yOf(t)} className="stroke-fg/10" strokeWidth={1} />
          <text x={M.left - 6} y={yOf(t) + 3} textAnchor="end" className="fill-fg/45" style={{ fontSize: 10 }}>
            {Math.round(t)}
          </text>
        </g>
      ))}

      {/* X labels */}
      {xTicks.map((t, i) => (
        <text
          key={`x${t}`}
          x={xOf(t)}
          y={VB_H - 8}
          textAnchor={i === 0 ? 'start' : i === xTicks.length - 1 ? 'end' : 'middle'}
          className="fill-fg/45"
          style={{ fontSize: 10 }}
        >
          {fmtKm(t)}
        </text>
      ))}

      {/* Terrain silhouette */}
      <path d={terrainArea} className="fill-fg/[0.14] stroke-fg/40" strokeWidth={1.5} strokeLinejoin="round" />

      {/* Fresnel clearance band (RF workspace only) */}
      {fresnelPath && (
        <>
          <path d={fresnelPath} fill={v.color} fillOpacity={0.1} stroke="none" />
          {fresnelLowerLine && (
            <path d={fresnelLowerLine} fill="none" stroke={v.color} strokeWidth={1} strokeDasharray="4 4" opacity={0.6} />
          )}
        </>
      )}

      {/* Antenna masts */}
      <line x1={xOf(0)} x2={xOf(0)} y1={yOf(pts[0]?.elevation_m ?? 0)} y2={yOf(h1)} stroke={v.color} strokeWidth={1.5} opacity={0.7} />
      <line x1={xOf(total)} x2={xOf(total)} y1={yOf(pts[pts.length - 1]?.elevation_m ?? 0)} y2={yOf(h2)} stroke={v.color} strokeWidth={1.5} opacity={0.7} />

      {/* Line of sight */}
      <line
        x1={xOf(0)}
        y1={yOf(h1)}
        x2={xOf(total)}
        y2={yOf(h2)}
        stroke={v.color}
        strokeWidth={2}
        strokeDasharray={v.dashed ? '7 5' : undefined}
        strokeLinecap="round"
      />

      {/* Antenna markers */}
      <circle cx={xOf(0)} cy={yOf(h1)} r={4} fill={v.color} />
      <circle cx={xOf(total)} cy={yOf(h2)} r={4} fill={v.color} />
      <text x={xOf(0) + 6} y={yOf(h1) - 6} className="fill-fg/70" style={{ fontSize: 10, fontWeight: 600 }}>TX</text>
      <text x={xOf(total) - 6} y={yOf(h2) - 6} textAnchor="end" className="fill-fg/70" style={{ fontSize: 10, fontWeight: 600 }}>RX</text>
    </svg>
  );
}
