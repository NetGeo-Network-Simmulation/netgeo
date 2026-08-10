/**
 * Topology-workspace UI state (Phase 1 UX foundation).
 * Holds the transient chrome state that the full-canvas topology needs but
 * that isn't graph data: the active tool, the floating device picker, the
 * context-inspector pin, and a handle to the canvas "fit view" action so the
 * global `F` shortcut can drive React Flow without prop-drilling the instance.
 */
import { create } from 'zustand';
import { useUiStore } from './uiStore';

export type TopoTool = 'select' | 'link' | 'group';

/** Canvas protocol/layer overlays (design §6.1) — client-side highlight only. */
export type OverlayKey = 'ospf' | 'bgp' | 'vlan' | 'l3';

export interface FlowPos {
  x: number;
  y: number;
}

interface TopoUiState {
  tool: TopoTool;
  /** Flow-coords where a picked device should land (double-click), else null.
   *  The picker's open/close state lives in uiStore.activeModal ('devicePicker'). */
  pickerPos: FlowPos | null;
  inspectorPinned: boolean;
  /** Active canvas overlays; protocol chips dim non-members, `l3` labels links. */
  overlays: Record<OverlayKey, boolean>;
  /** Registered by TopologyCanvas.onInit; drives the `F` fit shortcut. */
  fit: (() => void) | null;
  /** Registered by TopologyCanvas.onInit; centers the viewport on a point. */
  centerOn: ((x: number, y: number) => void) | null;
  /** Registered by TopologyCanvas; deletes the current selection (confirms,
   *  calls the API, then updates the store). Null while the canvas isn't mounted. */
  deleteSelected: (() => void) | null;

  setTool: (t: TopoTool) => void;
  openPicker: (pos?: FlowPos) => void;
  closePicker: () => void;
  togglePin: () => void;
  toggleOverlay: (k: OverlayKey) => void;
  setFit: (fn: (() => void) | null) => void;
  setCenterOn: (fn: ((x: number, y: number) => void) | null) => void;
  setDeleteSelected: (fn: (() => void) | null) => void;
}

export const useTopoUiStore = create<TopoUiState>((set) => ({
  tool: 'select',
  pickerPos: null,
  inspectorPinned: false,
  overlays: { ospf: false, bgp: false, vlan: false, l3: false },
  fit: null,
  centerOn: null,
  deleteSelected: null,

  setTool: (tool) => set({ tool }),
  // Device picker is one of the exclusive modals; its open state is the shared
  // uiStore slot, only the landing position lives here.
  openPicker: (pos) => {
    set({ pickerPos: pos ?? null });
    useUiStore.getState().openModal('devicePicker');
  },
  closePicker: () => {
    set({ pickerPos: null });
    const ui = useUiStore.getState();
    if (ui.activeModal === 'devicePicker') ui.closeModal();
  },
  togglePin: () => set((s) => ({ inspectorPinned: !s.inspectorPinned })),
  toggleOverlay: (k) =>
    set((s) => ({ overlays: { ...s.overlays, [k]: !s.overlays[k] } })),
  setFit: (fit) => set({ fit }),
  setCenterOn: (centerOn) => set({ centerOn }),
  setDeleteSelected: (deleteSelected) => set({ deleteSelected }),
}));
