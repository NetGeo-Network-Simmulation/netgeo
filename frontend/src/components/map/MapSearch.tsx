/**
 * MapSearch — place/address geocoding box (top-left of the map).
 *
 * Enter runs a Nominatim search (services/geocodeService); picking a result flies
 * the map there and drops a temporary marker (SearchResultLayer in MapView).
 * Placed top-left by convention — the vertical tool bar is left-center, and the
 * counter chips, basemap switcher, signal legend, and GIS toggle stack the
 * top-right, so this corner is free.
 */
import { useRef, useState } from 'react';
import { Search, Loader2, X } from 'lucide-react';
import { useMapStore } from '@/store/mapStore';
import { geocode, type GeoResult } from '@/services/geocodeService';
import { cn } from '@/lib/cn';
import { zc } from '@/theme/z';
import { MAP_CHROME_INSET } from '@/theme/shell';

export function MapSearch() {
  const setSearchResult = useMapStore((s) => s.setSearchResult);
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<GeoResult[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  async function runSearch() {
    const q = query.trim();
    if (!q) return;
    abortRef.current?.abort();
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    setLoading(true);
    setError(null);
    try {
      setResults(await geocode(q, ctrl.signal));
    } catch (err) {
      if ((err as Error).name === 'AbortError') return; // superseded by a newer search
      setError('Search failed — try again.');
      setResults(null);
    } finally {
      if (abortRef.current === ctrl) setLoading(false);
    }
  }

  function pick(r: GeoResult) {
    setSearchResult(r);
    setQuery(r.label);
    setResults(null);
  }

  function clear() {
    abortRef.current?.abort();
    setQuery('');
    setResults(null);
    setError(null);
    setSearchResult(null);
  }

  return (
    <div className={cn('pointer-events-auto absolute top-3 w-72', MAP_CHROME_INSET, zc.popover)}>
      <div className="glass-strong flex items-center gap-2 rounded-xl border border-fg/15 px-3 shadow-glass-lg">
        <Search className="h-4 w-4 shrink-0 text-fg/45" />
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') void runSearch();
            if (e.key === 'Escape') clear();
          }}
          placeholder="Search location…"
          aria-label="Search location"
          className="h-11 w-full bg-transparent text-sm text-fg placeholder:text-fg/40 focus:outline-none"
        />
        {loading && <Loader2 className="h-4 w-4 shrink-0 animate-spin text-fg/45" />}
        {!loading && query && (
          <button
            onClick={clear}
            aria-label="Clear search"
            className="grid h-6 w-6 shrink-0 place-items-center rounded text-fg/45 hover:text-fg"
          >
            <X className="h-4 w-4" />
          </button>
        )}
      </div>

      {(results || error) && (
        <div className="glass-strong mt-1.5 overflow-hidden rounded-xl border border-fg/15 shadow-glass-lg">
          {error && <p className="px-3 py-2.5 text-xs text-danger">{error}</p>}
          {results && results.length === 0 && (
            <p className="px-3 py-2.5 text-xs text-fg/50">No matching places.</p>
          )}
          {results?.map((r, i) => (
            <button
              key={`${r.lat},${r.lng},${i}`}
              onClick={() => pick(r)}
              title={r.label}
              className={cn(
                'block w-full px-3 py-2 text-left transition-colors hover:bg-fg/10',
                i > 0 && 'border-t border-fg/10',
              )}
            >
              <span className="block truncate text-xs text-fg/85">{r.label}</span>
              <span className="mt-0.5 block font-mono text-[10px] text-fg/45">
                {r.lat.toFixed(4)}, {r.lng.toFixed(4)}
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
