/**
 * ModeSwitch — Packet Tracer-parity Realtime | Simulation toggle (NG-SIM-01).
 *
 * Realtime: lab actions run the engine to completion (classic behaviour).
 * Simulation: actions only enqueue events; the Event Ledger window (opened
 * automatically) steps, seeks and rewinds the deterministic event stream.
 */
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Clock, ListVideo } from 'lucide-react';
import { labApi, type LabMode } from '@/api/client';
import { useLabStore } from '@/store/labStore';
import { useUiStore } from '@/store/uiStore';
import { cn } from '@/lib/cn';

export function ModeSwitch() {
  const projectId = useUiStore((s) => s.projectId);
  const mode = useLabStore((s) => s.mode);
  const setMode = useLabStore((s) => s.setMode);
  const openDrawer = useUiStore((s) => s.openDrawer);
  const queryClient = useQueryClient();

  const m = useMutation({
    mutationFn: (next: LabMode) => labApi.mode(projectId!, next),
    onSuccess: (data) => {
      setMode(data.mode);
      // Entering simulation surfaces the ledger — but the drawer only lives in
      // topology/map, so open it only there (otherwise it'd be a no-op panel).
      const vm = useUiStore.getState().viewMode;
      if (data.mode === 'simulation' && (vm === 'topology' || vm === 'map')) openDrawer('ledger');
      void queryClient.invalidateQueries({ queryKey: ['ledger', projectId] });
    },
  });

  const pick = (next: LabMode) => {
    if (!projectId || next === mode || m.isPending) return;
    m.mutate(next);
  };

  return (
    <div
      className="flex shrink-0 items-center rounded-md border border-fg/10 bg-fg/5 p-0.5"
      role="group"
      aria-label="Lab mode"
    >
      <ModeButton
        active={mode === 'realtime'}
        onClick={() => pick('realtime')}
        icon={Clock}
        label="Realtime"
        title="Realtime: actions run the lab to completion"
      />
      <ModeButton
        active={mode === 'simulation'}
        onClick={() => pick('simulation')}
        icon={ListVideo}
        label="Simulation"
        title="Simulation: step through every event in the ledger"
      />
    </div>
  );
}

function ModeButton({
  active,
  onClick,
  icon: Icon,
  label,
  title,
}: {
  active: boolean;
  onClick: () => void;
  icon: typeof Clock;
  label: string;
  title: string;
}) {
  return (
    <button
      onClick={onClick}
      title={title}
      aria-label={label}
      aria-pressed={active}
      className={cn(
        'flex items-center gap-1.5 whitespace-nowrap rounded px-2 py-1 text-xs transition-colors',
        active ? 'bg-accent text-accent-fg' : 'text-fg/50 hover:text-fg/80',
      )}
    >
      <Icon className="h-3.5 w-3.5 shrink-0" />
      {/* Icon-only below 2xl (same floor as SubNavStrip's tabs, leader
          review 2026-09-20): at md (768px) this was showing full text at
          every width the row actually has to survive, which is what still
          overflowed the NATIVE window at 1280 — its 3 title-bar buttons
          (132px) eat into the same 1280px budget the browser-mode header
          doesn't have to pay. aria-label above keeps the accessible name. */}
      <span className="hidden whitespace-nowrap 2xl:inline">{label}</span>
    </button>
  );
}
