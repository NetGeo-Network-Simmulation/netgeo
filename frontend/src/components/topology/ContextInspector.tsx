/**
 * ContextInspector — right slide-in that hosts the existing PropertiesPanel
 * (design §5.3). It is NOT a permanent panel: it appears only when an object is
 * selected, can be pinned open, and closes on Esc (handled globally in
 * useShortcuts) or its close button. ~360px, resizing is deferred to a later
 * phase. The panel body is the untouched PropertiesPanel component.
 */
import { Pin, PinOff, Trash2, X } from 'lucide-react';
import { PropertiesPanel } from '@/components/PropertiesPanel';
import { useTopologyStore } from '@/store/topologyStore';
import { useTopoUiStore } from '@/store/topoUiStore';
import { cn } from '@/lib/cn';
import { zc } from '@/theme/z';

export function ContextInspector() {
  const selectedNodeId = useTopologyStore((s) => s.selectedNodeId);
  const selectedLinkId = useTopologyStore((s) => s.selectedLinkId);
  const select = useTopologyStore((s) => s.select);
  const pinned = useTopoUiStore((s) => s.inspectorPinned);
  const togglePin = useTopoUiStore((s) => s.togglePin);
  const deleteSelected = useTopoUiStore((s) => s.deleteSelected);

  const hasSelection = Boolean(selectedNodeId || selectedLinkId);
  const visible = hasSelection || pinned;
  if (!visible) return null;

  const close = () => {
    select({ nodeId: null, linkId: null });
    if (pinned) togglePin();
  };

  return (
    <aside
      aria-label="Selection inspector"
      className={cn(
        // B2-glitch-2 fix: removed animate-fade-in — its opacity:0→1 + translateY
        // left the panel semi-transparent over the map workspace during the
        // topology-mount frame (the animation starts before React commits the
        // old workspace unmount). The panel surface (.panel) is already opaque;
        // no transition is needed to avoid layout pop because the panel
        // is absolutely positioned outside normal flow.
        'panel absolute right-0 top-0 flex h-full w-[360px] max-w-[85vw] flex-col border-l border-fg/10 shadow-glass-lg',
        zc.workspace,
      )}
    >
      <div className="flex shrink-0 items-center justify-between border-b border-fg/10 px-3 py-2">
        <span className="text-xs font-semibold uppercase tracking-wider text-fg/50">Inspector</span>
        <div className="flex items-center gap-1">
          {hasSelection && (
            <button
              onClick={() => deleteSelected?.()}
              aria-label={selectedNodeId ? 'Delete device' : 'Delete link'}
              title={selectedNodeId ? 'Delete device' : 'Delete link'}
              className="grid h-6 w-6 place-items-center rounded text-fg/50 transition-colors hover:bg-danger/10 hover:text-danger"
            >
              <Trash2 className="h-3.5 w-3.5" />
            </button>
          )}
          <button
            onClick={togglePin}
            aria-label={pinned ? 'Unpin inspector' : 'Pin inspector'}
            aria-pressed={pinned}
            title={pinned ? 'Unpin (auto-hide when nothing selected)' : 'Pin open'}
            className={cn(
              'grid h-6 w-6 place-items-center rounded transition-colors',
              pinned ? 'text-accent hover:bg-fg/10' : 'text-fg/50 hover:bg-fg/10 hover:text-fg/80',
            )}
          >
            {pinned ? <Pin className="h-3.5 w-3.5" /> : <PinOff className="h-3.5 w-3.5" />}
          </button>
          <button
            onClick={close}
            aria-label="Close inspector"
            title="Close (Esc)"
            className="grid h-6 w-6 place-items-center rounded text-fg/50 transition-colors hover:bg-fg/10 hover:text-fg/80"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>
      <div className="min-h-0 flex-1 overflow-hidden">
        <PropertiesPanel />
      </div>
    </aside>
  );
}
