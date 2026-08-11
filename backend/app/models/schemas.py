"""Pydantic v2 schemas — the API contract.

These mirror MASTER_SPEC §4 exactly and are kept in lock-step with:
  * the engine model      (`engine/model.py`)
  * the frontend types     (`frontend/src/api/types.ts`)
  * the PostgreSQL schema  (`infra/db/schema.sql`)

Field names MUST match across all four. `extra="forbid"` is deliberate
(security hardening — see security/hardening-guide.md §4: reject unknown fields).
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


class _Base(BaseModel):
    # validate_default: enum defaults must coerce to their string values just
    # like explicit input — otherwise `status: LinkStatus = LinkStatus.up`
    # stays an Enum member and `str(field) == "up"` comparisons silently fail.
    model_config = ConfigDict(
        extra="forbid", use_enum_values=True, validate_default=True
    )


# --- enumerations (identical to schema.sql ENUMs) ---------------------------
class NodeKind(str, Enum):
    router = "router"
    switch = "switch"
    host = "host"
    ap = "ap"
    cpe = "cpe"   # Customer Premises Equipment — wireless client (map deploy)
    olt = "olt"
    firewall = "firewall"
    server = "server"
    # Bridge to the real world — a GNS3-style "cloud" node bound to a host
    # ethernet adapter so the topology can reach the LAN / internet. The chosen
    # adapter + bridge mode live in ``Node.intent["uplink"]``
    # (see app/services/netbridge.py and app/api/system.py).
    cloud = "cloud"


class Nos(str, Enum):
    forgeos = "forgeos"
    ios = "ios"
    iosxr = "iosxr"
    nxos = "nxos"
    junos = "junos"
    eos = "eos"
    routeros = "routeros"
    vyos = "vyos"
    sros = "sros"
    frr = "frr"
    vrp = "vrp"


class NodeMode(str, Enum):
    sim = "sim"
    emul = "emul"


class NodeStatus(str, Enum):
    stopped = "stopped"
    booting = "booting"
    running = "running"
    degraded = "degraded"
    error = "error"


class IfaceType(str, Enum):
    eth = "eth"
    sfp = "sfp"
    sfp28 = "sfp28"
    qsfp = "qsfp"
    gpon = "gpon"
    wifi = "wifi"


class LinkType(str, Enum):
    copper = "copper"
    fiber = "fiber"
    wireless = "wireless"
    virtual = "virtual"


class LinkStatus(str, Enum):
    up = "up"
    down = "down"
    admin_down = "admin_down"
    # Physically errored: the run brought it down (e.g. cable over max length,
    # NG-PH-03) rather than an operator. Distinct from admin_down so the UI can
    # explain *why* — a teachable failure, not a config choice.
    errored = "errored"
    unknown = "unknown"


class ConfigFormat(str, Enum):
    cli = "cli"
    netconf = "netconf"
    yaml = "yaml"


# --- core resources ---------------------------------------------------------
class Interface(_Base):
    id: str
    node_id: str
    name: str
    type: IfaceType = IfaceType.eth
    ip: list[str] = Field(default_factory=list)
    mac: str | None = None
    speed: int = 1000          # Mbps
    mtu: int = 1500
    peer_link_id: str | None = None
    # Device console P5 (docs/design/stitch-html/clay/device-console): per-port
    # admin state and PoE delivery, editable from the console's Port Settings
    # tab via the existing generic node PATCH (interfaces: [...]), same pattern
    # links.py already uses for peer_link_id — no new endpoint needed.
    admin_enabled: bool = True
    poe_enabled: bool = False


class Radio(_Base):
    """Wireless radio/antenna parameters for map-based RF planning.

    Mirrors ``engine.wireless.Radio``. Present only on wireless-capable nodes
    (kind ``ap``/``host`` placed on the map); ``None`` for wired infrastructure.
    """

    tx_power_dbm: float = 20.0
    frequency_ghz: float = 5.8
    antenna_gain_dbi: float = 14.0
    bandwidth_mhz: float = 20.0
    rx_sensitivity_dbm: float = -85.0
    misc_loss_db: float = 2.0
    max_range_m: float | None = None
    # Antenna height above ground level, metres (NG-N3.1). Stored only —
    # engine.wireless still takes height as an explicit per-call parameter
    # (tx_height_m/rx_height_m), so this field is not yet read by the engine.
    height_agl_m: float = 6.0


class Node(_Base):
    id: str
    project_id: str
    name: str
    kind: NodeKind = NodeKind.router
    nos: Nos = Nos.forgeos
    mode: NodeMode = NodeMode.sim
    x: float = 0.0
    y: float = 0.0
    # Geographic position for map mode (WGS84). ``None`` => canvas-only node.
    lat: float | None = None
    lon: float | None = None
    radio: Radio | None = None
    interfaces: list[Interface] = Field(default_factory=list)
    config_ref: str | None = None
    status: NodeStatus = NodeStatus.stopped
    # Physical placement (NG-PH-01): rack + rack-unit span; None = unplaced.
    rack_id: str | None = None
    ru_start: int | None = None
    ru_span: int = 1
    # Owning site (NG-PH-01). Source of truth for site membership — set it from
    # rack.site_id whenever the node is placed into a rack, so a node in a rack
    # can never disagree with the rack's own site. Settable directly too, for
    # outdoor nodes (tower/pole) deployed on the map without a rack.
    site_id: str | None = None
    # extension fields used by the ForgeOS compiler (see protocols/NEEDS.md)
    intent: dict | None = None


class NodeCreate(_Base):
    project_id: str
    name: str
    kind: NodeKind = NodeKind.router
    nos: Nos = Nos.forgeos
    mode: NodeMode = NodeMode.sim
    x: float = 0.0
    y: float = 0.0
    lat: float | None = None
    lon: float | None = None
    radio: Radio | None = None
    interfaces: list[Interface] = Field(default_factory=list)
    site_id: str | None = None
    intent: dict | None = None


class NodeUpdate(_Base):
    name: str | None = None
    nos: Nos | None = None
    mode: NodeMode | None = None
    x: float | None = None
    y: float | None = None
    lat: float | None = None
    lon: float | None = None
    radio: Radio | None = None
    status: NodeStatus | None = None
    interfaces: list[Interface] | None = None
    rack_id: str | None = None
    ru_start: int | None = None
    ru_span: int | None = None
    site_id: str | None = None
    intent: dict | None = None


class Link(_Base):
    id: str
    project_id: str
    a_iface: str
    b_iface: str
    type: LinkType = LinkType.virtual
    bandwidth: int = 1000      # Mbps
    delay: float = 0.0         # ms one-way
    loss: float = 0.0          # 0..1 drop probability
    mtu: int = 1500
    status: LinkStatus = LinkStatus.up


class LinkCreate(_Base):
    project_id: str
    a_iface: str
    b_iface: str
    type: LinkType = LinkType.virtual
    bandwidth: int = 1000
    delay: float = 0.0
    loss: float = 0.0
    mtu: int = 1500
    status: LinkStatus = LinkStatus.up


class LinkUpdate(_Base):
    bandwidth: int | None = None
    delay: float | None = None
    loss: float | None = None
    mtu: int | None = None
    status: LinkStatus | None = None


class Project(_Base):
    id: str
    name: str
    description: str = ""
    version: int = 1
    created_at: datetime = Field(default_factory=_now)


class ProjectCreate(_Base):
    name: str
    description: str = ""


# --- physical plant (NG-PH-01/02) -------------------------------------------


class CableMedia(str, Enum):
    """Physical media types (NG-PH-02). Each has a max length + per-metre
    propagation latency in :mod:`app.services.physical`."""

    cat5e = "cat5e"
    cat6 = "cat6"
    cat6a = "cat6a"
    mmf_om3 = "mmf_om3"
    mmf_om4 = "mmf_om4"
    smf_os2 = "smf_os2"
    dac = "dac"
    coax = "coax"
    gpon_drop = "gpon_drop"


class Site(_Base):
    """A building/location that holds racks (NG-PH-01).

    ``lat``/``lon`` are optional so pre-geo projects keep loading; a site
    without coordinates simply never renders on the map.
    """

    id: str
    project_id: str
    name: str
    region: str = ""
    lat: float | None = Field(None, ge=-90, le=90)
    lon: float | None = Field(None, ge=-180, le=180)


class Rack(_Base):
    """An RU-gridded rack inside a site (NG-PH-01). Devices are placed into it
    via ``Node.rack_id`` / ``ru_start`` / ``ru_span``."""

    id: str
    project_id: str
    site_id: str | None = None
    name: str
    ru_height: int = 42


class Cable(_Base):
    """A physical run realizing a logical link (NG-PH-02). ``length_m`` feeds
    propagation delay; exceeding the media's max length degrades the link
    (NG-PH-03)."""

    id: str
    project_id: str
    link_id: str
    media: CableMedia = CableMedia.cat6
    length_m: float = 1.0
    label: str = ""


class SiteCreate(_Base):
    project_id: str
    name: str
    region: str = ""
    lat: float | None = Field(None, ge=-90, le=90)
    lon: float | None = Field(None, ge=-180, le=180)


class SiteUpdate(_Base):
    """PATCH body — every field optional, only provided keys are applied."""

    name: str | None = None
    region: str | None = None
    lat: float | None = Field(None, ge=-90, le=90)
    lon: float | None = Field(None, ge=-180, le=180)


class RackCreate(_Base):
    project_id: str
    site_id: str | None = None
    name: str
    ru_height: int = 42


class CableCreate(_Base):
    project_id: str
    link_id: str
    media: CableMedia = CableMedia.cat6
    length_m: float = 1.0
    label: str = ""


class CableUpdate(_Base):
    media: CableMedia | None = None
    length_m: float | None = None
    label: str | None = None


class Topology(_Base):
    project: Project
    nodes: list[Node] = Field(default_factory=list)
    links: list[Link] = Field(default_factory=list)
    # Physical plant (NG-PH-01/02); empty for purely logical projects.
    sites: list[Site] = Field(default_factory=list)
    racks: list[Rack] = Field(default_factory=list)
    cables: list[Cable] = Field(default_factory=list)


class ScenarioStep(_Base):
    order: int
    action: str
    params: dict = Field(default_factory=dict)


class Scenario(_Base):
    id: str
    project_id: str
    name: str
    steps: list[ScenarioStep] = Field(default_factory=list)
    expected_outcomes: list[str] = Field(default_factory=list)


class ConfigArtifact(_Base):
    id: str
    node_id: str
    vendor: str
    format: ConfigFormat = ConfigFormat.cli
    content: str
    generated_at: datetime = Field(default_factory=_now)


class GenerateConfigRequest(_Base):
    node_id: str
    vendor: str | None = None     # default: derive from node.nos


class SimulateRequest(_Base):
    project_id: str
    seed: int = 0
    horizon: float | None = None
    realtime: bool = False


# --- education mode (NG-EDU-01/02) ------------------------------------------


class GradeCheckKind(str, Enum):
    """The kinds of assertion the auto-grader (NG-EDU-02) can make over a
    student's live model state."""

    node_exists = "node_exists"       # a device with this name exists
    iface_ip = "iface_ip"             # node/iface carries an expected CIDR
    vlan_present = "vlan_present"      # a vlan id is configured on a node
    ospf_neighbor = "ospf_neighbor"   # node has a full OSPF adjacency (opt. peer)
    ping = "ping"                     # src node reaches dst (ip or node name)


class GradeCheck(_Base):
    """One weighted assertion in an activity's grading tree.

    ``kind`` selects which params matter; the rest are plain optional fields
    (no fancy union — the grader reads only the fields its kind needs).
    """

    kind: GradeCheckKind
    weight: float = 1.0
    label: str | None = None
    node: str | None = None     # subject device name (all kinds except pure ping-by-ip)
    iface: str | None = None    # iface_ip: interface name
    cidr: str | None = None     # iface_ip: expected "10.0.0.1/24"
    vlan: int | None = None     # vlan_present: vlan id
    peer: str | None = None     # ospf_neighbor: optional peer node name
    dst: str | None = None      # ping: destination IP or node name


class Activity(_Base):
    """An education activity (NG-EDU-01): instructions plus a starting network
    and a model answer (both stored as NG-WS-03 archive envelopes), the UI
    elements locked for the student, and the grading checks (NG-EDU-02)."""

    id: str
    name: str
    instructions: str = ""                          # markdown, multi-step
    initial: dict = Field(default_factory=dict)     # archive envelope
    answer: dict = Field(default_factory=dict)      # archive envelope
    locked_ui: list[str] = Field(default_factory=list)
    checks: list[GradeCheck] = Field(default_factory=list)
    # Timed assessment (NG-EDU-03): optional wall-clock budget in seconds. A
    # graded submission records whether it landed within this limit.
    time_limit_s: int | None = None


class ActivityCreate(_Base):
    name: str
    instructions: str = ""
    initial: dict = Field(default_factory=dict)
    answer: dict = Field(default_factory=dict)
    locked_ui: list[str] = Field(default_factory=list)
    checks: list[GradeCheck] = Field(default_factory=list)
    time_limit_s: int | None = None


class GradeItem(_Base):
    """One graded check's verdict, with a human-readable reason (the AC)."""

    label: str
    passed: bool
    weight: float
    reason: str


class GradeReport(_Base):
    items: list[GradeItem] = Field(default_factory=list)
    score_pct: float = 100.0
    earned_weight: float = 0.0
    total_weight: float = 0.0


class GradeSubmit(_Base):
    """Request body for a graded attempt (NG-EDU-03): grade the ``project_id``
    against the activity and persist the result under ``student``. ``elapsed_s``
    is the student's wall-clock time, checked against the activity time limit."""

    project_id: str
    student: str = ""
    elapsed_s: float | None = None


class GradeResult(_Base):
    """A persisted graded attempt (NG-EDU-03) — a :class:`GradeReport` plus who
    took it, how long it took, and whether that was within the time limit.
    Instructors export these per activity as CSV."""

    id: str
    activity_id: str
    student: str = ""
    score_pct: float = 100.0
    earned_weight: float = 0.0
    total_weight: float = 0.0
    elapsed_s: float | None = None
    within_time: bool | None = None
    graded_at: datetime = Field(default_factory=_now)
    items: list[GradeItem] = Field(default_factory=list)


# --- fiber plant / FTTH (NG-FI-01/02/03) ------------------------------------


class FiberKind(str, Enum):
    fiber = "fiber"          # a fiber span: length + attenuation/km
    splitter = "splitter"    # passive 1:N splitter
    splice = "splice"        # fusion splice
    connector = "connector"  # mated connector pair


class GponClass(str, Enum):
    """GPON optical power-budget class → usable loss (dB)."""

    b_plus = "b_plus"   # 28 dB
    c_plus = "c_plus"   # 32 dB
    c2 = "c2"           # 32 dB (kept distinct for labelling)


class FiberElement(_Base):
    """One passive hop in an OLT→ONU path. Loss derives from ``kind`` + params
    (see ``services/fiber.py``); ``loss_db`` overrides the derived value."""

    kind: FiberKind
    length_m: float = 0.0        # fiber only
    atten_db_km: float = 0.22    # fiber only (G.652 @1550)
    split_ratio: int = 32        # splitter only (the N in 1:N)
    loss_db: float | None = None  # explicit override for any element
    label: str = ""


class FiberPath(_Base):
    """A GPON distribution path (NG-FI-01): an ordered OLT→ONU chain of passive
    elements, scored against ``gpon_class``'s budget."""

    id: str
    project_id: str
    name: str
    gpon_class: GponClass = GponClass.c_plus
    elements: list[FiberElement] = Field(default_factory=list)


class FiberPathCreate(_Base):
    project_id: str
    name: str
    gpon_class: GponClass = GponClass.c_plus
    elements: list[FiberElement] = Field(default_factory=list)


class FiberPathUpdate(_Base):
    name: str | None = None
    gpon_class: GponClass | None = None
    elements: list[FiberElement] | None = None


class FiberCheck(_Base):
    ok: bool
    reason: str


class LossBudget(_Base):
    """Auto loss budget + GPON sanity checks for one path (NG-FI-02/03)."""

    total_loss_db: float
    budget_db: float
    margin_db: float
    passed: bool
    total_length_m: float
    total_split: int
    checks: list[FiberCheck] = Field(default_factory=list)


class BomItem(_Base):
    """One line of a project bill of materials (NG-FI-04)."""

    category: str
    item: str
    qty: float
    unit: str = ""
    notes: str = ""


# --- wireless / RF planning -------------------------------------------------
class LinkBudgetRequest(_Base):
    """Compute a single point-to-point budget. Either give an explicit
    ``distance_m`` or both endpoint coordinates (then it's derived via
    Haversine)."""

    tx: Radio
    rx: Radio | None = None          # default: symmetric (rx == tx)
    distance_m: float | None = None
    a_lat: float | None = None
    a_lon: float | None = None
    b_lat: float | None = None
    b_lon: float | None = None
    rain_rate_mm_hr: float = 0.0     # ITU-R P.838 rain fade (0 = clear sky)


class LinkBudgetResult(_Base):
    distance_m: float
    fspl_db: float
    rssi_dbm: float
    margin_db: float
    noise_floor_dbm: float
    snr_db: float
    quality: str
    feasible: bool


class WirelessLink(_Base):
    """A planned wireless association between two geo-placed nodes."""

    a_id: str
    b_id: str
    distance_m: float
    fspl_db: float
    rssi_dbm: float
    margin_db: float
    noise_floor_dbm: float
    snr_db: float
    quality: str
    feasible: bool


class CoverageCircle(_Base):
    """Coverage footprint of a serving node, for map rendering."""

    node_id: str
    lat: float
    lon: float
    radius_m: float


class WirelessPlanResult(_Base):
    project_id: str
    links: list[WirelessLink] = Field(default_factory=list)
    coverage: list[CoverageCircle] = Field(default_factory=list)


class CoverageResult(_Base):
    node_id: str
    radius_m: float


# --- propagation model registry (NG-RF-01) ---------------------------------
class PropagationParam(_Base):
    """One tunable parameter a propagation model exposes."""

    name: str
    default: object
    description: str = ""
    options: list[str] = Field(default_factory=list)


class PropagationModelInfo(_Base):
    """Registry metadata for a single propagation model."""

    id: str
    name: str
    freq_min_mhz: float
    freq_max_mhz: float
    dist_min_m: float
    dist_max_m: float
    params: list[PropagationParam] = Field(default_factory=list)
    note: str = ""


class PathLossRequest(_Base):
    """Compute path loss (dB) with a chosen propagation model."""

    # ``model_id`` collides with Pydantic's protected ``model_`` namespace; free
    # it so the field name can match the public API contract.
    model_config = ConfigDict(
        extra="forbid", use_enum_values=True, validate_default=True,
        protected_namespaces=(),
    )

    model_id: str
    distance_m: float = Field(..., gt=0)
    freq_mhz: float = Field(..., gt=0)
    tx_height_m: float = Field(30.0, gt=0)
    rx_height_m: float = Field(1.5, gt=0)
    params: dict[str, object] = Field(default_factory=dict)


class PathLossResult(_Base):
    model_config = ConfigDict(
        extra="forbid", use_enum_values=True, validate_default=True,
        protected_namespaces=(),
    )

    model_id: str
    path_loss_db: float
    distance_m: float
    freq_mhz: float


# --- terrain line-of-sight / Fresnel ---------------------------------------
class ElevationPoint(_Base):
    lat: float
    lon: float
    elevation_m: float
    distance_m: float = 0.0   # distance from the first point along the path


class ElevationProfile(_Base):
    samples: int
    total_distance_m: float
    points: list[ElevationPoint] = Field(default_factory=list)
    # F67: honest terrain provenance. "dem" = real elevation data from the
    # provider; "flat_fallback" = the provider was unreachable and every point
    # was flattened to elevation_m=0 — callers MUST surface this, never treat
    # a flat-fallback result as if it were real terrain.
    terrain_source: Literal["dem", "flat_fallback"] = "dem"


class LosCheckRequest(_Base):
    """Terrain line-of-sight + Fresnel check between two endpoints.

    Supply ``profile`` directly (offline / pre-fetched), or omit it to have the
    server fetch terrain from the elevation provider for the given coordinates.
    """

    a_lat: float
    a_lon: float
    b_lat: float
    b_lon: float
    frequency_ghz: float = 5.8
    tx_height_m: float = 10.0
    rx_height_m: float = 5.0
    samples: int = 24
    profile: list[ElevationPoint] | None = None


class LosCheckResult(_Base):
    los_clear: bool
    fresnel_clear: bool
    worst_obstruction_m: float
    min_clearance_ratio: float
    distance_m: float
    profile: ElevationProfile | None = None


# --- point-to-point link planner (NG-RF-03) --------------------------------
class PtpRequest(_Base):
    """Plan a point-to-point link between two endpoints.

    Path loss is computed through the propagation registry (NG-RF-01) so the
    chosen ``model_id`` is honoured. LoS/Fresnel reuse the terrain profile:
    supply ``profile`` for offline/deterministic runs, else the server fetches
    it (degrading to flat terrain if the provider is offline)."""

    # ``model_id`` collides with Pydantic's protected ``model_`` namespace.
    model_config = ConfigDict(
        extra="forbid", use_enum_values=True, validate_default=True,
        protected_namespaces=(),
    )

    a_lat: float = Field(..., ge=-90, le=90)
    a_lon: float = Field(..., ge=-180, le=180)
    b_lat: float = Field(..., ge=-90, le=90)
    b_lon: float = Field(..., ge=-180, le=180)
    freq_mhz: float = Field(..., gt=0)
    tx_power_dbm: float = 20.0
    tx_gain_dbi: float = 0.0
    rx_gain_dbi: float = 0.0
    misc_loss_db: float = 0.0
    rx_sensitivity_dbm: float = -85.0
    tx_height_m: float = Field(10.0, gt=0)
    rx_height_m: float = Field(5.0, gt=0)
    model_id: str = "fspl"
    params: dict[str, object] = Field(default_factory=dict)
    samples: int = Field(24, ge=2, le=128)
    profile: list[ElevationPoint] | None = None


class PtpResult(_Base):
    model_config = ConfigDict(
        extra="forbid", use_enum_values=True, validate_default=True,
        protected_namespaces=(),
    )

    model_id: str
    distance_m: float
    eirp_dbm: float
    path_loss_db: float
    rssi_dbm: float
    fade_margin_db: float        # rssi - rx sensitivity
    los_clear: bool
    fresnel_clear: bool
    worst_obstruction_m: float
    min_clearance_ratio: float
    verdict: str                 # "clear" | "obstructed"
    link_ok: bool                # rssi >= sensitivity AND 60% Fresnel clear
    profile: ElevationProfile | None = None


# --- coverage raster (NG-RF-02) --------------------------------------------
class CoverageSite(_Base):
    """One transmitter in a coverage study. Radio fields left ``None`` inherit
    the request's ``technology`` preset."""

    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)
    height_m: float = Field(30.0, gt=0)
    tx_power_dbm: float | None = None
    freq_mhz: float | None = None
    tx_gain_dbi: float | None = None


class CoverageLegendItem(_Base):
    label: str
    min_dbm: float


class CoverageRasterRequest(_Base):
    """Best-server received-signal raster over a bounding box.

    Supply an explicit bbox (all four ``min/max_lat/lon``) OR
    ``center_lat``/``center_lon`` + ``radius_m`` (bbox derived). The grid is
    ``rows × cols`` cells and is capped at ``MAX_COVERAGE_CELLS`` (see
    ``services.wireless``) so the synchronous compute stays within budget."""

    model_config = ConfigDict(
        extra="forbid", use_enum_values=True, validate_default=True,
        protected_namespaces=(),
    )

    sites: list[CoverageSite] = Field(..., min_length=1)
    technology: str = "wifi_5ghz"
    model_id: str = "fspl"
    params: dict[str, object] = Field(default_factory=dict)
    rows: int = Field(32, ge=1)
    cols: int = Field(32, ge=1)
    # bounding box (give these four ...)
    min_lat: float | None = Field(None, ge=-90, le=90)
    min_lon: float | None = Field(None, ge=-180, le=180)
    max_lat: float | None = Field(None, ge=-90, le=90)
    max_lon: float | None = Field(None, ge=-180, le=180)
    # ... or a centre + radius)
    center_lat: float | None = Field(None, ge=-90, le=90)
    center_lon: float | None = Field(None, ge=-180, le=180)
    radius_m: float | None = Field(None, gt=0)
    rx_gain_dbi: float = 0.0
    misc_loss_db: float = 0.0
    rx_height_m: float = Field(1.5, gt=0)


class CoverageRasterResult(_Base):
    model_config = ConfigDict(
        extra="forbid", use_enum_values=True, validate_default=True,
        protected_namespaces=(),
    )

    model_id: str
    technology: str
    rows: int
    cols: int
    bounds: dict[str, float]           # min_lat / min_lon / max_lat / max_lon
    values: list[list[float]]          # best-server dBm, row-major (rows × cols)
    legend: list[CoverageLegendItem] = Field(default_factory=list)
    study_key: str                     # deterministic cache key (same in→same out)
    site_count: int


# --- point-to-multipoint sector planner (NG-RF-04) -------------------------
class PtmpCpe(_Base):
    """One client radio served by a sector. Give either ``lat``/``lon`` (bearing
    + distance derived from the AP) or explicit ``distance_m`` + ``bearing_deg``."""

    id: str
    lat: float | None = Field(None, ge=-90, le=90)
    lon: float | None = Field(None, ge=-180, le=180)
    distance_m: float | None = Field(None, gt=0)
    bearing_deg: float | None = Field(None, ge=0, lt=360)
    height_m: float = Field(5.0, gt=0)
    rx_gain_dbi: float = 0.0
    misc_loss_db: float = 0.0


class PtmpRequest(_Base):
    """Plan one AP sector serving a set of CPEs (NG-RF-04). Per CPE: in-beam test
    (bearing within azimuth ± beamwidth/2), path loss via the registry, RSSI, an
    MCS from the documented RSSI ladder, and a PHY throughput."""

    model_config = ConfigDict(
        extra="forbid", use_enum_values=True, validate_default=True,
        protected_namespaces=(),
    )

    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)
    height_m: float = Field(30.0, gt=0)
    azimuth_deg: float = Field(..., ge=0, lt=360)     # sector boresight
    beamwidth_deg: float = Field(..., gt=0, le=360)   # full -3 dB width
    downtilt_deg: float = 0.0                         # informational (not modelled)
    freq_mhz: float = Field(..., gt=0)
    bandwidth_mhz: float = Field(20.0, gt=0)
    tx_power_dbm: float = 23.0
    tx_gain_dbi: float = 16.0
    rx_sensitivity_dbm: float = -85.0
    model_id: str = "fspl"
    params: dict[str, object] = Field(default_factory=dict)
    cpes: list[PtmpCpe] = Field(..., min_length=1)


class PtmpCpeResult(_Base):
    cpe_id: str
    distance_m: float
    bearing_deg: float
    in_beam: bool
    path_loss_db: float
    rssi_dbm: float
    mcs: int | None                    # None = below the lowest MCS floor
    modulation: str | None
    throughput_mbps: float
    served: bool                       # in beam AND rssi ≥ sensitivity AND has an MCS


class PtmpResult(_Base):
    model_config = ConfigDict(
        extra="forbid", use_enum_values=True, validate_default=True,
        protected_namespaces=(),
    )

    model_id: str
    azimuth_deg: float
    beamwidth_deg: float
    freq_mhz: float
    bandwidth_mhz: float
    cpes: list[PtmpCpeResult] = Field(default_factory=list)
    served_count: int
    # sum_phy = contention-free sum of served PHY rates (upper bound); airtime_fair
    # = mean served rate (equal-airtime share of one shared sector medium).
    sum_phy_mbps: float
    airtime_fair_mbps: float


# --- auto product selection (NG-RF-05) -------------------------------------
class RadioCandidate(_Base):
    """A candidate radio (a symmetric pair uses the same model both ends). The
    device-types library carries no RF/price specs, so candidates are supplied by
    the caller; wiring specs into that library is a follow-up."""

    name: str
    tx_power_dbm: float
    antenna_gain_dbi: float
    rx_sensitivity_dbm: float
    cost: float = Field(..., gt=0)     # currency-agnostic; ranks margin-per-cost
    bandwidth_mhz: float = Field(20.0, gt=0)


class ProductSelectRequest(_Base):
    """Rank candidate radio pairs for a required distance + target throughput
    (NG-RF-05). Best link-margin-per-cost first; pass/fail vs the target."""

    model_config = ConfigDict(
        extra="forbid", use_enum_values=True, validate_default=True,
        protected_namespaces=(),
    )

    distance_m: float = Field(..., gt=0)
    freq_mhz: float = Field(..., gt=0)
    target_throughput_mbps: float = Field(..., ge=0)
    model_id: str = "fspl"
    params: dict[str, object] = Field(default_factory=dict)
    tx_height_m: float = Field(30.0, gt=0)
    rx_height_m: float = Field(5.0, gt=0)
    misc_loss_db: float = 0.0
    candidates: list[RadioCandidate] = Field(..., min_length=1)


class ProductSelectItem(_Base):
    name: str
    rssi_dbm: float
    margin_db: float                   # rssi - rx sensitivity
    predicted_throughput_mbps: float
    cost: float
    margin_per_cost: float
    link_closes: bool                  # margin ≥ 0 and an MCS is usable
    meets_target: bool                 # link_closes AND throughput ≥ target


class ProductSelectResult(_Base):
    model_config = ConfigDict(
        extra="forbid", use_enum_values=True, validate_default=True,
        protected_namespaces=(),
    )

    distance_m: float
    freq_mhz: float
    target_throughput_mbps: float
    ranked: list[ProductSelectItem] = Field(default_factory=list)


# --- RF study persistence (R4 cross-cutting) --------------------------------

class RfStudyKind(str, Enum):
    coverage = "coverage"
    ptp = "ptp"
    ptmp = "ptmp"
    product_select = "product_select"


class RfStudy(_Base):
    """A saved RF study: the request plus its *computed result*, snapshotted at
    save time. PtP's budget depends on a live elevation-service fetch that can
    drift or go offline later, so re-opening a study must return the stored
    result verbatim (no recompute) for it to "re-open identically"."""

    id: str
    project_id: str
    kind: RfStudyKind
    name: str = ""
    request: dict = Field(default_factory=dict)
    result: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_now)


class RfStudyCreate(_Base):
    project_id: str
    kind: RfStudyKind
    name: str = ""
    request: dict = Field(default_factory=dict)
    result: dict = Field(default_factory=dict)


# --- digital twin: drift-diff (NG-TW-03) ------------------------------------

class ImportSnapshot(_Base):
    """Raw config text as imported, keyed by node — one snapshot per node (latest wins)."""

    id: str
    node_id: str
    project_id: str
    vendor: str           # key into configimport.PARSERS
    text: str             # raw config as imported (size-capped at import boundary)
    imported_at: datetime = Field(default_factory=_now)


class DriftReport(_Base):
    """Canonical diff between the last-imported snapshot and the node's current intent."""

    node_id: str
    node_name: str
    has_snapshot: bool
    drifted: bool         # diff non-empty
    diff: str             # unified diff, "" when clean or no snapshot
    imported_at: datetime | None
