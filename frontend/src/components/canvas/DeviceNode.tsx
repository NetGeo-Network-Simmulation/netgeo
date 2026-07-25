/**
 * DeviceNode — custom React Flow node renderer for a NetGeo device.
 * UISP-style device card: kind-colored icon with a status badge, node name,
 * and a secondary line (mgmt IP / kind + NOS). A kind accent bar and clean
 * selected/hover states read as planning-grade. Handles on all four sides
 * allow link creation in any direction (revealed on hover).
 * Memoized: with thousands of nodes, re-rendering only changed nodes matters.
 */
import { memo } from 'react';
import { Handle, Position, type NodeProps } from '@xyflow/react';
import type { NodeKind, NodeMode, NodeStatus, Nos } from '@/api/types';
import { nodeColors } from '@/theme/tokens';
import { cn } from '@/lib/cn';
import { DeviceIcon } from './DeviceIcon';

export interface DeviceNodeData extends Record<string, unknown> {
  name: string;
  kind: NodeKind;
  nos: Nos;
  mode: NodeMode;
  status: NodeStatus;
  /** Management IP (first configured interface address), if any. */
  ip?: string;
  /** Protocol overlay active but this node isn't a member → fade it back. */
  dim?: boolean;
  /** Protocol overlay member → ring it so the set stands out. */
  highlight?: boolean;
  /** Custom icon id from iconStore, if assigned. */
  icon?: string;
}

/** Status → badge dot color (theme-aware semantic tokens). */
const STATUS_DOT: Record<NodeStatus, string> = {
  running: 'bg-success',
  booting: 'bg-warning animate-pulse',
  stopped: 'bg-fg/30',
  degraded: 'bg-warning',
  error: 'bg-danger',
};

/** Status → 2.5D beacon glow under the tile (icons25d.png reference style). */
const STATUS_GLOW: Record<NodeStatus, string> = {
  running: 'bg-success',
  booting: 'bg-warning',
  stopped: 'bg-fg/20',
  degraded: 'bg-warning',
  error: 'bg-danger',
};

const STATUS_LABEL: Record<NodeStatus, string> = {
  running: 'Running',
  booting: 'Booting',
  stopped: 'Stopped',
  degraded: 'Degraded',
  error: 'Error',
};

const SIDES = [
  ['top', Position.Top],
  ['right', Position.Right],
  ['bottom', Position.Bottom],
  ['left', Position.Left],
] as const;

function DeviceNodeImpl({ data, selected }: NodeProps) {
  const d = data as DeviceNodeData;
  const color = nodeColors[d.kind];
  const secondary = d.ip ?? d.kind;

  return (
    <div
      title={`${d.name} — ${STATUS_LABEL[d.status]}`}
      className={cn(
        'group relative flex w-[184px] items-center gap-2.5 rounded-lg px-2.5 py-2',
        'glass border transition-all duration-fast',
        selected
          ? 'border-accent shadow-glass ring-1 ring-accent/40'
          : 'border-fg/10 hover:border-fg/25 hover:bg-fg/5',
        d.highlight && 'border-accent ring-2 ring-accent/70',
        d.dim && 'opacity-30',
      )}
    >
      {/* Kind accent bar */}
      <span
        className="absolute inset-y-2 left-0 w-0.5 rounded-full"
        style={{ background: color }}
        aria-hidden
      />

      {/* Connection ports (n8n-style): 8px visual dot with a 12px grab target.
          Size lives only in globals.css `.react-flow__handle` — do not re-declare
          it here, the two copies drifted apart and made the dots hang off-card.
          Fully revealed on hover; faint at rest so they don't crowd the canvas. */}
      {SIDES.map(([side, position]) => (
        <Handle
          key={side}
          type="source"
          position={position}
          id={side}
          className="!opacity-40 transition-opacity duration-fast group-hover:!opacity-100"
        />
      ))}

      {/* Icon tile with 2.5D beacon glow + status badge */}
      <span className="relative shrink-0">
        {/* Beacon: soft status-colored glow pooling at the tile's base */}
        <span
          className={cn(
            'pointer-events-none absolute -bottom-1 left-1/2 h-1.5 w-8 -translate-x-1/2 rounded-full opacity-70 blur-[5px]',
            STATUS_GLOW[d.status],
          )}
          aria-hidden
        />
        {/* Tile: subtle top-lit gradient in the kind color + lift shadow */}
        <span
          className="relative grid h-9 w-9 place-items-center rounded-lg shadow-soft ring-1 ring-inset ring-fg/15"
          style={{ background: `linear-gradient(155deg, ${color}38, ${color}12)`, color }}
        >
          <DeviceIcon kind={d.kind} iconId={d.icon} className="h-[18px] w-[18px]" />
        </span>
        <span
          className={cn(
            'absolute -bottom-0.5 -right-0.5 h-2.5 w-2.5 rounded-full ring-2 ring-recess/30',
            STATUS_DOT[d.status],
          )}
          aria-hidden
        />
      </span>

      {/* Name + secondary line */}
      <div className="min-w-0 flex-1">
        <p className="truncate text-[13px] font-semibold leading-tight text-fg/90">{d.name}</p>
        <div className="mt-0.5 flex items-center gap-1.5">
          <span className="truncate font-mono text-[10px] leading-none text-fg/55">{secondary}</span>
          <span className="shrink-0 rounded bg-fg/10 px-1 py-0.5 text-[9px] uppercase leading-none tracking-wide text-fg/50">
            {d.nos}
          </span>
          {d.mode === 'emul' && (
            <span className="shrink-0 rounded bg-accent/25 px-1 py-0.5 text-[9px] uppercase leading-none tracking-wide text-accent-soft">
              emul
            </span>
          )}
        </div>
      </div>
    </div>
  );
}

export const DeviceNode = memo(DeviceNodeImpl);
