/**
 * Rack3DElevationPanel — the 2.5D physical-plant view (Desain Rackmount 2.5).
 *
 * Owns one WebGL renderer and one orthographic camera on a fixed isometric
 * POV: no perspective convergence, so the rack reads like the elevation view
 * it replaces. The scene graph itself lives in lib/three/rack3d.
 *
 * ponytail: plain three.js, no react-three-fiber. The scene is imperative and
 * rebuilt wholesale on a rack/link change; wrapping it in a reconciler would
 * add a dependency and buy nothing — React owns the chrome, three owns the
 * canvas.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import * as THREE from 'three';
import { Cable, DoorClosed, Plus, RotateCcw, Tag, Zap } from 'lucide-react';
import {
  applyDoors,
  applyLabels,
  applySelection,
  buildScene,
  disposeScene,
  mediaFor,
  tick,
  FITOUT,
  LINKS,
  MEDIA,
  RACK_SPECS,
  U,
  type BuiltScene,
  type DeviceDef,
  type LinkDef,
} from '@/lib/three/rack3d';

const POV_KEY = 'netgeo.rack3d.pov.v2';
const POV = { az: (10 * Math.PI) / 180, elev: (10 * Math.PI) / 180, span: 0.75, dist: 14 };

type Mode = 'cable' | 'adddev' | null;
type Face = 'front' | 'back';

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

export function Rack3DElevationPanel() {
  const hostRef = useRef<HTMLDivElement>(null);
  const builtRef = useRef<BuiltScene | null>(null);
  const camRef = useRef<THREE.OrthographicCamera | null>(null);
  const rendererRef = useRef<THREE.WebGLRenderer | null>(null);
  // mutable view state the render loop reads every frame — deliberately not
  // React state: a 60 fps camera must not re-render the component tree.
  const view = useRef({ az: POV.az, anim: true, doors: false, labels: false, sel: null as string | null, zoomed: false, focusRack: 'A' });
  const povRef = useRef<Pov>(loadPov());

  const [rackA, setRackA] = useState('apc');
  const [rackB, setRackB] = useState('vertiv');
  const [links, setLinks] = useState<LinkDef[]>(() => LINKS.map((l) => ({ ...l })));
  const [fitout, setFitout] = useState<Record<string, DeviceDef[]>>(() => ({
    A: FITOUT.A!.map((d) => ({ ...d })),
    B: FITOUT.B!.map((d) => ({ ...d })),
  }));
  const [pov, setPov] = useState<Pov>(povRef.current);
  const [face, setFace] = useState<Face>('front');
  const [mode, setMode] = useState<Mode>(null);
  const [doors, setDoors] = useState(false);
  const [labels, setLabels] = useState(false);
  const [anim, setAnim] = useState(true);
  const [sel, setSel] = useState<string | null>(null);
  const [pick, setPick] = useState<{ devId: string; portIx: number } | null>(null);
  const [status, setStatus] = useState('Klik perangkat di scene');

  /** Half-height the shot needs: the tallest rack plus tray headroom. */
  const spanFor = useCallback((scale: number, zoomed: boolean) => {
    const racks = builtRef.current ? Object.values(builtRef.current.registry.racks) : [];
    const top = (racks.length ? Math.max(...racks.map((r) => r.h)) : 2.0) + 0.3;
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
    if (!host) return;
    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.setSize(host.clientWidth || 1, host.clientHeight || 1);
    host.appendChild(renderer.domElement);
    rendererRef.current = renderer;

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
    };
  }, [fitCamera, placeCamera]);

  /* ─── (re)build the scene whenever its inputs change ───────────────────── */
  useEffect(() => {
    const renderer = rendererRef.current;
    const scene = (renderer as unknown as { _sceneRef?: THREE.Scene } | null)?._sceneRef;
    if (!scene) return;
    if (builtRef.current) disposeScene(builtRef.current);
    const built = buildScene({ rackA, rackB, fitout, links });
    scene.add(built.root);
    builtRef.current = built;
    applyDoors(built.registry, view.current.doors);
    applyLabels(built.registry, view.current.labels, view.current.sel);
    applySelection(built.registry, view.current.sel);
    fitCamera();
    placeCamera();
  }, [rackA, rackB, fitout, links, fitCamera, placeCamera]);

  /* ─── toggles: mirror React state into the scene ───────────────────────── */
  useEffect(() => {
    view.current.doors = doors;
    if (builtRef.current) applyDoors(builtRef.current.registry, doors);
  }, [doors]);

  useEffect(() => {
    view.current.labels = labels;
    view.current.sel = sel;
    if (builtRef.current) {
      applyLabels(builtRef.current.registry, labels, sel);
      applySelection(builtRef.current.registry, sel);
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

  /** Cable Mode: first click arms a port, second click patches the pair. */
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
    const m = mediaFor(built.registry.devices[a.devId]!.def, built.registry.devices[devId]!.def);
    setLinks((prev) => [...prev, { a: [a.devId, a.portIx], b: [devId, portIx], m, live: true }]);
    setStatus(`Patched ${MEDIA[m]!.label.split(' · ')[0]}: ${a.devId}:${a.portIx} → ${devId}:${portIx}`);
  }, [pick]);

  /** Add device: click a free U on a rack, get a 1U switch there. */
  const handleAddDevice = useCallback((rackKey: string, y: number) => {
    const built = builtRef.current;
    const rack = built?.registry.racks[rackKey];
    if (!rack) return;
    const u = Math.floor((y - 0.055) / U) + 1;
    if (u < 1 || u > rack.spec.u) {
      setStatus('Di luar rail — pilih U yang kosong');
      return;
    }
    const list = fitout[rackKey];
    if (!list) {
      setStatus('Rak itu tidak bisa diedit');
      return;
    }
    if (list.some((d) => u >= d.u && u < d.u + d.h)) {
      setStatus(`U${u} sudah terisi — pilih U yang kosong`);
      return;
    }
    const id = `new-${rackKey}-${u}`;
    const dev: DeviceDef = {
      id, u, h: 1, kind: 'switch', brand: 'NetGeo', model: 'NG-4800 1U switch',
      accent: 0xd97757, ports: 24, ptype: 'rj45', chassis: 0x1e1e1c,
    };
    setFitout((prev) => ({ ...prev, [rackKey]: [...(prev[rackKey] ?? []), dev] }));
    setStatus(`Ditambahkan NG-4800 di U${u} rak ${rackKey}`);
  }, [fitout]);

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
        let best: string | null = null, bd = Infinity;
        for (const k of ['A', 'B']) {
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
        ? id === 'cable' ? 'Cable Mode: pilih dua port' : 'Add device: pilih U yang kosong'
        : 'Klik perangkat di scene');
      return on ? id : null;
    });
  };

  const deviceCount = Object.values(fitout).reduce((n, l) => n + l.length, 0);
  const btn = (on: boolean) =>
    `flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs transition ${
      on ? 'bg-accent/20 text-accent ring-1 ring-accent/40' : 'text-recess hover:bg-fg/5 hover:text-fg'
    }`;

  return (
    <div className="absolute inset-0 flex flex-col">
      {/* toolbar */}
      <div className="flex flex-wrap items-center gap-2 border-b border-fg/10 px-3 py-2">
        {(['A', 'B'] as const).map((key) => (
          <label key={key} className="flex items-center gap-1.5 text-xs text-recess">
            {key}
            <select
              value={key === 'A' ? rackA : rackB}
              onChange={(e) => (key === 'A' ? setRackA : setRackB)(e.target.value)}
              className="rounded-md border border-fg/10 bg-transparent px-1.5 py-1 text-xs text-fg outline-none focus:border-accent/50"
            >
              {Object.entries(RACK_SPECS)
                .filter(([k]) => k !== 'cpi')
                .map(([k, s]) => (
                  <option key={k} value={k}>{s.label.replace(/ \d+U.*/, '')}</option>
                ))}
            </select>
          </label>
        ))}
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
        <button type="button" className={btn(mode === 'cable')} onClick={() => toggleMode('cable')}>
          <Cable className="size-3.5" /> Cable Mode
        </button>
        <button type="button" className={btn(mode === 'adddev')} onClick={() => toggleMode('adddev')}>
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
      </div>

      {/* canvas */}
      <div ref={hostRef} className="relative min-h-0 flex-1" />

      {/* status bar + legend */}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-fg/10 px-3 py-1.5 text-[11px] text-recess">
        <span className="text-fg">{status}</span>
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
