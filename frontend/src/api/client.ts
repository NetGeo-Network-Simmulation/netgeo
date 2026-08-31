/**
 * REST client for the NetGeo FastAPI backend (see NetGeo/09_API_STANDARD.md).
 * Thin axios wrapper: single base instance, JSON, error normalization, and
 * typed resource functions. The dev server proxies `/api` to the backend, so
 * we use a same-origin relative baseURL by default.
 */
import axios, { AxiosError, type AxiosRequestConfig } from 'axios';
import type {
  Activity,
  ActivityCreate,
  Cable,
  CableCreate,
  CableUpdate,
  ConfigArtifact,
  GradeReport,
  GradeResult,
  GradeSubmit,
  HostInterface,
  InternetStatus,
  LinkModel,
  NodeModel,
  PlantReport,
  Project,
  Rack,
  RackCreate,
  RackUpdate,
  ReachabilityResult,
  Scenario,
  SimulateRequest,
  Site,
  SiteCreate,
  SiteUpdate,
  Topology,
} from './types';
import {
  isEnvelope,
  type ApiErrorBody,
  type Page,
  type PageMeta,
  type PageParams,
} from './envelope';
import { getToken, notifyUnauthorized } from './token';

export const API_BASE = import.meta.env.VITE_API_BASE ?? '/api';

export const http = axios.create({
  baseURL: API_BASE,
  headers: { 'Content-Type': 'application/json' },
  timeout: 20_000,
});

/** Normalized error shape surfaced to UI (error states must be first-class). */
export interface ApiError {
  status: number;
  message: string;
  /** Machine-readable code from the envelope `error.code`, when present. */
  code?: string | number;
  detail?: unknown;
}

/**
 * Module-augment AxiosResponse so the unwrapping interceptor can stash the
 * envelope `meta` block (pagination) without a separate request. Read it via
 * {@link fetchPage} rather than reaching for `res.meta` directly.
 */
declare module 'axios' {
  // Type parameters must mirror axios's own `AxiosResponse<T = any, D = any>`
  // declaration verbatim, otherwise TS2428 ("identical type parameters").
  // eslint-disable-next-line @typescript-eslint/no-unused-vars, @typescript-eslint/no-explicit-any
  interface AxiosResponse<T = any, D = any> {
    /** Envelope meta (NetGeo/09 §Response Format). Undefined for bare bodies. */
    meta?: PageMeta;
  }
}

/**
 * Request interceptor: attach the bearer token (AUTH_CONTRACT §3) to every
 * outgoing request. `/auth/login` runs before a token exists, so the header is
 * simply omitted then.
 */
http.interceptors.request.use((config) => {
  const t = getToken();
  if (t) {
    config.headers = config.headers ?? {};
    (config.headers as Record<string, string>).Authorization = `Bearer ${t}`;
  }
  return config;
});

/**
 * Success interceptor: transparently unwrap the NetGeo envelope.
 *  - `{ success:false, error }` → reject with a normalized {@link ApiError}
 *    even on HTTP 200 (defensive: some gateways wrap errors with a 200).
 *  - `{ success:true, data, meta }` → replace `res.data` with `data`, stash
 *    `meta` on the response so existing `.then(r => r.data)` callers are
 *    untouched.
 *  - bare payloads → passed through verbatim (tolerant migration).
 */
http.interceptors.response.use(
  (res) => {
    if (isEnvelope(res.data)) {
      const env = res.data;
      if (env.success === false) {
        const body = (env.error ?? {}) as ApiErrorBody;
        const apiError: ApiError = {
          status: res.status,
          message: body.message ?? 'Request failed',
          code: body.code,
          detail: body.detail ?? body,
        };
        return Promise.reject(apiError);
      }
      res.meta = env.meta;
      res.data = env.data;
    }
    return res;
  },
  (err: AxiosError<{ detail?: unknown; error?: ApiErrorBody; message?: string }>) => {
    const data = err.response?.data;
    const envError = data?.error;
    // A 401 on an authenticated request means the session is dead (expired or
    // revoked) — tear it down so the app falls back to the login screen. We
    // only fire when a token was actually attached, so a failed login attempt
    // (no token yet) keeps its inline error instead of bouncing the UI.
    // /auth/change-password also 401s on a wrong *current password* — that is
    // an inline form error, not a dead session, so it must not log the user out.
    const url = err.config?.url ?? '';
    if (
      err.response?.status === 401 &&
      getToken() &&
      !url.includes('/auth/login') &&
      !url.includes('/auth/change-password')
    ) {
      notifyUnauthorized();
    }
    const apiError: ApiError = {
      status: err.response?.status ?? 0,
      message:
        envError?.message ||
        (typeof data?.detail === 'string' ? data.detail : undefined) ||
        data?.message ||
        err.message ||
        'Network error',
      code: envError?.code,
      detail: envError?.detail ?? data?.detail ?? data,
    };
    return Promise.reject(apiError);
  },
);

/**
 * Fetch a paginated resource, returning both the rows and the envelope `meta`
 * (total/limit/offset/cursor). Use this for list views that render pagination;
 * plain list helpers below still return just the rows for convenience.
 */
export async function fetchPage<T>(
  url: string,
  params?: PageParams,
  config?: AxiosRequestConfig,
): Promise<Page<T>> {
  const res = await http.get<T>(url, { ...config, params });
  return { data: res.data, meta: res.meta ?? {} };
}

/* ------------------------------- Auth ------------------------------------ */
/** Login/identity endpoints — see docs/security/AUTH_CONTRACT.md. */
export interface LoginResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
}
export interface CurrentUser {
  username: string;
  role: string;
}
export const authApi = {
  login: (username: string, password: string) =>
    http.post<LoginResponse>('/auth/login', { username, password }).then((r) => r.data),
  me: () => http.get<CurrentUser>('/auth/me').then((r) => r.data),
  /** First-run setup: is there no account yet? (public) */
  setupStatus: () =>
    http.get<{ setup_required: boolean }>('/auth/setup').then((r) => r.data),
  /** First-run setup: create the admin account; returns a token (auto-login). */
  setup: (username: string, password: string) =>
    http.post<LoginResponse>('/auth/setup', { username, password }).then((r) => r.data),
  /** Change own password; requires the current password (re-auth). */
  changePassword: (currentPassword: string, newPassword: string) =>
    http
      .post('/auth/change-password', {
        current_password: currentPassword,
        new_password: newPassword,
      })
      .then((r) => r.data),
};

/* ----------------------------- Projects ---------------------------------- */
export const projectsApi = {
  list: (params?: PageParams) =>
    http.get<Project[]>('/projects', { params }).then((r) => r.data),
  /** Paginated variant — returns rows + envelope meta for list views. */
  listPage: (params?: PageParams) => fetchPage<Project[]>('/projects', params),
  get: (id: string) => http.get<Project>(`/projects/${id}`).then((r) => r.data),
  create: (body: Partial<Project>) => http.post<Project>('/projects', body).then((r) => r.data),
  topology: (id: string) =>
    http.get<Topology>(`/projects/${id}/topology`).then((r) => r.data),
  /** Export the project as an NG-WS-03 archive envelope (used to capture an
   *  activity's initial/answer network in the Education author flow). */
  archive: (id: string) =>
    http.get<Record<string, unknown>>(`/projects/${id}/archive`).then((r) => r.data),
};

/* ------------------------------- Nodes ----------------------------------- */
export const nodesApi = {
  create: (body: Partial<NodeModel>) => http.post<NodeModel>('/nodes', body).then((r) => r.data),
  update: (id: string, patch: Partial<NodeModel>) =>
    http.patch<NodeModel>(`/nodes/${id}`, patch).then((r) => r.data),
  remove: (id: string) => http.delete(`/nodes/${id}`).then(() => undefined),
  /** Move is sent debounced from the canvas to avoid request floods. */
  move: (id: string, x: number, y: number) =>
    http.patch<NodeModel>(`/nodes/${id}`, { x, y }).then((r) => r.data),
};

/* ------------------------------- Links ----------------------------------- */
export const linksApi = {
  create: (body: Partial<LinkModel>) => http.post<LinkModel>('/links', body).then((r) => r.data),
  update: (id: string, patch: Partial<LinkModel>) =>
    http.patch<LinkModel>(`/links/${id}`, patch).then((r) => r.data),
  remove: (id: string) => http.delete(`/links/${id}`).then(() => undefined),
};

/* ----------------------------- Scenarios --------------------------------- */
export const scenariosApi = {
  list: (projectId: string) =>
    http.get<Scenario[]>('/scenarios', { params: { project_id: projectId } }).then((r) => r.data),
};

/* --------------------------- Physical plant ------------------------------ */
/**
 * Sites, racks, and cables (NG-PH-01/02/03). Devices are placed into racks via
 * the existing `nodesApi.update` (rack_id / ru_start / ru_span) — there is no
 * separate placement endpoint. `plant` returns per-link physical verdicts
 * (total length, added propagation delay, over-length errors).
 */
export const physicalApi = {
  createSite: (body: SiteCreate) => http.post<Site>('/sites', body).then((r) => r.data),
  listSites: (projectId: string) =>
    http.get<Site[]>('/sites', { params: { project_id: projectId } }).then((r) => r.data),
  getSite: (id: string) => http.get<Site>(`/sites/${id}`).then((r) => r.data),
  updateSite: (id: string, patch: SiteUpdate) =>
    http.patch<Site>(`/sites/${id}`, patch).then((r) => r.data),
  removeSite: (id: string) => http.delete(`/sites/${id}`).then(() => undefined),
  createRack: (body: RackCreate) => http.post<Rack>('/racks', body).then((r) => r.data),
  updateRack: (id: string, patch: RackUpdate) =>
    http.patch<Rack>(`/racks/${id}`, patch).then((r) => r.data),
  createCable: (body: CableCreate) => http.post<Cable>('/cables', body).then((r) => r.data),
  getCable: (id: string) => http.get<Cable>(`/cables/${id}`).then((r) => r.data),
  updateCable: (id: string, patch: CableUpdate) =>
    http.patch<Cable>(`/cables/${id}`, patch).then((r) => r.data),
  removeCable: (id: string) => http.delete(`/cables/${id}`).then(() => undefined),
  plant: (projectId: string) =>
    http.get<PlantReport>(`/projects/${projectId}/plant`).then((r) => r.data),
};

/* ------------------------------ Education -------------------------------- */
/**
 * Education activities + auto-grading (NG-EDU-01/02/03). An activity bundles
 * instructions with a starting network, a model answer, and weighted grading
 * checks; `grade` returns a live completion report, `submit` persists an
 * attempt. Import/export move activities as `.netgeo-lab` envelopes.
 */
export const educationApi = {
  list: () => http.get<Activity[]>('/activities').then((r) => r.data),
  get: (id: string) => http.get<Activity>(`/activities/${id}`).then((r) => r.data),
  create: (body: ActivityCreate) =>
    http.post<Activity>('/activities', body).then((r) => r.data),
  remove: (id: string) => http.delete(`/activities/${id}`).then(() => undefined),
  /** Live, side-effect-free grade of a student's project against this activity. */
  grade: (activityId: string, projectId: string) =>
    http
      .post<GradeReport>(`/activities/${activityId}/grade`, { project_id: projectId })
      .then((r) => r.data),
  /** Grade **and persist** the attempt (records student + elapsed time). */
  submit: (activityId: string, body: GradeSubmit) =>
    http
      .post<GradeResult>(`/activities/${activityId}/submit`, body)
      .then((r) => r.data),
  /** Export an activity as a sharable `.netgeo-lab` envelope. */
  export: (activityId: string) =>
    http
      .get<Record<string, unknown>>(`/activities/${activityId}/export`)
      .then((r) => r.data),
  /** Create a fresh activity from a `.netgeo-lab` envelope. */
  import: (envelope: Record<string, unknown>) =>
    http.post<Activity>('/activities/import', envelope).then((r) => r.data),
  /** Import the activity's starting network as a fresh project for a student. */
  instantiate: (activityId: string) =>
    http
      .post<Project>(`/activities/${activityId}/instantiate`)
      .then((r) => r.data),
  /** Download all graded attempts for this activity as a CSV file. */
  downloadResultsCsv: async (activityId: string) => {
    const resp = await http.get<Blob>(`/activities/${activityId}/results.csv`, {
      responseType: 'blob',
    });
    const url = URL.createObjectURL(resp.data as unknown as Blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `activity-${activityId}-results.csv`;
    a.click();
    URL.revokeObjectURL(url);
  },
};

/* ----------------------------- Digital twin ------------------------------ */
/**
 * Digital twin (NG-TW-01/02, backend `app/api/twin.py`). Import a device config
 * into a real node; infer links across imported nodes (bulk, idempotent);
 * answer "can src reach dst?" over the twin with path + RIB evidence.
 * Per-proposal Accept/Reject is done client-side (see `twin/twinLogic.ts`) —
 * there is no dry-run endpoint; accepting a proposal calls `linksApi.create`.
 */
export type ConfigVendor = 'ios' | 'routeros';
export const twinApi = {
  importConfig: (projectId: string, vendor: ConfigVendor, text: string) =>
    http
      .post<NodeModel>(`/projects/${projectId}/import-config`, { vendor, text })
      .then((r) => r.data),
  inferLinks: (projectId: string) =>
    http.post<LinkModel[]>(`/projects/${projectId}/infer-links`).then((r) => r.data),
  reachability: (projectId: string, src: string, dst: string, count?: number) =>
    http
      .post<ReachabilityResult>(`/projects/${projectId}/reachability`, { src, dst, count })
      .then((r) => r.data),
};

/* ----------------------------- Simulation -------------------------------- */
export const simApi = {
  start: (body: SimulateRequest) => {
    const { project_id, realtime = true } = body;
    // Ignore seed and horizon as per instructions
    if (realtime) {
      // Set mode to realtime first, then start simulation
      return http.post(`/lab/${project_id}/mode`, { mode: 'realtime' })
        .then(() => http.post(`/lab/${project_id}/run`))
        .then(r => r.data);
    } else {
      // For simulation mode, just start (could also set mode to simulation if needed)
      return http.post(`/lab/${project_id}/run`).then(r => r.data);
    }
  },
  pause: (projectId: string) => http.post(`/lab/${projectId}/run/pause`).then(r => r.data),
  resume: (projectId: string) => http.post(`/lab/${projectId}/run/resume`).then(r => r.data),
  step: (projectId: string) => http.post(`/lab/${projectId}/run/step`).then(r => r.data),
  stop: (projectId: string) => http.post(`/lab/${projectId}/run/stop`).then(r => r.data),
  status: (projectId: string) => http.get(`/lab/${projectId}/run/status`).then(r => r.data),
};

/* ------------------------------ Live lab ---------------------------------- */
/**
 * Packet-level diagnostics against the project's living lab (backend
 * `app/api/lab.py`). Ping/traceroute run through the discrete-event engine;
 * captures/tables expose the live per-link frames and per-device state.
 */
export interface PingResult {
  src: string;
  dst: string;
  sent: number;
  received: number;
  loss_pct: number;
  rtts_ms: number[];
  min_ms: number | null;
  avg_ms: number | null;
  max_ms: number | null;
  errors: string[];
  /** Session id + completion flag — in simulation mode results arrive as you step. */
  ident?: number;
  done?: boolean;
  mode?: LabMode;
}

export type LabMode = 'realtime' | 'simulation';

/** One dispatched engine event (NG-SIM-01). PACKET_TX/RX rows carry frame context. */
export interface LedgerRecord {
  seq: number;
  t: number;
  type: string;
  node: string;
  info?: string;
  link?: string;
  iface?: string;
  frame_id?: number;
  size?: number;
}

export interface LedgerResponse {
  hash: string;
  total: number;
  mode: LabMode;
  sim_time: number;
  pending_events: number;
  records: LedgerRecord[];
}

export interface StepResponse {
  project_id: string;
  dispatched: number;
  seq: number;
  sim_time: number;
  pending_events: number;
  records: LedgerRecord[];
}

export interface SeekResponse {
  project_id: string;
  mode: LabMode;
  seq: number;
  hash: string;
  sim_time: number;
  pending_events: number;
}

export interface TracerouteHop {
  hop: number;
  address: string | null;
  rtt_ms: number | null;
}
export interface TracerouteResult {
  src: string;
  dst: string;
  reached: boolean;
  hops: TracerouteHop[];
}

export interface CaptureRecord {
  t: number;
  link_id: string;
  iface: string;
  dir: 'tx' | 'rx' | 'drop';
  frame_id: number;
  size: number;
  info: string;
  layers: Record<string, Record<string, unknown>>;
}

export interface CliResult {
  node: string;
  output: string;
  prompt: string;
}

/** One IPv4 subnet row in the addressing-wizard preview (server-summarized). */
export interface AddressV4Row {
  segment: string;
  subnet: string;
  gateway: string;
  hosts: number;
}
/** One IPv6 (ULA) /64 row in the addressing-wizard preview. */
export interface AddressV6Row {
  segment: string;
  prefix: string;
  gateway: string;
}
export interface AddressPlanSummary {
  interfaces: number;
  lan_domains: number;
  p2p_links: number;
  ipv4: AddressV4Row[];
  ipv6: AddressV6Row[];
}
/** Result of POST /lab/{id}/auto-address (dry_run or apply). */
export interface AutoAddressResult {
  project_id: string;
  dry_run: boolean;
  nodes_updated: number;
  summary: AddressPlanSummary;
}

export const labApi = {
  ping: (projectId: string, src: string, dst: string, count = 4) =>
    http
      .post<PingResult>(`/lab/${projectId}/ping`, { src, dst, count })
      .then((r) => r.data),
  traceroute: (projectId: string, src: string, dst: string) =>
    http
      .post<TracerouteResult>(`/lab/${projectId}/traceroute`, { src, dst })
      .then((r) => r.data),
  cli: (projectId: string, node: string, command: string) =>
    http
      .post<CliResult>(`/lab/${projectId}/cli`, { node, command })
      .then((r) => r.data),
  captures: (projectId: string, linkId?: string, limit = 200, filter?: string) =>
    http
      .get<{ records: CaptureRecord[] }>(`/lab/${projectId}/captures`, {
        params: { link_id: linkId, limit, filter: filter || undefined },
      })
      .then((r) => r.data.records),
  /** Download the capture as a real .pcapng (opens in Wireshark). */
  downloadPcapng: async (projectId: string, linkId?: string) => {
    const resp = await http.get<Blob>(`/lab/${projectId}/pcapng`, {
      params: { link_id: linkId },
      responseType: 'blob',
    });
    const url = URL.createObjectURL(resp.data as unknown as Blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `netgeo-${projectId.slice(0, 8)}.pcapng`;
    a.click();
    URL.revokeObjectURL(url);
  },
  tables: (projectId: string, nodeRef: string) =>
    http
      .get<Record<string, unknown>>(`/lab/${projectId}/tables/${nodeRef}`)
      .then((r) => r.data),
  autoAddress: (projectId: string, opts?: { dryRun?: boolean }) =>
    http
      .post<AutoAddressResult>(`/lab/${projectId}/auto-address`, undefined, {
        params: { dry_run: opts?.dryRun ? true : undefined },
      })
      .then((r) => r.data),
  /* --- Simulation mode (NG-SIM-01) --- */
  mode: (projectId: string, mode: LabMode) =>
    http
      .post<{ mode: LabMode; seq: number }>(`/lab/${projectId}/mode`, { mode })
      .then((r) => r.data),
  step: (projectId: string, body: { events?: number; duration?: number }) =>
    http.post<StepResponse>(`/lab/${projectId}/step`, body).then((r) => r.data),
  seek: (projectId: string, seq: number) =>
    http.post<SeekResponse>(`/lab/${projectId}/seek`, { seq }).then((r) => r.data),
  ledger: (projectId: string, fromSeq = 0, limit = 300) =>
    http
      .get<LedgerResponse>(`/lab/${projectId}/ledger`, {
        params: { from_seq: fromSeq, limit },
      })
      .then((r) => r.data),
};

/* ------------------------------ Wireless / RF ---------------------------- */
/**
 * Terrain LoS/Fresnel check (backend `app/api/wireless.py`). When no `profile`
 * is supplied the server tries to fetch a real DEM elevation profile from its
 * pinned, SSRF-safe provider and returns it alongside the verdict — the map
 * elevation-profile tool consumes exactly this. If the provider is offline,
 * `/wireless/elevation` (the raw proxy) returns 503, but `/los-check` and
 * `/rf/ptp` degrade gracefully to a flat-terrain profile instead (so RF
 * planning still works on air-gapped installs) — check `terrain_source`
 * (F67) before trusting the numbers: `"flat_fallback"` means every elevation
 * in `points` is 0 m, not measured.
 */
export interface ElevationSample {
  lat: number;
  lon: number;
  elevation_m: number;
  distance_m: number;
}
export interface ElevationProfile {
  samples: number;
  total_distance_m: number;
  points: ElevationSample[];
  terrain_source: 'dem' | 'flat_fallback';
}
export interface LosCheckResult {
  los_clear: boolean;
  fresnel_clear: boolean;
  worst_obstruction_m: number;
  min_clearance_ratio: number;
  distance_m: number;
  profile: ElevationProfile | null;
}
export const wirelessApi = {
  losCheck: (body: {
    a_lat: number;
    a_lon: number;
    b_lat: number;
    b_lon: number;
    frequency_ghz?: number;
    tx_height_m?: number;
    rx_height_m?: number;
    samples?: number;
  }) => http.post<LosCheckResult>('/wireless/los-check', body).then((r) => r.data),
};

/* ------------------------------ RF planning ------------------------------ */
/**
 * RF coverage raster (NG-RF-02, backend `app/api/rf.py`). One transmitter site
 * per placed AP/tower; the server returns a best-server RSSI grid (dBm per cell)
 * over the requested bbox. Deterministic — identical request → identical raster.
 * 422 on cell-cap overflow / bad geometry / unknown technology or model.
 */
export interface CoverageSite {
  lat: number;
  lon: number;
  height_m: number;
  tx_power_dbm?: number;
  freq_mhz?: number;
  tx_gain_dbi?: number;
}
export interface CoverageRasterRequest {
  sites: CoverageSite[];
  technology?: string;
  model_id?: string;
  rows: number;
  cols: number;
  min_lat: number;
  min_lon: number;
  max_lat: number;
  max_lon: number;
  rx_height_m?: number;
  rx_gain_dbi?: number;
  misc_loss_db?: number;
}
export interface CoverageRasterResult {
  model_id: string;
  technology: string;
  rows: number;
  cols: number;
  bounds: { min_lat: number; min_lon: number; max_lat: number; max_lon: number };
  values: number[][]; // best-server dBm, row-major (rows × cols); row 0 = south
  legend: { label: string; min_dbm: number }[];
  study_key: string;
  site_count: number;
}
/**
 * PtP link planning (NG-RF-03). `models` lists the propagation registry; `ptp`
 * runs a full link budget + terrain LoS/Fresnel verdict for one link. When
 * `profile` is omitted the server fetches a DEM profile and returns it, so the
 * RF workspace gets the elevation chart from the same call — but if the DEM
 * provider is offline it degrades to flat terrain rather than failing (F67:
 * check `result.profile.terrain_source`, see `ElevationProfile` above). 422 on
 * an unknown model / out-of-range frequency.
 */
export interface PropagationParam {
  name: string;
  default: unknown;
  description?: string;
  options?: string[];
}
export interface PropagationModelInfo {
  id: string;
  name: string;
  freq_min_mhz: number;
  freq_max_mhz: number;
  dist_min_m: number;
  dist_max_m: number;
  params: PropagationParam[];
  note?: string;
}
export interface PtpRequest {
  a_lat: number;
  a_lon: number;
  b_lat: number;
  b_lon: number;
  freq_mhz: number;
  tx_power_dbm?: number;
  tx_gain_dbi?: number;
  rx_gain_dbi?: number;
  misc_loss_db?: number;
  rx_sensitivity_dbm?: number;
  tx_height_m?: number;
  rx_height_m?: number;
  model_id?: string;
  params?: Record<string, unknown>;
  samples?: number;
  profile?: ElevationSample[] | null;
}
export interface PtpResult {
  model_id: string;
  distance_m: number;
  eirp_dbm: number;
  path_loss_db: number;
  rssi_dbm: number;
  fade_margin_db: number; // rssi − rx sensitivity
  los_clear: boolean;
  fresnel_clear: boolean;
  worst_obstruction_m: number;
  min_clearance_ratio: number;
  verdict: string; // "clear" | "obstructed"
  link_ok: boolean;
  profile: ElevationProfile | null;
}
/**
 * PtMP sector planning (NG-RF-04) and auto product-select (NG-RF-05) — both
 * stateless POSTs over the same propagation registry as PtP. `RfStudy` is the
 * save-on-demand persistence record shared by all RF study kinds (NG-RF cross-
 * cutting): the client snapshots request + already-computed result, and reopening
 * returns that result verbatim (no recompute).
 */
export interface PtmpCpe {
  id: string;
  lat?: number | null;
  lon?: number | null;
  distance_m?: number | null;
  bearing_deg?: number | null;
  height_m?: number;
  rx_gain_dbi?: number;
  misc_loss_db?: number;
}
export interface PtmpRequest {
  lat: number;
  lon: number;
  height_m?: number;
  azimuth_deg: number;
  beamwidth_deg: number;
  downtilt_deg?: number;
  freq_mhz: number;
  bandwidth_mhz?: number;
  tx_power_dbm?: number;
  tx_gain_dbi?: number;
  rx_sensitivity_dbm?: number;
  model_id?: string;
  params?: Record<string, unknown>;
  cpes: PtmpCpe[];
}
export interface PtmpCpeResult {
  cpe_id: string;
  distance_m: number;
  bearing_deg: number;
  in_beam: boolean;
  path_loss_db: number;
  rssi_dbm: number;
  mcs: number | null;
  modulation: string | null;
  throughput_mbps: number;
  served: boolean;
}
export interface PtmpResult {
  model_id: string;
  azimuth_deg: number;
  beamwidth_deg: number;
  freq_mhz: number;
  bandwidth_mhz: number;
  cpes: PtmpCpeResult[];
  served_count: number;
  sum_phy_mbps: number;
  airtime_fair_mbps: number;
}
export interface RadioCandidate {
  name: string;
  tx_power_dbm: number;
  antenna_gain_dbi: number;
  rx_sensitivity_dbm: number;
  cost: number;
  bandwidth_mhz?: number;
}
export interface ProductSelectRequest {
  distance_m: number;
  freq_mhz: number;
  target_throughput_mbps: number;
  model_id?: string;
  params?: Record<string, unknown>;
  tx_height_m?: number;
  rx_height_m?: number;
  misc_loss_db?: number;
  candidates: RadioCandidate[];
}
export interface ProductSelectItem {
  name: string;
  rssi_dbm: number;
  margin_db: number;
  predicted_throughput_mbps: number;
  cost: number;
  margin_per_cost: number;
  link_closes: boolean;
  meets_target: boolean;
}
export interface ProductSelectResult {
  distance_m: number;
  freq_mhz: number;
  target_throughput_mbps: number;
  ranked: ProductSelectItem[];
}
export type RfStudyKind = 'coverage' | 'ptp' | 'ptmp' | 'product_select';
export interface RfStudy {
  id: string;
  project_id: string;
  kind: RfStudyKind;
  name: string;
  request: Record<string, unknown>;
  result: Record<string, unknown>;
  created_at: string;
}
export interface RfStudyCreate {
  project_id: string;
  kind: RfStudyKind;
  name?: string;
  request: Record<string, unknown>;
  result: Record<string, unknown>;
}
/** Static ISP radio catalog (23 devices, real datasheet specs) served by
 * `GET /api/rf/radios` — used to prefill Auto-Select/PtMP candidate fields.
 * No cost/bandwidth here: those stay user-supplied (see `RadioCandidate`). */
export interface RadioCatalogEntry {
  id: string;
  name: string;
  vendor: string;
  model: string;
  type: string;
  frequency_ghz: number;
  tx_power_dbm: number;
  antenna_gain_dbi: number;
  rx_sensitivity_dbm: number;
  max_range_km: number;
  max_throughput_mbps: number;
  standard: string;
  notes: string;
  /** True when the vendor's own product page marks this SKU discontinued. */
  eol?: boolean;
}
export const rfApi = {
  coverage: (body: CoverageRasterRequest) =>
    http.post<CoverageRasterResult>('/rf/coverage', body).then((r) => r.data),
  models: () => http.get<PropagationModelInfo[]>('/rf/models').then((r) => r.data),
  radios: () => http.get<RadioCatalogEntry[]>('/rf/radios').then((r) => r.data),
  ptp: (body: PtpRequest) => http.post<PtpResult>('/rf/ptp', body).then((r) => r.data),
  ptmp: (body: PtmpRequest) => http.post<PtmpResult>('/rf/ptmp', body).then((r) => r.data),
  productSelect: (body: ProductSelectRequest) =>
    http.post<ProductSelectResult>('/rf/product-select', body).then((r) => r.data),
  studies: {
    list: (projectId: string) =>
      http.get<RfStudy[]>('/rf/studies', { params: { project_id: projectId } }).then((r) => r.data),
    create: (body: RfStudyCreate) => http.post<RfStudy>('/rf/studies', body).then((r) => r.data),
    get: (id: string) => http.get<RfStudy>(`/rf/studies/${id}`).then((r) => r.data),
    remove: (id: string) => http.delete<{ deleted: string }>(`/rf/studies/${id}`).then((r) => r.data),
  },
};

/* ------------------------------ Fiber / FTTH ----------------------------- */
/**
 * Fiber plant / FTTH (NG-FI-01/02/03, backend `app/api/fiber.py`). A `FiberPath`
 * is a single ordered OLT→ONU chain of passive `FiberElement`s; `/budget` runs
 * the loss budget + GPON checks server-side (`services/fiber.py` is authoritative
 * — the client never recomputes loss). `bom`/`report` cover NG-FI-04 / NG-CFG-02.
 */
export type FiberKind = 'fiber' | 'splitter' | 'splice' | 'connector';
export type GponClass = 'b_plus' | 'c_plus' | 'c2';

export interface FiberElement {
  kind: FiberKind;
  length_m?: number; // fiber only
  atten_db_km?: number; // fiber only (G.652 default 0.22)
  split_ratio?: number; // splitter only (the N in 1:N)
  loss_db?: number | null; // explicit override for any element
  label?: string;
}
export interface FiberPath {
  id: string;
  project_id: string;
  name: string;
  gpon_class: GponClass;
  elements: FiberElement[];
}
export interface FiberPathCreate {
  project_id: string;
  name: string;
  gpon_class?: GponClass;
  elements?: FiberElement[];
}
export interface FiberPathUpdate {
  name?: string;
  gpon_class?: GponClass;
  elements?: FiberElement[];
}
export interface FiberCheck {
  ok: boolean;
  reason: string;
}
export interface LossBudget {
  total_loss_db: number;
  budget_db: number;
  margin_db: number;
  passed: boolean;
  total_length_m: number;
  total_split: number;
  checks: FiberCheck[];
}
export interface BomItem {
  category: string;
  item: string;
  qty: number;
  unit: string;
  notes: string;
}
export const fiberApi = {
  list: (projectId: string) =>
    http.get<FiberPath[]>('/fiber-paths', { params: { project_id: projectId } }).then((r) => r.data),
  create: (body: FiberPathCreate) => http.post<FiberPath>('/fiber-paths', body).then((r) => r.data),
  update: (id: string, patch: FiberPathUpdate) =>
    http.patch<FiberPath>(`/fiber-paths/${id}`, patch).then((r) => r.data),
  remove: (id: string) => http.delete(`/fiber-paths/${id}`).then((r) => r.data),
  budget: (id: string) => http.get<LossBudget>(`/fiber-paths/${id}/budget`).then((r) => r.data),
  bom: (projectId: string) =>
    http.get<BomItem[]>(`/projects/${projectId}/bom`).then((r) => r.data),
  // HTML report is auth-gated (Bearer header), so it must go through this client
  // and be opened as a blob — a bare window.open would 401.
  report: (projectId: string) =>
    http.get<string>(`/projects/${projectId}/report`, { responseType: 'text' }).then((r) => r.data),
};

/* ----------------------------- Config-gen -------------------------------- */
/** Unified diff of a node's stored config vs a freshly regenerated one (NG-CFG-03). */
export interface ConfigDiff {
  node_id: string;
  vendor: string;
  had_stored: boolean;
  changed: boolean;
  /** Empty string when there is no change. `+`/`-`-prefixed unified-diff text. */
  diff: string;
}
/** Whole-project vendor export (NG-CFG-01): device name → rendered config text. */
export interface ProjectConfigExport {
  project_id: string;
  vendor: string;
  configs: Record<string, string>;
}
export const configsApi = {
  generate: (nodeId: string, vendor?: string) =>
    http
      .post<ConfigArtifact>('/configs/generate', { node_id: nodeId, vendor })
      .then((r) => r.data),
  forNode: (nodeId: string) =>
    http.get<ConfigArtifact[]>('/configs', { params: { node_id: nodeId } }).then((r) => r.data),
  /** Preview diff for one node; `vendor` omitted → the node's native NOS. */
  diff: (nodeId: string, vendor?: string) =>
    http
      .get<ConfigDiff>(`/nodes/${nodeId}/config/diff`, { params: { vendor: vendor || undefined } })
      .then((r) => r.data),
  /** Render every device's config for the project (native, or one target vendor). */
  exportProject: (projectId: string, vendor?: string) =>
    http
      .get<ProjectConfigExport>(`/projects/${projectId}/configs/export`, {
        params: { vendor: vendor || undefined },
      })
      .then((r) => r.data),
  /** Download the whole-project export as one `.cfg` text file, device by device.
   *  ponytail: single concatenated file (no zip dep); split per-device only if asked. */
  downloadProjectConfigs: async (projectId: string, vendor?: string) => {
    const { configs, vendor: v } = await configsApi.exportProject(projectId, vendor);
    const text =
      Object.entries(configs)
        .map(([name, cfg]) => `! ===== ${name} =====\n${cfg}`)
        .join('\n\n') || '! no configs generated\n';
    const url = URL.createObjectURL(new Blob([text], { type: 'text/plain' }));
    const a = document.createElement('a');
    a.href = url;
    a.download = `netgeo-${projectId.slice(0, 8)}-${v}.cfg`;
    a.click();
    URL.revokeObjectURL(url);
  },
};

/* --------------------------- Device types -------------------------------- */
/**
 * Device-type registry (MASTER_SPEC §4 / backend `device_types.py`).
 * Powers the map-mode Device Library: built-in catalog + operator-added
 * custom types (manual entry, Docker image, or uploaded appliance image).
 */
export interface DeviceType {
  id: string;
  name: string;
  category: string;
  icon?: string | null;
  description: string;
  builtin: boolean;
  /** Rack power datasheet (C-4/Rack#3) — optional, older entries omit these. */
  power_watts_idle?: number | null;
  power_watts_max?: number | null;
  /** Device console PoE budget — distinct from idle/max chassis draw above.
   *  null when the pack's datasheet never published one (non-PoE device). */
  poe_budget_w?: number | null;
  snmp_oids?: Record<string, string> | null;
  /** Rack faceplate identity (N4) — verbatim from the pack JSON, only present
   *  for device-library-pack entries. None for _BUILTIN/custom entries. */
  vendor?: string | null;
  ports?:
    | { pattern?: string; count: number; type: string; speed_mbps?: number; role?: string; poe?: boolean }[]
    | null;
  physical?: { ru?: number; form_factor?: string } | null;
}

export interface DeviceTypeCreate {
  name: string;
  category?: string;
  icon?: string | null;
  description?: string;
}

export const deviceTypesApi = {
  list: () => http.get<DeviceType[]>('/device-types').then((r) => r.data),
  create: (body: DeviceTypeCreate) =>
    http.post<DeviceType>('/device-types', body).then((r) => r.data),
  remove: (id: string) => http.delete(`/device-types/${id}`).then(() => undefined),

  /** Register a custom type backed by a Docker image. */
  fromDocker: (image: string, name?: string) =>
    http
      .post<DeviceType>('/device-types', {
        name: name?.trim() || image,
        category: 'docker',
        icon: 'docker',
        description: `Docker image: ${image}`,
      } satisfies DeviceTypeCreate)
      .then((r) => r.data),

  /**
   * Upload an appliance image (ISO/qcow2/img) to register a custom device type.
   * Sent as multipart/form-data to `/device-types/upload`.
   * NOTE: this backend endpoint is a cross-area dependency — see frontend/NEEDS.md.
   */
  uploadImage: (
    file: File,
    opts?: { name?: string; onProgress?: (pct: number) => void },
  ) => {
    const form = new FormData();
    form.append('file', file);
    if (opts?.name) form.append('name', opts.name);
    return http
      .post<DeviceType>('/device-types/upload', form, {
        headers: { 'Content-Type': 'multipart/form-data' },
        timeout: 0, // large uploads: no client timeout
        onUploadProgress: (e) => {
          if (opts?.onProgress && e.total) {
            opts.onProgress(Math.round((e.loaded / e.total) * 100));
          }
        },
      })
      .then((r) => r.data);
  },
};

/* --------------------------- Device library packs ------------------------ */
/**
 * Device library packs (NG-DL-02 / docs/design/15-DEVICE-LIBRARY-PACKS.md).
 * A pack is a drop-in folder of brand devices that merges into
 * `deviceTypesApi.list()` when enabled — see `backend/app/api/device_types.py`.
 */
export interface DevicePack {
  id: string;
  name: string;
  version: string;
  categories: string[];
  device_count: number;
  enabled_by_default: boolean;
  source_note: string;
  enabled: boolean;
}

export const devicePacksApi = {
  list: () => http.get<DevicePack[]>('/device-packs').then((r) => r.data),
  enable: (id: string) =>
    http.post<DevicePack>(`/device-packs/${id}/enable`).then((r) => r.data),
  disable: (id: string) =>
    http.post<DevicePack>(`/device-packs/${id}/disable`).then((r) => r.data),
};

/* --------------------------- Host system / NICs -------------------------- */
export const systemApi = {
  interfaces: () =>
    http.get<{ interfaces: HostInterface[] }>('/system/interfaces').then((r) => r.data.interfaces),
  internet: () => http.get<InternetStatus>('/system/internet').then((r) => r.data),
};

/* ----------------------------- Self-update ------------------------------- */
export interface UpdateCheck {
  current: string;
  latest: string | null;
  update_available: boolean;
  notes?: string;
  url?: string;
  published_at?: string;
  checked_at: number;
  can_apply?: boolean;
  /** True when the backend has UPDATE_TOKEN configured — apply() must then
   *  send the matching X-Update-Token header on top of the admin session. */
  token_required?: boolean;
  error?: string;
}
export interface UpdateStatus {
  state:
    | 'idle'
    | 'queued'
    | 'updating'
    | 'rebuilding'
    | 'restarting'
    | 'done'
    | 'up-to-date'
    | 'error';
  message?: string;
  at?: number;
}
export const updateApi = {
  check: () => http.get<UpdateCheck>('/update/check').then((r) => r.data),
  status: () => http.get<UpdateStatus>('/update/status').then((r) => r.data),
  apply: (token?: string) =>
    http
      .post<UpdateStatus>('/update/apply', null, {
        headers: token ? { 'X-Update-Token': token } : undefined,
      })
      .then((r) => r.data),
};
