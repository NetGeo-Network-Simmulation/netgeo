/**
 * MapToolbar — left-side vertical toolbar for the satellite map view.
 * Tools: Select, Place AP, Place CPE, Place Tower, Measure Distance.
 * Also includes a rain rate slider for field-condition simulation.
 */
import { MousePointer2, Radio, Smartphone, RadioTower, Ruler, Mountain, Trash2, Droplets, MapPin, Building2 } from 'lucide-react';
import { useMapStore, rainRateLabel, type MapTool } from '@/store/mapStore';
import { cn } from '@/lib/cn';
import { zc } from '@/theme/z';
import { MAP_CHROME_INSET } from '@/theme/shell';

interface ToolItem {
  tool: MapTool;
  icon: typeof MousePointer2;
  label: string;
  color: string;
}

// ponytail-debt: every color below is a raw hex literal, not a theme token —
// pre-existing convention in this file (every tool was already keyed this
// way before this slice touched it). Kept consistent rather than making
// `site` the one entry that reads a token while its seven siblings don't;
// a partial migration would look like an accident, not a decision. If this
// ever gets tokenized, do the whole array in one pass.
const TOOLS: ToolItem[] = [
  { tool: 'select',  icon: MousePointer2, label: 'Select',                       color: '#8E8E93' },
  { tool: 'deploy',  icon: MapPin,        label: 'Deploy Device',                 color: '#FF9F0A' },
  { tool: 'site',    icon: Building2,     label: 'Place Site',                    color: '#27C28B' }, // matches TopologySiteLayer's SITE_COLOR (MapView.tsx)
  { tool: 'ap',      icon: Radio,         label: 'Place AP (RF planning)',        color: '#5856D6' },
  { tool: 'cpe',     icon: Smartphone,    label: 'Place CPE (RF planning)',       color: '#007AFF' },
  { tool: 'tower',   icon: RadioTower,    label: 'Place Tower (RF planning)',     color: '#FF9F0A' },
  { tool: 'measure', icon: Ruler,         label: 'Measure Distance',              color: '#34C759' },
  { tool: 'profile', icon: Mountain,      label: 'Elevation Profile',             color: '#A0785A' },
];

export function MapToolbar() {
  const tool    = useMapStore((s) => s.tool);
  const setTool = useMapStore((s) => s.setTool);
  const selectedId   = useMapStore((s) => s.selectedDeviceId);
  const removeDevice = useMapStore((s) => s.removeDevice);
  const selectDevice = useMapStore((s) => s.selectDevice);
  const rainRate     = useMapStore((s) => s.rainRate);
  const setRainRate  = useMapStore((s) => s.setRainRate);

  const handleDelete = () => {
    if (selectedId) { removeDevice(selectedId); selectDevice(null); }
  };

  return (
    <div className={cn('pointer-events-auto absolute top-1/2 -translate-y-1/2', MAP_CHROME_INSET, zc.workspace)}>
      <div className="glass-strong flex flex-col gap-1 rounded-xl border border-fg/15 p-1.5 shadow-glass-lg">

        {/* Placement tools */}
        {TOOLS.map(({ tool: t, icon: Icon, label, color }) => (
          <button
            key={t}
            onClick={() => setTool(t)}
            title={label}
            aria-label={label}
            aria-pressed={tool === t}
            className={cn(
              'group relative grid h-10 w-10 place-items-center rounded-lg transition-all duration-fast',
              tool === t
                ? 'shadow-lg'
                : 'text-fg/50 hover:bg-fg/10 hover:text-fg',
            )}
            style={
              tool === t
                ? { background: `${color}25`, color, boxShadow: `0 4px 16px ${color}40` }
                : undefined
            }
          >
            <Icon className="h-5 w-5" />
            {/* Tooltip */}
            <span className="pointer-events-none absolute left-[calc(100%+10px)] top-1/2 -translate-y-1/2 whitespace-nowrap rounded-md border border-hairline bg-panel-2 px-2 py-1 text-[11px] text-fg/90 opacity-0 shadow transition-opacity group-hover:opacity-100">
              {label}
            </span>
          </button>
        ))}

        {/* Delete separator */}
        <div className="my-0.5 border-t border-fg/10" />
        <button
          onClick={handleDelete}
          title="Delete selected device"
          aria-label="Delete selected device"
          disabled={!selectedId}
          className={cn(
            'grid h-10 w-10 place-items-center rounded-lg transition-all duration-fast',
            selectedId
              ? 'text-danger/80 hover:bg-danger/10 hover:text-danger'
              : 'cursor-not-allowed text-fg/20',
          )}
        >
          <Trash2 className="h-5 w-5" />
        </button>

        {/* Rain rate separator */}
        <div className="my-0.5 border-t border-fg/10" />

        {/* Rain indicator button (opens tooltip with slider) */}
        <div className="group relative">
          <button
            title={`Rain: ${rainRateLabel(rainRate)} (${rainRate} mm/hr)`}
            aria-label="Rain rate control"
            className={cn(
              'grid h-10 w-10 place-items-center rounded-lg transition-all duration-fast',
              rainRate > 0
                ? 'text-info'
                : 'text-fg/40 hover:bg-fg/10 hover:text-fg/80',
            )}
            style={rainRate > 0 ? { background: 'rgba(59,130,246,0.15)' } : undefined}
          >
            <Droplets className="h-5 w-5" />
          </button>

          {/* Rain slider popover */}
          <div className="pointer-events-auto absolute left-[calc(100%+10px)] top-1/2 -translate-y-1/2 hidden w-56 group-hover:block group-focus-within:block">
            <div className="glass-strong rounded-lg border border-fg/15 p-3 shadow-glass-lg">
              <div className="mb-2 flex items-center justify-between">
                <span className="text-[10px] font-semibold uppercase tracking-wide text-fg/50">
                  Rain Rate
                </span>
                <span className="font-mono text-xs text-info">
                  {rainRate === 0 ? 'Clear' : `${rainRate} mm/hr`}
                </span>
              </div>
              <input
                type="range"
                min={0} max={100} step={2.5}
                value={rainRate}
                onChange={(e) => setRainRate(Number(e.target.value))}
                className="w-full"
              />
              <div className="mt-1 flex justify-between text-[9px] text-fg/30">
                <span>Clear</span>
                <span>Drizzle</span>
                <span>Heavy</span>
              </div>
              <p className="mt-1.5 text-center text-[10px] text-fg/45">
                {rainRateLabel(rainRate)}
              </p>
            </div>
          </div>
        </div>

      </div>
    </div>
  );
}
