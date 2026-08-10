/**
 * TopologyToolbar — floating bottom-left tool dock (design §5.1).
 * Add opens the device picker; Select/Link are the canvas tool modes
 * (Select is the default direct-manipulation pointer; Link is a hint mode —
 * links are drawn by dragging between device ports). Group is reserved for a
 * later phase and is disabled so it never reads as a dead control.
 */
import { MousePointer2, Spline, Group as GroupIcon, Plus, Trash2 } from 'lucide-react';
import { useTopoUiStore } from '@/store/topoUiStore';
import { useTopologyStore } from '@/store/topologyStore';
import { cn } from '@/lib/cn';
import { zc } from '@/theme/z';

export function TopologyToolbar() {
  const tool = useTopoUiStore((s) => s.tool);
  const setTool = useTopoUiStore((s) => s.setTool);
  const openPicker = useTopoUiStore((s) => s.openPicker);
  const deleteSelected = useTopoUiStore((s) => s.deleteSelected);
  const selectedNodeId = useTopologyStore((s) => s.selectedNodeId);
  const selectedLinkId = useTopologyStore((s) => s.selectedLinkId);
  const hasSelection = Boolean(selectedNodeId || selectedLinkId);
  const deleteLabel = selectedNodeId ? 'Delete device' : selectedLinkId ? 'Delete link' : 'Delete';

  return (
    <div className={cn('pointer-events-auto absolute bottom-4 left-4 flex items-center gap-1', zc.workspace)}>
      <div className="glass flex items-center gap-1 rounded-full border border-fg/12 p-1 shadow-glass">
        <button
          onClick={() => openPicker()}
          aria-label="Add device"
          title="Add device (A)"
          className="flex items-center gap-1.5 rounded-full bg-accent px-3 py-2 text-xs font-medium text-accent-fg transition-colors hover:bg-accent-soft"
        >
          <Plus className="h-4 w-4" />
          <span className="hidden sm:inline">Add</span>
        </button>

        <span className="mx-0.5 h-6 w-px bg-fg/10" aria-hidden />

        <ToolButton active={tool === 'select'} onClick={() => setTool('select')} icon={MousePointer2} label="Select" hint="Select (V)" />
        <ToolButton active={tool === 'link'} onClick={() => setTool('link')} icon={Spline} label="Link" hint="Link mode (L) — drag between device ports" />
        <ToolButton active={false} onClick={() => {}} icon={GroupIcon} label="Group" hint="Grouping — coming in a later phase" disabled />

        <span className="mx-0.5 h-6 w-px bg-fg/10" aria-hidden />

        <button
          onClick={() => deleteSelected?.()}
          disabled={!hasSelection}
          aria-label={deleteLabel}
          title={hasSelection ? `${deleteLabel} (Delete/Backspace)` : 'Select a device or link to delete'}
          className={cn(
            'flex items-center gap-1.5 rounded-full px-2.5 py-2 text-xs transition-colors',
            hasSelection
              ? 'text-danger/80 hover:bg-danger/10 hover:text-danger'
              : 'cursor-not-allowed opacity-40',
          )}
        >
          <Trash2 className="h-4 w-4" />
          <span className="hidden md:inline">Delete</span>
        </button>
      </div>
    </div>
  );
}

function ToolButton({
  active,
  onClick,
  icon: Icon,
  label,
  hint,
  disabled,
}: {
  active: boolean;
  onClick: () => void;
  icon: typeof MousePointer2;
  label: string;
  hint: string;
  disabled?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      aria-label={label}
      aria-pressed={active}
      title={hint}
      className={cn(
        'flex items-center gap-1.5 rounded-full px-2.5 py-2 text-xs transition-colors',
        disabled && 'cursor-not-allowed opacity-40',
        !disabled && active && 'bg-fg/12 text-fg',
        !disabled && !active && 'text-fg/60 hover:bg-fg/8 hover:text-fg/90',
      )}
    >
      <Icon className="h-4 w-4" />
      <span className="hidden md:inline">{label}</span>
    </button>
  );
}
