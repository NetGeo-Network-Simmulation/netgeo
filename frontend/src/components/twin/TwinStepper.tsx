/**
 * TwinStepper — the twin's step-progress row. A *visual state indicator*
 * (design), not a wizard: the current stage is derived from what the twin
 * already contains (deriveStepIndex). The only action here is Import Config,
 * the twin's entry point.
 *
 * Rendered as `TopologyCanvas`'s `topLeftExtra` (see TwinWorkspace), so it
 * stacks above the canvas's own overlay-chips/toolbar row inside one
 * coordinated container instead of a second absolutely-positioned layer.
 */
import { Check, FileInput } from 'lucide-react';
import { TWIN_STEPS } from './twinLogic';
import { cn } from '@/lib/cn';

export function TwinStepper({
  stepIndex,
  onImport,
}: {
  stepIndex: number;
  onImport: () => void;
}) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <ol className="glass flex flex-wrap items-center gap-1 rounded-full border border-fg/10 px-2 py-1.5 shadow-glass">
        {TWIN_STEPS.map((step, i) => {
          const done = i < stepIndex;
          const active = i === stepIndex;
          return (
            <li key={step} className="flex items-center">
              <span
                className={cn(
                  'flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs transition-colors',
                  active && 'bg-accent/20 font-semibold text-accent',
                  done && 'text-fg/70',
                  !active && !done && 'text-fg/40',
                )}
                aria-current={active ? 'step' : undefined}
              >
                <span
                  className={cn(
                    'grid h-4 w-4 place-items-center rounded-full text-[9px] font-bold',
                    done && 'bg-success/25 text-success',
                    active && 'bg-accent text-accent-fg',
                    !active && !done && 'bg-fg/10 text-fg/50',
                  )}
                  aria-hidden
                >
                  {done ? <Check className="h-2.5 w-2.5" /> : i + 1}
                </span>
                {step}
              </span>
              {i < TWIN_STEPS.length - 1 && (
                <span className="mx-0.5 h-px w-3 bg-fg/15" aria-hidden />
              )}
            </li>
          );
        })}
      </ol>

      <button
        onClick={onImport}
        className="glass flex shrink-0 items-center gap-1.5 rounded-full border border-accent/40 bg-accent/15 px-3.5 py-2 text-xs font-semibold text-accent shadow-glass transition-colors hover:bg-accent/25"
      >
        <FileInput className="h-4 w-4" /> Import Config
      </button>
    </div>
  );
}
