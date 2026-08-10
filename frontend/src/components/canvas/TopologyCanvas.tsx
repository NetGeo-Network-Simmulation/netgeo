/**
 * TopologyCanvas — the heart of NetGeo's topology workspace. React Flow canvas with:
 *  - drag-drop device creation from the palette (HTML5 DnD)
 *  - link creation by dragging between node handles
 *  - zoom/pan, minimap, snap grid
 *  - selection wired to the shared topology store
 *  - debounced position persistence to the backend (move flood control)
 *
 * Performance: store nodes live in Maps; we derive RF nodes/edges with a
 * memoized projection. `onlyRenderVisibleElements` keeps thousands of nodes
 * cheap. Node moves are committed locally immediately, pushed to the server
 * on drag-stop only (not on every frame).
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ReactFlow,
  Background,
  BackgroundVariant,
  ConnectionMode,
  Controls,
  MiniMap,
  Panel,
  type Connection,
  type Edge,
  type Node,
  type NodeChange,
  type OnNodeDrag,
  type NodeMouseHandler,
  type ReactFlowInstance,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { Search, Plus, X, Waypoints } from 'lucide-react';
import { DeviceNode, type DeviceNodeData } from './DeviceNode';
import { PulseEdge } from './PulseEdge';
import { ConnectionLine } from './ConnectionLine';
import { OverlayChips } from './OverlayChips';
import { hierarchyLayout } from './hierarchyLayout';
import { useTopologyStore } from '@/store/topologyStore';
import { useUiStore } from '@/store/uiStore';
import { useLabStore } from '@/store/labStore';
import { useTopoUiStore, type OverlayKey } from '@/store/topoUiStore';
import { nodesApi, linksApi } from '@/api/client';
import { placeDevice } from '@/lib/placeDevice';
import { linkStatusColors, nodeColors } from '@/theme/tokens';
import type { LinkType, NodeModel } from '@/api/types';
import { cn } from '@/lib/cn';

const nodeTypes = { device: DeviceNode };
const edgeTypes = { pulse: PulseEdge };

/** Type-aware edge styling: fiber solid+bold, copper solid, wireless/virtual dashed. */
const EDGE_WIDTH: Record<LinkType, number> = { fiber: 2.6, copper: 2, wireless: 2, virtual: 1.6 };
const EDGE_DASH: Record<LinkType, string | undefined> = {
  fiber: undefined,
  copper: undefined,
  wireless: '4 5',
  virtual: '1 6',
};

export function TopologyCanvas() {
  const rfRef = useRef<ReactFlowInstance<Node<DeviceNodeData>, Edge> | null>(null);
  const nodesMap = useTopologyStore((s) => s.nodes);
  const linksMap = useTopologyStore((s) => s.links);
  const selectedNodeId = useTopologyStore((s) => s.selectedNodeId);
  const selectedLinkId = useTopologyStore((s) => s.selectedLinkId);
  const moveNode = useTopologyStore((s) => s.moveNode);
  const upsertLink = useTopologyStore((s) => s.upsertLink);
  const removeNode = useTopologyStore((s) => s.removeNode);
  const removeLink = useTopologyStore((s) => s.removeLink);
  const select = useTopologyStore((s) => s.select);
  const projectId = useUiStore((s) => s.projectId);
  const openPicker = useTopoUiStore((s) => s.openPicker);
  const setFit = useTopoUiStore((s) => s.setFit);
  const setCenterOn = useTopoUiStore((s) => s.setCenterOn);
  const setDeleteSelected = useTopoUiStore((s) => s.setDeleteSelected);
  const overlays = useTopoUiStore((s) => s.overlays);
  const inspectorPinned = useTopoUiStore((s) => s.inspectorPinned);
  const protoActive = overlays.ospf || overlays.bgp || overlays.vlan;
  // BUG-05: the ContextInspector (360px right panel) is on-screen whenever
  // something is selected or the inspector is pinned — mirror that condition so
  // the minimap can slide clear of it instead of hiding underneath.
  const inspectorOpen = Boolean(selectedNodeId || selectedLinkId) || inspectorPinned;

  /** Select a node and pan/zoom the viewport to center it (search "locate"). */
  const locateNode = useCallback(
    (n: NodeModel) => {
      select({ nodeId: n.id });
      rfRef.current?.setCenter(n.x, n.y, { zoom: 1.2, duration: 400 });
    },
    [select],
  );

  // BUG-08: "Open in Topology" (Problem Center) parks the node id in the ui
  // store; consume it here — SELECT the node (drives the React Flow `selected`
  // prop + the StatusBar), center the viewport, then clear the request. Store
  // instead of a window event so the request survives the workspace switch
  // (this canvas mounts after the dispatch); rfReady gates until the React
  // Flow instance exists so the centering isn't silently skipped.
  const focusNodeId = useUiStore((s) => s.focusNodeId);
  const setFocusNode = useUiStore((s) => s.setFocusNode);
  const [rfReady, setRfReady] = useState(false);
  // Frozen at mount: React Flow's initial fit runs deferred (after nodes are
  // measured) and consults the fitView prop AT THAT TIME — so the suppression
  // must hold for the whole mount, not just until the request is consumed
  // (clearing focusNodeId first flipped the prop back and the late fit
  // overrode setCenter; QA round 2).
  const [suppressInitialFit] = useState(() => Boolean(useUiStore.getState().focusNodeId));
  useEffect(() => {
    if (!focusNodeId || !rfReady) return;
    const n = nodesMap.get(focusNodeId);
    if (!n) {
      // Stale id (node deleted) — drop the request and do the fit we suppressed.
      setFocusNode(null);
      rfRef.current?.fitView({ duration: 400 });
      return;
    }
    select({ nodeId: focusNodeId });
    rfRef.current?.setCenter(n.x, n.y, { zoom: 1.2, duration: 400 });
    setFocusNode(null);
  }, [focusNodeId, rfReady, nodesMap, select, setFocusNode]);

  // Project store Maps -> React Flow nodes/edges (memoized by identity).
  const rfNodes: Node<DeviceNodeData>[] = useMemo(
    () =>
      Array.from(nodesMap.values()).map((n) => {
        const member = protoActive && nodeInOverlay(n, overlays);
        return {
          id: n.id,
          type: 'device',
          position: { x: n.x, y: n.y },
          selected: n.id === selectedNodeId,
          data: {
            name: n.name,
            kind: n.kind,
            nos: n.nos,
            mode: n.mode,
            status: n.status,
            ip: mgmtIp(n),
            highlight: member,
            dim: protoActive && !member,
            icon: typeof n.intent?.icon === 'string' ? (n.intent.icon as string) : undefined,
          },
        };
      }),
    [nodesMap, selectedNodeId, overlays, protoActive],
  );

  // Follow-the-packet animation (NG-CAP-04 MVP): latest fresh pulse per link.
  const pulses = useLabStore((s) => s.pulses);
  const pulseByLink = useMemo(() => {
    const map = new Map<string, (typeof pulses)[number]>();
    for (const p of pulses) map.set(p.linkId, p); // later pulses win
    return map;
  }, [pulses]);

  const rfEdges: Edge[] = useMemo(
    () =>
      Array.from(linksMap.values()).map((l) => {
        const color = linkStatusColors[l.status ?? 'unknown'];
        const source = ifaceNodeId(nodesMap, l.a_iface);
        const target = ifaceNodeId(nodesMap, l.b_iface);
        const pulse = pulseByLink.get(l.id);
        // On a protocol overlay, keep only edges whose both ends are members.
        const onOverlay =
          !protoActive || (memberById(nodesMap, source, overlays) && memberById(nodesMap, target, overlays));
        // A down/errored/admin-down link reads as a dashed line (on top of its red
        // status color); an up link keeps its type-based dash (wireless/virtual).
        const down = l.status && l.status !== 'up' && l.status !== 'unknown';
        return {
          id: l.id,
          source,
          target,
          type: 'pulse',
          animated: l.type === 'wireless',
          style: {
            stroke: color,
            strokeWidth: EDGE_WIDTH[l.type] ?? 2,
            strokeDasharray: down ? '6 5' : EDGE_DASH[l.type],
            opacity: onOverlay ? 1 : 0.12,
          },
          data: {
            type: l.type,
            bandwidth: l.bandwidth,
            pulse,
            pulseReverse: pulse ? pulse.fromNode === target : false,
            // Bandwidth pill shows whenever the link carries a rate (n8n-look),
            // no overlay required; no data → no label (never fabricated).
            label: l.bandwidth ? `${l.bandwidth}M` : undefined,
            // Stashed for onReconnect rollback (not rendered).
            a_iface: l.a_iface,
            b_iface: l.b_iface,
          },
        } as Edge;
      }),
    [linksMap, nodesMap, pulseByLink, overlays, protoActive],
  );

  /* --- Node drag: local move now, persist on drag-stop --------------------- */
  const onNodesChange = useCallback(
    (changes: NodeChange[]) => {
      for (const c of changes) {
        if (c.type === 'position' && c.position) {
          moveNode(c.id, c.position.x, c.position.y);
        }
      }
    },
    [moveNode],
  );

  const onNodeDragStop: OnNodeDrag<Node<DeviceNodeData>> = useCallback((_e, node) => {
    void nodesApi.move(node.id, node.position.x, node.position.y).catch(() => {});
  }, []);

  /* --- Auto-layout (E3): explicit, destructive, never runs on its own ------ */
  // Rewrites x,y for every node via the same move path drag-stop already uses
  // (local update + persisted PATCH), so it's a one-click "tidy up" rather than
  // a new persistence surface. No undo — a confirm is the ponytail-sized guard.
  const onAutoLayout = useCallback(() => {
    if (nodesMap.size === 0) return;
    const ok = window.confirm(
      'Auto-layout repositions every node on this canvas by hierarchy. Manual placement will be lost. Continue?',
    );
    if (!ok) return;
    const positions = hierarchyLayout(Array.from(nodesMap.values()), Array.from(linksMap.values()));
    for (const [id, pos] of positions) {
      moveNode(id, pos.x, pos.y);
      void nodesApi.move(id, pos.x, pos.y).catch(() => {});
    }
  }, [nodesMap, linksMap, moveNode]);

  const onSelectionNode: NodeMouseHandler<Node<DeviceNodeData>> = useCallback(
    (_e, node) => select({ nodeId: node.id }),
    [select],
  );

  /* --- Delete: confirm (nodes cascade their links), call the API, only then
   * touch the store — a failed DELETE must leave the object in place. -------- */
  const deleteNode = useCallback(
    (id: string) => {
      const node = nodesMap.get(id);
      if (!node) return;
      const ifaceIds = new Set(node.interfaces.map((i) => i.id));
      const linkCount = Array.from(linksMap.values()).filter(
        (l) => ifaceIds.has(l.a_iface) || ifaceIds.has(l.b_iface),
      ).length;
      const msg =
        linkCount > 0
          ? `Delete "${node.name}"? Its ${linkCount} connected link${linkCount === 1 ? '' : 's'} will be deleted too. This can't be undone.`
          : `Delete "${node.name}"? This can't be undone.`;
      if (!window.confirm(msg)) return;
      void nodesApi
        .remove(id)
        .then(() => removeNode(id))
        .catch(() => window.alert(`Failed to delete "${node.name}". It was not removed — try again.`));
    },
    [nodesMap, linksMap, removeNode],
  );

  const deleteLink = useCallback(
    (id: string) => {
      if (!linksMap.has(id)) return;
      void linksApi
        .remove(id)
        .then(() => removeLink(id))
        .catch(() => window.alert('Failed to delete link. It was not removed — try again.'));
    },
    [linksMap, removeLink],
  );

  // Shared by the toolbar button, the Inspector's delete button, and the
  // Delete/Backspace key below — one entry point, one confirm/error path.
  useEffect(() => {
    const run = () => {
      if (selectedNodeId) deleteNode(selectedNodeId);
      else if (selectedLinkId) deleteLink(selectedLinkId);
    };
    setDeleteSelected(run);

    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key !== 'Delete' && e.key !== 'Backspace') return;
      const el = e.target as HTMLElement | null;
      // Never hijack Delete/Backspace while typing (Inspector's hostname field etc).
      if (el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.tagName === 'SELECT' || el.isContentEditable)) return;
      if (!selectedNodeId && !selectedLinkId) return;
      e.preventDefault();
      run();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => {
      setDeleteSelected(null);
      window.removeEventListener('keydown', onKeyDown);
    };
  }, [selectedNodeId, selectedLinkId, deleteNode, deleteLink, setDeleteSelected]);

  /* --- Link creation ------------------------------------------------------- */
  // A completed handle→handle drag is the ONLY thing that creates a link. We
  // reject self-links and duplicates (an identical A↔B pair already present),
  // so stray clicks after a link is drawn can't spawn phantom edges.
  const onConnect = useCallback(
    (conn: Connection) => {
      if (!conn.source || !conn.target || !projectId) return;
      if (conn.source === conn.target) return; // no self-loops
      // Duplicate guard: same unordered node pair already linked → no-op.
      const exists = Array.from(linksMap.values()).some((l) => {
        const s = ifaceNodeId(nodesMap, l.a_iface);
        const t = ifaceNodeId(nodesMap, l.b_iface);
        return (
          (s === conn.source && t === conn.target) || (s === conn.target && t === conn.source)
        );
      });
      if (exists) return;
      const aIface = firstFreeIface(nodesMap.get(conn.source));
      const bIface = firstFreeIface(nodesMap.get(conn.target));
      if (!aIface || !bIface) return;

      // Optimistic local edge; backend assigns the real link id.
      // OBS-01 fix: removeLink(tempId) before inserting the real link so the
      // store never holds both at once (was the source of the transient double-count).
      const tempId = `tmp-${conn.source}-${conn.target}-${Date.now()}`;
      upsertLink({
        id: tempId,
        project_id: projectId,
        a_iface: aIface,
        b_iface: bIface,
        type: 'copper',
        bandwidth: 1000,
        delay: 1,
        loss: 0,
        mtu: 1500,
        status: 'up',
      });
      void linksApi
        .create({ project_id: projectId, a_iface: aIface, b_iface: bIface, type: 'copper' })
        .then((real) => {
          removeLink(tempId);
          upsertLink(real);
        })
        .catch(() => removeLink(tempId));
    },
    [nodesMap, linksMap, upsertLink, removeLink, projectId],
  );

  /* --- Edge reconnect: drag endpoint of an existing edge to a new node ------ */
  // React Flow v12 API: onReconnect fires when the user drops a dragged edge
  // endpoint onto a new handle. We delete the old link then create a fresh one.
  // Drop on empty space → React Flow does NOT fire onReconnect (edge is restored
  // to its previous position automatically), so no cancel logic needed here.
  const onReconnect = useCallback(
    (oldEdge: Edge, newConnection: Connection) => {
      if (!newConnection.source || !newConnection.target || !projectId) return;
      if (newConnection.source === newConnection.target) return;

      // Fix #2: compute both ifaces BEFORE touching the store.
      // If either is null the reconnect cannot proceed — bail without removing
      // the existing link (was: link disappeared even on failed reconnects).
      const aIface = firstFreeIface(nodesMap.get(newConnection.source));
      const bIface = firstFreeIface(nodesMap.get(newConnection.target));
      if (!aIface || !bIface) return;

      // Fix #1: capture the full LinkModel from the store for rollback so we
      // restore the original type/bandwidth/delay/loss/mtu (was: hardcoded defaults).
      const oldLink = linksMap.get(oldEdge.id);

      // Optimistic: remove old edge from store then create a new link.
      removeLink(oldEdge.id);
      void linksApi.remove(oldEdge.id).catch(() => {
        // Rollback: restore the original link with its real properties.
        if (oldLink) {
          upsertLink(oldLink);
        } else {
          // Fallback if the link was somehow already evicted from the store.
          const oldData = oldEdge.data as { a_iface?: string; b_iface?: string } | undefined;
          upsertLink({
            id: oldEdge.id,
            project_id: projectId,
            a_iface: oldData?.a_iface ?? oldEdge.source,
            b_iface: oldData?.b_iface ?? oldEdge.target,
            type: 'copper',
            bandwidth: 1000,
            delay: 1,
            loss: 0,
            mtu: 1500,
            status: 'up',
          });
        }
      });

      // Fix #3: inherit link properties from the old link instead of copper defaults.
      const inheritedProps = oldLink
        ? { type: oldLink.type, bandwidth: oldLink.bandwidth, delay: oldLink.delay, loss: oldLink.loss, mtu: oldLink.mtu }
        : { type: 'copper' as const, bandwidth: 1000, delay: 1, loss: 0, mtu: 1500 };

      const tempId = `tmp-${newConnection.source}-${newConnection.target}-${Date.now()}`;
      upsertLink({
        id: tempId,
        project_id: projectId,
        a_iface: aIface,
        b_iface: bIface,
        ...inheritedProps,
        status: 'up',
      });
      void linksApi
        .create({ project_id: projectId, a_iface: aIface, b_iface: bIface, type: inheritedProps.type })
        .then((real) => {
          removeLink(tempId);
          upsertLink(real);
        })
        .catch(() => {
          removeLink(tempId);
        });
    },
    [nodesMap, linksMap, projectId, removeLink, upsertLink],
  );

  /* --- Drag-drop device creation from the palette -------------------------- */
  const onDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'copy';
  }, []);

  const onDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      if (!projectId || !rfRef.current) return;
      const key = e.dataTransfer.getData('application/netgeo-device');
      if (!key) return;
      const pos = rfRef.current.screenToFlowPosition({ x: e.clientX, y: e.clientY });
      placeDevice(projectId, key, pos);
    },
    [projectId],
  );

  /* --- Double-click empty canvas → quick-add at that point (design §5.2) ---- */
  const onDoubleClick = useCallback(
    (e: React.MouseEvent) => {
      // Only the empty pane, not a node/edge/control, opens the picker.
      const el = e.target as HTMLElement;
      if (!el.classList.contains('react-flow__pane') || !rfRef.current) return;
      const pos = rfRef.current.screenToFlowPosition({ x: e.clientX, y: e.clientY });
      openPicker(pos);
    },
    [openPicker],
  );

  return (
    <div className="h-full w-full" onDragOver={onDragOver} onDrop={onDrop} onDoubleClick={onDoubleClick}>
      <ReactFlow
        onInit={(inst) => {
          rfRef.current = inst;
          setRfReady(true);
          // Register the fit action so the global `F` shortcut and the command
          // palette can fit the view without holding the React Flow instance.
          setFit(() => inst.fitView({ duration: 400 }));
          // Register a "center on point" action so the event ledger can bring a
          // clicked event's device into view (design §6.4).
          setCenterOn((x, y) => inst.setCenter(x, y, { zoom: 1.2, duration: 400 }));
        }}
        nodes={rfNodes}
        edges={rfEdges}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        onNodesChange={onNodesChange}
        onNodeDragStop={onNodeDragStop}
        onNodeClick={onSelectionNode}
        onConnect={onConnect}
        onReconnect={onReconnect}
        onPaneClick={() => select({ nodeId: null, linkId: null })}
        onEdgeClick={(_e, edge) => select({ linkId: edge.id })}
        connectionMode={ConnectionMode.Loose}
        // BUG-02: links are made by DRAGGING a port to another port. Disabling
        // click-to-connect kills React Flow's pending click-connect state — the
        // one that turned every stray click after a link into a phantom edge.
        connectOnClick={false}
        // Figma-style live connection preview (dashed bezier with glow dot).
        connectionLineComponent={ConnectionLine}
        // ±30px snap radius so dragged connections snap to the nearest port (n8n feel).
        connectionRadius={30}
        // A focus request pending at mount suppresses the initial whole-graph
        // fit for this mount (see suppressInitialFit above) so it can't
        // override the focus-node setCenter (BUG-08).
        fitView={!suppressInitialFit}
        snapToGrid
        snapGrid={[16, 16]}
        minZoom={0.2}
        maxZoom={2.5}
        onlyRenderVisibleElements
        proOptions={{ hideAttribution: true }}
        defaultEdgeOptions={{ type: 'pulse', reconnectable: true }}
      >
        <Background variant={BackgroundVariant.Dots} gap={20} size={1} color="var(--ng-border)" />
        <Controls position="bottom-right" className="!border-fg/10 !bg-fg/5 backdrop-blur" />

        <Panel position="top-left" className="!m-3">
          <div className="flex flex-col gap-2">
            <OverlayChips />
            <div className="flex items-start gap-2">
              <CanvasToolbar
                nodes={rfNodes.length}
                allNodes={nodesMap}
                onLocate={locateNode}
                onAddDevice={() => openPicker()}
              />
              <button
                onClick={onAutoLayout}
                disabled={rfNodes.length === 0}
                title="Auto-layout (hierarki) — repositions every node, cannot be undone"
                aria-label="Auto-layout hierarchy"
                className="glass flex shrink-0 items-center gap-1.5 rounded-lg border border-fg/10 px-2.5 py-1.5 text-xs font-medium text-fg/70 shadow-glass hover:bg-fg/10 hover:text-fg/90 disabled:pointer-events-none disabled:opacity-40"
              >
                <Waypoints className="h-3.5 w-3.5" />
                Auto-layout
              </button>
            </div>
          </div>
        </Panel>

        {/* MiniMap only once there's something to map — no empty gray box. */}
        {rfNodes.length > 0 && (
          <MiniMap
            pannable
            zoomable
            nodeStrokeWidth={0}
            nodeBorderRadius={3}
            offsetScale={4}
            nodeColor={(n) => nodeColors[(n.data as DeviceNodeData).kind] ?? '#888'}
            style={{ width: 148, height: 100 }}
            className={cn(
              '!rounded-lg !border !border-fg/10 !bg-surface !shadow-glass backdrop-blur transition-all duration-fast',
              // Slide clear of the 360px inspector when it's open (BUG-05).
              inspectorOpen ? '!mb-3 !mt-3 !mr-[372px]' : '!m-3',
            )}
          />
        )}
      </ReactFlow>
    </div>
  );
}

/* --------------------------- Canvas toolbar ------------------------------- */
/**
 * Slim floating toolbar (UISP-style): a device "locate" search that centers the
 * viewport on a matched node, plus an add-device shortcut that opens the palette.
 * ponytail: pan/zoom/fit live in React Flow's <Controls>; we don't duplicate
 * them here, and modal select/link/measure "modes" are direct-manipulation
 * already (drag a handle to link, drag from palette to add) — no dead buttons.
 */
function CanvasToolbar({
  nodes,
  allNodes,
  onLocate,
  onAddDevice,
}: {
  nodes: number;
  allNodes: Map<string, NodeModel>;
  onLocate: (n: NodeModel) => void;
  onAddDevice: () => void;
}) {
  const [q, setQ] = useState('');
  const query = q.trim().toLowerCase();

  const matches = useMemo(() => {
    if (!query) return [];
    const out: NodeModel[] = [];
    for (const n of allNodes.values()) {
      if (n.name.toLowerCase().includes(query) || mgmtIp(n)?.toLowerCase().includes(query)) {
        out.push(n);
        if (out.length >= 6) break;
      }
    }
    return out;
  }, [allNodes, query]);

  const pick = (n: NodeModel) => {
    onLocate(n);
    setQ('');
  };

  return (
    <div className="w-64">
      <div className="glass flex items-center gap-2 rounded-lg border border-fg/10 px-2.5 py-1.5 shadow-glass">
        <Search className="h-3.5 w-3.5 shrink-0 text-fg/40" />
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && matches[0]) pick(matches[0]);
            if (e.key === 'Escape') setQ('');
          }}
          placeholder={nodes ? 'Find device or IP…' : 'No devices yet'}
          aria-label="Find device on canvas"
          className="w-full bg-transparent text-xs text-fg/90 placeholder:text-fg/35 outline-none"
        />
        {q ? (
          <button
            onClick={() => setQ('')}
            aria-label="Clear search"
            className="grid h-5 w-5 shrink-0 place-items-center rounded text-fg/40 hover:bg-fg/10 hover:text-fg/70"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        ) : (
          <button
            onClick={onAddDevice}
            aria-label="Add device"
            title="Add device"
            className="grid h-5 w-5 shrink-0 place-items-center rounded text-fg/50 hover:bg-fg/10 hover:text-fg/90"
          >
            <Plus className="h-4 w-4" />
          </button>
        )}
      </div>

      {query && (
        <ul className="glass mt-1.5 max-h-56 overflow-auto rounded-lg border border-fg/10 py-1 shadow-glass ng-scroll">
          {matches.length === 0 ? (
            <li className="px-3 py-2 text-xs text-fg/40">No match for “{q}”</li>
          ) : (
            matches.map((n) => (
              <li key={n.id}>
                <button
                  onClick={() => pick(n)}
                  className={cn(
                    'flex w-full items-center justify-between gap-2 px-3 py-1.5 text-left',
                    'hover:bg-fg/8',
                  )}
                >
                  <span className="truncate text-xs font-medium text-fg/90">{n.name}</span>
                  <span className="shrink-0 font-mono text-[10px] text-fg/45">
                    {mgmtIp(n) ?? n.kind}
                  </span>
                </button>
              </li>
            ))
          )}
        </ul>
      )}
    </div>
  );
}

/* ----------------------------- helpers ----------------------------------- */
/** First configured interface address — the device's management IP for the card. */
function mgmtIp(node: NodeModel): string | undefined {
  return node.interfaces.find((i) => i.ip.length > 0)?.ip[0];
}

/**
 * Does a node belong to any active protocol overlay? Membership is read from the
 * node's free-form `intent` bag already in the store — a substring scan is the
 * lazy, robust check (works whatever nested shape the engine writes).
 * ponytail: highlight-only heuristic; no per-node engine/diagnostics call.
 */
function nodeInOverlay(n: NodeModel, o: Record<OverlayKey, boolean>): boolean {
  if (!n.intent) return false;
  const bag = JSON.stringify(n.intent).toLowerCase();
  return (
    (o.ospf && bag.includes('ospf')) ||
    (o.bgp && bag.includes('bgp')) ||
    (o.vlan && bag.includes('vlan'))
  );
}

function memberById(nodes: Map<string, NodeModel>, id: string, o: Record<OverlayKey, boolean>): boolean {
  const n = nodes.get(id);
  return !!n && nodeInOverlay(n, o);
}

function ifaceNodeId(nodes: Map<string, NodeModel>, ifaceId: string): string {
  for (const n of nodes.values()) {
    if (n.interfaces.some((i) => i.id === ifaceId)) return n.id;
  }
  // Fallback for optimistic temp links keyed by node id directly.
  return ifaceId;
}

function firstFreeIface(node?: NodeModel): string | null {
  if (!node) return null;
  const free = node.interfaces.find((i) => i.peer_link_id == null);
  // If no modeled interfaces yet (fresh node), use node id as a stand-in so the
  // optimistic edge renders; backend resolves the real interface on create.
  return free?.id ?? node.id;
}
