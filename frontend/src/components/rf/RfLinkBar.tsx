/**
 * RfLinkBar — bottom-centre control bar for the RF workspace: the two endpoint
 * selectors, the FREQ / BW / MDL parameter chips, and the Calculate action.
 * Centred within the map area (clear of the 380px right panel).
 */
import { ArrowRight, ArrowLeftRight, Radio, RadioTower } from 'lucide-react';
import { useRfStore } from '@/store/rfStore';
import { useMapStore } from '@/store/mapStore';
import { zc } from '@/theme/z';
import { cn } from '@/lib/cn';

/** Placed AP/Tower sites are the selectable PtP endpoints. */
function useRfEndpoints() {
  const devices = useMapStore((s) => s.deviceList());
  return devices
    .filter((d) => d.kind === 'ap' || d.kind === 'tower')
    .map((d) => ({ id: d.id, name: d.name }));
}

/** Exported for reuse by RfAnalysisPanel's PtMP sector-AP / CPE pickers — same
 *  compact device-select control, same tokens. */
export function EndpointSelect({
  value,
  onChange,
  options,
  label,
}: {
  value: string | null;
  onChange: (id: string | null) => void;
  options: { id: string; name: string }[];
  label: string;
}) {
  return (
    <select
      aria-label={label}
      value={value ?? ''}
      onChange={(e) => onChange(e.target.value || null)}
      className="max-w-[130px] rounded-md border border-fg/15 bg-recess/20 px-2 py-1 text-xs text-fg/85 focus:border-accent/50 focus:outline-none"
    >
      <option value="">{label}</option>
      {options.map((o) => (
        <option key={o.id} value={o.id}>
          {o.name}
        </option>
      ))}
    </select>
  );
}

function ParamChip({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex items-center gap-1.5 rounded-md border border-fg/15 bg-recess/50 px-2 py-1">
      <span className="text-[9px] font-semibold uppercase tracking-wider text-fg/40">{label}</span>
      {children}
    </div>
  );
}

function PlaceButton({
  active,
  onClick,
  icon: Icon,
  label,
}: {
  active: boolean;
  onClick: () => void;
  icon: typeof Radio;
  label: string;
}) {
  return (
    <button
      onClick={onClick}
      aria-pressed={active}
      title={label}
      className={cn(
        'flex items-center gap-1.5 rounded-md px-2 py-1 text-xs transition-colors',
        active ? 'bg-accent/20 text-accent' : 'text-fg/60 hover:bg-fg/10 hover:text-fg',
      )}
    >
      <Icon className="h-3.5 w-3.5" />
      <span className="hidden lg:inline">{label}</span>
    </button>
  );
}

export function RfLinkBar() {
  const aId = useRfStore((s) => s.aId);
  const bId = useRfStore((s) => s.bId);
  const freqGhz = useRfStore((s) => s.freqGhz);
  const bwMhz = useRfStore((s) => s.bwMhz);
  const modelId = useRfStore((s) => s.modelId);
  const models = useRfStore((s) => s.models);
  const loading = useRfStore((s) => s.loading);
  const setA = useRfStore((s) => s.setA);
  const setB = useRfStore((s) => s.setB);
  const swap = useRfStore((s) => s.swap);
  const setFreq = useRfStore((s) => s.setFreq);
  const setBw = useRfStore((s) => s.setBw);
  const setModel = useRfStore((s) => s.setModel);
  const calculate = useRfStore((s) => s.calculate);
  const tool = useMapStore((s) => s.tool);
  const setTool = useMapStore((s) => s.setTool);
  const endpoints = useRfEndpoints();

  const canCalc = !!aId && !!bId && aId !== bId && !loading;

  return (
    <div
      className={cn('pointer-events-auto absolute bottom-6 -translate-x-1/2', zc.workspace)}
      style={{ left: 'calc(50% - 190px)' }}
    >
      <div className="glass-strong flex flex-wrap items-center gap-2 rounded-xl border border-fg/15 px-3 py-2 shadow-glass-lg">
        {/* Place real NetGeo RF sites straight from the RF view (design 12-UI §3.1). */}
        <div className="flex items-center gap-1">
          <PlaceButton active={tool === 'ap'} onClick={() => setTool(tool === 'ap' ? 'select' : 'ap')} icon={Radio} label="Place AP site" />
          <PlaceButton active={tool === 'tower'} onClick={() => setTool(tool === 'tower' ? 'select' : 'tower')} icon={RadioTower} label="Place tower" />
        </div>
        <span className="mx-1 h-5 w-px bg-fg/10" />

        <EndpointSelect value={aId} onChange={setA} options={endpoints} label="Endpoint A" />
        <button
          onClick={swap}
          aria-label="Swap endpoints"
          title="Swap endpoints"
          className="grid h-6 w-6 place-items-center rounded text-fg/50 hover:bg-fg/10 hover:text-fg"
        >
          <ArrowLeftRight className="h-3.5 w-3.5" />
        </button>
        <EndpointSelect value={bId} onChange={setB} options={endpoints} label="Endpoint B" />

        <span className="mx-1 h-5 w-px bg-fg/10" />

        <ParamChip label="Freq">
          <input
            type="number"
            inputMode="decimal"
            step={0.1}
            min={0.1}
            value={freqGhz}
            onChange={(e) => Number(e.target.value) > 0 && setFreq(Number(e.target.value))}
            aria-label="Frequency (GHz)"
            className="w-12 bg-transparent text-right font-mono text-xs text-fg/85 focus:outline-none"
          />
          <span className="text-[10px] text-fg/40">GHz</span>
        </ParamChip>

        <ParamChip label="BW">
          <input
            type="number"
            inputMode="numeric"
            step={5}
            min={1}
            value={bwMhz}
            onChange={(e) => Number(e.target.value) > 0 && setBw(Number(e.target.value))}
            aria-label="Bandwidth (MHz)"
            className="w-11 bg-transparent text-right font-mono text-xs text-fg/85 focus:outline-none"
          />
          <span className="text-[10px] text-fg/40">MHz</span>
        </ParamChip>

        <ParamChip label="Mdl">
          <select
            aria-label="Propagation model"
            value={modelId}
            onChange={(e) => setModel(e.target.value)}
            className="bg-transparent font-mono text-xs uppercase text-fg/85 focus:outline-none"
          >
            {models.length === 0 && <option value={modelId}>{modelId}</option>}
            {models.map((m) => (
              <option key={m.id} value={m.id}>
                {m.id}
              </option>
            ))}
          </select>
        </ParamChip>

        <button
          onClick={() => void calculate()}
          disabled={!canCalc}
          className="ml-1 inline-flex items-center gap-1.5 rounded-lg bg-accent px-3.5 py-1.5 text-xs font-semibold text-accent-fg transition-colors hover:bg-accent-soft disabled:cursor-not-allowed disabled:opacity-40"
        >
          {loading ? <Radio className="h-3.5 w-3.5 animate-pulse" /> : <ArrowRight className="h-3.5 w-3.5" />}
          Calculate
        </button>
      </div>
    </div>
  );
}
