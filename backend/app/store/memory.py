"""In-memory repository (v0.1 default).

A process-local store so the backend is **runnable and importable without a live
PostgreSQL** — matching the secureops/storagehub "imports OK" smoke-test style.
The async PostgreSQL repository (``infra/db/schema.sql``) is the production
target; both implement the same :class:`Repository` surface so the API layer is
storage-agnostic. Swap via ``app.store.get_repo``.

Thread-safety: guarded by a single ``asyncio.Lock`` — adequate for the single
-worker dev server. Production uses Postgres row-level guarantees instead.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from collections import defaultdict
from pathlib import Path

from app.core.config import get_settings
from app.models import (
    Activity,
    ConfigArtifact,
    Cable,
    FiberPath,
    GradeResult,
    ImportSnapshot,
    Link,
    Node,
    Project,
    Rack,
    RfStudy,
    Scenario,
    Site,
    Topology,
)
from app.services.physical import apply_physical
from app.utils.ids import new_id

logger = logging.getLogger(__name__)


class NotFound(KeyError):
    """Raised when a resource id does not resolve. Mapped to HTTP 404."""


_TRAILING_DIGITS = re.compile(r"^(.*?)(\d+)$")

# (attribute, model class, dict key field) — drives disk load/save (S2 PERSIST-01).
# Every entry is a pure-data pydantic model; MemoryRepository never holds an
# engine/Network/LabManager object, so there is nothing else to persist.
_PERSISTED: list[tuple[str, type, str]] = [
    ("_projects", Project, "id"),
    ("_nodes", Node, "id"),
    ("_links", Link, "id"),
    ("_scenarios", Scenario, "id"),
    ("_configs", ConfigArtifact, "id"),
    ("_sites", Site, "id"),
    ("_racks", Rack, "id"),
    ("_cables", Cable, "id"),
    ("_fiber_paths", FiberPath, "id"),
    ("_rf_studies", RfStudy, "id"),
    ("_activities", Activity, "id"),
    ("_grade_results", GradeResult, "id"),
    ("_import_snapshots", ImportSnapshot, "node_id"),  # keyed by node, not its own id
]


def _unique_node_name(proposed: str, taken: set[str]) -> str:
    """Return ``proposed`` if free, else the next non-colliding name for its base.

    The base is ``proposed`` with any trailing digits stripped (so ``EdgeRouter1``
    -> base ``EdgeRouter``). We then pick ``base`` + one above the highest existing
    suffix for that base, scanning *all* current names rather than trusting a
    counter — this keeps auto-generated defaults unique even when several clients
    create nodes concurrently or the client suggests a stale number.

    Manually chosen names are unaffected: this only rewrites a name that already
    collides with an existing node in the same project.
    """
    if proposed not in taken:
        return proposed
    m = _TRAILING_DIGITS.match(proposed)
    base = m.group(1) if m else proposed
    suffix_re = re.compile(rf"^{re.escape(base)}(\d+)$")
    highest = 0
    for name in taken:
        sm = suffix_re.match(name)
        if sm:
            highest = max(highest, int(sm.group(1)))
    n = highest + 1
    candidate = f"{base}{n}"
    while candidate in taken:
        n += 1
        candidate = f"{base}{n}"
    return candidate


class MemoryRepository:
    def __init__(self, state_path: str | Path | None = None) -> None:
        self._lock = asyncio.Lock()
        self._projects: dict[str, Project] = {}
        self._nodes: dict[str, Node] = {}
        self._links: dict[str, Link] = {}
        self._scenarios: dict[str, Scenario] = {}
        self._configs: dict[str, ConfigArtifact] = {}
        self._configs_by_node: dict[str, list[str]] = defaultdict(list)
        # Physical plant (NG-PH-01/02)
        self._sites: dict[str, Site] = {}
        self._racks: dict[str, Rack] = {}
        self._cables: dict[str, Cable] = {}
        # Fiber plant (NG-FI-01)
        self._fiber_paths: dict[str, FiberPath] = {}
        # RF study persistence (R4 cross-cutting)
        self._rf_studies: dict[str, RfStudy] = {}
        # Education activities (NG-EDU-01)
        self._activities: dict[str, Activity] = {}
        # Graded attempts (NG-EDU-03), keyed by result id.
        self._grade_results: dict[str, GradeResult] = {}
        # Import snapshots (NG-TW-03): latest snapshot per node_id (re-import overwrites).
        self._import_snapshots: dict[str, ImportSnapshot] = {}
        # Disk persistence (S2 PERSIST-01). state_path=None -> configured default;
        # "" (e.g. NETGEO_STATE_STORE="") disables it — same convention as auth store.
        raw = get_settings().NETGEO_STATE_STORE if state_path is None else state_path
        self._state_path = Path(raw).expanduser() if raw else None
        self._load()

    def _load(self) -> None:
        if self._state_path is None or not self._state_path.is_file():
            return
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
            for attr, model, key in _PERSISTED:
                for raw in data.get(attr, []):
                    obj = model.model_validate(raw)
                    getattr(self, attr)[getattr(obj, key)] = obj
            for cfg in self._configs.values():
                self._configs_by_node[cfg.node_id].append(cfg.id)
            logger.info("Loaded state from %s", self._state_path)
        except Exception:
            logger.warning(
                "State store %s is corrupt — starting empty.", self._state_path, exc_info=True
            )

    def _save(self) -> None:
        # ponytail: synchronous write on every mutation per the S2 PERSIST-01
        # brief; revisit with a debounce/background task only if profiling on a
        # real project shows this write is actually slow.
        if self._state_path is None:
            return
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                attr: [obj.model_dump(mode="json") for obj in getattr(self, attr).values()]
                for attr, _, _ in _PERSISTED
            }
            tmp = self._state_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload), encoding="utf-8")
            os.chmod(tmp, 0o600)
            tmp.replace(self._state_path)
        except Exception:
            logger.error("Failed to persist state to %s", self._state_path, exc_info=True)

    # --- projects -----------------------------------------------------------
    async def list_projects(self) -> list[Project]:
        return list(self._projects.values())

    async def get_project(self, pid: str) -> Project:
        try:
            return self._projects[pid]
        except KeyError as exc:
            raise NotFound(pid) from exc

    async def create_project(self, name: str, description: str = "") -> Project:
        async with self._lock:
            proj = Project(id=new_id(), name=name, description=description)
            self._projects[proj.id] = proj
            self._save()
            return proj

    async def export_project(self, pid: str) -> dict:
        """Raw per-project entities for archival (NG-WS-03).

        Unlike :meth:`topology` this does *not* fold cable physics into links:
        the archive must capture links exactly as stored so a round-trip does
        not double-apply propagation delay / re-derive ``errored`` on import.
        Raises :class:`NotFound` if the project id is unknown.
        """
        proj = await self.get_project(pid)  # raises NotFound
        node_ids = {n.id for n in self._nodes.values() if n.project_id == pid}
        return {
            "project": proj,
            "nodes": [n for n in self._nodes.values() if n.project_id == pid],
            "links": [l for l in self._links.values() if l.project_id == pid],
            "sites": [s for s in self._sites.values() if s.project_id == pid],
            "racks": [rk for rk in self._racks.values() if rk.project_id == pid],
            "cables": [c for c in self._cables.values() if c.project_id == pid],
            "scenarios": [s for s in self._scenarios.values() if s.project_id == pid],
            "configs": [c for c in self._configs.values() if c.node_id in node_ids],
        }

    async def topology(self, pid: str) -> Topology:
        proj = await self.get_project(pid)
        nodes = [n for n in self._nodes.values() if n.project_id == pid]
        links = [l for l in self._links.values() if l.project_id == pid]
        sites = [s for s in self._sites.values() if s.project_id == pid]
        racks = [rk for rk in self._racks.values() if rk.project_id == pid]
        cables = [c for c in self._cables.values() if c.project_id == pid]
        topo = Topology(
            project=proj, nodes=nodes, links=links,
            sites=sites, racks=racks, cables=cables,
        )
        # Fold cable physics (propagation delay + over-length errors, NG-PH-03)
        # into the links so every consumer — view, sim, lab — is consistent.
        return apply_physical(topo)

    # --- nodes --------------------------------------------------------------
    async def get_node(self, nid: str) -> Node:
        try:
            return self._nodes[nid]
        except KeyError as exc:
            raise NotFound(nid) from exc

    async def add_node(self, node: Node) -> Node:
        async with self._lock:
            # Enforce unique node names within a project. The store is the
            # authority so uniqueness holds even with multiple clients; the
            # final (possibly rewritten) name is returned to the caller.
            taken = {
                n.name for n in self._nodes.values() if n.project_id == node.project_id
            }
            unique = _unique_node_name(node.name, taken)
            if unique != node.name:
                node = node.model_copy(update={"name": unique})
            self._nodes[node.id] = node
            self._save()
            return node

    # Node fields whose value may legitimately be None (i.e. "clear this field"),
    # as opposed to fields where None simply means "not provided in this PATCH".
    _NULLABLE_NODE_FIELDS: frozenset[str] = frozenset(
        {"lat", "lon", "radio", "intent", "config_ref", "site_id"}
    )

    async def update_node(self, nid: str, patch: dict) -> Node:
        async with self._lock:
            node = await self.get_node(nid)
            # Allow None only for genuinely nullable fields (clearing a geo position,
            # removing a radio, etc.).  For non-nullable fields (name, nos, mode, …)
            # a None value from a partial PATCH body means "not provided", so skip it.
            filtered = {
                k: v
                for k, v in patch.items()
                if v is not None or k in self._NULLABLE_NODE_FIELDS
            }
            # site_id is the truth. A node placed into a rack inherits the rack's
            # site so the two can never disagree; reject rather than reconcile
            # afterwards (NG-PH-01 A1.3).
            if "rack_id" in filtered and filtered["rack_id"] is not None:
                rack = self._racks.get(filtered["rack_id"])
                if rack is not None and rack.site_id is not None:
                    # effective site_id = what this patch explicitly says, else
                    # whatever the node already has — either way, it must not
                    # disagree with the target rack's site.
                    current = filtered["site_id"] if "site_id" in filtered else node.site_id
                    if current is not None and current != rack.site_id:
                        from app.exceptions.base import Conflict
                        raise Conflict(
                            f"node site_id {current!r} conflicts with rack "
                            f"{filtered['rack_id']!r}'s site {rack.site_id!r}"
                        )
                    filtered["site_id"] = rack.site_id
            updated = node.model_copy(update=filtered)
            self._nodes[nid] = updated
            self._save()
            return updated

    async def delete_node(self, nid: str) -> None:
        async with self._lock:
            node = await self.get_node(nid)
            iface_ids = {i.id for i in node.interfaces}
            # cascade: drop links touching this node's interfaces
            dead = [lid for lid, l in self._links.items()
                    if l.a_iface in iface_ids or l.b_iface in iface_ids]
            for lid in dead:
                del self._links[lid]
                self._drop_cables_for_link(lid)
            # cascade: drop import snapshot for this node (NG-TW-03)
            self._import_snapshots.pop(nid, None)
            del self._nodes[nid]
            self._save()

    # --- links --------------------------------------------------------------
    async def add_link(self, link: Link) -> Link:
        async with self._lock:
            # Guard: an interface may only be an endpoint of one live link at a
            # time (the correct invariant is per-interface, not per-node-pair —
            # multi-link LAG between the same two nodes is intentionally allowed,
            # as long as each physical interface is only used once).
            occupied = {l.a_iface for l in self._links.values()} | {
                l.b_iface for l in self._links.values()
            }
            busy = [iid for iid in (link.a_iface, link.b_iface) if iid in occupied]
            if busy:
                from app.exceptions.base import Conflict
                raise Conflict(
                    f"interface(s) already in use by another link: {', '.join(busy)}"
                )
            self._links[link.id] = link
            self._save()
            return link

    async def get_link(self, lid: str) -> Link:
        try:
            return self._links[lid]
        except KeyError as exc:
            raise NotFound(lid) from exc

    async def update_link(self, lid: str, patch: dict) -> Link:
        async with self._lock:
            link = await self.get_link(lid)
            updated = link.model_copy(update={k: v for k, v in patch.items() if v is not None})
            self._links[lid] = updated
            self._save()
            return updated

    async def delete_link(self, lid: str) -> None:
        async with self._lock:
            await self.get_link(lid)
            del self._links[lid]
            self._drop_cables_for_link(lid)
            self._save()

    # --- physical plant (NG-PH-01/02) ---------------------------------------
    def _drop_cables_for_link(self, lid: str) -> None:
        """Cables realize a link; a link's removal takes its cables with it.
        Caller holds the lock (or is inside a cascading delete)."""
        for cid in [c.id for c in self._cables.values() if c.link_id == lid]:
            del self._cables[cid]

    async def add_site(self, site: Site) -> Site:
        async with self._lock:
            self._sites[site.id] = site
            self._save()
            return site

    async def list_sites(self, pid: str) -> list[Site]:
        return [s for s in self._sites.values() if s.project_id == pid]

    async def get_site(self, sid: str) -> Site:
        try:
            return self._sites[sid]
        except KeyError as exc:
            raise NotFound(sid) from exc

    async def update_site(self, sid: str, patch: dict) -> Site:
        async with self._lock:
            site = self._sites.get(sid)
            if site is None:
                raise NotFound(sid)
            # model_copy skips validation — revalidate the merged dict so a bad
            # lat/lon in the patch is rejected instead of stored raw.
            updated = Site.model_validate(
                {**site.model_dump(), **{k: v for k, v in patch.items() if v is not None}}
            )
            self._sites[sid] = updated
            self._save()
            return updated

    async def delete_site(self, sid: str) -> None:
        async with self._lock:
            if sid not in self._sites:
                raise NotFound(sid)
            del self._sites[sid]
            # Racks/nodes keep working without a site — clear the back-reference
            # rather than cascading a delete the user did not ask for.
            for rid, rack in self._racks.items():
                if rack.site_id == sid:
                    self._racks[rid] = rack.model_copy(update={"site_id": None})
            for nid, node in self._nodes.items():
                if node.site_id == sid:
                    self._nodes[nid] = node.model_copy(update={"site_id": None})
            self._save()

    async def add_rack(self, rack: Rack) -> Rack:
        async with self._lock:
            self._racks[rack.id] = rack
            self._save()
            return rack

    async def add_cable(self, cable: Cable) -> Cable:
        async with self._lock:
            if cable.link_id not in self._links:
                raise NotFound(cable.link_id)
            self._cables[cable.id] = cable
            self._save()
            return cable

    async def get_cable(self, cid: str) -> Cable:
        try:
            return self._cables[cid]
        except KeyError as exc:
            raise NotFound(cid) from exc

    async def update_cable(self, cid: str, patch: dict) -> Cable:
        async with self._lock:
            cable = self._cables.get(cid)
            if cable is None:
                raise NotFound(cid)
            updated = cable.model_copy(
                update={k: v for k, v in patch.items() if v is not None}
            )
            self._cables[cid] = updated
            self._save()
            return updated

    async def delete_cable(self, cid: str) -> None:
        async with self._lock:
            if cid not in self._cables:
                raise NotFound(cid)
            del self._cables[cid]
            self._save()

    # --- scenarios ----------------------------------------------------------
    async def list_scenarios(self, pid: str) -> list[Scenario]:
        return [s for s in self._scenarios.values() if s.project_id == pid]

    async def add_scenario(self, scenario: Scenario) -> Scenario:
        async with self._lock:
            self._scenarios[scenario.id] = scenario
            self._save()
            return scenario

    # --- education activities (NG-EDU-01) -----------------------------------
    async def list_activities(self) -> list[Activity]:
        return list(self._activities.values())

    async def get_activity(self, aid: str) -> Activity:
        try:
            return self._activities[aid]
        except KeyError as exc:
            raise NotFound(aid) from exc

    async def add_activity(self, activity: Activity) -> Activity:
        async with self._lock:
            self._activities[activity.id] = activity
            self._save()
            return activity

    async def delete_activity(self, aid: str) -> None:
        async with self._lock:
            if aid not in self._activities:
                raise NotFound(aid)
            del self._activities[aid]
            self._save()

    # --- graded attempts (NG-EDU-03) ----------------------------------------
    async def add_grade_result(self, result: GradeResult) -> GradeResult:
        async with self._lock:
            self._grade_results[result.id] = result
            self._save()
            return result

    async def list_grade_results(self, activity_id: str) -> list[GradeResult]:
        return [
            g for g in self._grade_results.values() if g.activity_id == activity_id
        ]

    # --- fiber plant (NG-FI-01) ---------------------------------------------
    async def list_fiber_paths(self, pid: str) -> list[FiberPath]:
        return [p for p in self._fiber_paths.values() if p.project_id == pid]

    async def get_fiber_path(self, fid: str) -> FiberPath:
        try:
            return self._fiber_paths[fid]
        except KeyError as exc:
            raise NotFound(fid) from exc

    async def add_fiber_path(self, path: FiberPath) -> FiberPath:
        async with self._lock:
            self._fiber_paths[path.id] = path
            self._save()
            return path

    async def update_fiber_path(self, fid: str, patch: dict) -> FiberPath:
        async with self._lock:
            path = self._fiber_paths.get(fid)
            if path is None:
                raise NotFound(fid)
            # model_copy skips validation → patched dicts would stay raw dicts
            # and crash loss_budget; revalidate the merged path instead.
            updated = FiberPath.model_validate(
                {**path.model_dump(), **{k: v for k, v in patch.items() if v is not None}}
            )
            self._fiber_paths[fid] = updated
            self._save()
            return updated

    async def delete_fiber_path(self, fid: str) -> None:
        async with self._lock:
            if fid not in self._fiber_paths:
                raise NotFound(fid)
            del self._fiber_paths[fid]
            self._save()

    # --- RF study persistence (R4 cross-cutting) ----------------------------
    async def list_rf_studies(self, pid: str) -> list[RfStudy]:
        return [s for s in self._rf_studies.values() if s.project_id == pid]

    async def get_rf_study(self, sid: str) -> RfStudy:
        try:
            return self._rf_studies[sid]
        except KeyError as exc:
            raise NotFound(sid) from exc

    async def add_rf_study(self, study: RfStudy) -> RfStudy:
        async with self._lock:
            self._rf_studies[study.id] = study
            self._save()
            return study

    async def delete_rf_study(self, sid: str) -> None:
        async with self._lock:
            if sid not in self._rf_studies:
                raise NotFound(sid)
            del self._rf_studies[sid]
            self._save()

    # --- import snapshots (NG-TW-03) ----------------------------------------
    async def save_import_snapshot(self, snap: ImportSnapshot) -> ImportSnapshot:
        async with self._lock:
            self._import_snapshots[snap.node_id] = snap
            self._save()
            return snap

    async def get_import_snapshot(self, node_id: str) -> ImportSnapshot | None:
        return self._import_snapshots.get(node_id)

    async def delete_import_snapshot(self, node_id: str) -> None:
        async with self._lock:
            self._import_snapshots.pop(node_id, None)
            self._save()

    # --- config artifacts (append-only history) -----------------------------
    async def add_config(self, artifact: ConfigArtifact) -> ConfigArtifact:
        async with self._lock:
            self._configs[artifact.id] = artifact
            self._configs_by_node[artifact.node_id].append(artifact.id)
            self._save()
            return artifact

    async def configs_for_node(self, nid: str) -> list[ConfigArtifact]:
        return [self._configs[i] for i in self._configs_by_node.get(nid, [])]
