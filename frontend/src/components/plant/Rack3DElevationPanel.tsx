/**
 * Rack3DElevationPanel — the physical-plant view (Desain Rackmount 2.5). The
 * only physical-plant renderer now (the old 2D elevation panel is deleted;
 * this owns rack/site creation too, moved over from that panel).
 *
 * Owns one WebGL renderer and one orthographic camera on a fixed POV
 * (permintaan Surya: az 7° / elev 4° / scale 1.40 — see POV below): no
 * perspective convergence, no user-adjustable zoom/elevation. The scene
 * graph itself lives in lib/three/rack3d; the translation from the
 * project's real topology into that scene graph's input lives in
 * lib/three/plantAdapter (NG-PH3D P1).
 *
 * ponytail: plain three.js, no react-three-fiber. The scene is imperative and
 * rebuilt wholesale on a rack/link change; wrapping it in a reconciler would
 * add a dependency and buy nothing — React owns the chrome, three owns the
 * canvas.
 *
 * rack3d.ts lays out however many real bays it's given, left to right
 * (NG-PH3D P41 — was a fixed A/B pair, see report). Which bays those are is
 * driven entirely by the site selector below: this panel shows *every* rack
 * belonging to the selected site, nothing else — zero racks in that site
 * draws zero enclosures (WorkspaceEmptyState instead), and switching site
 * swaps the whole row for that site's racks. No manual per-rack picking.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertTriangle, Cable, DoorClosed, Move, Plus, Server, Tag, Zap } from 'lucide-react';
import * as THREE from 'three';
import type { Rack } from '@/api/types';
import { deviceTypesApi, linksApi, nodesApi, physicalApi, projectsApi, type ApiError } from '@/api/client';
import { useUiStore } from '@/store/uiStore';
import { WorkspaceEmptyState } from '@/components/shell/WorkspaceEmptyState';
import { cn } from '@/lib/cn';
import { nodeWatts, overLengthCables, unplacedNodes, wattsByIconMap, wattsToBtu } from '@/lib/plant';
import { loadBootAssets } from '@/lib/three/bootAssets';
import {
  adaptTopology,
  cableLengthUpdatesForNode,
  cableMediaForVisual,
  canPlaceDevice,
  DEFAULT_ENCLOSURE,
  dropDecision,
  ENCLOSURE_KEYS,
  frustumSpan,
  racksForSite,
  resolveDropTarget,
} from '@/lib/three/plantAdapter';
import {
  applyDoors,
  applyLabels,
  applyLod,
  applySelection,
  buildScene,
  devicePortWorld,
  disposeScene,
  mediaFor,
  stockLength,
  tick,
  CPI_KEY,
  MEDIA,
  RACK_SPECS,
  U,
  type BuiltScene,
  type LinkDef,
  type RackBay,
} from '@/lib/three/rack3d';

/** Feature-detect before ever touching THREE.WebGLRenderer — a browser with
 *  WebGL disabled (flag, policy, headless-no-GPU) throws from the renderer
 *  constructor itself, which would otherwise leave a blank canvas div and no
 *  clue why (NG-PH3D P4). */
function webglAvailable(): boolean {
  try {
    const canvas = document.createElement('canvas');
    return !!(canvas.getContext('webgl2') || canvas.getContext('webgl'));
  } catch {
    return false;
  }
}

// Fixed POV (permintaan Surya): azimuth 7°, elevation 4°, scale 1.40 — no
// free camera, no user-adjustable zoom/elevation. `dist` is the camera's
// fixed physical distance from the rack; `span` (the frustum half-height
// input to spanFor()) is what "scale" means here.
const POV = { az: (7 * Math.PI) / 180, elev: (4 * Math.PI) / 180, span: 1.4, dist: 14 };
const EMPTY_BAYS: RackBay[] = [];
const EMPTY_LINKS: LinkDef[] = [];

/** Selectable rack heights (10U–48U) — moved here from the deleted 2D panel,
 *  the only place rack creation lives now. */
const RACK_SIZES = [10, 12, 18, 24, 36, 42, 48];

type Mode = 'cable' | 'adddev' | null;
type Face = 'front' | 'back';

export function Rack3DElevationPanel() {
  const projectId = useUiStore((s) => s.projectId);
  const queryClient = useQueryClient();
  const hostRef = useRef<HTMLDivElement>(null);
  const builtRef = useRef<BuiltScene | null>(null);
  const camRef = useRef<THREE.OrthographicCamera | null>(null);
  const rendererRef = useRef<THREE.WebGLRenderer | null>(null);
  // mutable view state the render loop reads every frame — deliberately not
  // React state: a 60 fps camera must not re-render the component tree.
  const view = useRef({ az: POV.az, anim: true, doors: false, labels: false, sel: null as string | null, zoomed: false, focusRack: '' });
  // Real per-bay rack height in metres, keyed by rack id, from the backend's
  // `ru_height` — NOT the enclosure mesh's height (RACK_SPECS is a fixed
  // 42U shell regardless of the real rack). Camera framing (spanFor/
  // placeCamera) reads this so devices near the bottom of a real short rack
  // aren't centred as if the rack were always 42U tall. Kept in a ref
  // (mirrors the render-loop-facing refs below) so spanFor/placeCamera stay
  // referentially stable across however many bays are shown (NG-PH3D P41).
  const rackHeightRef = useRef<Record<string, number>>({});
  // Computed once — WebGL support doesn't change mid-session. Also re-checked
  // defensively at renderer construction (below) in case detection passes
  // but the real context still fails to init.
  // Lazy initializer (`() => ...`, not a bare call): a bare `!webglAvailable()`
  // argument is re-evaluated by React on every render even though useState
  // only consumes it once, and webglAvailable() itself creates a throwaway
  // WebGL context that's never disposed — every keystroke/dropdown/rack-add
  // re-render leaked one more context until Chromium's per-page context cap
  // evicted the real renderer's own context, killing the scene to a silent
  // black canvas with no error (QA 2026-08-31's "blank frame", root-caused
  // and closed here — the POV lock alone did not fix it, this did).
  const [webglError, setWebglError] = useState(() => !webglAvailable());
  // NG-PH3D 3a/3b: the Blender-authored boot/cage assets load once per app
  // session (loadBootAssets() is idempotent/cached) and the scene rebuild
  // effect below re-runs once they resolve, swapping the procedural
  // rj45/lc/sfp/qsfp fallback shapes for the real geometry.
  const [assetsLoaded, setAssetsLoaded] = useState(false);
  useEffect(() => {
    let live = true;
    loadBootAssets()
      .then(() => { if (live) setAssetsLoaded(true); })
      .catch(() => {}); // offline/blocked fetch — stays on the procedural fallback
    return () => { live = false; };
  }, []);

  // Same query keys the old 2D panel used, kept on purpose: when this
  // panel mutates and invalidates ['topology', projectId] /
  // ['plant', projectId], this panel's cache entries are the same entries,
  // so it refetches too without any cross-panel event wiring.
  const topoQ = useQuery({
    queryKey: ['topology', projectId],
    queryFn: () => projectsApi.topology(projectId!),
    enabled: !!projectId,
  });
  // NG-PH3D P3: over-length cable warnings, same query the 2D panel already
  // fetches (same key ⇒ same cache entry ⇒ both panels agree on the verdict).
  const plantQ = useQuery({
    queryKey: ['plant', projectId],
    queryFn: () => physicalApi.plant(projectId!),
    enabled: !!projectId,
  });
  // Static catalog, one fetch, used for per-device wattage lookup (NG-PH3D P3).
  const deviceTypesQ = useQuery({
    queryKey: ['device-types'],
    queryFn: () => deviceTypesApi.list(),
    staleTime: Infinity,
  });
  const wattsByIcon = useMemo(() => wattsByIconMap(deviceTypesQ.data), [deviceTypesQ.data]);
  // N4: id -> catalog entry, for resolving each node's device_type_id to real
  // pack port data (rack faceplate rendering, not a second /device-types fetch).
  const deviceTypesById = useMemo(
    () => new Map((deviceTypesQ.data ?? []).map((dt) => [dt.id, dt])),
    [deviceTypesQ.data],
  );

  const racks = topoQ.data?.racks ?? [];
  const sites = topoQ.data?.sites ?? [];

  // Which site's racks fill the scene — every rack belonging to it, in
  // topology order, no manual per-rack picking (permintaan Surya: the
  // display is exactly "the racks that exist in this site", nothing more).
  // '' is the "(no site)" bucket, matching the create-rack dropdown below.
  const [viewSiteId, setViewSiteId] = useState('');
  useEffect(() => {
    setViewSiteId((cur) => (sites.some((s) => s.id === cur) ? cur : (sites[0]?.id ?? '')));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, sites.map((s) => s.id).join(',')]);

  const viewRackIds = useMemo(
    () => racksForSite(racks, viewSiteId || null),
    [racks, viewSiteId],
  );
  const viewRacks = useMemo(
    () => viewRackIds.map((id) => racks.find((r) => r.id === id)).filter((r): r is Rack => !!r),
    [viewRackIds, racks],
  );
  // Rack-name disambiguation for the move-device dropdown below (QA 2026-08-31
  // defect: two racks named e.g. "QA-Rack-1" were indistinguishable in that
  // list). Only racks whose name collides with another rack's get the
  // site-name suffix — an unambiguous name stays plain.
  const siteNameById = useMemo(() => new Map(sites.map((s) => [s.id, s.name])), [sites]);
  const rackLabel = useCallback((r: Rack) => {
    const dupe = racks.filter((x) => x.name === r.name).length > 1;
    if (!dupe) return r.name;
    const siteName = r.site_id ? siteNameById.get(r.site_id) : undefined;
    return `${r.name} · ${siteName ?? r.id.slice(0, 6)}`;
  }, [racks, siteNameById]);
  const adapted = useMemo(
    () => (topoQ.data ? adaptTopology(topoQ.data, viewRackIds, deviceTypesById) : null),
    [topoQ.data, viewRackIds, deviceTypesById],
  );

  // Cable Mode / Add-device / move all write straight to the backend (P2) —
  // the scene shows exactly `adapted`, nothing session-only layered on top.
  const bays = adapted?.racks ?? EMPTY_BAYS;
  const links = adapted?.links ?? EMPTY_LINKS;

  // NG-PH3D P3: shared helpers with the old 2D panel's rollups (deleted;
  // these numbers used to need to agree between the two views).
  const unplaced = useMemo(() => unplacedNodes(topoQ.data?.nodes ?? []), [topoQ.data]);
  const overLength = useMemo(
    () => overLengthCables(topoQ.data?.cables ?? [], plantQ.data?.links),
    [topoQ.data, plantQ.data],
  );
  // Power/heat for exactly the devices in the bays actually on screen — the
  // 2D panel rolls this up per site; comparing the same racks' watts in both
  // views is the apples-to-apples check (see QA doc).
  const shownWatts = useMemo(() => {
    const shown = new Set(viewRackIds);
    const nodes = (topoQ.data?.nodes ?? []).filter((n) => n.rack_id != null && shown.has(n.rack_id));
    return nodes.reduce((sum, n) => sum + nodeWatts(n, wattsByIcon), 0);
  }, [topoQ.data, viewRackIds, wattsByIcon]);

  // Create-rack controls — moved here from the deleted 2D elevation panel,
  // the only place a rack could be created before this (see slice notes).
  const [newRackName, setNewRackName] = useState('');
  const [newRackSite, setNewRackSite] = useState('');
  const [newRackU, setNewRackU] = useState(42);
  const newRackNameRef = useRef<HTMLInputElement>(null);

  const [face, setFace] = useState<Face>('front');
  const [mode, setMode] = useState<Mode>(null);
  const [doors, setDoors] = useState(false);
  const [labels, setLabels] = useState(false);
  const [anim, setAnim] = useState(true);
  const [sel, setSel] = useState<string | null>(null);
  const selNode = useMemo(
    () => (sel ? (topoQ.data?.nodes ?? []).find((n) => n.id === sel) ?? null : null),
    [sel, topoQ.data],
  );
  const [pick, setPick] = useState<{ devId: string; portIx: number } | null>(null);
  const [status, setStatus] = useState('Klik perangkat di scene');
  const [error, setError] = useState<string | null>(null);

  // Same invalidate-both idiom used throughout this panel's mutations —
  // both query keys refresh together so nothing goes stale.
  const invalidate = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: ['topology', projectId] });
    queryClient.invalidateQueries({ queryKey: ['plant', projectId] });
  }, [queryClient, projectId]);

  const createRack = useMutation({
    mutationFn: (v: { name: string; siteId: string | null; ruHeight: number }) =>
      physicalApi.createRack({ project_id: projectId!, name: v.name, site_id: v.siteId, ru_height: v.ruHeight }),
    onSuccess: (_data, v) => {
      setNewRackName('');
      setError(null);
      // Show whichever site the new rack landed in — permintaan Surya: a
      // rack created for a different site than the one on screen resets the
      // view to that (now one-rack) site instead of leaving the new rack
      // invisible off in another site's row.
      setViewSiteId(v.siteId ?? '');
      invalidate();
    },
    onError: (e) => setError((e as unknown as ApiError).message || 'Failed to create rack.'),
  });

  const updateEnclosure = useMutation({
    mutationFn: (v: { rackId: string; profile: string }) =>
      physicalApi.updateRack(v.rackId, { enclosure_profile: v.profile }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['topology', projectId] }),
    onError: () => setError('Gagal menyimpan profil enclosure.'),
  });

  // Cable Mode (NG-PH3D P2): a patched port pair becomes a real logical link
  // plus the physical cable that realizes it — same POST /links + POST
  // /cables pair a 2D panel's cable tooling would have used, just driven
  // from a 3D port pick.
  const createPatch = useMutation({
    mutationFn: async (v: { aIface: string; bIface: string; media: string; lengthM: number }) => {
      const link = await linksApi.create({
        project_id: projectId!, a_iface: v.aIface, b_iface: v.bIface, type: 'copper',
      });
      await physicalApi.createCable({
        project_id: projectId!, link_id: link.id,
        media: cableMediaForVisual(v.media), length_m: v.lengthM,
      });
    },
    onSuccess: () => { setError(null); invalidate(); },
    onError: (e) => setError((e as unknown as ApiError).message || 'Gagal menyimpan kabel.'),
  });

  // Tambah perangkat (NG-PH3D P2): create → place, mirroring
  // the create → PATCH RU pattern used throughout this panel.
  const addDevice = useMutation({
    mutationFn: async (v: { rackId: string; ruStart: number }) => {
      const n = (topoQ.data?.nodes ?? []).filter((x) => x.name.startsWith('NG-')).length;
      const created = await nodesApi.create({
        project_id: projectId!, name: `NG-${n + 1}`, kind: 'switch', x: 0, y: 0,
      });
      return nodesApi.update(created.id, { rack_id: v.rackId, ru_start: v.ruStart, ru_span: 1 });
    },
    onSuccess: () => { setError(null); invalidate(); },
    onError: (e) => setError((e as unknown as ApiError).message || 'Gagal menambah perangkat.'),
  });

  // Pindah perangkat antar-rack (permintaan Surya, P2): backend is the one
  // source of truth for "same site only" — see update_node in memory.py.
  // This mutation just relays whatever it says; it never enforces the rule
  // itself. Also covers placing a previously-unplaced node from the tray
  // (NG-PH3D P3) — same write, same validation, it just had no rack before.
  const justMovedNodeId = useRef<string | null>(null);
  const moveDevice = useMutation({
    mutationFn: (v: { nodeId: string; rackId: string; ruStart: number; ruSpan: number }) =>
      nodesApi.update(v.nodeId, { rack_id: v.rackId, ru_start: v.ruStart, ru_span: v.ruSpan }),
    onSuccess: (_data, v) => {
      setError(null);
      justMovedNodeId.current = v.nodeId; // NG-PH3D P3 §5: recompute its cables' length_m next rebuild
      invalidate();
      setStatus('Perangkat dipindahkan.');
    },
    onError: (e) => setError((e as unknown as ApiError).message || 'Gagal memindahkan perangkat.'),
  });

  // "Latest" ref (permintaan Surya, slice C: drag straight in the 3D view,
  // no more 2D elevation mode) — the pointer handlers below are attached
  // once and read this every event instead of being re-subscribed whenever
  // `bays`/`viewRacks` change, so a background refetch mid-drag can never
  // tear down the listener between pointerdown and pointerup.
  const dragCtxRef = useRef({ bays, viewRacks, moveDevice, rackLabel });
  dragCtxRef.current = { bays, viewRacks, moveDevice, rackLabel };
  const currentSelNode = useRef(selNode);
  currentSelNode.current = selNode;

  /** In-flight drag gesture (pointerdown on a placed device through
   *  pointerup/Escape) — a ref, not state: it's read every pointermove and
   *  must never trigger a re-render. `ghost` is the translucent preview box
   *  showing the candidate slot; null until the pointer has actually moved
   *  past the click threshold (so a plain click never grows one). */
  const dragRef = useRef<{
    devId: string;
    span: number;
    origin: { rackKey: string; ru: number };
    startX: number;
    startY: number;
    moved: boolean;
    ghost: THREE.Mesh | null;
    target: { rackKey: string; ru: number } | null;
  } | null>(null);

  /** Frustum half-height the shot needs — the exact-fit arithmetic lives in
   *  plantAdapter's frustumSpan() (pure, unit-tested); this just supplies
   *  the ref-derived inputs and applies the outer zoom factor. `scale`
   *  (POV.span, 1.40 — permintaan Surya) is headroom on top of the exact
   *  fit, not a divisor: it widens the frustum, it never shrinks it below
   *  what frustumSpan() says is needed to avoid cropping the rack (Slice F
   *  bug 1 — the old `/scale` shrank the shot ~29% and always clipped the
   *  top). Zoomed work-mode still deliberately closes in (0.42). */
  const spanFor = useCallback((scale: number, zoomed: boolean) => {
    const heights = Object.values(rackHeightRef.current);
    const railTopM = heights.length ? Math.max(...heights) : 42 * U;
    const host = hostRef.current;
    const aspect = host ? (host.clientWidth || 1) / (host.clientHeight || 1) : 16 / 9;
    const rowWidthM = builtRef.current?.rowWidthM ?? 0;
    const span = frustumSpan({ railTopM, rowWidthM, aspect, zoomed });
    return zoomed ? span * 0.42 : span * Math.max(1, scale);
  }, []);

  const fitCamera = useCallback(() => {
    const host = hostRef.current, cam = camRef.current;
    if (!host || !cam) return;
    const w = host.clientWidth || 1, h = host.clientHeight || 1;
    const span = spanFor(POV.span, view.current.zoomed);
    const aspect = w / h;
    cam.left = -span * aspect;
    cam.right = span * aspect;
    cam.top = span;
    cam.bottom = -span;
    cam.updateProjectionMatrix();
    // NG-PH3D 3b LOD: this ortho camera sits at a fixed physical distance
    // (POV.dist) — "zoom" only changes the frustum half-height (`span`), so
    // that's the signal to drive fine-detail visibility from, not
    // distance-to-camera (see applyLod()'s own comment).
    if (builtRef.current) applyLod(builtRef.current.registry, span);
  }, [spanFor]);

  /** Place the ortho camera on the fixed 2.5D POV. az is the only thing that
   *  turns. Works for any number of real bays (NG-PH3D P41) — the old fixed
   *  A/B version required *both* named bays to exist and silently left the
   *  camera stuck wherever it last was otherwise (Surya's QA: picking the
   *  same rack into both slots, or leaving one empty, froze the camera with
   *  no error). Framing a real row of any size instead of two named slots
   *  makes that state unrepresentable. */
  const placeCamera = useCallback((az = view.current.az) => {
    const cam = camRef.current, built = builtRef.current;
    if (!cam || !built) return;
    const keys = Object.keys(built.registry.racks).filter((k) => k !== CPI_KEY);
    if (keys.length === 0) return; // nothing real built (shouldn't happen — buildScene only runs for >=1 bay)
    const entries = keys.map((k) => built.registry.racks[k]!);
    const focusKey = view.current.zoomed && built.registry.racks[view.current.focusRack] ? view.current.focusRack : null;
    const focus = focusKey ? built.registry.racks[focusKey]! : null;
    const minX = Math.min(...entries.map((r) => r.x));
    const maxX = Math.max(...entries.map((r) => r.x));
    const cx = (focus ? focus.x : (minX + maxX) / 2) + 0.12;
    // Look-at height comes from the real backend ru_height per bay (P41,
    // see rackHeightRef), not focus.h — that's the enclosure mesh's fixed
    // 42U height regardless of the rack's actual size, which used to pin
    // the look-at near the top of a 42U shell no matter how short the real
    // rack was, burying low-RU devices off-frame.
    const heights = Object.values(rackHeightRef.current);
    const cy = (focusKey ? (rackHeightRef.current[focusKey] ?? 42 * U) : (heights.length ? Math.max(...heights) : 42 * U)) * 0.46;
    const d = POV.dist, el = POV.elev;
    cam.position.set(
      cx + Math.sin(az) * Math.cos(el) * d,
      cy + Math.sin(el) * d,
      Math.cos(az) * Math.cos(el) * d,
    );
    cam.up.set(0, 1, 0);
    cam.lookAt(new THREE.Vector3(cx, cy, 0));
  }, []);

  /* ─── renderer lifecycle: one canvas for the panel's whole life ────────── */
  useEffect(() => {
    const host = hostRef.current;
    if (!host || webglError) return;
    let renderer: THREE.WebGLRenderer;
    try {
      renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    } catch {
      setWebglError(true);
      return;
    }
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.setSize(host.clientWidth || 1, host.clientHeight || 1);
    host.appendChild(renderer.domElement);
    rendererRef.current = renderer;

    // NG-PH3D P4: dev-only escape hatch to read renderer.info (draw calls,
    // geometry/texture counts) from outside React for perf/leak verification.
    // Dead in production — `import.meta.env.DEV` is statically false there,
    // so esbuild/Vite strip this whole block from the shipped bundle.
    if (import.meta.env.DEV) {
      (window as unknown as { __ngRack3dDebug?: unknown }).__ngRack3dDebug = {
        renderer: () => rendererRef.current,
        built: () => builtRef.current,
        camera: () => camRef.current,
      };
    }

    const scene = new THREE.Scene();
    const cam = new THREE.OrthographicCamera(-1, 1, 1, -1, 0.01, 60);
    cam.name = 'iso-camera';
    camRef.current = cam;

    let raf = 0;
    let last = performance.now();
    const loop = (now: number) => {
      raf = requestAnimationFrame(loop);
      let dt = (now - last) / 1000;
      if (!Number.isFinite(dt) || dt < 0) dt = 0;
      last = now;
      const built = builtRef.current;
      if (built) tick(built.registry, Math.min(0.05, dt), view.current.anim);
      renderer.render(scene, cam);
    };
    raf = requestAnimationFrame(loop);

    const ro = new ResizeObserver(() => {
      const w = host.clientWidth || 1, h = host.clientHeight || 1;
      renderer.setSize(w, h);
      fitCamera();
      placeCamera();
    });
    ro.observe(host);

    // stash the scene so the build effect can swap roots into it
    (renderer as unknown as { _sceneRef?: THREE.Scene })._sceneRef = scene;

    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
      if (builtRef.current) disposeScene(builtRef.current);
      builtRef.current = null;
      renderer.dispose();
      renderer.domElement.remove();
      rendererRef.current = null;
      camRef.current = null;
      if (import.meta.env.DEV) {
        delete (window as unknown as { __ngRack3dDebug?: unknown }).__ngRack3dDebug;
      }
    };
  }, [fitCamera, placeCamera, webglError]);

  /* ─── (re)build the scene whenever its inputs change ───────────────────── */
  useEffect(() => {
    const renderer = rendererRef.current;
    const scene = (renderer as unknown as { _sceneRef?: THREE.Scene } | null)?._sceneRef;
    if (!scene) return;
    if (builtRef.current) disposeScene(builtRef.current);
    builtRef.current = null;
    // adapted === null means zero real racks resolved for the current site
    // (NG-PH3D P41: 0 racks -> 0 enclosures, no scene at all — not the old
    // fixed-2-bay build that always drew a DEFAULT_ENCLOSURE ghost).
    if (adapted && adapted.racks.length > 0) {
      const built = buildScene({ racks: adapted.racks, links });
      scene.add(built.root);
      builtRef.current = built;
      applyDoors(built.registry, view.current.doors);
      applyLabels(built.registry, view.current.labels, view.current.sel);
      applySelection(built.registry, view.current.sel);

      // NG-PH3D P3 §5: a device just placed/moved from this panel — its
      // cables' stored length_m predates the new geometry. Recompute only
      // the ones this rebuild can actually see (both ends in the shown
      // bays); the rest is the documented shown-bays-only gap, not a new bug.
      const movedId = justMovedNodeId.current;
      justMovedNodeId.current = null;
      if (movedId) {
        const updates = cableLengthUpdatesForNode(built.registry, adapted, topoQ.data?.cables ?? [], movedId);
        if (updates.length > 0) {
          void Promise.all(
            updates.map((u) => physicalApi.updateCable(u.cableId, { length_m: u.lengthM })),
          ).then(invalidate);
        }
      }
    }
    fitCamera();
    placeCamera();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [adapted, links, fitCamera, placeCamera, assetsLoaded]);

  /* ─── toggles: mirror React state into the scene ───────────────────────── */
  useEffect(() => {
    view.current.doors = doors;
    if (builtRef.current) applyDoors(builtRef.current.registry, doors);
  }, [doors]);

  useEffect(() => {
    // A tray-selected node (NG-PH3D P3) may not exist in the scene yet — an
    // unplaced node has no mesh to highlight. Dim nothing rather than
    // dimming every cable in the scene for a "selection" nothing touches.
    const sceneSel = sel && builtRef.current?.registry.devices[sel] ? sel : null;
    view.current.labels = labels;
    view.current.sel = sceneSel;
    if (builtRef.current) {
      applyLabels(builtRef.current.registry, labels, sceneSel);
      applySelection(builtRef.current.registry, sceneSel);
    }
  }, [labels, sel]);

  useEffect(() => {
    view.current.anim = anim;
  }, [anim]);

  /* ─── real rack heights: keep the camera-framing ref in sync ───────────── */
  useEffect(() => {
    const map: Record<string, number> = {};
    for (const r of viewRacks) map[r.id] = (r.ru_height || 42) * U;
    rackHeightRef.current = map;
    fitCamera();
    placeCamera();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [viewRacks.map((r) => `${r.id}:${r.ru_height}`).join(','), fitCamera, placeCamera]);

  /* ─── face flip: az is the only thing about the fixed POV that turns ──── */
  useEffect(() => {
    view.current.az = face === 'back' ? POV.az + Math.PI : POV.az;
    fitCamera();
    placeCamera();
  }, [face, fitCamera, placeCamera]);

  /* ─── work zoom: only for cabling / adding, only on the worked rack ────── */
  useEffect(() => {
    const on = mode !== null;
    if (view.current.zoomed === on) return;
    const from = spanFor(POV.span, view.current.zoomed);
    view.current.zoomed = on;
    view.current.focusRack = (sel && builtRef.current?.registry.devices[sel]?.rackKey) || bays[0]?.key || '';
    const to = spanFor(POV.span, on);
    const t0 = performance.now();
    const step = () => {
      const cam = camRef.current, host = hostRef.current;
      if (!cam || !host) return;
      const k = Math.min(1, (performance.now() - t0) / 620);
      const e = 1 - Math.pow(1 - k, 3);
      const span = from + (to - from) * e;
      const aspect = (host.clientWidth || 1) / (host.clientHeight || 1);
      cam.left = -span * aspect;
      cam.right = span * aspect;
      cam.top = span;
      cam.bottom = -span;
      cam.updateProjectionMatrix();
      placeCamera();
      if (k < 1) requestAnimationFrame(step);
    };
    step();
  }, [mode, sel, spanFor, placeCamera]);

  /* ─── picking ──────────────────────────────────────────────────────────── */
  const selectDevice = useCallback((id: string | null) => {
    setSel(id);
    if (!id) {
      setStatus('Klik perangkat di scene');
      return;
    }
    const built = builtRef.current;
    const d = built?.registry.devices[id];
    if (!d) return;
    const cables = built!.registry.cables.filter((c) => c.meta.devs.includes(id));
    const media = [...new Set(cables.map((c) => MEDIA[c.mediaKey]!.label.split(' · ')[0]))];
    setStatus(`${d.def.brand} ${d.def.model} · U${d.def.u} · ${d.def.h}U · rack ${d.rackKey} · ${cables.length} kabel${media.length ? ' · ' + media.join(', ') : ''}`);
  }, []);

  /** Tray click (NG-PH3D P3): select an unplaced node — it has no mesh yet,
   *  so just arm the "Pindahkan" bar below with it instead of routing
   *  through `selectDevice`'s scene-registry lookup. */
  const selectFromTray = useCallback((id: string, name: string) => {
    setSel(id);
    setStatus(`Pilih rak + RU untuk ${name}`);
  }, []);

  /** Cable Mode: first click arms a port, second click patches the pair and
   *  saves it (NG-PH3D P2: POST /links + POST /cables). */
  const handlePortPick = useCallback((devId: string, portIx: number) => {
    const built = builtRef.current;
    if (!built) return;
    if (!pick) {
      setPick({ devId, portIx });
      const d = built.registry.devices[devId]!;
      setStatus(`Port A: ${d.def.brand} ${d.def.model} :${portIx} — pilih port B`);
      return;
    }
    const a = pick;
    setPick(null);
    if (a.devId === devId && a.portIx === portIx) {
      setStatus('Cable Mode: pilih dua port');
      return;
    }
    const aIface = adapted?.ifaceByDevPort.get(`${a.devId}:${a.portIx}`);
    const bIface = adapted?.ifaceByDevPort.get(`${devId}:${portIx}`);
    if (!aIface || !bIface) {
      setStatus('Port ini tidak punya interface nyata untuk dikabel');
      return;
    }
    const m = mediaFor(built.registry.devices[a.devId]!.def, built.registry.devices[devId]!.def);
    // ponytail: real routed-curve length needs buildScene's own lane state,
    // which is private to that closure; the straight-line port-to-port
    // distance is a reasonable stand-in, then rounded to a stock length the
    // same way the display already does — upgrade to the true curve length
    // if the estimate turns out to matter for over-length verdicts.
    const pa = devicePortWorld(built.registry, a.devId, a.portIx);
    const pb = devicePortWorld(built.registry, devId, portIx);
    const lengthM = stockLength(pa && pb ? pa.distanceTo(pb) : 1);
    setStatus(`Menyimpan kabel ${MEDIA[m]!.label.split(' · ')[0]}…`);
    createPatch.mutate({ aIface, bIface, media: m, lengthM });
  }, [pick, adapted, createPatch]);

  /** Add device: click a free U on a rack, get a 1U switch there
   *  (NG-PH3D P2: POST /nodes + PATCH placement). `rackKey` is the real
   *  backend rack id — every shown bay is a real rack now, so unlike the
   *  old A/B slots there's no "empty bay" case left to guard against. */
  const handleAddDevice = useCallback((rackKey: string, y: number) => {
    const built = builtRef.current;
    const rack = built?.registry.racks[rackKey];
    const bay = bays.find((b) => b.key === rackKey);
    if (!rack || !bay) return;
    const u = Math.floor((y - 0.055) / U) + 1;
    if (u < 1 || u > rack.spec.u) {
      setStatus('Di luar rail — pilih U yang kosong');
      return;
    }
    if (bay.devices.some((d) => u >= d.u && u < d.u + d.h)) {
      setStatus(`U${u} sudah terisi — pilih U yang kosong`);
      return;
    }
    setStatus(`Menambahkan perangkat di U${u}…`);
    addDevice.mutate({ rackId: rackKey, ruStart: u });
  }, [bays, addDevice]);

  useEffect(() => {
    const renderer = rendererRef.current;
    const scene = (renderer as unknown as { _sceneRef?: THREE.Scene } | null)?._sceneRef;
    const canvas = renderer?.domElement;
    const cam = camRef.current;
    if (!canvas || !cam || !scene) return;
    const ray = new THREE.Raycaster();
    // NG-PH3D 3b: an instanced SFP/QSFP cage carries no per-port mesh (one
    // InstancedMesh for every cage in the scene) — its dev/port map rides
    // on the InstancedMesh itself, indexed by the raycast hit's
    // `instanceId`, the same information a per-part mesh would have carried
    // directly in `userData`.
    const hitPort = (h: THREE.Intersection): { dev: string; port: number } | null => {
      const o = h.object;
      if (o.userData?.port !== undefined) return { dev: o.userData.dev as string, port: o.userData.port as number };
      if (o instanceof THREE.InstancedMesh && h.instanceId != null) {
        const map = o.userData.portMap as { dev: string; port: number }[] | undefined;
        return map?.[h.instanceId] ?? null;
      }
      return null;
    };
    const hitDevice = (hits: THREE.Intersection[]) => {
      const hit = hits.find((h) => h.object.userData?.dev !== undefined || hitPort(h) != null);
      return hit ? ((hit.object.userData?.dev as string | undefined) ?? hitPort(hit)?.dev ?? null) : null;
    };
    const raycastFromEvent = (e: PointerEvent) => {
      const r = canvas.getBoundingClientRect();
      const ndc = new THREE.Vector2(
        ((e.clientX - r.left) / r.width) * 2 - 1,
        -((e.clientY - r.top) / r.height) * 2 + 1,
      );
      ray.setFromCamera(ndc, cam);
      return ray.intersectObjects([builtRef.current!.root], true);
    };

    /** Drop the ghost preview mesh, if the current gesture ever grew one
     *  (a plain click never does — see the `moved` threshold below). */
    const clearGhost = () => {
      const g = dragRef.current?.ghost;
      if (!g) return;
      scene.remove(g);
      g.geometry.dispose();
      (g.material as THREE.Material).dispose();
    };

    /** RU-move drag (permintaan Surya, slice C): pointerdown on a placed
     *  device arms a candidate; only real pointer movement past a small
     *  threshold turns it into a drag (so a plain click still just
     *  selects). Cable Mode / Tambah perangkat own the pointer instead when
     *  `mode !== null`, so this never starts there. */
    const onDown = (e: PointerEvent) => {
      if (mode !== null || dragCtxRef.current.moveDevice.isPending) return;
      const built = builtRef.current;
      if (!built) return;
      const devId = hitDevice(raycastFromEvent(e));
      if (!devId) return;
      const entry = built.registry.devices[devId];
      if (!entry) return;
      dragRef.current = {
        devId, span: entry.def.h, origin: { rackKey: entry.rackKey, ru: entry.def.u },
        startX: e.clientX, startY: e.clientY, moved: false, ghost: null, target: null,
      };
    };

    const onMove = (e: PointerEvent) => {
      const cand = dragRef.current;
      const built = builtRef.current;
      if (!cand || !built) return;
      if (!cand.moved) {
        if (Math.hypot(e.clientX - cand.startX, e.clientY - cand.startY) < 4) return;
        cand.moved = true;
        const ghost = new THREE.Mesh(
          new THREE.BoxGeometry(0.46, cand.span * U - 0.0015, 0.02),
          new THREE.MeshBasicMaterial({ color: 0x22c55e, transparent: true, opacity: 0.55, depthTest: false }),
        );
        ghost.visible = false;
        ghost.renderOrder = 999;
        scene.add(ghost);
        cand.ghost = ghost;
      }
      const pt = raycastFromEvent(e)[0]?.point;
      const rackXs = Object.entries(built.registry.racks)
        .filter(([k]) => k !== CPI_KEY)
        .map(([key, v]) => ({ key, x: v.x }));
      const target = pt ? resolveDropTarget(rackXs, { x: pt.x, y: pt.y }) : null;
      cand.target = target;
      const ghost = cand.ghost!;
      if (!target) {
        ghost.visible = false;
        setStatus('Lepas di dalam rak untuk memindahkan, Esc untuk batal');
        return;
      }
      const { bays: curBays, viewRacks: curRacks, rackLabel: curLabel } = dragCtxRef.current;
      const rackObj = curRacks.find((r) => r.id === target.rackKey);
      const ruHeight = rackObj?.ru_height ?? 42;
      const valid = canPlaceDevice(curBays, target.rackKey, target.ru, cand.span, ruHeight, cand.devId);
      const rackEntry = built.registry.racks[target.rackKey]!;
      ghost.position.set(
        rackEntry.x,
        0.055 + (target.ru - 1) * U + (cand.span * U) / 2,
        rackEntry.d / 2 - 0.088 + 0.012,
      );
      (ghost.material as THREE.MeshBasicMaterial).color.setHex(valid ? 0x22c55e : 0xef4444);
      ghost.visible = true;
      const lo = target.ru, hi = target.ru + cand.span - 1;
      setStatus(
        `${valid ? 'Lepas' : 'Tidak muat/terisi'} — ${rackObj ? curLabel(rackObj) : target.rackKey} U${lo}${hi > lo ? `-${hi}` : ''}`,
      );
    };

    /** Cancel an in-flight drag with zero requests sent — used for Escape
     *  and a pointercancel (e.g. a touch gesture interrupted mid-drag). */
    const cancelDrag = () => {
      if (!dragRef.current?.moved) { dragRef.current = null; return; }
      clearGhost();
      dragRef.current = null;
      setStatus('Dibatalkan — tidak ada perubahan dikirim');
    };

    const onUp = (e: PointerEvent) => {
      const cand = dragRef.current;
      if (cand?.moved) {
        clearGhost();
        dragRef.current = null;
        const { bays: curBays, viewRacks: curRacks, moveDevice: curMove } = dragCtxRef.current;
        const ruHeightByRack = Object.fromEntries(curRacks.map((r) => [r.id, r.ru_height || 42]));
        const decision = dropDecision(cand.target, cand.span, ruHeightByRack, curBays, cand.origin, cand.devId);
        if (decision.commit) {
          curMove.mutate({ nodeId: cand.devId, rackId: decision.rackId, ruStart: decision.ruStart, ruSpan: cand.span });
        } else {
          setStatus('Dibatalkan — tidak ada perubahan dikirim');
        }
        return;
      }
      dragRef.current = null;

      const built = builtRef.current;
      if (!built) return;
      const hits = raycastFromEvent(e);
      if (mode === 'cable') {
        const port = hits.map(hitPort).find((p) => p != null);
        if (port) {
          handlePortPick(port.dev, port.port);
          return;
        }
        setStatus('Cable Mode: klik port, bukan chassis');
        return;
      }
      if (mode === 'adddev') {
        const any = hits[0];
        if (!any) return;
        let best: string | null = null, bd = Infinity;
        for (const [k, rk] of Object.entries(built.registry.racks)) {
          if (k === CPI_KEY) continue; // decorative prop, not a real rack — never a valid add-device target
          const dx = Math.abs(any.point.x - rk.x);
          if (dx < bd) { bd = dx; best = k; }
        }
        if (best && bd < 0.45) handleAddDevice(best, any.point.y);
        return;
      }
      const devId = hitDevice(hits);
      if (devId) { selectDevice(devId); return; }

      // No device under the click — an unplaced tray selection (no mesh of
      // its own to drag) places on a plain click instead (NG-PH3D P3 flow,
      // now click-to-place since the rack/RU dropdown it used is gone).
      if (currentSelNode.current && currentSelNode.current.rack_id == null && hits[0]) {
        const rackXs = Object.entries(built.registry.racks)
          .filter(([k]) => k !== CPI_KEY)
          .map(([key, v]) => ({ key, x: v.x }));
        const target = resolveDropTarget(rackXs, { x: hits[0].point.x, y: hits[0].point.y });
        const { bays: curBays, viewRacks: curRacks, moveDevice: curMove } = dragCtxRef.current;
        const rackObj = target ? curRacks.find((r) => r.id === target.rackKey) : undefined;
        const span = currentSelNode.current.ru_span ?? 1;
        if (target && rackObj && canPlaceDevice(curBays, target.rackKey, target.ru, span, rackObj.ru_height ?? 42)) {
          setStatus(`Menempatkan ${currentSelNode.current.name} di U${target.ru}…`);
          curMove.mutate({ nodeId: currentSelNode.current.id, rackId: target.rackKey, ruStart: target.ru, ruSpan: span });
          return;
        }
        setStatus(target ? 'U ini terisi/di luar rak — pilih U lain' : 'Klik di dalam rak untuk menempatkan');
        return;
      }
      selectDevice(null);
    };

    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') cancelDrag();
    };
    const onCancel = () => cancelDrag();

    canvas.addEventListener('pointerdown', onDown);
    canvas.addEventListener('pointermove', onMove);
    canvas.addEventListener('pointerup', onUp);
    canvas.addEventListener('pointercancel', onCancel);
    window.addEventListener('keydown', onKeyDown);
    return () => {
      canvas.removeEventListener('pointerdown', onDown);
      canvas.removeEventListener('pointermove', onMove);
      canvas.removeEventListener('pointerup', onUp);
      canvas.removeEventListener('pointercancel', onCancel);
      window.removeEventListener('keydown', onKeyDown);
      clearGhost();
    };
  }, [mode, handlePortPick, handleAddDevice, selectDevice]);

  const toggleMode = (id: Exclude<Mode, null>) => {
    setPick(null);
    setMode((prev) => {
      const on = prev !== id;
      setStatus(on
        ? id === 'cable' ? 'Cable Mode: pilih dua port' : 'Tambah perangkat: pilih U yang kosong'
        : 'Klik perangkat di scene');
      return on ? id : null;
    });
  };

  const deviceCount = bays.reduce((n, b) => n + b.devices.length, 0);
  const btn = (on: boolean) =>
    `flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs transition ${
      on ? 'bg-accent/20 text-accent ring-1 ring-accent/40' : 'text-recess hover:bg-fg/5 hover:text-fg'
    }`;

  /** Per-rack enclosure-profile picker, one per bay actually shown — replaces
   *  the old fixed two-slot A/B picker (NG-PH3D P41: which racks are shown
   *  is automatic now, driven by the site selector, not manually chosen
   *  here; this control only still lets you change *how a shown rack looks*). */
  const rackChip = (rack: Rack) => (
    <label key={rack.id} className="flex min-w-0 items-center gap-1.5 text-xs text-recess">
      <span className="max-w-[7rem] truncate text-fg" title={rack.name}>{rack.name}</span>
      <select
        value={rack.enclosure_profile ?? DEFAULT_ENCLOSURE}
        onChange={(e) => updateEnclosure.mutate({ rackId: rack.id, profile: e.target.value })}
        className="w-28 min-w-0 truncate rounded-md border border-fg/10 bg-transparent px-1.5 py-1 text-xs text-fg outline-none focus:border-accent/50"
        title="Profil enclosure"
      >
        {ENCLOSURE_KEYS.filter((k) => k !== 'cpi' || rack.enclosure_profile === 'cpi').map((k) => (
          <option key={k} value={k}>{RACK_SPECS[k]!.label.replace(/ \d+U.*/, '')}</option>
        ))}
      </select>
    </label>
  );

  // NG-PH3D P4: no WebGL, no scene — a black/blank canvas here would look
  // like a crash. Say so plainly (there is no 2D fallback panel anymore).
  if (webglError) {
    return (
      <div className="absolute inset-0 flex flex-col">
        <div className="relative min-h-0 flex-1">
          <WorkspaceEmptyState
            icon={AlertTriangle}
            title="3D view unavailable"
            hint="This browser doesn't support WebGL, which the physical plant view requires. Try a different browser or enable hardware acceleration."
          />
        </div>
      </div>
    );
  }

  return (
    <div className="absolute inset-0 flex flex-col">
      {/* toolbar */}
      <div className="flex flex-wrap items-center gap-2 border-b border-fg/10 px-3 py-2">
        {/* Create site / rack — moved here from the deleted 2D elevation
            panel, the only place these existed before. */}
        <button
          type="button"
          onClick={() => {
            useUiStore.getState().setViewMode('map');
            // Dynamic: mapStore only otherwise loads inside the async map/rf
            // chunks. A static import here would pull it (and its MapLibre
            // neighbours) into the entry bundle just for this one call.
            void import('@/store/mapStore').then(({ useMapStore }) => useMapStore.getState().setTool('site'));
          }}
          title="Opens the map — click a point to place and name the new site"
          className={btn(false)}
        >
          <Plus className="size-3.5" /> Site
        </button>
        <input
          ref={newRackNameRef}
          value={newRackName}
          onChange={(e) => setNewRackName(e.target.value)}
          placeholder="New rack name"
          aria-label="New rack name"
          className="w-28 min-w-0 rounded-md border border-fg/10 bg-transparent px-1.5 py-1 text-xs text-fg outline-none placeholder:text-fg/30 focus:border-accent/50"
        />
        <select
          aria-label="New rack site"
          value={newRackSite}
          onChange={(e) => setNewRackSite(e.target.value)}
          className="w-24 min-w-0 truncate rounded-md border border-fg/10 bg-transparent px-1.5 py-1 text-xs text-fg outline-none focus:border-accent/50"
        >
          <option value="">(no site)</option>
          {sites.map((s) => (
            <option key={s.id} value={s.id}>{s.name}</option>
          ))}
        </select>
        <select
          aria-label="Rack height"
          value={String(newRackU)}
          onChange={(e) => setNewRackU(Number(e.target.value))}
          className="w-16 rounded-md border border-fg/10 bg-transparent px-1.5 py-1 text-xs text-fg outline-none focus:border-accent/50"
        >
          {RACK_SIZES.map((u) => (
            <option key={u} value={u}>{u}U</option>
          ))}
        </select>
        <button
          type="button"
          onClick={() => {
            const name = newRackName.trim();
            if (!name) {
              setError('Enter a rack name to create a rack.');
              newRackNameRef.current?.focus();
              return;
            }
            createRack.mutate({ name, siteId: newRackSite || null, ruHeight: newRackU });
          }}
          disabled={createRack.isPending}
          className={cn(btn(false), 'disabled:cursor-not-allowed disabled:opacity-40')}
        >
          <Plus className="size-3.5" /> Rack
        </button>
        <div className="mx-1 h-5 w-px bg-fg/10" />
        {/* Site being viewed — every rack in it renders, no manual per-rack
            picking (permintaan Surya). Switching this is what "resets to 0"
            and then shows only the new site's racks. */}
        <label className="flex min-w-0 items-center gap-1.5 text-xs text-recess">
          Site
          <select
            aria-label="Site ditampilkan"
            value={viewSiteId}
            onChange={(e) => setViewSiteId(e.target.value)}
            className="w-28 min-w-0 truncate rounded-md border border-fg/10 bg-transparent px-1.5 py-1 text-xs text-fg outline-none focus:border-accent/50"
          >
            <option value="">(no site)</option>
            {sites.map((s) => (
              <option key={s.id} value={s.id}>{s.name}</option>
            ))}
          </select>
        </label>
        <div className="mx-1 h-5 w-px bg-fg/10" />
        <button type="button" className={btn(face === 'front')} onClick={() => setFace('front')}>Depan</button>
        <button type="button" className={btn(face === 'back')} onClick={() => setFace('back')}>Belakang</button>
        <div className="mx-1 h-5 w-px bg-fg/10" />
        <button type="button" className={btn(doors)} onClick={() => setDoors((v) => !v)} title="Tutup pintu mesh depan">
          <DoorClosed className="size-3.5" /> Pintu
        </button>
        <button type="button" className={btn(labels)} onClick={() => setLabels((v) => !v)}>
          <Tag className="size-3.5" /> Label
        </button>
        <button type="button" className={btn(anim)} onClick={() => setAnim((v) => !v)}>
          <Zap className="size-3.5" /> Animasi
        </button>
        <div className="mx-1 h-5 w-px bg-fg/10" />
        <button type="button" className={btn(mode === 'cable')} onClick={() => toggleMode('cable')} title="Buat kabel patch nyata">
          <Cable className="size-3.5" /> Cable Mode
        </button>
        <button type="button" className={btn(mode === 'adddev')} onClick={() => toggleMode('adddev')} title="Tambah perangkat ke rak">
          <Plus className="size-3.5" /> Tambah perangkat
        </button>
      </div>

      {/* Per-rack enclosure profile, one chip per rack actually shown —
          replaces the old fixed two-slot A/B picker row. */}
      {viewRacks.length > 0 && (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-fg/10 px-3 py-1.5">
          {viewRacks.map((r) => rackChip(r))}
        </div>
      )}

      {error && (
        <div className="flex items-center gap-1.5 border-b border-fg/10 bg-red-500/10 px-3 py-1.5 text-xs text-red-400">
          <AlertTriangle size={13} /> {error}
        </div>
      )}
      {topoQ.isError && (
        <div className="flex items-center gap-1.5 border-b border-fg/10 bg-red-500/10 px-3 py-1.5 text-xs text-red-400">
          <AlertTriangle size={13} /> Gagal memuat topologi project.
        </div>
      )}

      {/* NG-PH3D P3: over-length banner (GET /plant, over_length flag). */}
      {overLength.length > 0 && (
        <div className="border-b border-amber-500/20 bg-amber-500/10 px-3 py-1.5 text-xs text-amber-300">
          <div className="flex items-center gap-1 font-medium">
            <AlertTriangle size={13} /> Cable exceeds maximum length (link errored)
          </div>
          <ul className="mt-1 space-y-0.5 pl-5">
            {overLength.map(({ cable, media }) => (
              <li key={cable.id} className="list-disc text-amber-200/80">
                {cable.label || cable.id.slice(0, 6)} — {media} @ {cable.length_m} m
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Unplaced tray (NG-PH3D P3): a node with no rack, or a rack but no RU
          start, has nothing to click on in the scene — list it here instead
          of losing it silently. Clicking one arms the "Pindahkan" bar below,
          the exact same place-a-device path P2 already shipped. */}
      {unplaced.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5 border-b border-fg/10 px-3 py-1.5 text-xs">
          <span className="text-recess">Unplaced ({unplaced.length})</span>
          {unplaced.map((n) => (
            <button
              key={n.id}
              type="button"
              onClick={() => selectFromTray(n.id, n.name)}
              className={cn(
                'rounded px-2 py-0.5 text-[11px]',
                sel === n.id
                  ? 'bg-accent/20 text-accent ring-1 ring-accent/40'
                  : 'bg-fg/10 text-fg/70 hover:bg-fg/20',
              )}
            >
              {n.name}
            </button>
          ))}
        </div>
      )}

      {/* Perangkat terpilih (permintaan Surya, slice C): tidak ada lagi
          dropdown rak + input U + tombol — pindah RU/rak sekarang langsung
          drag di scene (pointerdown+drag pada perangkat, lepas di slot
          tujuan; Esc atau lepas di luar rak = batal, nol request terkirim).
          Node dari tray Unplaced (tanpa mesh, tak bisa di-drag) tetap pakai
          klik-untuk-tempatkan, sama seperti sebelumnya. */}
      {selNode && (
        <div className="flex flex-wrap items-center gap-2 border-b border-fg/10 px-3 py-1.5 text-xs">
          <Move className="size-3.5 text-recess" />
          <span className="text-fg">{selNode.name}</span>
          <span className="text-recess">
            {selNode.rack_id
              ? 'Seret perangkat di scene untuk memindahkannya'
              : 'Klik U kosong di sebuah rak untuk menempatkannya'}
          </span>
        </div>
      )}

      {/* canvas */}
      <div ref={hostRef} className="relative min-h-0 flex-1">
        {topoQ.isSuccess && viewRackIds.length === 0 && (
          <WorkspaceEmptyState
            icon={Server}
            title="No racks yet"
            hint={racks.length > 0
              ? 'This site has no racks yet — create one above, or switch Site to see another one’s racks.'
              : 'Create a rack using the toolbar above — placed devices render as RU-accurate 3D blocks.'}
          />
        )}
      </div>

      {/* status bar + legend */}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-fg/10 px-3 py-1.5 text-[11px] text-recess">
        <span className="text-fg">{status}</span>
        {/* NG-PH3D P3: watts/BTU for exactly the two racks shown — same
            nodeWatts()/wattsToBtu() the 2D panel's per-site rollup uses. */}
        <span className="flex items-center gap-1 text-amber-300/80">
          <Zap size={12} /> {shownWatts} W · {wattsToBtu(shownWatts)} BTU/hr
        </span>
        <span className="ml-auto">{deviceCount} perangkat · {links.length} kabel</span>
        <div className="flex flex-wrap items-center gap-2">
          {[...new Set(links.map((l) => l.m))].map((k) => (
            <span key={k} className="flex items-center gap-1" title={MEDIA[k]?.label}>
              <span
                className="inline-block size-2 rounded-full"
                style={{ background: '#' + (MEDIA[k]?.jacket ?? 0).toString(16).padStart(6, '0') }}
              />
              {MEDIA[k]?.label.split(' · ')[0]}
            </span>
          ))}
        </div>
      </div>
    </div>
  );
}
