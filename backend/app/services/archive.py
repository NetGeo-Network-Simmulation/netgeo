"""Project archive (NG-WS-03): export/import a whole project as a versioned
JSON envelope.

*Export* snapshots one project's persisted state — the project, its nodes (with
interfaces and rack placement), links, physical plant (sites / racks / cables),
scenarios and per-node config artifacts — into a self-describing envelope.

*Import* replays that envelope into a **fresh** project: every entity is minted
a new id and every cross-reference (``project_id``, the interface ids a link
points at via ``a_iface`` / ``b_iface``, ``Interface.peer_link_id``,
``Cable.link_id``, ``Node.rack_id``, ``Node.site_id``, ``Rack.site_id``,
``ConfigArtifact.node_id``)
is rewritten through the id maps. The result is internally consistent and cannot
collide with an existing copy of the same content — so an archive round-trips
across a fresh install (the R3 exit criterion).

Everything here is pure: it turns domain objects <-> plain dicts and never
touches the store. The endpoints in ``app/api/projects.py`` own persistence.
"""
from __future__ import annotations

from app.exceptions.base import ValidationError
from app.models import (
    Activity,
    Cable,
    ConfigArtifact,
    Link,
    Node,
    Project,
    Rack,
    Scenario,
    Site,
)
from app.utils.ids import new_id

ARCHIVE_FORMAT = "netgeo-archive"
ARCHIVE_VERSION = 1

# Sharable activity file (NG-EDU-03). A ``.netgeo-lab`` archive is just the
# activity model wrapped in a versioned envelope — the ``initial``/``answer``
# it carries are already NG-WS-03 archive envelopes, so no inner remap is
# needed here (student instantiation mints fresh ids from them later).
ACTIVITY_FORMAT = "netgeo-lab"
ACTIVITY_VERSION = 1

# envelope section -> model. project is handled separately (it seeds the import).
_SECTIONS: dict[str, type] = {
    "nodes": Node,
    "links": Link,
    "sites": Site,
    "racks": Rack,
    "cables": Cable,
    "scenarios": Scenario,
    "configs": ConfigArtifact,
}


def build_archive(bundle: dict) -> dict:
    """Serialize a raw per-project entity bundle (domain objects, as returned by
    ``MemoryRepository.export_project``) into a versioned JSON envelope."""
    env = {
        "format": ARCHIVE_FORMAT,
        "version": ARCHIVE_VERSION,
        "project": bundle["project"].model_dump(mode="json"),
    }
    for section in _SECTIONS:
        env[section] = [e.model_dump(mode="json") for e in bundle.get(section, [])]
    return env


def parse_archive(env: dict) -> dict:
    """Validate an archive envelope and rebuild its domain objects (original
    ids preserved). Raises :class:`ValidationError` (HTTP 422) on a malformed or
    unknown-format envelope rather than letting a 500 escape."""
    if not isinstance(env, dict) or env.get("format") != ARCHIVE_FORMAT:
        raise ValidationError("not a NetGeo archive (missing/blank 'format')")
    if env.get("version") != ARCHIVE_VERSION:
        raise ValidationError(f"unsupported archive version {env.get('version')!r}")
    if not isinstance(env.get("project"), dict):
        raise ValidationError("archive is missing its 'project'")
    try:
        parsed: dict = {"project": Project(**env["project"])}
        for section, model in _SECTIONS.items():
            items = env.get(section) or []
            if not isinstance(items, list):
                raise ValidationError(f"archive section '{section}' must be a list")
            parsed[section] = [model(**d) for d in items]
    except ValidationError:
        raise
    except Exception as exc:  # pydantic / type errors on a broken entity payload
        raise ValidationError(f"malformed archive entity: {exc}") from exc
    return parsed


def build_activity_archive(activity: Activity) -> dict:
    """Serialize one activity into a versioned ``.netgeo-lab`` envelope."""
    return {
        "format": ACTIVITY_FORMAT,
        "version": ACTIVITY_VERSION,
        "activity": activity.model_dump(mode="json"),
    }


def parse_activity_archive(env: dict) -> Activity:
    """Validate a ``.netgeo-lab`` envelope and rebuild its :class:`Activity`
    (original id preserved — the caller mints a fresh one on import). Raises
    :class:`ValidationError` (HTTP 422) on a malformed/wrong-format envelope."""
    if not isinstance(env, dict) or env.get("format") != ACTIVITY_FORMAT:
        raise ValidationError("not a NetGeo activity file (missing/blank 'format')")
    if env.get("version") != ACTIVITY_VERSION:
        raise ValidationError(f"unsupported activity version {env.get('version')!r}")
    if not isinstance(env.get("activity"), dict):
        raise ValidationError("activity file is missing its 'activity'")
    try:
        return Activity(**env["activity"])
    except ValidationError:
        raise
    except Exception as exc:  # pydantic / type errors on a broken payload
        raise ValidationError(f"malformed activity file: {exc}") from exc


def remap(parsed: dict, new_project_id: str) -> dict:
    """Return fresh, id-remapped domain objects ready to persist into project
    ``new_project_id``. Pure — assumes ``parsed`` already validated."""
    node_map = {n.id: new_id() for n in parsed["nodes"]}
    iface_map = {i.id: new_id() for n in parsed["nodes"] for i in n.interfaces}
    link_map = {l.id: new_id() for l in parsed["links"]}
    site_map = {s.id: new_id() for s in parsed["sites"]}
    rack_map = {r.id: new_id() for r in parsed["racks"]}

    def endpoint(ref: str) -> str:
        # a link endpoint is an interface id, or (legacy payloads) a node id;
        # anything unresolved is kept verbatim.
        return iface_map.get(ref) or node_map.get(ref) or ref

    sites = [
        s.model_copy(update={"id": site_map[s.id], "project_id": new_project_id})
        for s in parsed["sites"]
    ]
    racks = [
        r.model_copy(
            update={
                "id": rack_map[r.id],
                "project_id": new_project_id,
                "site_id": site_map.get(r.site_id) if r.site_id else None,
            }
        )
        for r in parsed["racks"]
    ]
    nodes = []
    for n in parsed["nodes"]:
        ifaces = [
            i.model_copy(
                update={
                    "id": iface_map[i.id],
                    "node_id": node_map[n.id],
                    "peer_link_id": link_map.get(i.peer_link_id)
                    if i.peer_link_id
                    else None,
                }
            )
            for i in n.interfaces
        ]
        nodes.append(
            n.model_copy(
                update={
                    "id": node_map[n.id],
                    "project_id": new_project_id,
                    "interfaces": ifaces,
                    "rack_id": rack_map.get(n.rack_id) if n.rack_id else None,
                    "site_id": site_map.get(n.site_id) if n.site_id else None,
                }
            )
        )
    links = [
        l.model_copy(
            update={
                "id": link_map[l.id],
                "project_id": new_project_id,
                "a_iface": endpoint(l.a_iface),
                "b_iface": endpoint(l.b_iface),
            }
        )
        for l in parsed["links"]
    ]
    cables = [
        c.model_copy(
            update={
                "id": new_id(),
                "project_id": new_project_id,
                "link_id": link_map.get(c.link_id, c.link_id),
            }
        )
        for c in parsed["cables"]
    ]
    scenarios = [
        s.model_copy(update={"id": new_id(), "project_id": new_project_id})
        for s in parsed["scenarios"]
    ]
    configs = [
        c.model_copy(
            update={"id": new_id(), "node_id": node_map.get(c.node_id, c.node_id)}
        )
        for c in parsed["configs"]
    ]
    # Persistence order matters: sites -> racks (ref sites) -> nodes (ref racks)
    # -> links -> cables (add_cable requires the link to exist).
    return {
        "sites": sites,
        "racks": racks,
        "nodes": nodes,
        "links": links,
        "cables": cables,
        "scenarios": scenarios,
        "configs": configs,
    }
