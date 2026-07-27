/**
 * MapDeployMenu — popover that appears at the click point on the map when the
 * "Deploy Device" tool is active. Two primary categories (Wireless / Cabled) plus
 * per-kind preset buttons. Positioned via pixel coords from MapLibre's `e.point`.
 *
 * Calls deployAt() from mapDeploy.ts, then closes itself. Also reused by
 * SitePopup.tsx (with `siteId` set) so "Add Device" from a site popup goes
 * through the exact same deploy flow as an empty-map click.
 */
import { useRef, useEffect } from 'react';
import { Radio, Cable, WifiOff, Server, Network, FlameKindling, X, Loader } from 'lucide-react';
import { useState } from 'react';
import { cn } from '@/lib/cn';
import { zc } from '@/theme/z';
import type { NodeKind } from '@/api/types';
import { deployAt } from '@/lib/mapDeploy';
import { useUiStore } from '@/store/uiStore';
import { useMapStore } from '@/store/mapStore';

interface Props {
  /** Pixel position (MapLibre `e.point` from the map click event). */
  px: { x: number; y: number };
  lat: number;
  lon: number;
  onClose: () => void;
  /** Set when opened from a site popup — deployed devices join that site. */
  siteId?: string;
}

interface PresetItem {
  kind: NodeKind;
  label: string;
  icon: typeof Radio;
}

const WIRELESS_PRESETS: PresetItem[] = [
  { kind: 'ap',  label: 'AP',  icon: Radio },
  { kind: 'cpe', label: 'CPE', icon: WifiOff },
];

const CABLED_PRESETS: PresetItem[] = [
  { kind: 'router',   label: 'Router',   icon: Network },
  { kind: 'switch',   label: 'Switch',   icon: Cable },
  { kind: 'olt',      label: 'OLT',      icon: Server },
  { kind: 'firewall', label: 'Firewall', icon: FlameKindling },
];

export function MapDeployMenu({ px, lat, lon, onClose, siteId }: Props) {
  const projectId = useUiStore((s) => s.projectId);
  const flashNotice = useMapStore((s) => s.flashNotice);
  const [busy, setBusy] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  // Close on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [onClose]);

  // Close on Escape
  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [onClose]);

  const deploy = async (kind: NodeKind) => {
    if (!projectId || busy) return;
    setBusy(true);
    try {
      await deployAt(projectId, kind, lat, lon, (msg) => flashNotice(msg), siteId);
    } catch {
      flashNotice('Deploy failed — check backend.');
    } finally {
      setBusy(false);
      onClose();
    }
  };

  // Keep menu inside the map pane (nudge left/up if too close to edge)
  const style: React.CSSProperties = {
    position: 'absolute',
    left: px.x + 12,
    top: px.y - 8,
    transform: 'translateY(-50%)',
  };

  return (
    <div
      ref={ref}
      style={style}
      className={cn(
        'glass-strong pointer-events-auto w-52 rounded-xl border border-fg/15 p-2 shadow-glass-lg',
        zc.popover,
      )}
      role="menu"
      aria-label="Deploy device"
    >
      {/* Header */}
      <div className="mb-1.5 flex items-center justify-between px-1">
        <span className="text-[10px] font-semibold uppercase tracking-wider text-fg/50">
          Deploy device here
        </span>
        <button
          onClick={onClose}
          aria-label="Close"
          className="grid h-5 w-5 place-items-center rounded text-fg/40 hover:text-fg"
        >
          <X className="h-3 w-3" />
        </button>
      </div>

      {/* Wireless category */}
      <button
        disabled={busy}
        onClick={() => void deploy('ap')}
        className="mb-0.5 w-full rounded-lg border border-accent/20 bg-accent/10 px-3 py-2 text-left transition-colors hover:bg-accent/20 disabled:opacity-50"
      >
        <p className="text-xs font-semibold text-accent">Wireless</p>
        <p className="text-[10px] text-fg/50">AP / CPE radio</p>
      </button>

      {/* Cabled category */}
      <button
        disabled={busy}
        onClick={() => void deploy('switch')}
        className="mb-2 w-full rounded-lg border border-info/20 bg-info/10 px-3 py-2 text-left transition-colors hover:bg-info/20 disabled:opacity-50"
      >
        <p className="text-xs font-semibold text-info">Cabled</p>
        <p className="text-[10px] text-fg/50">OLT / switch / router fiber</p>
      </button>

      {/* Separator + presets */}
      <div className="border-t border-fg/10 pt-1.5">
        <p className="mb-1 px-1 text-[9px] font-semibold uppercase tracking-wider text-fg/35">
          Preset
        </p>
        <div className="grid grid-cols-3 gap-1">
          {[...WIRELESS_PRESETS, ...CABLED_PRESETS].map(({ kind, label, icon: Icon }) => (
            <button
              key={kind}
              disabled={busy}
              onClick={() => void deploy(kind)}
              className="flex flex-col items-center gap-0.5 rounded-lg border border-fg/10 py-1.5 text-[10px] text-fg/70 transition-colors hover:border-fg/25 hover:bg-fg/10 hover:text-fg disabled:opacity-50"
              aria-label={`Deploy ${label}`}
            >
              <Icon className="h-3.5 w-3.5" />
              {label}
            </button>
          ))}
        </div>
      </div>

      {/* Cancel + loading */}
      <div className="mt-1.5 flex items-center justify-between">
        <button
          onClick={onClose}
          className="text-[10px] text-fg/45 hover:text-fg/80"
        >
          Cancel
        </button>
        {busy && <Loader className="h-3.5 w-3.5 animate-spin text-fg/50" />}
      </div>
    </div>
  );
}
