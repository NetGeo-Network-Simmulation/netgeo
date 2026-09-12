/**
 * MapToolbar — left-side vertical toolbar for the satellite map view.
 * Also includes a rain rate slider for field-condition simulation.
 *
 * QA-visual #6 (2026-09-12): the 8 tools collapsed into Select (standalone —
 * it's the neutral default mode, not a placement/measurement action) plus 3
 * grouped parent buttons, each opening a flyout of its real sub-tools:
 *  - Place: deploy a real device / drop a Site — both create actual topology
 *    objects on the map.
 *  - RF Planning: the legacy, local-only AP/CPE/Tower sandbox (see each
 *    tool's own "(legacy, local-only)" label below — they were already
 *    documented as one family, just not grouped in the UI).
 *  - Measure: Distance + Elevation Profile — both read the map, neither
 *    places anything.
 * A group's parent button swaps to the active child's icon/color so the
 * engaged tool stays visible without opening the flyout (QA requirement).
 * The flyout reuses the exact hover/focus-within idiom already validated
 * below for the rain-rate popover, so every sub-tool is keyboard-reachable:
 * Tab focuses the parent button, which opens the flyout via
 * `group-focus-within`, then Tab continues into its buttons.
 */
import { MousePointer2, Radio, Smartphone, RadioTower, Ruler, Mountain, Trash2, Droplets, MapPin, Building2, type LucideIcon } from 'lucide-react';
import { useMapStore, rainRateLabel, type MapTool } from '@/store/mapStore';
import { cn } from '@/lib/cn';
import { zc } from '@/theme/z';
import { CHROME_INSET } from '@/theme/shell';

interface ToolItem {
  tool: MapTool;
  icon: LucideIcon;
  label: string;
  color: string;
}

interface ToolGroup {
  id: string;
  label: string;
  icon: LucideIcon;
  tools: ToolItem[];
}

// ponytail-debt: every color below is a raw hex literal, not a theme token —
// pre-existing convention in this file (every tool was already keyed this
// way before this slice touched it). Kept consistent rather than making
// `site` the one entry that reads a token while its seven siblings don't;
// a partial migration would look like an accident, not a decision. If this
// ever gets tokenized, do the whole array in one pass.
const SELECT_TOOL: ToolItem = { tool: 'select', icon: MousePointer2, label: 'Select', color: '#8E8E93' };

const TOOL_GROUPS: ToolGroup[] = [
  {
    id: 'place',
    label: 'Place',
    icon: MapPin,
    tools: [
      { tool: 'deploy', icon: MapPin, label: 'Deploy Device', color: '#FF9F0A' },
      { tool: 'site', icon: Building2, label: 'Place Site', color: '#27C28B' }, // matches TopologySiteLayer's SITE_COLOR (MapView.tsx)
    ],
  },
  {
    id: 'rf-planning',
    label: 'RF Planning (sandbox)',
    icon: Radio,
    tools: [
      { tool: 'ap', icon: Radio, label: 'Place AP (RF planning)', color: '#5856D6' },
      { tool: 'cpe', icon: Smartphone, label: 'Place CPE (RF planning)', color: '#007AFF' },
      { tool: 'tower', icon: RadioTower, label: 'Place Tower (RF planning)', color: '#FF9F0A' },
    ],
  },
  {
    id: 'measure',
    label: 'Measure',
    icon: Ruler,
    tools: [
      { tool: 'measure', icon: Ruler, label: 'Measure Distance', color: '#34C759' },
      { tool: 'profile', icon: Mountain, label: 'Elevation Profile', color: '#A0785A' },
    ],
  },
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
    <div className={cn('pointer-events-auto absolute top-1/2 -translate-y-1/2', CHROME_INSET, zc.workspace)}>
      <div className="glass-strong flex flex-col gap-1 rounded-xl border border-fg/15 p-1.5 shadow-glass-lg">

        {/* Select — the neutral default mode, not a placement/measurement
            action, so it stays its own standalone button. */}
        <button
          onClick={() => setTool(SELECT_TOOL.tool)}
          title={SELECT_TOOL.label}
          aria-label={SELECT_TOOL.label}
          aria-pressed={tool === SELECT_TOOL.tool}
          className={cn(
            'group relative grid h-10 w-10 place-items-center rounded-lg transition-all duration-fast',
            tool === SELECT_TOOL.tool ? 'shadow-lg' : 'text-fg/50 hover:bg-fg/10 hover:text-fg',
          )}
          style={
            tool === SELECT_TOOL.tool
              ? { background: `${SELECT_TOOL.color}25`, color: SELECT_TOOL.color, boxShadow: `0 4px 16px ${SELECT_TOOL.color}40` }
              : undefined
          }
        >
          <SELECT_TOOL.icon className="h-5 w-5" />
          <span className="pointer-events-none absolute left-[calc(100%+10px)] top-1/2 -translate-y-1/2 whitespace-nowrap rounded-md border border-hairline bg-panel-2 px-2 py-1 text-[11px] text-fg/90 opacity-0 shadow transition-opacity group-hover:opacity-100">
            {SELECT_TOOL.label}
          </span>
        </button>

        <div className="my-0.5 border-t border-fg/10" />

        {/* Grouped tools — each parent opens a flyout of its real sub-tools
            (see the QA-visual #6 header comment). The parent shows the
            active child's own icon/color so the engaged tool is visible at
            a glance without opening the flyout. */}
        {TOOL_GROUPS.map((group) => {
          const activeChild = group.tools.find((t) => t.tool === tool);
          const Icon = activeChild?.icon ?? group.icon;
          const color = activeChild?.color;
          return (
            <div key={group.id} className="group relative">
              <button
                title={activeChild?.label ?? group.label}
                aria-label={activeChild ? `${group.label}: ${activeChild.label}` : group.label}
                aria-haspopup="true"
                className={cn(
                  'relative grid h-10 w-10 place-items-center rounded-lg transition-all duration-fast',
                  activeChild ? 'shadow-lg' : 'text-fg/50 hover:bg-fg/10 hover:text-fg',
                )}
                style={
                  activeChild
                    ? { background: `${color}25`, color, boxShadow: `0 4px 16px ${color}40` }
                    : undefined
                }
              >
                <Icon className="h-5 w-5" />
              </button>

              {/* Flyout — hover or keyboard-focus reveals it (same idiom as
                  the rain popover below), so every sub-tool stays reachable
                  by Tab even though it's visually hidden until then. */}
              <div className="pointer-events-auto absolute left-[calc(100%+10px)] top-1/2 hidden -translate-y-1/2 group-hover:block group-focus-within:block">
                <div className="glass-strong flex flex-col gap-0.5 rounded-lg border border-fg/15 p-1.5 shadow-glass-lg">
                  <p className="px-1.5 pb-0.5 text-[9px] font-semibold uppercase tracking-wide text-fg/45">
                    {group.label}
                  </p>
                  {group.tools.map(({ tool: t, icon: SubIcon, label, color: c }) => (
                    <button
                      key={t}
                      onClick={() => setTool(t)}
                      title={label}
                      aria-label={label}
                      aria-pressed={tool === t}
                      className={cn(
                        'flex items-center gap-2 whitespace-nowrap rounded-md px-2 py-1.5 text-left text-xs transition-colors',
                        tool === t ? '' : 'text-fg/60 hover:bg-fg/10 hover:text-fg',
                      )}
                      style={tool === t ? { background: `${c}25`, color: c } : undefined}
                    >
                      <SubIcon className="h-4 w-4 shrink-0" />
                      {label}
                    </button>
                  ))}
                </div>
              </div>
            </div>
          );
        })}

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
