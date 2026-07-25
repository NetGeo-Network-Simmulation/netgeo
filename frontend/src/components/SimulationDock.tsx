/**
 * SimulationDock — floating bottom-center transport for Simulation mode
 * (design §6.3). Recomposes the deterministic event-ledger controls: restart,
 * previous event, play/pause, next event, speed, the sim clock, event count,
 * and a toggle for the Event Ledger drawer. Only shown in simulation mode —
 * realtime keeps its Run controls in the TopBar. No new engine: transport
 * reuses labApi.step/seek and the shared ['ledger'] query.
 */
import { useEffect, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ListVideo, Pause, Play, RotateCcw, SkipBack, SkipForward } from 'lucide-react';
import { labApi } from '@/api/client';
import { useLabStore } from '@/store/labStore';
import { useUiStore } from '@/store/uiStore';
import { cn } from '@/lib/cn';
import { zc } from '@/theme/z';

const SPEEDS = [0.25, 0.5, 1, 2, 4];

/** Sim virtual clock → hh:mm:ss.mmm (matches the design dock read-out). */
function fmtClock(t: number): string {
  const ms = Math.max(0, Math.round(t * 1000));
  const s = Math.floor(ms / 1000);
  const hh = String(Math.floor(s / 3600)).padStart(2, '0');
  const mm = String(Math.floor(s / 60) % 60).padStart(2, '0');
  const ss = String(s % 60).padStart(2, '0');
  return `${hh}:${mm}:${ss}.${String(ms % 1000).padStart(3, '0')}`;
}

export function SimulationDock() {
  const projectId = useUiStore((s) => s.projectId);
  const speed = useUiStore((s) => s.simSpeed);
  const setSpeed = useUiStore((s) => s.setSimSpeed);
  const pushRecords = useLabStore((s) => s.pushRecords);
  const setCursor = useLabStore((s) => s.setCursor);
  const setMode = useLabStore((s) => s.setMode);
  const openDrawer = useUiStore((s) => s.openDrawer);
  const ledgerOpen = useUiStore((s) => s.drawerOpen && s.drawerTab === 'ledger');
  const qc = useQueryClient();
  const [playing, setPlaying] = useState(false);

  const q = useQuery({
    queryKey: ['ledger', projectId],
    queryFn: () => labApi.ledger(projectId!, 0, 400),
    enabled: !!projectId,
  });
  const total = q.data?.total ?? 0;
  const simTime = q.data?.sim_time ?? 0;
  const refresh = () => qc.invalidateQueries({ queryKey: ['ledger', projectId] });

  const step = useMutation({
    mutationFn: (body: { events?: number }) => labApi.step(projectId!, body),
    onSuccess: (d) => {
      pushRecords(d.records);
      refresh();
      if (d.dispatched === 0) setPlaying(false); // queue drained → stop autoplay
    },
    onError: () => setPlaying(false),
  });
  const seek = useMutation({
    mutationFn: (seq: number) => labApi.seek(projectId!, seq),
    onSuccess: (d) => {
      setMode(d.mode);
      setCursor(d.seq);
      refresh();
    },
  });

  const busy = step.isPending || seek.isPending;

  // ponytail: autoplay = a timer that walks the ledger one event at a time, its
  // cadence scaled by the speed selector. No continuous engine — it just chains
  // labApi.step; step() self-stops when nothing is dispatched.
  const playRef = useRef(playing);
  playRef.current = playing;
  useEffect(() => {
    if (!playing || busy) return;
    const t = setTimeout(() => {
      if (playRef.current) step.mutate({ events: 1 });
    }, 500 / speed);
    return () => clearTimeout(t);
  }, [playing, busy, speed]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className={cn('pointer-events-auto fixed bottom-24 left-1/2 -translate-x-1/2', zc.dock)}>
      <div className="glass-strong flex items-center gap-1 rounded-full border border-fg/15 px-2 py-1.5 shadow-dock">
        <DockBtn
          label="Restart"
          onClick={() => {
            setPlaying(false);
            seek.mutate(0);
          }}
          disabled={busy || total === 0}
        >
          <RotateCcw className="h-4 w-4" />
        </DockBtn>
        <DockBtn
          label="Previous event"
          onClick={() => {
            setPlaying(false);
            seek.mutate(Math.max(0, total - 1));
          }}
          disabled={busy || total === 0}
        >
          <SkipBack className="h-4 w-4" />
        </DockBtn>

        <button
          onClick={() => setPlaying((p) => !p)}
          disabled={busy && !playing}
          aria-label={playing ? 'Pause' : 'Play'}
          aria-pressed={playing}
          title={playing ? 'Pause' : 'Play'}
          className={cn(
            'mx-0.5 grid h-9 w-9 place-items-center rounded-full shadow-glass transition-transform hover:scale-105 disabled:opacity-50',
            // Live playback = amber heartbeat (Stitch sim-active-pulse); idle =
            // sky primary. motion-reduce keeps the color state, drops the pulse.
            playing
              ? 'animate-sim-pulse bg-warning text-recess motion-reduce:animate-none'
              : 'bg-accent text-accent-fg',
          )}
        >
          {playing ? <Pause className="h-4 w-4" /> : <Play className="h-4 w-4" />}
        </button>

        <DockBtn
          label="Next event"
          onClick={() => {
            setPlaying(false);
            step.mutate({ events: 1 });
          }}
          disabled={busy}
        >
          <SkipForward className="h-4 w-4" />
        </DockBtn>

        <select
          aria-label="Simulation speed"
          value={speed}
          onChange={(e) => setSpeed(Number(e.target.value))}
          className="ml-1 rounded bg-transparent px-1 text-xs text-fg/80 outline-none"
        >
          {SPEEDS.map((s) => (
            <option key={s} value={s}>
              {s}×
            </option>
          ))}
        </select>

        <span className="mx-1 h-5 w-px bg-fg/15" aria-hidden />
        <span className="tabular-nums font-mono text-xs text-fg/85" aria-label="Simulation time">
          {fmtClock(simTime)}
        </span>
        <span className="ml-1.5 rounded-md bg-fg/10 px-1.5 py-0.5 text-[11px] tabular-nums text-fg/60">
          Events {total}
        </span>

        <span className="mx-1 h-5 w-px bg-fg/15" aria-hidden />
        <DockBtn
          label={ledgerOpen ? 'Hide event ledger' : 'Show event ledger'}
          active={ledgerOpen}
          onClick={() => openDrawer('ledger')}
        >
          <ListVideo className="h-4 w-4" />
        </DockBtn>
      </div>
    </div>
  );
}

function DockBtn({
  label,
  onClick,
  disabled,
  active,
  children,
}: {
  label: string;
  onClick: () => void;
  disabled?: boolean;
  active?: boolean;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      aria-label={label}
      aria-pressed={active}
      title={label}
      className={cn(
        'grid h-8 w-8 place-items-center rounded-full transition-colors',
        active ? 'bg-accent/20 text-accent' : 'text-fg/75 hover:bg-fg/10 hover:text-fg',
        disabled && 'opacity-40',
      )}
    >
      {children}
    </button>
  );
}
