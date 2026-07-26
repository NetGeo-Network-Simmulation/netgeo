/**
 * CheckListEditor + CheckRow — author-side grading-check builder. Each row edits
 * one GradeCheck; the visible fields switch on `kind` so the author only sees the
 * inputs that grading.py actually reads for that kind (CHECK_KIND_FIELDS mirrors
 * the backend `_HANDLERS`). Weight and an optional label are always available.
 */
import { Plus, Trash2 } from 'lucide-react';
import type { GradeCheck, GradeCheckKind } from '@/api/types';
import { useEduStore } from '@/store/eduStore';
import { CHECK_KINDS, CHECK_KIND_LABEL, CHECK_KIND_FIELDS } from './eduLogic';
import { Select } from '@/components/ui/Select';

const CHECK_KIND_OPTIONS = CHECK_KINDS.map((k) => ({ value: k, label: CHECK_KIND_LABEL[k] }));

const FIELD_META: Record<
  string,
  { label: string; placeholder: string; type: 'text' | 'number' }
> = {
  node: { label: 'Node', placeholder: 'R1', type: 'text' },
  iface: { label: 'Interface', placeholder: 'Gig0/0', type: 'text' },
  cidr: { label: 'CIDR', placeholder: '10.0.0.1/30', type: 'text' },
  vlan: { label: 'VLAN', placeholder: '10', type: 'number' },
  peer: { label: 'Peer (optional)', placeholder: 'R2', type: 'text' },
  dst: { label: 'Destination', placeholder: '192.168.1.10', type: 'text' },
};

export function CheckListEditor() {
  const checks = useEduStore((s) => s.draft.checks ?? []);
  const addCheck = useEduStore((s) => s.addCheck);

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <p className="text-[11px] font-semibold uppercase tracking-wider text-fg/50">
          Grading checks
        </p>
        <button
          onClick={() => addCheck({ kind: 'node_exists', weight: 1 })}
          className="inline-flex items-center gap-1 rounded-md border border-fg/15 px-2 py-1 text-[11px] font-medium text-fg/75 hover:bg-fg/8 hover:text-fg"
        >
          <Plus className="h-3 w-3" />
          Add check
        </button>
      </div>

      {checks.length === 0 ? (
        <p className="rounded-lg border border-dashed border-fg/15 px-3 py-4 text-center text-[11px] leading-relaxed text-fg/45">
          No checks yet. Add weighted assertions (node exists, interface IP, VLAN,
          OSPF adjacency, reachability) — the grader scores a student against these.
        </p>
      ) : (
        <ul className="space-y-2">
          {checks.map((c, i) => (
            <CheckRow key={i} index={i} check={c} />
          ))}
        </ul>
      )}
    </div>
  );
}

function CheckRow({ index, check }: { index: number; check: GradeCheck }) {
  const updateCheck = useEduStore((s) => s.updateCheck);
  const removeCheck = useEduStore((s) => s.removeCheck);
  const fields = CHECK_KIND_FIELDS[check.kind];

  return (
    <li className="rounded-xl border border-fg/10 bg-recess/30 p-2.5">
      <div className="flex items-center gap-2">
        <Select
          aria-label={`Check ${index + 1} kind`}
          value={check.kind}
          onChange={(v) => updateCheck(index, { kind: v as GradeCheckKind })}
          options={CHECK_KIND_OPTIONS}
          className="flex-1"
        />
        <label className="flex items-center gap-1 rounded-md border border-fg/15 bg-recess/50 px-2 py-1">
          <span className="text-[9px] font-semibold uppercase tracking-wider text-fg/40">Wt</span>
          <input
            type="number"
            min={0}
            step={1}
            value={check.weight ?? 1}
            onChange={(e) => updateCheck(index, { weight: Number(e.target.value) })}
            aria-label={`Check ${index + 1} weight`}
            className="w-10 bg-transparent text-right font-mono text-xs text-fg/85 focus:outline-none"
          />
        </label>
        <button
          onClick={() => removeCheck(index)}
          aria-label={`Remove check ${index + 1}`}
          className="grid h-7 w-7 place-items-center rounded-md text-fg/45 hover:bg-danger/15 hover:text-danger"
        >
          <Trash2 className="h-3.5 w-3.5" />
        </button>
      </div>

      <div className="mt-2 grid grid-cols-2 gap-2">
        {fields.map((f) => {
          const meta = FIELD_META[f as string];
          if (!meta) return null;
          const raw = check[f];
          return (
            <label key={f as string} className="flex flex-col gap-0.5">
              <span className="text-[9px] font-semibold uppercase tracking-wider text-fg/40">
                {meta.label}
              </span>
              <input
                type={meta.type}
                inputMode={meta.type === 'number' ? 'numeric' : undefined}
                placeholder={meta.placeholder}
                value={raw == null ? '' : String(raw)}
                onChange={(e) => {
                  const v = e.target.value;
                  updateCheck(index, {
                    [f]: meta.type === 'number' ? (v === '' ? null : Number(v)) : v || null,
                  } as Partial<GradeCheck>);
                }}
                className="rounded-md border border-fg/15 bg-recess/20 px-2 py-1 text-xs text-fg/85 placeholder:text-fg/30 focus:border-accent/50 focus:outline-none"
              />
            </label>
          );
        })}
        <label className="col-span-2 flex flex-col gap-0.5">
          <span className="text-[9px] font-semibold uppercase tracking-wider text-fg/40">
            Label (optional)
          </span>
          <input
            type="text"
            placeholder="Shown to the student as the objective"
            value={check.label ?? ''}
            onChange={(e) => updateCheck(index, { label: e.target.value || null })}
            className="rounded-md border border-fg/15 bg-recess/20 px-2 py-1 text-xs text-fg/85 placeholder:text-fg/30 focus:border-accent/50 focus:outline-none"
          />
        </label>
      </div>
    </li>
  );
}
