/**
 * FiberToolbar — bottom-centre control bar. Left: path selector, new-path, GPON
 * class, delete. Right: append-element buttons (Fiber span / Splitter / Connector
 * / Splice) + undo-last. These append real backend `FiberElement`s to the selected
 * path. The design's "OLT" head and "ODP" tail are the chain's implicit endpoints,
 * not element kinds; "Measure" is deferred (disabled).
 *
 * ponytail: element params (fiber km, splitter ratio) use inline fields rather
 * than a modal — appends are one PATCH each; add a param sheet only if fields grow.
 */
import { useState } from 'react';
import { Plus, Trash2, Undo2, Ruler } from 'lucide-react';
import { cn } from '@/lib/cn';
import { zc } from '@/theme/z';
import { useFiberStore } from '@/store/fiberStore';
import { GPON_OPTIONS, SPLIT_RATIOS } from './fiberLogic';
import type { GponClass } from '@/api/client';

function AppendButton({ label, onClick, disabled }: { label: string; onClick: () => void; disabled?: boolean }) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className="inline-flex items-center gap-1 rounded-md border border-fg/15 bg-recess/50 px-2.5 py-1 text-xs font-medium text-fg/80 hover:border-accent/50 hover:text-fg disabled:cursor-not-allowed disabled:opacity-40"
    >
      <Plus className="h-3 w-3" /> {label}
    </button>
  );
}

export function FiberToolbar() {
  const paths = useFiberStore((s) => s.paths);
  const selectedId = useFiberStore((s) => s.selectedId);
  const selected = paths.find((p) => p.id === selectedId);
  const search = useFiberStore((s) => s.search).trim().toLowerCase();
  const busy = useFiberStore((s) => s.busy);
  const projectId = useFiberStore((s) => s.projectId);
  const select = useFiberStore((s) => s.select);
  const createPath = useFiberStore((s) => s.createPath);
  const deletePath = useFiberStore((s) => s.deletePath);
  const setGpon = useFiberStore((s) => s.setGpon);
  const append = useFiberStore((s) => s.append);
  const removeElement = useFiberStore((s) => s.removeElement);

  const [km, setKm] = useState(1.0);
  const [ratio, setRatio] = useState(8);
  // Inline new-path naming (replaces window.prompt — QA v1.2.019): Enter commits,
  // Escape/blur cancels; matches the toolbar's inline-field pattern.
  const [newName, setNewName] = useState<string | null>(null);

  const visible = paths.filter((p) => !search || p.name.toLowerCase().includes(search));
  const hasPath = !!selected;
  const disabled = !hasPath || busy;

  function commitNewPath() {
    const name = newName?.trim();
    if (name) void createPath(name, (selected?.gpon_class ?? 'c_plus') as GponClass);
    setNewName(null);
  }

  return (
    <div className={cn('pointer-events-auto absolute bottom-8', zc.workspace)} style={{ left: 'calc(50% - 190px)' }}>
      <div className="glass-strong flex flex-wrap items-center gap-2 rounded-xl border border-fg/15 px-3 py-2 shadow-glass-lg">
        {/* Path selection */}
        <select
          aria-label="Fiber path"
          value={selectedId ?? ''}
          onChange={(e) => select(e.target.value || null)}
          className="max-w-[150px] rounded-md border border-fg/15 bg-recess/20 px-2 py-1 text-xs text-fg/85 focus:border-accent/50 focus:outline-none"
        >
          {visible.length === 0 && <option value="">No paths</option>}
          {visible.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
        {newName !== null ? (
          <input
            autoFocus
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') commitNewPath();
              if (e.key === 'Escape') setNewName(null);
            }}
            onBlur={() => setNewName(null)}
            aria-label="New fiber path name"
            placeholder="Path name — Enter to create"
            className="w-32 rounded-md border border-accent/50 bg-recess/20 px-2 py-1 text-xs text-fg/85 placeholder:text-fg/35 focus:outline-none"
          />
        ) : (
          <button
            onClick={() => setNewName(`ODP-${paths.length + 1}`)}
            disabled={!projectId || busy}
            aria-label="New fiber path"
            title="New fiber path"
            className="grid h-6 w-6 place-items-center rounded text-fg/60 hover:bg-fg/10 hover:text-fg disabled:opacity-40"
          >
            <Plus className="h-4 w-4" />
          </button>
        )}
        <select
          aria-label="GPON class"
          value={selected?.gpon_class ?? 'c_plus'}
          onChange={(e) => void setGpon(e.target.value as GponClass)}
          disabled={disabled}
          className="rounded-md border border-fg/15 bg-recess/20 px-2 py-1 text-xs text-fg/85 focus:border-accent/50 focus:outline-none disabled:opacity-40"
        >
          {GPON_OPTIONS.map((o) => (
            <option key={o.id} value={o.id}>
              {o.label}
            </option>
          ))}
        </select>
        <button
          onClick={() => selected && void deletePath(selected.id)}
          disabled={disabled}
          aria-label="Delete path"
          title="Delete path"
          className="grid h-6 w-6 place-items-center rounded text-fg/50 hover:bg-danger/15 hover:text-danger disabled:opacity-40"
        >
          <Trash2 className="h-3.5 w-3.5" />
        </button>

        <span className="mx-1 h-5 w-px bg-fg/10" />

        {/* Append elements */}
        <div className="flex items-center gap-1 rounded-md border border-fg/15 bg-recess/40 px-1.5 py-0.5">
          <input
            type="number"
            step={0.1}
            min={0.01}
            value={km}
            onChange={(e) => Number(e.target.value) > 0 && setKm(Number(e.target.value))}
            aria-label="Fiber length (km)"
            disabled={disabled}
            className="w-11 bg-transparent text-right font-mono text-xs text-fg/85 focus:outline-none disabled:opacity-40"
          />
          <span className="text-[9px] text-fg/40">km</span>
          <AppendButton
            label="Fiber"
            disabled={disabled}
            onClick={() => void append({ kind: 'fiber', length_m: km * 1000, atten_db_km: 0.22 })}
          />
        </div>
        <div className="flex items-center gap-1 rounded-md border border-fg/15 bg-recess/40 px-1.5 py-0.5">
          <span className="text-[9px] text-fg/40">1:</span>
          <select
            aria-label="Splitter ratio"
            value={ratio}
            onChange={(e) => setRatio(Number(e.target.value))}
            disabled={disabled}
            className="bg-transparent font-mono text-xs text-fg/85 focus:outline-none disabled:opacity-40"
          >
            {SPLIT_RATIOS.map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </select>
          <AppendButton
            label="Splitter"
            disabled={disabled}
            onClick={() => void append({ kind: 'splitter', split_ratio: ratio })}
          />
        </div>
        <AppendButton label="Connector" disabled={disabled} onClick={() => void append({ kind: 'connector' })} />
        <AppendButton label="Splice" disabled={disabled} onClick={() => void append({ kind: 'splice' })} />

        <button
          onClick={() => selected && selected.elements.length > 0 && void removeElement(selected.elements.length - 1)}
          disabled={disabled || (selected?.elements.length ?? 0) === 0}
          aria-label="Undo last element"
          title="Remove last element"
          className="grid h-6 w-6 place-items-center rounded text-fg/50 hover:bg-fg/10 hover:text-fg disabled:opacity-40"
        >
          <Undo2 className="h-3.5 w-3.5" />
        </button>

        <span
          className={cn('inline-flex cursor-not-allowed items-center gap-1 px-1.5 text-xs text-fg/30')}
          title="Measure — coming in a later phase"
        >
          <Ruler className="h-3.5 w-3.5" /> Measure
        </span>
      </div>
    </div>
  );
}
