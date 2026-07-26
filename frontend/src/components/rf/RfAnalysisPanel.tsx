/**
 * RfAnalysisPanel — the right dock of the RF workspace (~380px). Three modes
 * (PtP / PtMP / Auto-Select) share this one panel — a segmented control below
 * the header switches between them, reusing the same tab-button, StatCard, and
 * VerdictBadge visual language throughout (design decision: no new surface for
 * NG-RF-04/05, see docs/design/14-R4-RF-PLAN.md). PtP keeps its existing
 * Summary/Link Budget/Terrain/Fresnel sub-tabs over `/api/rf/ptp`; PtMP and
 * Auto-Select are single-scroll form+result sections over `/api/rf/ptmp` and
 * `/api/rf/product-select`. All physics/ranking figures come straight from the
 * backend responses; only the status band, reliability, and capacity are
 * derived client-side (see rfLogic).
 */
import { useState } from 'react';
import { RadioTower, X, Loader2, AlertTriangle, Radio, Plus } from 'lucide-react';
import { useMapStore } from '@/store/mapStore';
import { useRfStore, RF_ANT_GAIN_DBI, type RfTab, type RfMode, type PtmpCpeInput } from '@/store/rfStore';
import { zc } from '@/theme/z';
import { ProfileChart, type ChartPoint } from '@/components/map/ProfileChart';
import {
  marginStatus,
  reliabilityPct,
  estCapacityMbps,
  STATUS_COLOR,
  STATUS_LABEL,
  fmtKm,
} from './rfLogic';
import { EndpointSelect } from './RfLinkBar';
import { Select } from '@/components/ui/Select';
import type {
  PtpResult,
  PtmpResult,
  ProductSelectResult,
  RadioCandidate,
  RadioCatalogEntry,
  RfStudyKind,
} from '@/api/client';

const TABS: { id: RfTab; label: string }[] = [
  { id: 'summary', label: 'Summary' },
  { id: 'budget', label: 'Link Budget' },
  { id: 'terrain', label: 'Terrain' },
  { id: 'fresnel', label: 'Fresnel' },
];

const MODES: { id: RfMode; label: string }[] = [
  { id: 'ptp', label: 'PtP' },
  { id: 'ptmp', label: 'PtMP' },
  { id: 'product_select', label: 'Auto-Select' },
];

function chartPoints(res: PtpResult): ChartPoint[] {
  return (res.profile?.points ?? []).map((p) => ({ distance_m: p.distance_m, elevation_m: p.elevation_m }));
}

/* -------------------------------------------------------------------------- */
/* Small building blocks                                                       */
/* -------------------------------------------------------------------------- */
function StatCard({ label, value, highlight }: { label: string; value: string; highlight?: boolean }) {
  return (
    <div
      className={
        'rounded-lg border px-2.5 py-2 ' +
        (highlight ? 'border-accent/40 bg-accent/10' : 'border-fg/10 bg-recess/40')
      }
    >
      <p className="text-[10px] uppercase tracking-wide text-fg/40">{label}</p>
      <p className={'mt-0.5 font-mono text-sm ' + (highlight ? 'text-accent' : 'text-fg/85')}>{value}</p>
    </div>
  );
}

function BudgetRow({ label, value, strong }: { label: string; value: string; strong?: boolean }) {
  return (
    <div className={'flex items-center justify-between py-1.5 ' + (strong ? 'border-t border-fg/15' : '')}>
      <span className={'text-xs ' + (strong ? 'font-medium text-fg/80' : 'text-fg/55')}>{label}</span>
      <span className={'font-mono text-xs ' + (strong ? 'text-fg/90' : 'text-fg/75')}>{value}</span>
    </div>
  );
}

/** An editable StatCard — same surface/label tokens, a number input in place of
 *  the static value. Shared by the PtMP and Auto-Select forms. */
function FieldInput({
  label,
  value,
  onChange,
  suffix,
  step = 1,
  min,
}: {
  label: string;
  value: number;
  onChange: (v: number) => void;
  suffix?: string;
  step?: number;
  min?: number;
}) {
  return (
    <label className="block rounded-lg border border-fg/10 bg-recess/40 px-2.5 py-2">
      <span className="text-[10px] uppercase tracking-wide text-fg/40">{label}</span>
      <div className="mt-0.5 flex items-center gap-1">
        <input
          type="number"
          inputMode="decimal"
          step={step}
          min={min}
          value={value}
          onChange={(e) => onChange(Number(e.target.value))}
          aria-label={label}
          className="w-full min-w-0 bg-transparent font-mono text-sm text-fg/85 focus:outline-none"
        />
        {suffix && <span className="shrink-0 text-[10px] text-fg/40">{suffix}</span>}
      </div>
    </label>
  );
}

/** Prefill picker shared by the PtMP sector form and Auto-Select candidate
 *  rows — same "reset to placeholder after pick" idiom as the saved-study
 *  select above. Fills fields from the static radio catalog; the caller
 *  decides which fields to patch (cost/bandwidth are never touched here —
 *  the catalog carries no price, and ranking would mislead on a guess). */
function CatalogPicker({
  radios,
  onSelect,
  label = 'Prefill from catalog',
}: {
  radios: RadioCatalogEntry[];
  onSelect: (r: RadioCatalogEntry) => void;
  label?: string;
}) {
  if (radios.length === 0) return null;
  return (
    <Select
      aria-label={label}
      value=""
      onChange={(v) => {
        const r = radios.find((x) => x.id === v);
        if (r) onSelect(r);
      }}
      placeholder={`${label}…`}
      options={radios.map((r) => ({ value: r.id, label: r.name }))}
      className="w-full"
    />
  );
}

function StatusChip({ res }: { res: PtpResult }) {
  const status = marginStatus(res.fade_margin_db);
  const color = STATUS_COLOR[status];
  return (
    <div className="flex items-center justify-between gap-2">
      <span
        className="inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[11px] font-semibold"
        style={{ color, backgroundColor: `${color}22` }}
      >
        <span className="h-1.5 w-1.5 rounded-full" style={{ backgroundColor: color }} />
        {STATUS_LABEL[status]}
      </span>
      <span className="text-xs text-fg/55">
        Reliability: <span className="font-mono text-fg/85">{reliabilityPct(res.fade_margin_db).toFixed(3)}%</span>
      </span>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Tab bodies                                                                  */
/* -------------------------------------------------------------------------- */
function SummaryTab({ res }: { res: PtpResult }) {
  const { freqGhz, bwMhz, aId, bId } = useRfStore();
  const devices = useMapStore((s) => s.devices);
  const a = aId ? devices.get(aId) : undefined;
  const b = bId ? devices.get(bId) : undefined;

  return (
    <div className="space-y-4">
      <StatusChip res={res} />

      <div>
        <p className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-fg/50">RF Parameters</p>
        <div className="grid grid-cols-2 gap-2">
          <StatCard label="Distance" value={fmtKm(res.distance_m)} />
          <StatCard label="Frequency" value={`${freqGhz} GHz`} />
          <StatCard label="TX Power" value={`${a?.txPower ?? '—'} dBm`} />
          <StatCard label="Antenna Gain" value={`2× ${RF_ANT_GAIN_DBI} dBi`} />
          <StatCard label="Path Loss (FSPL)" value={`${res.path_loss_db.toFixed(1)} dB`} />
          <StatCard label="Received Signal" value={`${res.rssi_dbm.toFixed(1)} dBm`} highlight />
          <StatCard label="Fade Margin" value={`${res.fade_margin_db.toFixed(1)} dB`} />
          <StatCard label="Est. Capacity" value={`${estCapacityMbps(res.rssi_dbm, bwMhz)} Mbps`} />
        </div>
      </div>

      {res.profile && (
        <div>
          <p className="mb-1.5 text-[11px] font-semibold uppercase tracking-wider text-fg/50">Elevation Profile</p>
          <ProfileChart
            points={chartPoints(res)}
            totalDistanceM={res.profile.total_distance_m}
            losClear={res.los_clear}
            fresnelClear={res.fresnel_clear}
            txH={a?.antennaHeight ?? 10}
            rxH={b?.antennaHeight ?? 5}
            freqGhz={freqGhz}
            fresnelBand
          />
          <FresnelLegend />
        </div>
      )}
    </div>
  );
}

function BudgetTab({ res }: { res: PtpResult }) {
  return (
    <div className="rounded-lg border border-fg/10 bg-recess/30 px-3 py-1">
      <BudgetRow label="TX Power" value={`${(res.eirp_dbm - RF_ANT_GAIN_DBI).toFixed(1)} dBm`} />
      <BudgetRow label="TX Antenna Gain" value={`+${RF_ANT_GAIN_DBI.toFixed(1)} dBi`} />
      <BudgetRow label="EIRP" value={`${res.eirp_dbm.toFixed(1)} dBm`} strong />
      <BudgetRow label="RX Antenna Gain" value={`+${RF_ANT_GAIN_DBI.toFixed(1)} dBi`} />
      <BudgetRow label={`Path Loss (${res.model_id})`} value={`−${res.path_loss_db.toFixed(1)} dB`} />
      <BudgetRow label="Received Signal" value={`${res.rssi_dbm.toFixed(1)} dBm`} strong />
      <BudgetRow label="RX Sensitivity" value={`${(res.rssi_dbm - res.fade_margin_db).toFixed(1)} dBm`} />
      <BudgetRow label="Fade Margin" value={`${res.fade_margin_db.toFixed(1)} dB`} strong />
    </div>
  );
}

function VerdictBadge({ ok, okLabel, badLabel }: { ok: boolean; okLabel: string; badLabel: string }) {
  const color = ok ? STATUS_COLOR.excellent : STATUS_COLOR.poor;
  return (
    <span
      className="inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[11px] font-semibold"
      style={{ color, backgroundColor: `${color}22` }}
    >
      {ok ? okLabel : badLabel}
    </span>
  );
}

function TerrainTab({ res }: { res: PtpResult }) {
  const { freqGhz, aId, bId } = useRfStore();
  const devices = useMapStore((s) => s.devices);
  const a = aId ? devices.get(aId) : undefined;
  const b = bId ? devices.get(bId) : undefined;
  if (!res.profile) return <p className="text-xs text-fg/50">No terrain profile in the last result.</p>;
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <VerdictBadge ok={res.los_clear} okLabel="Line of sight clear" badLabel="LoS blocked" />
      </div>
      <ProfileChart
        points={chartPoints(res)}
        totalDistanceM={res.profile.total_distance_m}
        losClear={res.los_clear}
        fresnelClear={res.fresnel_clear}
        txH={a?.antennaHeight ?? 10}
        rxH={b?.antennaHeight ?? 5}
        freqGhz={freqGhz}
        fresnelBand
      />
      <FresnelLegend />
      <div className="grid grid-cols-2 gap-2">
        <StatCard label="Distance" value={fmtKm(res.distance_m)} />
        <StatCard
          label="Worst Obstruction"
          value={res.worst_obstruction_m > 0 ? `${res.worst_obstruction_m.toFixed(1)} m` : 'none'}
        />
      </div>
    </div>
  );
}

function FresnelTab({ res }: { res: PtpResult }) {
  const { freqGhz, aId, bId } = useRfStore();
  const devices = useMapStore((s) => s.devices);
  const a = aId ? devices.get(aId) : undefined;
  const b = bId ? devices.get(bId) : undefined;
  const pct = Math.round(res.min_clearance_ratio * 100);
  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between rounded-lg border border-fg/10 bg-recess/40 px-3 py-2.5">
        <div>
          <p className="text-[10px] uppercase tracking-wide text-fg/40">Min Fresnel Clearance</p>
          <p className="mt-0.5 font-mono text-xl text-fg/90">{pct}%</p>
        </div>
        <VerdictBadge ok={res.fresnel_clear} okLabel="Clear" badLabel="Obstructed" />
      </div>
      {res.profile && (
        <>
          <ProfileChart
            points={chartPoints(res)}
            totalDistanceM={res.profile.total_distance_m}
            losClear={res.los_clear}
            fresnelClear={res.fresnel_clear}
            txH={a?.antennaHeight ?? 10}
            rxH={b?.antennaHeight ?? 5}
            freqGhz={freqGhz}
            fresnelBand
          />
          <FresnelLegend />
        </>
      )}
    </div>
  );
}

function FresnelLegend() {
  return (
    <div className="flex items-center gap-4 text-[10px] text-fg/50">
      <span className="flex items-center gap-1.5">
        <span className="inline-block h-0.5 w-4 rounded bg-fg/60" /> LOS Beam
      </span>
      <span className="flex items-center gap-1.5">
        <span className="inline-block h-0.5 w-4 rounded border-t border-dashed border-fg/50" /> 60% Fresnel Clearance
      </span>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Study save/load — shared by PtMP and Auto-Select (NG-RF cross-cutting)     */
/* -------------------------------------------------------------------------- */
function StudySaveLoad({
  kind,
  request,
  result,
}: {
  kind: RfStudyKind;
  request: Record<string, unknown> | null;
  result: Record<string, unknown> | null;
}) {
  const allStudies = useRfStore((s) => s.studies);
  const studyBusy = useRfStore((s) => s.studyBusy);
  const saveStudy = useRfStore((s) => s.saveStudy);
  const openStudy = useRfStore((s) => s.openStudy);
  const [name, setName] = useState('');
  const studies = allStudies.filter((s) => s.kind === kind);

  return (
    <div className="rounded-lg border border-fg/10 bg-recess/30 p-2.5">
      <p className="mb-1.5 text-[10px] uppercase tracking-wide text-fg/40">Saved Studies</p>
      <Select
        aria-label="Open saved study"
        value=""
        onChange={(v) => v && void openStudy(v)}
        placeholder={studies.length ? 'Open a saved study…' : 'No saved studies yet'}
        options={studies.map((s) => ({ value: s.id, label: s.name || s.id.slice(0, 8) }))}
        className="w-full"
      />
      <div className="mt-1.5 flex items-center gap-1.5">
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Study name (optional)"
          aria-label="Study name"
          className="min-w-0 flex-1 rounded-md border border-fg/15 bg-recess/50 px-2 py-1 text-xs text-fg/85 placeholder:text-fg/35 focus:border-accent/50 focus:outline-none"
        />
        <button
          onClick={() => {
            if (!request || !result) return;
            void saveStudy(kind, name.trim() || undefined, request, result);
            setName('');
          }}
          disabled={!result || studyBusy}
          className="shrink-0 rounded-md bg-accent px-2.5 py-1 text-[11px] font-semibold text-accent-fg hover:bg-accent-soft disabled:cursor-not-allowed disabled:opacity-40"
        >
          Save
        </button>
      </div>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* PtMP sector planner (NG-RF-04)                                              */
/* -------------------------------------------------------------------------- */
function CpeRow({ cpe, onRemove }: { cpe: PtmpCpeInput; onRemove: () => void }) {
  return (
    <div className="flex items-center justify-between gap-2 rounded-md border border-fg/10 bg-recess/30 px-2 py-1 text-xs">
      <span className="truncate text-fg/75">{cpe.name}</span>
      <button
        onClick={onRemove}
        aria-label={`Remove ${cpe.name}`}
        className="shrink-0 text-fg/40 hover:text-warning"
      >
        <X className="h-3 w-3" />
      </button>
    </div>
  );
}

function PtmpResultView({ res }: { res: PtmpResult }) {
  return (
    <div className="space-y-3">
      <div>
        <p className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-fg/50">Sector Roll-up</p>
        <div className="grid grid-cols-3 gap-2">
          <StatCard label="Served" value={`${res.served_count}/${res.cpes.length}`} highlight />
          <StatCard label="Sum PHY" value={`${res.sum_phy_mbps.toFixed(0)} Mbps`} />
          <StatCard label="Airtime-fair" value={`${res.airtime_fair_mbps.toFixed(0)} Mbps`} />
        </div>
      </div>
      <div>
        <p className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-fg/50">Per-CPE</p>
        <div className="space-y-1.5">
          {res.cpes.map((c) => (
            <div key={c.cpe_id} className="rounded-lg border border-fg/10 bg-recess/40 px-2.5 py-1.5">
              <div className="flex items-center justify-between gap-2">
                <span className="truncate text-xs font-medium text-fg/80">{c.cpe_id}</span>
                <VerdictBadge ok={c.served} okLabel="Served" badLabel={c.in_beam ? 'No link' : 'Out of beam'} />
              </div>
              <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 font-mono text-[11px] text-fg/55">
                <span>{c.rssi_dbm.toFixed(1)} dBm</span>
                <span>{c.mcs !== null ? `MCS ${c.mcs}` : 'no MCS'}</span>
                <span>{c.throughput_mbps.toFixed(0)} Mbps</span>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function PtmpBody() {
  const {
    ptmpApId, ptmpAzimuthDeg, ptmpBeamwidthDeg, ptmpDowntiltDeg, ptmpFreqMhz, ptmpBandwidthMhz,
    ptmpTxPowerDbm, ptmpTxGainDbi, ptmpRxSensitivityDbm, ptmpModelId, ptmpCpes, ptmpResult,
    ptmpLastRequest, ptmpLoading, ptmpError, models, radios,
    setPtmpApId, setPtmpAzimuth, setPtmpBeamwidth, setPtmpDowntilt, setPtmpFreqMhz, setPtmpBandwidthMhz,
    setPtmpTxPower, setPtmpTxGain, setPtmpRxSens, setPtmpModel, addCpeFromDevice, addManualCpe, removeCpe,
    calculatePtmp,
  } = useRfStore();
  const devices = useMapStore((s) => s.deviceList());
  const apRadios = radios.filter((r) => r.type === 'ptmp_ap');
  const apOptions = devices.filter((d) => d.kind === 'ap' || d.kind === 'tower').map((d) => ({ id: d.id, name: d.name }));
  const cpeOptions = devices.filter((d) => d.kind === 'cpe').map((d) => ({ id: d.id, name: d.name }));
  const [manualLat, setManualLat] = useState('');
  const [manualLon, setManualLon] = useState('');

  const canCalc = !!ptmpApId && ptmpCpes.length > 0 && !ptmpLoading;

  return (
    <div className="space-y-4">
      <div>
        <p className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-fg/50">Sector</p>
        <EndpointSelect value={ptmpApId} onChange={setPtmpApId} options={apOptions} label="AP / Tower site" />
        <div className="mt-2">
          <CatalogPicker
            radios={apRadios}
            label="Prefill AP radio from catalog"
            onSelect={(r) => {
              setPtmpTxPower(r.tx_power_dbm);
              setPtmpTxGain(r.antenna_gain_dbi);
              setPtmpRxSens(r.rx_sensitivity_dbm);
            }}
          />
        </div>
        <div className="mt-2 grid grid-cols-2 gap-2">
          <FieldInput label="Azimuth" value={ptmpAzimuthDeg} onChange={setPtmpAzimuth} suffix="deg" step={5} min={0} />
          <FieldInput label="Beamwidth" value={ptmpBeamwidthDeg} onChange={setPtmpBeamwidth} suffix="deg" step={5} min={1} />
          <FieldInput label="Downtilt" value={ptmpDowntiltDeg} onChange={setPtmpDowntilt} suffix="deg" step={1} />
          <FieldInput label="Frequency" value={ptmpFreqMhz} onChange={setPtmpFreqMhz} suffix="MHz" step={100} min={1} />
          <FieldInput label="Bandwidth" value={ptmpBandwidthMhz} onChange={setPtmpBandwidthMhz} suffix="MHz" step={5} min={1} />
          <FieldInput label="TX Power" value={ptmpTxPowerDbm} onChange={setPtmpTxPower} suffix="dBm" step={1} />
          <FieldInput label="TX Gain" value={ptmpTxGainDbi} onChange={setPtmpTxGain} suffix="dBi" step={1} />
          <FieldInput label="RX Sens." value={ptmpRxSensitivityDbm} onChange={setPtmpRxSens} suffix="dBm" step={1} />
        </div>
        <label className="mt-2 block rounded-lg border border-fg/10 bg-recess/40 px-2.5 py-2">
          <span className="text-[10px] uppercase tracking-wide text-fg/40">Model</span>
          <Select
            value={ptmpModelId}
            onChange={setPtmpModel}
            aria-label="Propagation model"
            options={
              models.length === 0
                ? [{ value: ptmpModelId, label: ptmpModelId }]
                : models.map((m) => ({ value: m.id, label: m.id }))
            }
            className="mt-0.5 w-full"
          />
        </label>
      </div>

      <div>
        <p className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-fg/50">
          CPEs ({ptmpCpes.length})
        </p>
        <div className="space-y-1.5">
          {ptmpCpes.map((c) => (
            <CpeRow key={c.id} cpe={c} onRemove={() => removeCpe(c.id)} />
          ))}
          {ptmpCpes.length === 0 && <p className="text-xs text-fg/40">No CPEs added yet.</p>}
        </div>
        <div className="mt-2">
          <EndpointSelect
            value={null}
            onChange={(id) => id && addCpeFromDevice(id)}
            options={cpeOptions}
            label="Add placed CPE"
          />
        </div>
        <div className="mt-1.5 flex items-center gap-1.5">
          <input
            value={manualLat}
            onChange={(e) => setManualLat(e.target.value)}
            placeholder="lat"
            aria-label="Manual CPE latitude"
            inputMode="decimal"
            className="w-0 flex-1 rounded-md border border-fg/15 bg-recess/50 px-2 py-1 text-xs text-fg/85 placeholder:text-fg/35 focus:border-accent/50 focus:outline-none"
          />
          <input
            value={manualLon}
            onChange={(e) => setManualLon(e.target.value)}
            placeholder="lon"
            aria-label="Manual CPE longitude"
            inputMode="decimal"
            className="w-0 flex-1 rounded-md border border-fg/15 bg-recess/50 px-2 py-1 text-xs text-fg/85 placeholder:text-fg/35 focus:border-accent/50 focus:outline-none"
          />
          <button
            onClick={() => {
              const lat = Number(manualLat);
              const lon = Number(manualLon);
              if (manualLat && manualLon && Number.isFinite(lat) && Number.isFinite(lon)) {
                addManualCpe(lat, lon);
                setManualLat('');
                setManualLon('');
              }
            }}
            disabled={!manualLat || !manualLon}
            aria-label="Add manual CPE"
            className="shrink-0 grid h-7 w-7 place-items-center rounded-md border border-fg/15 text-fg/60 hover:border-accent/50 hover:text-accent disabled:cursor-not-allowed disabled:opacity-40"
          >
            <Plus className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>

      <button
        onClick={() => void calculatePtmp()}
        disabled={!canCalc}
        className="w-full rounded-lg bg-accent px-3 py-1.5 text-xs font-semibold text-accent-fg hover:bg-accent-soft disabled:cursor-not-allowed disabled:opacity-40"
      >
        {ptmpLoading ? 'Computing…' : 'Calculate sector'}
      </button>

      {ptmpError && (
        <div className="grid place-items-center gap-2 py-2 text-center text-xs text-fg/60">
          <AlertTriangle className="h-4 w-4 text-warning" />
          <p>{ptmpError}</p>
        </div>
      )}

      {ptmpResult && <PtmpResultView res={ptmpResult} />}

      <StudySaveLoad
        kind="ptmp"
        request={ptmpLastRequest as unknown as Record<string, unknown> | null}
        result={ptmpResult as unknown as Record<string, unknown> | null}
      />
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Auto product-select (NG-RF-05)                                             */
/* -------------------------------------------------------------------------- */
function CandidateRow({
  candidate,
  radios,
  onChange,
  onRemove,
}: {
  candidate: RadioCandidate;
  radios: RadioCatalogEntry[];
  onChange: (patch: Partial<RadioCandidate>) => void;
  onRemove: () => void;
}) {
  return (
    <div className="rounded-lg border border-fg/10 bg-recess/40 px-2.5 py-2">
      <div className="flex items-center gap-2">
        <input
          value={candidate.name}
          onChange={(e) => onChange({ name: e.target.value })}
          aria-label="Candidate name"
          className="min-w-0 flex-1 bg-transparent text-xs font-medium text-fg/85 focus:outline-none"
        />
        <button
          onClick={onRemove}
          aria-label={`Remove ${candidate.name}`}
          className="shrink-0 text-fg/40 hover:text-warning"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>
      <div className="mt-1.5">
        <CatalogPicker
          radios={radios}
          onSelect={(r) =>
            onChange({
              name: r.name,
              tx_power_dbm: r.tx_power_dbm,
              antenna_gain_dbi: r.antenna_gain_dbi,
              rx_sensitivity_dbm: r.rx_sensitivity_dbm,
            })
          }
        />
      </div>
      <div className="mt-1.5 grid grid-cols-2 gap-1.5">
        <MiniField label="TX dBm" value={candidate.tx_power_dbm} onChange={(v) => onChange({ tx_power_dbm: v })} />
        <MiniField label="Gain dBi" value={candidate.antenna_gain_dbi} onChange={(v) => onChange({ antenna_gain_dbi: v })} />
        <MiniField label="Sens dBm" value={candidate.rx_sensitivity_dbm} onChange={(v) => onChange({ rx_sensitivity_dbm: v })} />
        <MiniField label="Cost" value={candidate.cost} onChange={(v) => onChange({ cost: v })} />
        <MiniField label="BW MHz" value={candidate.bandwidth_mhz ?? 20} onChange={(v) => onChange({ bandwidth_mhz: v })} />
      </div>
    </div>
  );
}

function MiniField({ label, value, onChange }: { label: string; value: number; onChange: (v: number) => void }) {
  return (
    <label className="flex items-center justify-between gap-1 rounded border border-fg/10 bg-recess/50 px-1.5 py-0.5">
      <span className="text-[9px] uppercase text-fg/40">{label}</span>
      <input
        type="number"
        inputMode="decimal"
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        aria-label={label}
        className="w-12 min-w-0 bg-transparent text-right font-mono text-[11px] text-fg/85 focus:outline-none"
      />
    </label>
  );
}

function ProductSelectResultView({ res }: { res: ProductSelectResult }) {
  return (
    <div>
      <p className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-fg/50">
        Ranked (best margin/cost first)
      </p>
      <div className="space-y-1.5">
        {res.ranked.map((r) => (
          <div key={r.name} className="rounded-lg border border-fg/10 bg-recess/40 px-2.5 py-1.5">
            <div className="flex items-center justify-between gap-2">
              <span className="truncate text-xs font-medium text-fg/80">{r.name}</span>
              <VerdictBadge ok={r.meets_target} okLabel="Meets target" badLabel={r.link_closes ? 'Below target' : 'No link'} />
            </div>
            <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 font-mono text-[11px] text-fg/55">
              <span>{r.rssi_dbm.toFixed(1)} dBm</span>
              <span>margin {r.margin_db.toFixed(1)} dB</span>
              <span>{r.predicted_throughput_mbps.toFixed(0)} Mbps</span>
              <span>cost {r.cost}</span>
              <span>{r.margin_per_cost.toFixed(3)}/cost</span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function ProductSelectBody() {
  const {
    psDistanceM, psFreqMhz, psTargetMbps, psTxHeightM, psRxHeightM, psMiscLossDb, psModelId,
    psCandidates, psResult, psLastRequest, psLoading, psError, models, result: ptpResult, radios,
    setPsDistance, setPsFreq, setPsTarget, setPsTxHeight, setPsRxHeight, setPsMiscLoss, setPsModel,
    addCandidate, updateCandidate, removeCandidate, fillFromPtp, calculateProductSelect,
  } = useRfStore();

  const canCalc = psCandidates.length > 0 && psDistanceM > 0 && psFreqMhz > 0 && !psLoading;
  // 'antenna' catalog entries are passive (no tx power / sensitivity of their
  // own) — not a sensible prefill for a candidate radio slot.
  const candidateRadios = radios.filter((r) => r.type !== 'antenna');

  return (
    <div className="space-y-4">
      <div>
        <p className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-fg/50">Link Requirement</p>
        <div className="grid grid-cols-2 gap-2">
          <FieldInput label="Distance" value={psDistanceM} onChange={setPsDistance} suffix="m" step={10} min={1} />
          <FieldInput label="Frequency" value={psFreqMhz} onChange={setPsFreq} suffix="MHz" step={100} min={1} />
          <FieldInput label="Target" value={psTargetMbps} onChange={setPsTarget} suffix="Mbps" step={10} min={0} />
          <FieldInput label="Misc Loss" value={psMiscLossDb} onChange={setPsMiscLoss} suffix="dB" step={0.5} />
          <FieldInput label="TX Height" value={psTxHeightM} onChange={setPsTxHeight} suffix="m" step={1} min={0.1} />
          <FieldInput label="RX Height" value={psRxHeightM} onChange={setPsRxHeight} suffix="m" step={1} min={0.1} />
        </div>
        <label className="mt-2 block rounded-lg border border-fg/10 bg-recess/40 px-2.5 py-2">
          <span className="text-[10px] uppercase tracking-wide text-fg/40">Model</span>
          <Select
            value={psModelId}
            onChange={setPsModel}
            aria-label="Propagation model"
            options={
              models.length === 0
                ? [{ value: psModelId, label: psModelId }]
                : models.map((m) => ({ value: m.id, label: m.id }))
            }
            className="mt-0.5 w-full"
          />
        </label>
        {ptpResult && (
          <button
            onClick={fillFromPtp}
            className="mt-2 text-[11px] font-medium text-accent hover:underline"
          >
            Use last PtP link's distance + frequency
          </button>
        )}
      </div>

      <div>
        <p className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-fg/50">
          Candidates ({psCandidates.length})
        </p>
        <div className="space-y-1.5">
          {psCandidates.map((c, i) => (
            <CandidateRow
              key={i}
              candidate={c}
              radios={candidateRadios}
              onChange={(patch) => updateCandidate(i, patch)}
              onRemove={() => removeCandidate(i)}
            />
          ))}
        </div>
        <button
          onClick={addCandidate}
          className="mt-1.5 inline-flex items-center gap-1 rounded-md border border-fg/15 bg-recess/50 px-2.5 py-1 text-xs font-medium text-fg/80 hover:border-accent/50 hover:text-fg"
        >
          <Plus className="h-3 w-3" /> Add candidate
        </button>
      </div>

      <button
        onClick={() => void calculateProductSelect()}
        disabled={!canCalc}
        className="w-full rounded-lg bg-accent px-3 py-1.5 text-xs font-semibold text-accent-fg hover:bg-accent-soft disabled:cursor-not-allowed disabled:opacity-40"
      >
        {psLoading ? 'Ranking…' : 'Rank candidates'}
      </button>

      {psError && (
        <div className="grid place-items-center gap-2 py-2 text-center text-xs text-fg/60">
          <AlertTriangle className="h-4 w-4 text-warning" />
          <p>{psError}</p>
        </div>
      )}

      {psResult && <ProductSelectResultView res={psResult} />}

      <StudySaveLoad
        kind="product_select"
        request={psLastRequest as unknown as Record<string, unknown> | null}
        result={psResult as unknown as Record<string, unknown> | null}
      />
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Panel shell                                                                 */
/* -------------------------------------------------------------------------- */
export function RfAnalysisPanel() {
  const mode = useRfStore((s) => s.mode);
  const setMode = useRfStore((s) => s.setMode);
  const tab = useRfStore((s) => s.tab);
  const setTab = useRfStore((s) => s.setTab);
  const result = useRfStore((s) => s.result);
  const loading = useRfStore((s) => s.loading);
  const error = useRfStore((s) => s.error);
  const clear = useRfStore((s) => s.clear);
  const endpointCount = useMapStore(
    (s) => s.deviceList().filter((d) => d.kind === 'ap' || d.kind === 'tower').length,
  );

  return (
    <aside
      role="region"
      aria-label="RF Planning"
      className={`glass-strong pointer-events-auto absolute right-0 top-0 ${zc.workspace} flex h-full w-[380px] max-w-[85vw] flex-col border-l border-fg/12 shadow-glass-lg`}
    >
      {/* Header */}
      <div className="flex items-center gap-2 border-b border-fg/10 px-4 py-3">
        <RadioTower className="h-4 w-4 text-accent" />
        <h2 className="text-sm font-semibold text-fg/85">RF Planning</h2>
        {mode === 'ptp' && (
          <button
            onClick={clear}
            aria-label="Clear analysis"
            className="ml-auto grid h-6 w-6 place-items-center rounded text-fg/50 hover:bg-fg/10 hover:text-fg"
          >
            <X className="h-4 w-4" />
          </button>
        )}
      </div>

      {/* Mode selector */}
      <div role="tablist" aria-label="RF planning mode" className="flex gap-1 border-b border-fg/10 px-2 py-1.5">
        {MODES.map((m) => (
          <button
            key={m.id}
            role="tab"
            aria-selected={mode === m.id}
            onClick={() => setMode(m.id)}
            className={
              'rounded-md px-2.5 py-1 text-xs font-medium transition-colors ' +
              (mode === m.id ? 'bg-accent/20 text-accent' : 'text-fg/55 hover:bg-fg/8 hover:text-fg/85')
            }
          >
            {m.label}
          </button>
        ))}
      </div>

      {/* PtP sub-tabs (Summary / Link Budget / Terrain / Fresnel) */}
      {mode === 'ptp' && (
        <div role="tablist" aria-label="Link analysis views" className="flex gap-1 border-b border-fg/10 px-2 py-1.5">
          {TABS.map((t) => (
            <button
              key={t.id}
              role="tab"
              aria-selected={tab === t.id}
              onClick={() => setTab(t.id)}
              className={
                'rounded-md px-2.5 py-1 text-xs transition-colors ' +
                (tab === t.id ? 'bg-accent/20 text-accent' : 'text-fg/55 hover:bg-fg/8 hover:text-fg/85')
              }
            >
              {t.label}
            </button>
          ))}
        </div>
      )}

      {/* Body */}
      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4">
        {mode === 'ptmp' ? (
          <PtmpBody />
        ) : mode === 'product_select' ? (
          <ProductSelectBody />
        ) : error ? (
          <div className="grid place-items-center gap-2 py-10 text-center text-xs text-fg/60">
            <AlertTriangle className="h-5 w-5 text-warning" />
            <p className="max-w-[16rem]">{error}</p>
          </div>
        ) : loading ? (
          <div className="grid place-items-center gap-2 py-10 text-xs text-fg/55">
            <Loader2 className="h-5 w-5 animate-spin text-accent" />
            Computing link budget…
          </div>
        ) : !result ? (
          <EmptyState endpointCount={endpointCount} />
        ) : tab === 'summary' ? (
          <SummaryTab res={result} />
        ) : tab === 'budget' ? (
          <BudgetTab res={result} />
        ) : tab === 'terrain' ? (
          <TerrainTab res={result} />
        ) : (
          <FresnelTab res={result} />
        )}
      </div>
    </aside>
  );
}

/** 3-tier honest empty state (design 12-UI §3.3): guide the real next step by
 *  how many selectable NetGeo AP/tower sites actually exist, and state plainly
 *  that OSM reference towers are not selectable when that layer is on. */
function EmptyState({ endpointCount }: { endpointCount: number }) {
  const setTool = useMapStore((s) => s.setTool);
  const towersVisible = useMapStore((s) => s.gisLayers['util-tower']?.visible ?? false);

  return (
    <div className="grid place-items-center gap-3 py-10 text-center">
      <Radio className="h-8 w-8 text-fg/25" />
      {endpointCount === 0 ? (
        <>
          <p className="max-w-[16rem] text-xs leading-relaxed text-fg/55">
            No AP or tower sites in this project yet. Place one on the map to start a point-to-point
            link.
          </p>
          <button
            onClick={() => setTool('ap')}
            className="rounded-lg bg-accent px-3 py-1.5 text-xs font-semibold text-accent-fg hover:bg-accent-soft"
          >
            Place an AP site
          </button>
        </>
      ) : endpointCount === 1 ? (
        <p className="max-w-[16rem] text-xs leading-relaxed text-fg/55">
          One site placed. Place or click a second AP/tower site to define the link.
        </p>
      ) : (
        <p className="max-w-[16rem] text-xs leading-relaxed text-fg/55">
          Click two sites on the map (or use the selectors below), then press Calculate.
        </p>
      )}
      {towersVisible && (
        <p className="max-w-[16rem] text-[11px] leading-relaxed text-fg/40">
          Visible OSM towers are reference-only — not selectable as endpoints.
        </p>
      )}
    </div>
  );
}
