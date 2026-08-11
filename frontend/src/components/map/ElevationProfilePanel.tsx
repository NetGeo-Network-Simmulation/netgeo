/**
 * ElevationProfilePanel — Phase B1 elevation-profile tool (docs/design/05).
 *
 * Shows the real terrain profile between the two points picked with the map's
 * "Elevation Profile" tool, plus a line-of-sight overlay and a Fresnel/LoS
 * verdict. Terrain + verdict come straight from the backend `los-check`
 * endpoint (which proxies a DEM provider via `app/services/elevation.py`), so
 * no elevation math lives here.
 *
 * The chart is a self-contained inline SVG (no chart dependency), shared with
 * the RF workspace via `./ProfileChart`. Theme-aware via `fg`/`accent`/status
 * tokens.
 */
import { useEffect, useState } from 'react';
import { Mountain, X, Check, AlertTriangle, Loader2, RefreshCw } from 'lucide-react';
import { useMapStore, type ProfileData } from '@/store/mapStore';
import { zc } from '@/theme/z';
import { ProfileChart, TerrainSourceNotice, verdictOf, fmtKm } from './ProfileChart';

/* -------------------------------------------------------------------------- */
/* Verdict chips                                                               */
/* -------------------------------------------------------------------------- */
function Chip({ color, icon: Icon, label, value }: { color: string; icon: typeof Check; label: string; value: string }) {
  return (
    <div className="flex items-center gap-1.5 rounded-lg border border-fg/10 bg-recess/40 px-2.5 py-1">
      <Icon className="h-3.5 w-3.5 shrink-0" style={{ color }} />
      <span className="text-[10px] uppercase tracking-wide text-fg/40">{label}</span>
      <span className="ml-auto text-xs font-medium text-fg/80">{value}</span>
    </div>
  );
}

function Summary({ data }: { data: ProfileData }) {
  const v = verdictOf(data);
  return (
    <div className="grid grid-cols-2 gap-1.5 sm:grid-cols-4">
      <Chip color="#5C8AFF" icon={Mountain} label="Distance" value={fmtKm(data.totalDistanceM)} />
      <Chip color={v.color} icon={v.icon} label="Sight line" value={data.losClear ? 'Clear' : 'Blocked'} />
      <Chip
        color={data.fresnelClear ? '#27C28B' : '#F5A623'}
        icon={data.fresnelClear ? Check : AlertTriangle}
        label="Fresnel"
        value={data.fresnelClear ? 'Clear' : 'Obstructed'}
      />
      <Chip
        color={data.worstObstructionM > 0 ? '#F5A623' : '#27C28B'}
        icon={data.worstObstructionM > 0 ? AlertTriangle : Check}
        label="Worst obstr."
        value={data.worstObstructionM > 0 ? `${data.worstObstructionM.toFixed(1)} m` : 'none'}
      />
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Link parameter inputs (heights + frequency drive the Fresnel hint)          */
/* -------------------------------------------------------------------------- */
function NumField({
  label, unit, value, step, min, onCommit,
}: {
  label: string; unit: string; value: number; step: number; min: number;
  onCommit: (v: number) => void;
}) {
  const [local, setLocal] = useState(String(value));
  useEffect(() => setLocal(String(value)), [value]);
  const commit = () => {
    const n = Number(local);
    if (Number.isFinite(n) && n >= min) onCommit(n);
    else setLocal(String(value));
  };
  return (
    <label className="flex items-center gap-1.5 text-[10px] text-fg/50">
      <span className="uppercase tracking-wide">{label}</span>
      <input
        type="number" inputMode="decimal" step={step} min={min}
        value={local}
        onChange={(e) => setLocal(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => e.key === 'Enter' && (e.target as HTMLInputElement).blur()}
        aria-label={`${label} (${unit})`}
        className="w-14 rounded-md border border-fg/15 bg-recess/50 px-1.5 py-0.5 text-right font-mono text-xs text-fg/85 focus:border-accent/50 focus:outline-none"
      />
      <span className="text-fg/30">{unit}</span>
    </label>
  );
}

/* -------------------------------------------------------------------------- */
/* Panel                                                                       */
/* -------------------------------------------------------------------------- */
export function ElevationProfilePanel() {
  const pts = useMapStore((s) => s.profilePts);
  const data = useMapStore((s) => s.profileData);
  const loading = useMapStore((s) => s.profileLoading);
  const error = useMapStore((s) => s.profileError);
  const txH = useMapStore((s) => s.profileTxH);
  const rxH = useMapStore((s) => s.profileRxH);
  const freq = useMapStore((s) => s.profileFreq);
  const setParams = useMapStore((s) => s.setProfileParams);
  const runProfile = useMapStore((s) => s.runProfile);
  const clearProfile = useMapStore((s) => s.clearProfile);

  // Escape closes the tool (a11y escape route).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && clearProfile();
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [clearProfile]);

  // Stay hidden until both endpoints are picked — a center-bottom panel would
  // otherwise cover the map and swallow the click for the 2nd point.
  if (pts.length < 2) return null;

  return (
    <section
      role="region"
      aria-label="Elevation profile"
      className={`pointer-events-auto absolute bottom-4 left-1/2 ${zc.workspace} w-[min(46rem,calc(100%-2rem))] -translate-x-1/2 animate-fade-in`}
    >
      <div className="glass-strong rounded-xl border border-fg/15 shadow-glass-lg">
        {/* Header */}
        <div className="flex items-center gap-2 border-b border-fg/10 px-3 py-2">
          <Mountain className="h-4 w-4 text-accent" />
          <h2 className="text-xs font-semibold uppercase tracking-wider text-fg/70">Elevation Profile</h2>
          <div className="ml-auto flex items-center gap-3">
            <NumField label="TX" unit="m" value={txH} step={1} min={0} onCommit={(v) => setParams({ txH: v })} />
            <NumField label="RX" unit="m" value={rxH} step={1} min={0} onCommit={(v) => setParams({ rxH: v })} />
            <NumField label="Freq" unit="GHz" value={freq} step={0.1} min={0.1} onCommit={(v) => setParams({ freq: v })} />
            <button
              onClick={clearProfile}
              aria-label="Close elevation profile"
              className="grid h-6 w-6 place-items-center rounded text-fg/50 hover:bg-fg/10 hover:text-fg"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
        </div>

        {/* Body */}
        <div className="px-3 py-2.5">
          {loading ? (
            <div className="grid h-40 place-items-center gap-2 text-xs text-fg/50">
              <Loader2 className="h-5 w-5 animate-spin text-accent" />
              Fetching terrain elevation…
            </div>
          ) : error ? (
            <div className="grid h-40 place-items-center gap-2 text-center text-xs text-fg/60">
              <AlertTriangle className="h-5 w-5 text-warning" />
              <p className="max-w-xs">{error}</p>
              <button
                onClick={() => void runProfile()}
                className="mt-1 flex items-center gap-1.5 rounded-lg border border-fg/15 px-3 py-1.5 text-fg/70 hover:border-accent/40 hover:text-accent"
              >
                <RefreshCw className="h-3.5 w-3.5" /> Retry
              </button>
            </div>
          ) : data ? (
            <div className="space-y-2.5">
              <TerrainSourceNotice source={data.terrainSource} />
              <ProfileChart
                points={data.points}
                totalDistanceM={data.totalDistanceM}
                losClear={data.losClear}
                fresnelClear={data.fresnelClear}
                txH={txH}
                rxH={rxH}
              />
              <Summary data={data} />
            </div>
          ) : null}
        </div>
      </div>
    </section>
  );
}
