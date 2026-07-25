/**
 * ActivityAuthorPanel — author mode's left dock. Composes a whole activity in one
 * pass (name, instructions, optional time limit, captured initial/answer networks,
 * grading checks) then POSTs it — there is no PATCH, so this is create-once and
 * "editing" an existing one clones it into a fresh draft (see eduStore).
 *
 * "Capture" snapshots the *current* project's live network (the canvas underneath)
 * into the draft's initial/answer archive envelope, so the author builds the graph
 * on the canvas exactly as a student or operator would.
 */
import {
  Save,
  Camera,
  CheckCircle2,
  Loader2,
  AlertTriangle,
  X,
} from 'lucide-react';
import { useEduStore } from '@/store/eduStore';
import { zc } from '@/theme/z';
import { CheckListEditor } from './CheckListEditor';

function isCaptured(env: Record<string, unknown> | undefined): boolean {
  return !!env && Object.keys(env).length > 0;
}

export function ActivityAuthorPanel() {
  const draft = useEduStore((s) => s.draft);
  const saving = useEduStore((s) => s.saving);
  const error = useEduStore((s) => s.error);
  const updateDraft = useEduStore((s) => s.updateDraft);
  const captureNetwork = useEduStore((s) => s.captureNetwork);
  const saveActivity = useEduStore((s) => s.saveActivity);
  const toBrowse = useEduStore((s) => s.toBrowse);

  const minutes = draft.time_limit_s != null ? Math.round(draft.time_limit_s / 60) : '';

  return (
    <aside
      aria-label="Author activity"
      className={`glass-strong pointer-events-auto absolute left-3 top-16 bottom-3 ${zc.workspace} flex w-[360px] max-w-[calc(100vw-5rem)] flex-col rounded-2xl border border-fg/12 shadow-glass-lg`}
    >
      <header className="flex items-center gap-2 border-b border-fg/10 px-4 py-3">
        <h2 className="text-sm font-semibold text-fg/90">Author activity</h2>
        <button
          onClick={toBrowse}
          aria-label="Close author"
          className="ml-auto grid h-6 w-6 place-items-center rounded text-fg/50 hover:bg-fg/10 hover:text-fg"
        >
          <X className="h-4 w-4" />
        </button>
      </header>

      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto ng-scroll px-4 py-4">
        <Field label="Name" required>
          <input
            type="text"
            value={draft.name}
            onChange={(e) => updateDraft({ name: e.target.value })}
            placeholder="Lab: OSPF Multi-Area Basics"
            className="w-full rounded-md border border-fg/15 bg-recess/20 px-2.5 py-1.5 text-sm text-fg/90 placeholder:text-fg/30 focus:border-accent/50 focus:outline-none"
          />
        </Field>

        <Field label="Instructions">
          <textarea
            value={draft.instructions ?? ''}
            onChange={(e) => updateDraft({ instructions: e.target.value })}
            rows={5}
            placeholder="What should the student build? Plain text — line breaks are preserved."
            className="w-full resize-y rounded-md border border-fg/15 bg-recess/20 px-2.5 py-1.5 text-xs leading-relaxed text-fg/85 placeholder:text-fg/30 focus:border-accent/50 focus:outline-none"
          />
        </Field>

        <Field label="Time limit (minutes)" hint="Leave blank for an untimed activity.">
          <input
            type="number"
            min={1}
            inputMode="numeric"
            value={minutes}
            onChange={(e) =>
              updateDraft({ time_limit_s: e.target.value ? Number(e.target.value) * 60 : null })
            }
            placeholder="—"
            className="w-28 rounded-md border border-fg/15 bg-recess/20 px-2.5 py-1.5 text-sm text-fg/90 placeholder:text-fg/30 focus:border-accent/50 focus:outline-none"
          />
        </Field>

        <div className="grid grid-cols-2 gap-2">
          <CaptureButton
            label="Initial network"
            captured={isCaptured(draft.initial)}
            onClick={() => void captureNetwork('initial')}
            disabled={saving}
          />
          <CaptureButton
            label="Answer network"
            captured={isCaptured(draft.answer)}
            onClick={() => void captureNetwork('answer')}
            disabled={saving}
          />
        </div>

        <div className="border-t border-fg/10 pt-3">
          <CheckListEditor />
        </div>
      </div>

      {/* Footer actions */}
      <footer className="space-y-2 border-t border-fg/10 px-4 py-3">
        {error && (
          <p role="alert" className="flex items-start gap-1.5 text-[11px] text-warning">
            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            {error}
          </p>
        )}
        <button
          onClick={() => void saveActivity()}
          disabled={saving || !draft.name.trim()}
          className="inline-flex w-full items-center justify-center gap-1.5 rounded-lg bg-accent px-3 py-2 text-sm font-semibold text-accent-fg transition-colors hover:bg-accent-soft disabled:cursor-not-allowed disabled:opacity-40"
        >
          {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
          Save activity
        </button>
      </footer>
    </aside>
  );
}

function Field({
  label,
  required,
  hint,
  children,
}: {
  label: string;
  required?: boolean;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block">
      <span className="mb-1 block text-[11px] font-semibold uppercase tracking-wider text-fg/50">
        {label}
        {required && <span className="ml-0.5 text-danger">*</span>}
      </span>
      {children}
      {hint && <span className="mt-1 block text-[10px] text-fg/40">{hint}</span>}
    </label>
  );
}

function CaptureButton({
  label,
  captured,
  onClick,
  disabled,
}: {
  label: string;
  captured: boolean;
  onClick: () => void;
  disabled?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={
        'flex flex-col items-start gap-1 rounded-lg border px-3 py-2.5 text-left transition-colors disabled:opacity-40 ' +
        (captured
          ? 'border-success/40 bg-success/10'
          : 'border-fg/15 bg-recess/40 hover:border-fg/25')
      }
    >
      <span className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-fg/55">
        {captured ? (
          <CheckCircle2 className="h-3.5 w-3.5 text-success" />
        ) : (
          <Camera className="h-3.5 w-3.5 text-fg/50" />
        )}
        {label}
      </span>
      <span className="text-[10px] text-fg/45">
        {captured ? 'Captured — click to re-capture' : 'Capture current canvas'}
      </span>
    </button>
  );
}
