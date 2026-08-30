/**
 * MapContextMenu — right-click, two-step device placement (v1.2.59).
 * Step 1: pick a device category. Step 2: pick a real catalog product,
 * filtered by search. Picking a product auto-creates a Site at the click
 * point, then deploys that product's device into it with its catalog
 * identity stamped on (`device_type_id`, N4) so the placed node genuinely
 * references the chosen product, not a generic kind — and the rack view
 * can draw its real faceplate instead of guessing.
 *
 * See docs/design/stitch-html/clay/map-context-menu/NOTES.md for the
 * approved layout reference and the verified categorization (section 5) —
 * this mirrors that grouping exactly, keyed off each DeviceType's pack-id
 * prefix (`"<pack-id>:<device-id>"`) rather than the backend's own
 * `category` field, which mis-buckets host-kind ONU/ONT devices (NOTES.md
 * section 5's documented backend/category mismatch).
 *
 * Reuses the same pixel-anchored popover shell as MapDeployMenu/
 * SiteCreateMenu (glass-strong, outside-click + Escape close, px anchor) —
 * a third instance of that established idiom, not a new primitive.
 *
 * Scope fence (read before editing): this menu only lists devices backed by
 * the 9 real device-library packs (38 devices) — built-in/custom device
 * types have no verified NodeKind mapping for this categorization and are
 * deliberately left out rather than guessed at (see NOTES.md section 6).
 *
 * v1.2.60: optional `siteId` + `initialCategoryKey` so `SitePopup`'s footer
 * ("Add AP" / "Add Fiber") can reopen this exact menu pre-filtered to a
 * category and scoped to an EXISTING site — skips the auto-create-site step
 * below and attaches the placed device to that site directly. The category
 * back-arrow still works, so this doubles as the site's general "add any
 * device" entry point (all 4 categories reachable, not just the prefilled one).
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Radio, Cable, Network, Server, ChevronLeft, Search, X, Loader, MapPin } from 'lucide-react';
import { cn } from '@/lib/cn';
import { zc } from '@/theme/z';
import { deviceTypesApi, physicalApi, type DeviceType, type ApiError } from '@/api/client';
import type { NodeKind, Topology } from '@/api/types';
import { deployAt } from '@/lib/mapDeploy';
import { useUiStore } from '@/store/uiStore';
import { useMapStore } from '@/store/mapStore';

interface Props {
  px: { x: number; y: number };
  lat: number;
  lon: number;
  onClose: () => void;
  /** Attach the placed device to this existing site instead of minting a new
   *  one (SitePopup's footer buttons). */
  siteId?: string;
  /** Pre-select a category (SitePopup passes its own vocabulary's target
   *  category); user can still hit the back arrow to browse the others. */
  initialCategoryKey?: string;
}

interface Category {
  key: string;
  label: string;
  icon: typeof Radio;
  /** Pack ids (device_types.py `_device_json_to_type`'s `id` prefix) that
   *  belong to this category — verified against network/devices/packs/*. */
  packIds: string[];
}

const CATEGORIES: Category[] = [
  { key: 'wireless', label: 'Wireless', icon: Radio, packIds: ['wireless-ap', 'cell-site'] },
  { key: 'fiber', label: 'Fiber', icon: Cable, packIds: ['olt', 'onu', 'optical-transport'] },
  { key: 'routing', label: 'Routing & Switching', icon: Network, packIds: ['routers', 'switches', 'firewalls'] },
  { key: 'compute', label: 'Compute', icon: Server, packIds: ['servers'] },
];

const packIdOf = (dt: DeviceType) => dt.id.split(':')[0] ?? '';

export function MapContextMenu({ px, lat, lon, onClose, siteId, initialCategoryKey }: Props) {
  const projectId = useUiStore((s) => s.projectId);
  const flashNotice = useMapStore((s) => s.flashNotice);
  const queryClient = useQueryClient();
  const [category, setCategory] = useState<Category | null>(
    () => CATEGORIES.find((c) => c.key === initialCategoryKey) ?? null,
  );
  const [search, setSearch] = useState('');
  const [busy, setBusy] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  const typesQ = useQuery({
    queryKey: ['device-types'],
    queryFn: deviceTypesApi.list,
    staleTime: Infinity,
  });

  const products = useMemo(() => {
    if (!category) return [];
    const all = (typesQ.data ?? []).filter((dt) => category.packIds.includes(packIdOf(dt)));
    const q = search.trim().toLowerCase();
    return q ? all.filter((dt) => dt.name.toLowerCase().includes(q)) : all;
  }, [typesQ.data, category, search]);

  // Close on outside click (mirrors MapDeployMenu/SiteCreateMenu exactly).
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

  const place = async (dt: DeviceType) => {
    if (!projectId || busy || !dt.icon) return;
    setBusy(true);
    try {
      let targetSiteId = siteId;
      if (!targetSiteId) {
        const topo = queryClient.getQueryData<Topology>(['topology', projectId]);
        const existing = topo?.sites?.length ?? 0;
        const site = await physicalApi.createSite({
          project_id: projectId,
          name: `Site-${existing + 1}`,
          lat,
          lon,
        });
        targetSiteId = site.id;
      }
      const kind = dt.icon as NodeKind;
      await deployAt(projectId, kind, lat, lon, (msg) => flashNotice(msg), targetSiteId, dt.id);
      await queryClient.invalidateQueries({ queryKey: ['topology', projectId] });
      // Notice text must match deployAt's own auto-open gate (autoOpenRf,
      // mapDeploy.ts) exactly, keyed off the real `kind` — not off which menu
      // category the user clicked. Branching on category here was the bug:
      // the Compute category's "Generic Linux Host" catalog device also
      // deploys with kind='host' (network/devices/packs/servers), so it hit
      // autoOpenRf same as a fiber ONU/ONT — but had no matching notice branch
      // and silently jumped the user into RF Planning. Keying on `kind`
      // covers every category at once (root cause, not per-category).
      if (kind === 'ap' || kind === 'cpe' || kind === 'host') {
        flashNotice(`${dt.name} placed — opened in RF Planning as ${kind === 'ap' ? 'an endpoint' : 'a CPE'}.`);
      } else if (category?.key === 'wireless' || category?.key === 'fiber') {
        // Picked from a category that implies RF/Fiber, but this product's
        // kind isn't RF-capable — say so instead of a silent no-op close.
        flashNotice(`${dt.name} placed — no RF/Fiber workspace support for this device yet.`);
      }
      onClose();
    } catch (err) {
      flashNotice((err as unknown as ApiError)?.message || 'Failed to place device — check backend.');
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
        'glass-strong pointer-events-auto w-64 rounded-xl border border-fg/15 p-2.5 shadow-glass-lg',
        zc.popover,
      )}
      role="menu"
      aria-label="Place device here"
    >
      <div className="mb-1.5 flex items-center gap-1 px-0.5">
        {category ? (
          <button
            onClick={() => setCategory(null)}
            aria-label="Back to categories"
            className="grid h-4 w-4 shrink-0 place-items-center rounded text-fg/50 hover:text-fg"
          >
            <ChevronLeft className="h-3.5 w-3.5" />
          </button>
        ) : (
          <MapPin className="h-3 w-3 shrink-0 text-fg/50" />
        )}
        <span className="flex-1 truncate text-[10px] font-semibold uppercase tracking-wider text-fg/50">
          {category ? category.label : `${lat.toFixed(5)}, ${lon.toFixed(5)}`}
        </span>
        <button
          onClick={onClose}
          aria-label="Close"
          className="grid h-5 w-5 shrink-0 place-items-center rounded text-fg/40 hover:text-fg"
        >
          <X className="h-3 w-3" />
        </button>
      </div>

      {!category ? (
        <div className="space-y-1">
          {CATEGORIES.map((c) => (
            <button
              key={c.key}
              onClick={() => setCategory(c)}
              className="flex w-full items-center gap-2 rounded-lg border border-fg/10 px-2.5 py-2 text-left transition-colors hover:border-fg/25 hover:bg-fg/10"
            >
              <c.icon className="h-3.5 w-3.5 text-fg/60" />
              <span className="text-xs font-medium text-fg/85">{c.label}</span>
            </button>
          ))}
        </div>
      ) : (
        <>
          <div className="relative mb-1.5">
            <Search className="pointer-events-none absolute left-2 top-1/2 h-3 w-3 -translate-y-1/2 text-fg/35" />
            <input
              autoFocus
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search products…"
              disabled={busy}
              className="w-full rounded-lg border border-fg/10 bg-recess/20 py-1.5 pl-7 pr-2.5 text-xs text-fg/90 outline-none focus:border-accent"
            />
          </div>
          <div className="ng-scroll max-h-56 space-y-0.5 overflow-y-auto">
            {typesQ.isLoading && <p className="px-1 py-2 text-[11px] text-fg/40">Loading catalog…</p>}
            {typesQ.isError && (
              <p className="px-1 py-2 text-[11px] text-danger">
                {(typesQ.error as unknown as ApiError)?.message ?? 'Failed to load catalog.'}
              </p>
            )}
            {!typesQ.isLoading && !typesQ.isError && products.length === 0 && (
              <p className="px-1 py-2 text-[11px] text-fg/40">No matching products.</p>
            )}
            {products.map((dt) => (
              <button
                key={dt.id}
                disabled={busy}
                onClick={() => void place(dt)}
                className="block w-full rounded-lg px-2 py-1.5 text-left transition-colors hover:bg-fg/10 disabled:opacity-50"
              >
                <p className="truncate text-[11px] font-medium text-fg/85">{dt.name}</p>
                {dt.description && <p className="truncate text-[9px] text-fg/40">{dt.description}</p>}
              </button>
            ))}
          </div>
        </>
      )}

      <div className="mt-1.5 flex items-center justify-between border-t border-fg/10 pt-1.5">
        <button onClick={onClose} className="text-[10px] text-fg/45 hover:text-fg/80">
          Cancel
        </button>
        {busy && <Loader className="h-3.5 w-3.5 animate-spin text-fg/50" />}
      </div>
    </div>
  );
}
