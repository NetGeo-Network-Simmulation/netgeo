/**
 * NetGeo shared data model — mirrors the backend API contract exactly.
 * These types are the contract between frontend and the FastAPI backend.
 * Keep field names in sync with the backend Pydantic schemas.
 */

export type NodeKind =
  | 'router'
  | 'switch'
  | 'host'
  | 'ap'
  | 'cpe'   // Customer Premises Equipment — wireless client placed via map deploy
  | 'olt'
  | 'firewall'
  | 'server'
  | 'cloud'; // bridge to a real host NIC / the internet (GNS3-style cloud)

export type Nos =
  | 'forgeos'
  | 'ios'
  | 'iosxr'
  | 'nxos'
  | 'junos'
  | 'eos'
  | 'routeros'
  | 'vyos'
  | 'sros'
  | 'frr'
  | 'vrp';

export type NodeMode = 'sim' | 'emul';
export type NodeStatus = 'stopped' | 'booting' | 'running' | 'degraded' | 'error';

export type IfaceType = 'eth' | 'sfp' | 'sfp28' | 'qsfp' | 'gpon' | 'wifi';
export type LinkType = 'copper' | 'fiber' | 'wireless' | 'virtual';
/**
 * `errored` = a physical run brought the link down (e.g. cable over its rated
 * max length, NG-PH-03) rather than an operator — a teachable failure, distinct
 * from `admin_down`. Must match the backend LinkStatus enum exactly.
 */
export type LinkStatus = 'up' | 'down' | 'admin_down' | 'errored' | 'unknown';
export type ConfigFormat = 'cli' | 'netconf' | 'yaml';

export interface Interface {
  id: string;
  node_id: string;
  name: string;
  type: IfaceType;
  ip: string[];
  mac: string;
  speed: number; // Mbps
  mtu: number;
  peer_link_id: string | null;
  /** Device console P5: per-port admin state + PoE delivery. Always present —
   *  the backend defaults old interfaces to enabled/no-PoE. */
  admin_enabled: boolean;
  poe_enabled: boolean;
}

/** Where a cloud node attaches to the real world. Persisted in `intent.uplink`. */
export type UplinkMode = 'nat' | 'bridge';
export interface Uplink {
  adapter: string; // host NIC name, e.g. "eth0" / "Ethernet"
  mode: UplinkMode;
}

/** Wireless radio parameters (mirrors backend Radio schema). Present on ap/cpe nodes. */
export interface Radio {
  tx_power_dbm: number;
  frequency_ghz: number;
  antenna_gain_dbi: number;
  bandwidth_mhz: number;
  rx_sensitivity_dbm: number;
  misc_loss_db: number;
  max_range_m: number | null;
  /** Antenna height above ground level, metres (NG-N3.1). */
  height_agl_m: number;
}

export interface NodeModel {
  id: string;
  project_id: string;
  name: string;
  kind: NodeKind;
  nos: Nos;
  mode: NodeMode;
  x: number;
  y: number;
  /** Geographic position (WGS84). null => canvas-only node (no map marker). */
  lat?: number | null;
  lon?: number | null;
  /** Wireless radio params — present on ap/cpe nodes placed on the map. */
  radio?: Radio | null;
  interfaces: Interface[];
  config_ref: string | null;
  status: NodeStatus;
  /** Physical placement (NG-PH-01): rack + rack-unit span. `rack_id`/`ru_start`
   *  null => the device is unplaced (sits in the rack view's tray). */
  rack_id?: string | null;
  ru_start?: number | null;
  ru_span?: number;
  /** Owning site (NG-PH-01). Source of truth for site membership — mirrors
   *  the rack's site when racked, settable directly for outdoor map nodes. */
  site_id?: string | null;
  /** Extension bag (ForgeOS intent; cloud-node `uplink`). */
  intent?: Record<string, unknown> | null;
}

/** A detected host network adapter (GET /api/system/interfaces). */
export interface HostInterface {
  name: string;
  mac: string | null;
  ipv4: string[];
  ipv6: string[];
  is_up: boolean;
  speed_mbps: number;
  mtu: number;
  is_virtual: boolean;
  is_primary: boolean;
}

/** Internet reachability (GET /api/system/internet). */
export interface InternetStatus {
  online: boolean;
  latency_ms: number | null;
  via: string;
  source_ip: string | null;
}

export interface LinkModel {
  id: string;
  project_id: string;
  a_iface: string;
  b_iface: string;
  type: LinkType;
  bandwidth: number; // Mbps
  delay: number; // ms
  loss: number; // percent 0..100
  mtu: number;
  status?: LinkStatus;
}

export interface Project {
  id: string;
  name: string;
  description: string;
  version: string;
  topology_ref: string;
  created_at: string;
}

export interface ScenarioStep {
  id: string;
  description: string;
  action: string;
}

export interface Scenario {
  id: string;
  project_id: string;
  name: string;
  steps: ScenarioStep[];
  expected_outcomes: string[];
}

export interface ConfigArtifact {
  id: string;
  node_id: string;
  vendor: string;
  format: ConfigFormat;
  content: string;
  generated_at: string;
}

/** Full graph payload from GET /api/projects/{id}/topology and /ws/topology.
 *  Physical-plant arrays (NG-PH-01/02) are empty for purely logical projects. */
export interface Topology {
  nodes: NodeModel[];
  links: LinkModel[];
  sites?: Site[];
  racks?: Rack[];
  cables?: Cable[];
}

/** Realtime events pushed over /ws/topology. */
export type TopologyEvent =
  | { type: 'snapshot'; topology: Topology }
  | { type: 'node.updated'; node: NodeModel }
  | { type: 'node.deleted'; node_id: string }
  | { type: 'node.status'; node_id: string; status: NodeStatus }
  | { type: 'link.updated'; link: LinkModel }
  | { type: 'link.deleted'; link_id: string }
  | { type: 'link.status'; link_id: string; status: LinkStatus }
  | {
      type: 'sim.tick';
      t: number;
      metrics?: Record<string, number>;
      /** Authoritative engine state for this tick; terminal values reset the bar. */
      state?: SimState | 'completed' | 'stopped' | 'error';
    };

/** Console frames over /ws/console/{node_id}. */
export type ConsoleEvent =
  | { type: 'banner'; node_id?: string; text: string; prompt?: string }
  | { type: 'output'; node_id: string; data: string; prompt?: string }
  | { type: 'prompt'; node_id: string; prompt: string }
  | { type: 'error'; node_id?: string; text: string }
  | { type: 'closed'; node_id: string; reason?: string };

/* -------------------------------------------------------------------------- */
/* Realtime collaboration (presence + CRDT op-log) — /ws/collab               */
/* -------------------------------------------------------------------------- */

/** A connected collaborator's presence record. */
export interface Peer {
  id: string;
  name: string;
  /** Stable accent color (hex) derived from the peer id for avatars/cursors. */
  color: string;
  /** Last known pointer position, in canvas coords or map lat/lng. */
  cursor?: { x: number; y: number } | { lat: number; lng: number } | null;
  /** Currently selected entity id (node/link/device), for shared highlight. */
  selection?: string | null;
  /** Epoch ms of the last frame from this peer (for stale eviction). */
  lastSeen: number;
}

/**
 * A single CRDT-style operation in the shared op-log. Carries a Lamport
 * timestamp + actor so a future CRDT merge layer (e.g. Yjs/Automerge) can be
 * dropped in without changing the transport. This is scaffolding: today the
 * server is still authoritative via /ws/topology; ops let presence-driven
 * intent ride the same channel.
 */
export interface CrdtOp {
  id: string;
  actor: string;
  /** Lamport clock for causal ordering across peers. */
  lamport: number;
  /** Target entity: "node:<id>", "link:<id>", "device:<id>". */
  entity: string;
  field?: string;
  value?: unknown;
  ts: number;
}

/** Frames over /ws/collab. */
export type PresenceEvent =
  | { type: 'presence.sync'; peers: Peer[] }
  | { type: 'presence.join'; peer: Peer }
  | { type: 'presence.leave'; peer_id: string }
  | { type: 'presence.cursor'; peer_id: string; cursor: Peer['cursor'] }
  | { type: 'presence.selection'; peer_id: string; selection: string | null }
  | { type: 'crdt.op'; op: CrdtOp };

/* -------------------------------------------------------------------------- */
/* Physical plant (NG-PH-01/02/03) — sites, racks, cables                      */
/* -------------------------------------------------------------------------- */

/** Physical media types (NG-PH-02). Must match the backend CableMedia enum. */
export type CableMedia =
  | 'cat5e'
  | 'cat6'
  | 'cat6a'
  | 'mmf_om3'
  | 'mmf_om4'
  | 'smf_os2'
  | 'dac'
  | 'coax'
  | 'gpon_drop';

/** A building/location that holds racks (NG-PH-01). `lat`/`lon` are optional —
 *  a site without coordinates simply never renders on the map. */
export interface Site {
  id: string;
  project_id: string;
  name: string;
  region: string;
  lat: number | null;
  lon: number | null;
}

/** An RU-gridded rack inside a site (NG-PH-01). Devices are placed into it
 *  via `Node.rack_id` / `ru_start` / `ru_span`. */
export interface Rack {
  id: string;
  project_id: string;
  site_id: string | null;
  name: string;
  ru_height: number;
  /** Enclosure look for the 2.5D view (NG-PH3D P1). Keys mirror
   *  `RACK_SPECS` in lib/three/rack3d.ts; null => renderer's default. */
  enclosure_profile?: string | null;
}

/** A physical run realizing a logical link (NG-PH-02). `length_m` feeds
 *  propagation delay; exceeding the media's max length errors the link. */
export interface Cable {
  id: string;
  project_id: string;
  link_id: string;
  media: CableMedia;
  length_m: number;
  label: string;
}

export interface SiteCreate {
  project_id: string;
  name: string;
  region?: string;
  lat?: number | null;
  lon?: number | null;
}

/** PATCH body — every field optional, only provided keys are applied. */
export interface SiteUpdate {
  name?: string;
  region?: string;
  lat?: number | null;
  lon?: number | null;
}

export interface RackCreate {
  project_id: string;
  site_id?: string | null;
  name: string;
  ru_height?: number;
  enclosure_profile?: string | null;
}

/** PATCH body — every field optional, only provided keys are applied. */
export interface RackUpdate {
  enclosure_profile?: string | null;
}

export interface CableCreate {
  project_id: string;
  link_id: string;
  media?: CableMedia;
  length_m?: number;
  label?: string;
}

export interface CableUpdate {
  media?: CableMedia;
  length_m?: number;
  label?: string;
}

/** Per-link physical verdict from GET /api/projects/{id}/plant (NG-PH-03). */
export interface PlantLink {
  total_length_m: number;
  added_delay_ms: number;
  over_length: boolean;
  /** The media that blew its budget, else null. */
  over_media: CableMedia | null;
}

/** GET /api/projects/{id}/plant — per-link physical effects, keyed by link id. */
export interface PlantReport {
  project_id: string;
  links: Record<string, PlantLink>;
}

/* -------------------------------------------------------------------------- */
/* Education mode (NG-EDU-01/02/03) — activities + auto-grading                */
/* -------------------------------------------------------------------------- */

export type GradeCheckKind =
  | 'node_exists'
  | 'iface_ip'
  | 'vlan_present'
  | 'ospf_neighbor'
  | 'ping';

/** One weighted assertion in an activity's grading tree (NG-EDU-02). */
export interface GradeCheck {
  kind: GradeCheckKind;
  weight?: number;
  label?: string | null;
  node?: string | null;
  iface?: string | null;
  cidr?: string | null;
  vlan?: number | null;
  peer?: string | null;
  dst?: string | null;
}

/** An education activity (NG-EDU-01). `initial`/`answer` are NG-WS-03 archive
 *  envelopes; the grader only reads `checks`. */
export interface Activity {
  id: string;
  name: string;
  instructions: string;
  initial: Record<string, unknown>;
  answer: Record<string, unknown>;
  locked_ui: string[];
  checks: GradeCheck[];
  time_limit_s: number | null;
}

export interface ActivityCreate {
  name: string;
  instructions?: string;
  initial?: Record<string, unknown>;
  answer?: Record<string, unknown>;
  locked_ui?: string[];
  checks?: GradeCheck[];
  time_limit_s?: number | null;
}

/** One graded check's verdict (NG-EDU-02). */
export interface GradeItem {
  label: string;
  passed: boolean;
  weight: number;
  reason: string;
}

/** Live completion report from POST /activities/{id}/grade (NG-EDU-02). */
export interface GradeReport {
  items: GradeItem[];
  score_pct: number;
  earned_weight: number;
  total_weight: number;
}

/** Request body for a graded submission (NG-EDU-03). */
export interface GradeSubmit {
  project_id: string;
  student?: string;
  elapsed_s?: number | null;
}

/** A persisted graded attempt (NG-EDU-03). */
export interface GradeResult {
  id: string;
  activity_id: string;
  student: string;
  score_pct: number;
  earned_weight: number;
  total_weight: number;
  elapsed_s: number | null;
  within_time: boolean | null;
  graded_at: string;
  items: GradeItem[];
}

/* -------------------------------------------------------------------------- */
/* Digital twin (NG-TW-01/02) — config import + reachability                    */
/* -------------------------------------------------------------------------- */

/** Answer to POST /projects/{id}/reachability (see reachability.answer). */
export interface ReachabilityResult {
  src: string;
  dst: string;
  dst_ip: string;
  reachable: boolean;
  loss_pct: number;
  rtt_avg_ms: number | null;
  /** Traceroute hop addresses (non-null); its length is the hop count. */
  path: string[];
  /** Matched RIB route on the source (IPv4 routers only), else null. */
  route: Record<string, unknown> | null;
}

/** Simulation lifecycle. */
export type SimState = 'idle' | 'running' | 'paused' | 'stepping';

export interface SimulateRequest {
  project_id: string;
  /** Corresponds to backend `realtime` bool — true = wall-clock realtime. */
  realtime?: boolean;
  seed?: number;
  horizon?: number;
}
