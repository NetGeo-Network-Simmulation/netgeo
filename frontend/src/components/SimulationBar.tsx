/**
 * SimulationBar — transport controls for the discrete-event engine.
 * Play / pause / step / stop + speed selector. Calls the REST sim endpoints
 * and reflects optimistic state; the authoritative state arrives via sim.tick
 * events on /ws/topology.
 */
import { Pause, Play, Square, StepForward } from 'lucide-react';
import { simApi } from '@/api/client';
import { useUiStore } from '@/store/uiStore';
import { cn } from '@/lib/cn';
import { Select } from '@/components/ui/Select';

const SPEEDS = [0.5, 1, 2, 4, 8];
const SPEED_OPTIONS = SPEEDS.map((s) => ({ value: String(s), label: `${s}×` }));

export function SimulationBar() {
  const { simState, simSpeed, setSimState, setSimSpeed, projectId } = useUiStore();

  const guarded = (nextState: typeof simState, fn: () => Promise<unknown>) => {
    if (!projectId) return;
    setSimState(nextState);
    void fn().catch(() => setSimState('idle'));
  };

  const onPlay = () =>
    // Resume a paused/stepped run; only a fresh `idle` engine is (re)started.
    guarded('running', () =>
      simState === 'idle'
        ? simApi.start({ project_id: projectId!, realtime: true })
        : simApi.resume(projectId!),
    );
  const onPause = () =>
    guarded('paused', () => simApi.pause(projectId!));
  // After a single step the engine is paused — reflect that so Play resumes.
  const onStep = () =>
    guarded('paused', () => simApi.step(projectId!));
  const onStop = () =>
    guarded('idle', () => simApi.stop(projectId!));

  const running = simState === 'running';

  return (
    <div className="flex items-center gap-1 rounded-md border border-fg/10 bg-fg/5 px-1 py-0.5">
      <CtrlButton label={running ? 'Pause' : 'Play'} onClick={running ? onPause : onPlay} active={running}>
        {running ? <Pause className="h-4 w-4" /> : <Play className="h-4 w-4" />}
      </CtrlButton>
      <CtrlButton label="Step" onClick={onStep}>
        <StepForward className="h-4 w-4" />
      </CtrlButton>
      <CtrlButton label="Stop" onClick={onStop}>
        <Square className="h-4 w-4" />
      </CtrlButton>

      <Select
        aria-label="Simulation speed"
        value={String(simSpeed)}
        onChange={(v) => setSimSpeed(Number(v))}
        options={SPEED_OPTIONS}
        className="ml-1 w-16"
      />
    </div>
  );
}

function CtrlButton({
  label,
  onClick,
  active,
  children,
}: {
  label: string;
  onClick: () => void;
  active?: boolean;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      aria-label={label}
      title={label}
      className={cn(
        'grid h-7 w-7 place-items-center rounded transition-colors',
        active ? 'bg-accent text-accent-fg' : 'text-fg/80 hover:bg-fg/10',
      )}
    >
      {children}
    </button>
  );
}
