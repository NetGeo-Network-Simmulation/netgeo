/**
 * Rack3DElevationPanel — the 2.5D physical-plant view (Desain Rackmount 2.5).
 *
 * Owns one WebGL renderer and one orthographic camera on a fixed isometric
 * POV: no perspective convergence, so the rack reads like the elevation view
 * it replaces. The scene graph itself lives in lib/three/rack3d; the
 * translation from the project's real topology into that scene graph's input
 * lives in lib/three/plantAdapter (NG-PH3D P1).
 *
 * ponytail: plain three.js, no react-three-fiber. The scene is imperative and
 * rebuilt wholesale on a rack/link change; wrapping it in a reconciler would
 * add a dependency and buy nothing — React owns the chrome, three owns the
 * canvas.
 *
 * rack3d.ts only lays out two rack bays side by side (registry keys 'A'/'B').
 * This panel now fills those two bays with the project's *real* racks
 * (chosen from a dropdown) instead of a fixed sample fit-out; a project with
 * more than two racks can view any pair, not all of them at once — full
 * N-rack layout is bigger than a data-binding slice (see docs/design/22).
 */
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertTriangle, Cable, DoorClosed, Move, Plus, RotateCcw, Server, Tag, Zap } from 'lucide-react';
import * as THREE from 'three';
import { deviceTypesApi, linksApi, nodesApi, physicalApi, projectsApi, type ApiError } from '@/api/client';
import { useUiStore } from '@/store/uiStore';
import { WorkspaceEmptyState } from '@/components/shell/WorkspaceEmptyState';
import { cn } from '@/lib/cn';
import { nodeWatts, overLengthCables, unplacedNodes, wattsByIconMap, wattsToBtu } from '@/lib/plant';
import {
  adaptTopology,
  cableLengthUpdatesForNode,
  cableMediaForVisual,
  DEFAULT_ENCLOSURE,
  ENCLOSURE_KEYS,
} from '@/lib/three/plantAdapter';
import {
  applyDoors,
  applyLabels,
  applySelection,
  buildScene,
  devicePortWorld,
  disposeScene,
  mediaFor,
  stockLength,
  tick,
  MEDIA,
  RACK_SPECS,
  U,
  type BuiltScene,
  type DeviceDef,
  type LinkDef,
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

const POV_KEY = 'netgeo.rack3d.pov.v2';
const POV = { az: (10 * Math.PI) / 180, elev: (10 * Math.PI) / 180, span: 0.75, dist: 14 };
const EMPTY_FITOUT: Record<string, DeviceDef[]> = { A: [], B: [] };
const EMPTY_LINKS: LinkDef[] = [];

type Mode = 'cable' | 'adddev' | null;
type Face = 'front' | 'back';
type Slot = 'A' | 'B';

interface Pov {
  baseAz: number;
  elev: number;
  span: number;
}

function loadPov(): Pov {
  try {
    const raw = localStorage.getItem(POV_KEY);
    const saved = raw ? JSON.parse(raw) : null;
    if (saved && Number.isFinite(saved.baseAz ?? saved.az)) {
      return {
        baseAz: saved.baseAz ?? saved.az,
        elev: Number.isFinite(saved.elev) ? saved.elev : POV.elev,
        span: Number.isFinite(saved.span) ? saved.span : POV.span,
      };
    }
  } catch {
    /* first run, or storage blocked */
  }
  return { baseAz: POV.az, elev: POV.elev, span: POV.span };
}

export function Rack3DElevationPanel({ viewSwitcher }: { viewSwitcher?: ReactNode } = {}) {
  const projectId = useUiStore((s) => s.projectId);
  const queryClient = useQueryClient();
  const hostRef = useRef<HTMLDivElement>(null);
  const builtRef = useRef<BuiltScene | null>(null);
  const camRef = useRef<THREE.OrthographicCamera | null>(null);
  const rendererRef = useRef<THREE.WebGLRenderer | null>(null);
  // mutable view state the render loop reads every frame — deliberately not
  // React state: a 60 fps camera must not re-render the component tree.
  const view = useRef({ az: POV.az, anim: true, doors: false, labels: false, sel: null as string | null, zoomed: false, focusRack: 'A' });
  const povRef = useRef<Pov>(loadPov());
  // Computed once — WebGL support doesn't change mid-session. Also re-checked
  // defensively at renderer construction (below) in case detection passes
  // but the real context still fails to init.
  const [webglError, setWebglError] = useState(!webglAvailable());

  // Same query keys as RackElevationPanel.tsx (the 2D panel), on purpose:
  // when the 2D panel mutates and invalidates ['topology', projectId] /
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
  // Static catalog, same key/staleTime as RackElevationPanel.tsx — one fetch,
  // shared cache entry, used for the same per-device wattage lookup (NG-PH3D P3).
  const deviceTypesQ = useQuery({
    queryKey: ['device-types'],
    queryFn: () => deviceTypesApi.list(),
    staleTime: Infinity,
  });
  const wattsByIcon = useMemo(() => wattsByIconMap(deviceTypesQ.data), [deviceTypesQ.data]);

  const racks = topoQ.data?.racks ?? [];

  // Which two real racks fill the scene's two bays.
  const [rackAId, setRackAId] = useState<string | null>(null);
  const [rackBId, setRackBId] = useState<string | null>(null);
  useEffect(() => {
    if (racks.length === 0) {
      setRackAId(null);
      setRackBId(null);
      return;
    }
    setRackAId((cur) => (cur && racks.some((r) => r.id === cur) ? cur : (racks[0]?.id ?? null)));
    setRackBId((cur) => (cur && racks.some((r) => r.id === cur) ? cur : (racks[1]?.id ?? null)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, racks.map((r) => r.id).join(',')]);

  const rackA = racks.find((r) => r.id === rackAId) ?? null;
  const rackB = racks.find((r) => r.id === rackBId) ?? null;
  const adapted = useMemo(
    () => (topoQ.data ? adaptTopology(topoQ.data, rackAId, rackBId) : null),
    [topoQ.data, rackAId, rackBId],
  );

  // Cable Mode / Add-device / move all write straight to the backend (P2) —
  // the scene shows exactly `adapted`, nothing session-only layered on top.
  const fitout = adapted?.fitout ?? EMPTY_FITOUT;
  const links = adapted?.links ?? EMPTY_LINKS;
  const rackASpec = adapted?.rackA ?? DEFAULT_ENCLOSURE;
  const rackBSpec = adapted?.rackB ?? DEFAULT_ENCLOSURE;

  // NG-PH3D P3: parity with RackElevationPanel.tsx, same shared helpers so
  // the two views can't disagree on what these numbers mean.
  const unplaced = useMemo(() => unplacedNodes(topoQ.data?.nodes ?? []), [topoQ.data]);
  const overLength = useMemo(
    () => overLengthCables(topoQ.data?.cables ?? [], plantQ.data?.links),
    [topoQ.data, plantQ.data],
  );
  // Power/heat for exactly the devices in the two bays actually on screen —
  // the 2D panel rolls this up per site; comparing the same two racks' watts
  // in both views is the apples-to-apples check (see QA doc).
  const shownWatts = useMemo(() => {
    const nodes = (topoQ.data?.nodes ?? []).filter(
      (n) => n.rack_id === rackAId || n.rack_id === rackBId,
    );
    return nodes.reduce((sum, n) => sum + nodeWatts(n, wattsByIcon), 0);
  }, [topoQ.data, rackAId, rackBId, wattsByIcon]);

  const [pov, setPov] = useState<Pov>(povRef.current);
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
  const [moveRackId, setMoveRackId] = useState('');
  const [moveRu, setMoveRu] = useState(1);
  useEffect(() => {
    setMoveRackId(selNode?.rack_id ?? '');
    setMoveRu(selNode?.ru_start ?? 1);
    // Only re-seed on an actual selection/placement change, not every
    // background refetch of an unrelated node (would clobber a pending edit).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sel, selNode?.rack_id, selNode?.ru_start]);
  const [pick, setPick] = useState<{ devId: string; portIx: number } | null>(null);
  const [status, setStatus] = useState('Klik perangkat di scene');
  const [error, setError] = useState<string | null>(null);

  // Same invalidate-both idiom RackElevationPanel.tsx uses after its own
  // mutations — the 2D and 3D panels read the same two query keys, so either
  // one invalidating them refreshes both.
  const invalidate = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: ['topology', projectId] });
    queryClient.invalidateQueries({ queryKey: ['plant', projectId] });
  }, [queryClient, projectId]);

  const updateEnclosure = useMutation({
    mutationFn: (v: { rackId: string; profile: string }) =>
      physicalApi.updateRack(v.rackId, { enclosure_profile: v.profile }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['topology', projectId] }),
    onError: () => setError('Gagal menyimpan profil enclosure.'),
  });

  // Cable Mode (NG-PH3D P2): a patched port pair becomes a real logical link
  // plus the physical cable that realizes it — same POST /links + POST
  // /cables pair RackElevationPanel.tsx's own cable tooling would use, just
  // driven from a 3D port pick instead of a 2D one.
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
  // RackElevationPanel.tsx's own addDevice mutation (create → PATCH RU).
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

  /** Half-height the shot needs: the tallest rack plus tray headroom. */
  const spanFor = useCallback((scale: number, zoomed: boolean) => {
    const racksBuilt = builtRef.current ? Object.values(builtRef.current.registry.racks) : [];
    const top = (racksBuilt.length ? Math.max(...racksBuilt.map((r) => r.h)) : 2.0) + 0.3;
    return ((top + 0.08) / 2 / Math.max(0.2, scale)) * (zoomed ? 0.42 : 1);
  }, []);

  const fitCamera = useCallback(() => {
    const host = hostRef.current, cam = camRef.current;
    if (!host || !cam) return;
    const w = host.clientWidth || 1, h = host.clientHeight || 1;
    const span = spanFor(povRef.current.span, view.current.zoomed);
    const aspect = w / h;
    cam.left = -span * aspect;
    cam.right = span * aspect;
    cam.top = span;
    cam.bottom = -span;
    cam.updateProjectionMatrix();
  }, [spanFor]);

  /** Place the ortho camera on the fixed 2.5D POV. az is the only thing that turns. */
  const placeCamera = useCallback((az = view.current.az) => {
    const cam = camRef.current, built = builtRef.current;
    if (!cam || !built) return;
    const a = built.registry.racks.A, b = built.registry.racks.B;
    if (!a || !b) return;
    const focus = view.current.zoomed ? built.registry.racks[view.current.focusRack] : null;
    const cx = (focus ? focus.x : (a.x + b.x) / 2) + 0.12;
    const cy = (focus ? focus.h : Math.max(a.h, b.h)) * 0.46;
    const d = POV.dist, el = povRef.current.elev;
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
    if (adapted) {
      const built = buildScene({ rackA: rackASpec, rackB: rackBSpec, fitout, links });
      scene.add(built.root);
      builtRef.current = built;
      applyDoors(built.registry, view.current.doors);
      applyLabels(built.registry, view.current.labels, view.current.sel);
      applySelection(built.registry, view.current.sel);

      // NG-PH3D P3 §5: a device just placed/moved from this panel — its
      // cables' stored length_m predates the new geometry. Recompute only
      // the ones this rebuild can actually see (both ends in the two shown
      // bays); the rest is the documented two-bay-view gap, not a new bug.
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
  }, [adapted, rackASpec, rackBSpec, fitout, links, fitCamera, placeCamera]);

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

  /* ─── POV: persist and re-place ────────────────────────────────────────── */
  useEffect(() => {
    povRef.current = pov;
    view.current.az = face === 'back' ? pov.baseAz + Math.PI : pov.baseAz;
    fitCamera();
    placeCamera();
    try {
      localStorage.setItem(POV_KEY, JSON.stringify({ ...pov, az: view.current.az }));
    } catch {
      /* storage blocked */
    }
  }, [pov, face, fitCamera, placeCamera]);

  /* ─── work zoom: only for cabling / adding, only on the worked rack ────── */
  useEffect(() => {
    const on = mode !== null;
    if (view.current.zoomed === on) return;
    const from = spanFor(povRef.current.span, view.current.zoomed);
    view.current.zoomed = on;
    view.current.focusRack = (sel && builtRef.current?.registry.devices[sel]?.rackKey) || 'A';
    const to = spanFor(povRef.current.span, on);
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
   *  (NG-PH3D P2: POST /nodes + PATCH placement). */
  const handleAddDevice = useCallback((rackKey: Slot, y: number) => {
    const built = builtRef.current;
    const rack = built?.registry.racks[rackKey];
    const realRack = rackKey === 'A' ? rackA : rackB;
    if (!rack || !realRack) {
      setStatus('Pilih rak nyata untuk bay itu dulu');
      return;
    }
    const u = Math.floor((y - 0.055) / U) + 1;
    if (u < 1 || u > rack.spec.u) {
      setStatus('Di luar rail — pilih U yang kosong');
      return;
    }
    if (fitout[rackKey]!.some((d) => u >= d.u && u < d.u + d.h)) {
      setStatus(`U${u} sudah terisi — pilih U yang kosong`);
      return;
    }
    setStatus(`Menambahkan perangkat di U${u} rak ${rackKey}…`);
    addDevice.mutate({ rackId: realRack.id, ruStart: u });
  }, [fitout, rackA, rackB, addDevice]);

  useEffect(() => {
    const renderer = rendererRef.current;
    const canvas = renderer?.domElement;
    const cam = camRef.current;
    if (!canvas || !cam) return;
    const ray = new THREE.Raycaster();
    const onUp = (e: PointerEvent) => {
      const built = builtRef.current;
      if (!built) return;
      const r = canvas.getBoundingClientRect();
      const ndc = new THREE.Vector2(
        ((e.clientX - r.left) / r.width) * 2 - 1,
        -((e.clientY - r.top) / r.height) * 2 + 1,
      );
      ray.setFromCamera(ndc, cam);
      const hits = ray.intersectObjects([built.root], true);
      if (mode === 'cable') {
        const port = hits.find((h) => h.object.userData?.port !== undefined);
        if (port) {
          handlePortPick(port.object.userData.dev as string, port.object.userData.port as number);
          return;
        }
        setStatus('Cable Mode: klik port, bukan chassis');
        return;
      }
      if (mode === 'adddev') {
        const any = hits[0];
        if (!any) return;
        let best: Slot | null = null, bd = Infinity;
        for (const k of ['A', 'B'] as const) {
          const rk = built.registry.racks[k];
          if (!rk) continue;
          const dx = Math.abs(any.point.x - rk.x);
          if (dx < bd) { bd = dx; best = k; }
        }
        if (best && bd < 0.45) handleAddDevice(best, any.point.y);
        return;
      }
      const hit = hits.find((h) => h.object.userData?.dev);
      selectDevice(hit ? (hit.object.userData.dev as string) : null);
    };
    canvas.addEventListener('pointerup', onUp);
    return () => canvas.removeEventListener('pointerup', onUp);
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

  const deviceCount = Object.values(fitout).reduce((n, l) => n + l.length, 0);
  const btn = (on: boolean) =>
    `flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs transition ${
      on ? 'bg-accent/20 text-accent ring-1 ring-accent/40' : 'text-recess hover:bg-fg/5 hover:text-fg'
    }`;

  const slotPicker = (slot: Slot) => {
    const rackId = slot === 'A' ? rackAId : rackBId;
    const setRackId = slot === 'A' ? setRackAId : setRackBId;
    const rack = slot === 'A' ? rackA : rackB;
    return (
      <div key={slot} className="flex flex-wrap items-center gap-1">
        <label className="flex min-w-0 items-center gap-1.5 text-xs text-recess">
          {slot}
          <select
            value={rackId ?? ''}
            onChange={(e) => setRackId(e.target.value || null)}
            className="w-24 min-w-0 truncate rounded-md border border-fg/10 bg-transparent px-1.5 py-1 text-xs text-fg outline-none focus:border-accent/50"
          >
            <option value="">— kosong —</option>
            {racks.map((r) => (
              <option key={r.id} value={r.id}>{r.name}</option>
            ))}
          </select>
        </label>
        <select
          value={rack?.enclosure_profile ?? DEFAULT_ENCLOSURE}
          disabled={!rack}
          onChange={(e) => rack && updateEnclosure.mutate({ rackId: rack.id, profile: e.target.value })}
          className="w-28 min-w-0 truncate rounded-md border border-fg/10 bg-transparent px-1.5 py-1 text-xs text-fg outline-none focus:border-accent/50 disabled:opacity-40"
          title="Profil enclosure"
        >
          {ENCLOSURE_KEYS.filter((k) => k !== 'cpi' || rack?.enclosure_profile === 'cpi').map((k) => (
            <option key={k} value={k}>{RACK_SPECS[k]!.label.replace(/ \d+U.*/, '')}</option>
          ))}
        </select>
      </div>
    );
  };

  // NG-PH3D P4: no WebGL, no scene — a black/blank canvas here would look
  // like a crash. Say so plainly and point back at the 2D panel, which the
  // parent toggle (`viewSwitcher`) can still reach.
  if (webglError) {
    return (
      <div className="absolute inset-0 flex flex-col">
        <div className="flex items-center justify-end border-b border-fg/10 px-3 py-2">{viewSwitcher}</div>
        <div className="relative min-h-0 flex-1">
          <WorkspaceEmptyState
            icon={AlertTriangle}
            title="3D view unavailable"
            hint="This browser doesn't support WebGL. Switch to the Elevation (2D) panel above to keep working with the same rack data."
          />
        </div>
      </div>
    );
  }

  return (
    <div className="absolute inset-0 flex flex-col">
      {/* toolbar */}
      <div className="flex flex-wrap items-center gap-2 border-b border-fg/10 px-3 py-2">
        {slotPicker('A')}
        {slotPicker('B')}
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
        <div className="ml-auto flex items-center gap-3">
          {([
            ['Az', 'baseAz', -180, 180, 1, (v: number) => (v * 180) / Math.PI, (v: number) => (v * Math.PI) / 180],
            ['El', 'elev', 0, 60, 1, (v: number) => (v * 180) / Math.PI, (v: number) => (v * Math.PI) / 180],
            ['Zoom', 'span', 0.3, 2, 0.01, (v: number) => v, (v: number) => v],
          ] as const).map(([label, key, min, max, step, out, into]) => (
            <label key={key} className="flex items-center gap-1.5 text-[11px] text-recess">
              {label}
              <input
                type="range"
                min={min}
                max={max}
                step={step}
                value={out(pov[key])}
                onChange={(e) => setPov((p) => ({ ...p, [key]: into(parseFloat(e.target.value)) }))}
                className="w-20 accent-accent"
              />
            </label>
          ))}
          <button
            type="button"
            className={btn(false)}
            onClick={() => { setPov({ baseAz: POV.az, elev: POV.elev, span: POV.span }); setFace('front'); }}
            title="Reset POV"
          >
            <RotateCcw className="size-3.5" />
          </button>
        </div>

        {viewSwitcher}
      </div>

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

      {/* NG-PH3D P3: same threshold/source as RackElevationPanel.tsx's own
          over-length banner (GET /plant, over_length flag) — both views
          agree on which runs are bad because they read the same query. */}
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

      {/* Pindah perangkat antar-rack (permintaan Surya, P2) — muncul saat ada
          perangkat terpilih. Batas satu-site ditegakkan di backend; ini hanya
          menyalurkan hasilnya. Juga jalur "tempatkan" untuk node dari tray
          Unplaced di atas (NG-PH3D P3) — write yang sama, cuma sebelumnya
          belum punya rak sama sekali. */}
      {selNode && (
        <div className="flex flex-wrap items-center gap-2 border-b border-fg/10 px-3 py-1.5 text-xs">
          <Move className="size-3.5 text-recess" />
          <span className="text-recess">Pindahkan {selNode.name} ke</span>
          <select
            value={moveRackId}
            onChange={(e) => setMoveRackId(e.target.value)}
            className="rounded-md border border-fg/10 bg-transparent px-1.5 py-1 text-xs text-fg outline-none focus:border-accent/50"
          >
            <option value="">— pilih rak —</option>
            {racks.map((r) => (
              <option key={r.id} value={r.id}>{r.name}</option>
            ))}
          </select>
          <label className="flex items-center gap-1 text-recess">
            U
            <input
              type="number"
              min={1}
              value={moveRu}
              onChange={(e) => setMoveRu(parseInt(e.target.value, 10) || 1)}
              className="w-14 rounded-md border border-fg/10 bg-transparent px-1.5 py-1 text-xs text-fg outline-none focus:border-accent/50"
            />
          </label>
          <button
            type="button"
            disabled={!moveRackId || moveDevice.isPending}
            onClick={() => moveDevice.mutate({
              nodeId: selNode.id, rackId: moveRackId, ruStart: moveRu, ruSpan: selNode.ru_span ?? 1,
            })}
            className="rounded-md bg-accent/20 px-2.5 py-1 text-accent ring-1 ring-accent/40 transition hover:bg-accent/30 disabled:opacity-40"
          >
            Pindahkan
          </button>
        </div>
      )}

      {/* canvas */}
      <div ref={hostRef} className="relative min-h-0 flex-1">
        {topoQ.isSuccess && racks.length === 0 && (
          <WorkspaceEmptyState
            icon={Server}
            title="No racks yet"
            hint="Create a rack first in the Elevation (2D) panel — devices placed there show up here."
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
