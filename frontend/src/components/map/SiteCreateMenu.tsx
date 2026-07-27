/**
 * SiteCreateMenu — inline naming popover that appears at the click point once
 * the "Place Site" tool drops a pin (v1.2.55). Mirrors MapDeployMenu's
 * pixel-anchored popover pattern; the only field is a name because a Site is
 * a location container, not a device with kind/radio choices (see
 * TopologySiteLayer / SitePopup).
 *
 * Replaces the old Plant-toolbar ghost input: naming now happens at the same
 * moment the location is chosen, instead of a text box with no coordinates.
 */
import { useEffect, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Building2, Loader, X } from 'lucide-react';
import { cn } from '@/lib/cn';
import { zc } from '@/theme/z';
import { physicalApi } from '@/api/client';
import { useUiStore } from '@/store/uiStore';
import { useMapStore } from '@/store/mapStore';

interface Props {
  px: { x: number; y: number };
  lat: number;
  lon: number;
  defaultName: string;
  onClose: () => void;
}

export function SiteCreateMenu({ px, lat, lon, defaultName, onClose }: Props) {
  const projectId = useUiStore((s) => s.projectId);
  const flashNotice = useMapStore((s) => s.flashNotice);
  const queryClient = useQueryClient();
  const [name, setName] = useState(defaultName);
  const [busy, setBusy] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // Auto-focus + select the proposed name so typing replaces it outright.
  useEffect(() => {
    inputRef.current?.select();
  }, []);

  // Close on outside click (no site created — the user backed out of the pin).
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [onClose]);

  // Close on Escape.
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [onClose]);

  const create = async () => {
    const trimmed = name.trim();
    if (!projectId || !trimmed || busy) return;
    setBusy(true);
    try {
      await physicalApi.createSite({ project_id: projectId, name: trimmed, lat, lon });
      await queryClient.invalidateQueries({ queryKey: ['topology', projectId] });
      onClose();
    } catch {
      flashNotice('Failed to place site — check backend.');
      setBusy(false);
    }
  };

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
        'glass-strong pointer-events-auto w-60 rounded-xl border border-fg/15 p-2.5 shadow-glass-lg',
        zc.popover,
      )}
      role="dialog"
      aria-label="Name new site"
    >
      <div className="mb-1.5 flex items-center justify-between px-0.5">
        <span className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wider text-fg/50">
          <Building2 className="h-3 w-3" /> New site here
        </span>
        <button
          onClick={onClose}
          aria-label="Cancel"
          className="grid h-5 w-5 place-items-center rounded text-fg/40 hover:text-fg"
        >
          <X className="h-3 w-3" />
        </button>
      </div>

      <label htmlFor="ng-site-create-name" className="mb-1 block px-0.5 text-[9px] font-medium text-fg/40">
        Site name
      </label>
      <input
        id="ng-site-create-name"
        ref={inputRef}
        value={name}
        onChange={(e) => setName(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') void create();
        }}
        disabled={busy}
        className="w-full rounded-lg border border-fg/10 bg-recess/20 px-2.5 py-1.5 text-xs text-fg/90 outline-none focus:border-accent"
      />

      <div className="mt-2 flex items-center justify-between">
        <button onClick={onClose} className="text-[10px] text-fg/45 hover:text-fg/80">
          Cancel
        </button>
        <button
          onClick={() => void create()}
          disabled={busy || !name.trim()}
          className="flex items-center gap-1.5 rounded-lg bg-accent px-2.5 py-1 text-[11px] font-semibold text-accent-fg transition-colors hover:bg-accent/90 disabled:cursor-not-allowed disabled:opacity-40"
        >
          {busy && <Loader className="h-3 w-3 animate-spin" />} Create
        </button>
      </div>
    </div>
  );
}
