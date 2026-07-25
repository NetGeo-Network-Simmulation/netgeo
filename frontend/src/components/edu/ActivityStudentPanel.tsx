/**
 * ActivityStudentPanel — student mode. Three floating pieces over the live canvas:
 *  · left objectives dock (instructions + per-check checklist + grader feedback),
 *  · a top-right amber timer chip (only when the activity is timed),
 *  · a bottom control bar (status chips + score + Check my work + Submit).
 *
 * Visual idiom follows the approved Stitch screen (objectives left per the PNG),
 * translated onto theme-aware tokens (fg/recess/accent/success/warning) so both
 * light and dark stay legible — no hardcoded dark surface colours.
 *
 * There is no "in progress" verdict server-side: a check is pending until graded,
 * then pass/fail. The design's amber "active" objective maps onto the first
 * FAILING check (the thing to work on) and its grader reason.
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import {
  CheckCircle2,
  Circle,
  Timer,
  Lightbulb,
  FileCheck2,
  Send,
  RotateCcw,
  Loader2,
  AlertTriangle,
  X,
} from 'lucide-react';
import { useEduStore } from '@/store/eduStore';
import { useUiStore } from '@/store/uiStore';
import { useAuthStore } from '@/store/authStore';
import { cn } from '@/lib/cn';
import { zc } from '@/theme/z';
import {
  checkLabel,
  checkStatuses,
  fmtClock,
  elapsedSeconds,
  CHECK_KIND_LABEL,
  type CheckStatus,
} from './eduLogic';

export function ActivityStudentPanel() {
  const selectedId = useEduStore((s) => s.selectedId);
  const activity = useEduStore((s) => s.activities.find((a) => a.id === s.selectedId));
  const liveReport = useEduStore((s) => s.liveReport);
  const startedAt = useEduStore((s) => s.startedAt);
  const submitted = useEduStore((s) => s.submitted);
  const saving = useEduStore((s) => s.saving);
  const error = useEduStore((s) => s.error);
  const projectId = useUiStore((s) => s.projectId);
  const instantiate = useEduStore((s) => s.instantiate);
  const checkWork = useEduStore((s) => s.checkWork);
  const submit = useEduStore((s) => s.submit);
  const toBrowse = useEduStore((s) => s.toBrowse);
  const username = useAuthStore((s) => s.username);

  const [student, setStudent] = useState(username ?? '');
  useEffect(() => {
    if (username) setStudent((s) => s || username);
  }, [username]);

  const checks = activity?.checks ?? [];
  const statuses = useMemo(() => checkStatuses(checks, liveReport), [checks, liveReport]);
  const started = startedAt != null && projectId != null;

  // Countdown: local ticking clock, no global store churn. Auto-submits exactly at
  // the limit boundary (within_time resolves true — the backend only records
  // lateness, never blocks). Fires once via a ref guard.
  const limitS = activity?.time_limit_s ?? null;
  const [remaining, setRemaining] = useState<number | null>(null);
  const autoSubmitted = useRef(false);

  useEffect(() => {
    autoSubmitted.current = false;
  }, [startedAt]);

  useEffect(() => {
    if (limitS == null || startedAt == null) {
      setRemaining(null);
      return;
    }
    const tick = () => {
      const left = limitS - elapsedSeconds(startedAt);
      setRemaining(left);
      if (left <= 0 && !autoSubmitted.current && !submitted) {
        autoSubmitted.current = true;
        void submit(student, limitS);
      }
    };
    tick();
    const id = window.setInterval(tick, 1000);
    return () => window.clearInterval(id);
  }, [limitS, startedAt, submitted, student, submit]);

  if (!activity) {
    // Selected id points at nothing (e.g. it was just deleted) — recover, don't blank.
    return (
      <aside className={cn('glass-strong pointer-events-auto absolute left-3 top-16 w-[340px] max-w-[calc(100vw-5rem)] rounded-2xl border border-fg/12 p-5 text-center shadow-glass-lg', zc.workspace)}>
        <p className="text-sm text-fg/70">This activity is no longer available.</p>
        <button onClick={toBrowse} className="mt-3 text-xs font-semibold text-accent hover:underline">
          Back to activities
        </button>
      </aside>
    );
  }

  const onSubmit = () => {
    if (startedAt == null) return;
    void submit(student, elapsedSeconds(startedAt));
  };

  return (
    <>
      {/* Timer chip (top-right) — only when timed. */}
      {remaining != null && (
        <div className={cn('pointer-events-auto absolute right-3 top-3', zc.workspace)}>
          <div
            className={cn(
              'inline-flex items-center gap-1.5 rounded-md border bg-recess/60 px-3 py-1.5 shadow-glass',
              remaining <= 30 ? 'border-danger/40' : 'border-warning/30',
            )}
            role="timer"
            aria-label="Time remaining"
          >
            <Timer className={cn('h-4 w-4', remaining <= 30 ? 'text-danger' : 'text-warning')} />
            <span
              className={cn(
                'font-mono text-xs font-bold tabular-nums',
                remaining <= 30 ? 'text-danger' : 'text-warning',
              )}
            >
              {fmtClock(remaining)}
            </span>
          </div>
        </div>
      )}

      {/* Objectives dock (left, per PNG). */}
      <aside
        aria-label="Objectives"
        className={cn('glass-strong pointer-events-auto absolute left-3 top-16 bottom-16 flex w-[340px] max-w-[calc(100vw-5rem)] flex-col rounded-2xl border border-fg/12 shadow-glass-lg', zc.workspace)}
      >
        <header className="flex items-start gap-2 border-b border-fg/10 px-4 py-3">
          <div className="min-w-0 flex-1">
            <span className="mb-1 inline-block rounded bg-fg/8 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider text-fg/55">
              Lab
            </span>
            <h2 className="truncate text-base font-semibold text-fg/90">{activity.name}</h2>
          </div>
          <button
            onClick={toBrowse}
            aria-label="Leave activity"
            className="grid h-6 w-6 shrink-0 place-items-center rounded text-fg/50 hover:bg-fg/10 hover:text-fg"
          >
            <X className="h-4 w-4" />
          </button>
        </header>

        <div className="min-h-0 flex-1 space-y-5 overflow-y-auto ng-scroll px-4 py-4">
          {activity.instructions?.trim() && (
            <p className="whitespace-pre-wrap text-[13px] leading-relaxed text-fg/70">
              {activity.instructions}
            </p>
          )}

          <div>
            <h3 className="mb-3 text-[11px] font-semibold uppercase tracking-[0.05em] text-fg/50">
              Objectives
            </h3>
            {checks.length === 0 ? (
              <p className="text-[13px] leading-relaxed text-fg/45">
                This activity has no grading checks — read the instructions and submit when done.
              </p>
            ) : (
              <ul className="space-y-4">
                {checks.map((c, i) => (
                  <ObjectiveItem
                    key={i}
                    label={checkLabel(c, i)}
                    status={statuses[i]!}
                    reason={liveReport?.items[i]?.reason}
                  />
                ))}
              </ul>
            )}
          </div>
        </div>

        <FeedbackCard statuses={statuses} report={liveReport} graded={!!liveReport} started={started} />
      </aside>

      {/* Control bar (bottom): status chips + score + actions. */}
      <div className={cn('pointer-events-auto absolute bottom-3 left-3 right-3', zc.workspace)}>
        <div className="glass-strong flex flex-wrap items-center gap-x-3 gap-y-2 rounded-xl border border-fg/12 px-3 py-2 shadow-glass-lg">
          <div className="flex min-w-0 flex-1 items-center gap-2 overflow-x-auto ng-scroll">
            {checks.length === 0 ? (
              <span className="text-[11px] text-fg/40">No checks to track.</span>
            ) : (
              checks.map((c, i) => (
                <StatusChip
                  key={i}
                  label={c.label?.trim() || CHECK_KIND_LABEL[c.kind]}
                  status={statuses[i]!}
                />
              ))
            )}
          </div>

          {liveReport && (
            <span className="shrink-0 rounded-md bg-accent/12 px-2.5 py-1 font-mono text-xs font-bold text-accent">
              {Math.round(liveReport.score_pct)}%
            </span>
          )}

          {error && (
            <span role="alert" className="flex shrink-0 items-center gap-1 text-[11px] text-warning">
              <AlertTriangle className="h-3.5 w-3.5" />
              {error}
            </span>
          )}

          <input
            aria-label="Student name"
            value={student}
            onChange={(e) => setStudent(e.target.value)}
            placeholder="student"
            className="w-24 shrink-0 rounded-md border border-fg/15 bg-recess/20 px-2 py-1 text-xs text-fg/85 placeholder:text-fg/30 focus:border-accent/50 focus:outline-none"
          />

          {!started ? (
            <button
              onClick={() => void instantiate(selectedId!)}
              disabled={saving}
              className="inline-flex shrink-0 items-center gap-1.5 rounded-lg bg-accent px-3 py-1.5 text-xs font-semibold text-accent-fg hover:bg-accent-soft disabled:opacity-40"
            >
              {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RotateCcw className="h-3.5 w-3.5" />}
              Start attempt
            </button>
          ) : (
            <>
              <button
                onClick={() => void checkWork()}
                disabled={saving}
                className="inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-fg/15 px-3 py-1.5 text-xs font-semibold text-fg/85 hover:bg-fg/8 disabled:opacity-40"
              >
                {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <FileCheck2 className="h-3.5 w-3.5" />}
                Check my work
              </button>
              {submitted ? (
                <button
                  onClick={() => void instantiate(selectedId!)}
                  disabled={saving}
                  className="inline-flex shrink-0 items-center gap-1.5 rounded-lg bg-accent px-3 py-1.5 text-xs font-semibold text-accent-fg hover:bg-accent-soft disabled:opacity-40"
                >
                  <RotateCcw className="h-3.5 w-3.5" />
                  New attempt
                </button>
              ) : (
                <button
                  onClick={onSubmit}
                  disabled={saving}
                  className="inline-flex shrink-0 items-center gap-1.5 rounded-lg bg-accent px-3 py-1.5 text-xs font-semibold text-accent-fg hover:bg-accent-soft disabled:opacity-40"
                >
                  <Send className="h-3.5 w-3.5" />
                  Submit
                </button>
              )}
            </>
          )}
        </div>
      </div>
    </>
  );
}

/* -------------------------------------------------------------------------- */

function ObjectiveItem({
  label,
  status,
  reason,
}: {
  label: string;
  status: CheckStatus;
  reason?: string;
}) {
  if (status === 'pass') {
    return (
      <li className="flex items-start gap-3 opacity-60">
        <CheckCircle2 className="mt-0.5 h-[18px] w-[18px] shrink-0 text-success" />
        <p className="text-sm text-fg/90 line-through">{label}</p>
      </li>
    );
  }
  if (status === 'fail') {
    return (
      <li className="flex items-start gap-3">
        <span
          className="mt-0.5 grid h-[18px] w-[18px] shrink-0 place-items-center rounded-full border-2 border-warning"
          aria-hidden
        >
          <span className="h-1.5 w-1.5 rounded-full bg-warning" />
        </span>
        <div className="min-w-0">
          <p className="text-sm font-semibold text-warning">{label}</p>
          {reason && <p className="mt-1 text-[13px] leading-relaxed text-fg/55">{reason}</p>}
        </div>
      </li>
    );
  }
  return (
    <li className="flex items-start gap-3 opacity-80">
      <Circle className="mt-0.5 h-[18px] w-[18px] shrink-0 text-fg/40" />
      <p className="text-sm text-fg/80">{label}</p>
    </li>
  );
}

/** Bottom-of-dock card: grader feedback (first failing reason), or guidance before
 *  the first grade. Repurposes the Stitch "hint" slot — the schema has no hints, so
 *  we surface real grader feedback instead of inventing a hint sequence. */
function FeedbackCard({
  statuses,
  report,
  graded,
  started,
}: {
  statuses: CheckStatus[];
  report: ReturnType<typeof useEduStore.getState>['liveReport'];
  graded: boolean;
  started: boolean;
}) {
  const failIndex = statuses.findIndex((s) => s === 'fail');
  let body: string;
  if (!graded) {
    body = started
      ? 'Build the network on the canvas, then press "Check my work" to see how each objective scores.'
      : 'Press "Start attempt" to load this activity\'s starting network onto the canvas.';
  } else if (failIndex >= 0) {
    body = report?.items[failIndex]?.reason || 'One or more objectives are not passing yet.';
  } else {
    body = 'All objectives pass. Submit to record your attempt.';
  }

  return (
    <div className="border-t border-fg/10 p-4">
      <div className="glass relative flex items-start gap-3 overflow-hidden rounded-lg p-3.5">
        <div className="pointer-events-none absolute -right-6 -top-6 h-16 w-16 rounded-full bg-accent/20 blur-xl" aria-hidden />
        <Lightbulb className="mt-0.5 h-5 w-5 shrink-0 text-accent" />
        <p className="text-[13px] leading-relaxed text-fg/80">{body}</p>
      </div>
    </div>
  );
}

function StatusChip({ label, status }: { label: string; status: CheckStatus }) {
  const meta =
    status === 'pass'
      ? { cls: 'border-success/25 bg-success/10 text-success', dot: 'bg-success', word: 'PASS' }
      : status === 'fail'
        ? { cls: 'border-warning/25 bg-warning/10 text-warning', dot: 'bg-warning', word: 'CHECK' }
        : { cls: 'border-fg/15 bg-fg/5 text-fg/55', dot: 'bg-fg/40', word: 'PENDING' };
  return (
    <span
      className={cn(
        'inline-flex shrink-0 items-center gap-1.5 rounded-full border px-2 py-0.5',
        meta.cls,
      )}
      title={`${label}: ${meta.word}`}
    >
      <span className={cn('h-1.5 w-1.5 rounded-full', meta.dot)} aria-hidden />
      <span className="max-w-[9rem] truncate text-[9px] font-semibold uppercase tracking-wider">
        {label} {meta.word}
      </span>
    </span>
  );
}
