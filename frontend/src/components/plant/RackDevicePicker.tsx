/**
 * RackDevicePicker — popover shown when the user clicks a free U in Add
 * Device mode (Rack3DElevationPanel). Lets them pick which real catalog
 * device gets created there instead of always getting a hardcoded 1U switch
 * (QA Q1, 2026-09-07).
 *
 * Same pixel-anchored glass popover idiom as MapDeployMenu/MapContextMenu
 * (outside-click + Escape close, px anchor, search + scrollable list) — not
 * a literal reuse of either: MapContextMenu's picker is wired to lat/lon +
 * deployAt + auto-site-creation, none of which apply inside a rack (there's
 * no map here, the rack/RU is already known from the click). What's reused
 * is the interaction pattern, not the component.
 *
 * `types` is pre-filtered by the parent (canRackMount + resolvable NodeKind)
 * — this component only searches and lists, no catalog fetch of its own
 * (the panel already holds one `/device-types` query, reused as-is).
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { Search, X, Loader, Server } from 'lucide-react';
import { cn } from '@/lib/cn';
import { zc } from '@/theme/z';
import type { DeviceType } from '@/api/client';

interface Props {
  px: { x: number; y: number };
  rackLabel: string;
  ruStart: number;
  types: DeviceType[];
  busy: boolean;
  onPick: (dt: DeviceType) => void;
  onClose: () => void;
}

export function RackDevicePicker({ px, rackLabel, ruStart, types, busy, onPick, onClose }: Props) {
  const [search, setSearch] = useState('');
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [onClose]);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [onClose]);

  const results = useMemo(() => {
    const q = search.trim().toLowerCase();
    return q ? types.filter((dt) => dt.name.toLowerCase().includes(q)) : types;
  }, [types, search]);

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
        'glass-strong pointer-events-auto w-64 rounded-xl border border-fg/15 p-2.5 shadow-glass-lg',
        zc.popover,
      )}
      role="menu"
      aria-label="Add device here"
    >
      <div className="mb-1.5 flex items-center gap-1 px-0.5">
        <Server className="h-3 w-3 shrink-0 text-fg/50" />
        <span className="flex-1 truncate text-[10px] font-semibold uppercase tracking-wider text-fg/50">
          {rackLabel} · U{ruStart}
        </span>
        <button
          onClick={onClose}
          aria-label="Close"
          className="grid h-5 w-5 shrink-0 place-items-center rounded text-fg/40 hover:text-fg"
        >
          <X className="h-3 w-3" />
        </button>
      </div>

      <div className="relative mb-1.5">
        <Search className="pointer-events-none absolute left-2 top-1/2 h-3 w-3 -translate-y-1/2 text-fg/35" />
        <input
          autoFocus
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search devices…"
          disabled={busy}
          className="w-full rounded-lg border border-fg/10 bg-recess/20 py-1.5 pl-7 pr-2.5 text-xs text-fg/90 outline-none focus:border-accent"
        />
      </div>

      <div className="ng-scroll max-h-56 space-y-0.5 overflow-y-auto">
        {results.length === 0 && (
          <p className="px-1 py-2 text-[11px] text-fg/40">No matching devices.</p>
        )}
        {results.map((dt) => {
          const ru = dt.physical?.ru;
          const sub = [dt.vendor, dt.category].filter(Boolean).join(' · ');
          return (
            <button
              key={dt.id}
              disabled={busy}
              onClick={() => onPick(dt)}
              className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left transition-colors hover:bg-fg/10 disabled:opacity-50"
            >
              <div className="min-w-0 flex-1">
                <p className="truncate text-[11px] font-medium text-fg/85">{dt.name}</p>
                {sub && <p className="truncate text-[9px] text-fg/40">{sub}</p>}
              </div>
              {ru != null && (
                <span className="shrink-0 rounded border border-fg/15 px-1 py-0.5 text-[9px] text-fg/50">
                  {ru}U
                </span>
              )}
            </button>
          );
        })}
      </div>

      <div className="mt-1.5 flex items-center justify-between border-t border-fg/10 pt-1.5">
        <button onClick={onClose} className="text-[10px] text-fg/45 hover:text-fg/80">
          Cancel
        </button>
        {busy && <Loader className="h-3.5 w-3.5 animate-spin text-fg/50" />}
      </div>
    </div>
  );
}
