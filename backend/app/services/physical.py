"""Physical plant model (NG-PH-02/03).

The logical topology says *what connects to what*; the physical plant says *how
long the wire is and what it's made of*. Cable length feeds propagation delay,
and a run that exceeds a media's rated max length degrades the link to
``errored`` — the same teachable failure Packet Tracer shows when a student
runs 120 m of Cat6 (rated 100 m).

Everything here is a pure, deterministic function of the topology, so it slots
in at a single seam (``store.topology()``) and every downstream consumer — the
canvas view, the sim kernel, the lab CLI — sees the same physically-resolved
links without any of them knowing this module exists.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from app.models import Cable, CableMedia, LinkStatus, Node, Topology


@dataclass(frozen=True)
class MediaSpec:
    """Rated limits for a cable medium.

    ``ns_per_m`` is one-way propagation latency per metre (signal velocity):
    ~5.5 ns/m for copper (velocity factor ~0.6c), ~4.9 ns/m for glass
    (n ≈ 1.47). ``max_length_m`` is the standards-based channel limit past
    which the run degrades the link.
    """

    max_length_m: float
    ns_per_m: float


# Standards-based channel limits (teaching values). Copper twisted-pair caps at
# the 100 m TIA channel; multimode at OM3/OM4 10G reach; single-mode at a
# conservative 10 km; DAC/coax/GPON at their typical deployment reach.
CABLE_SPECS: dict[CableMedia, MediaSpec] = {
    CableMedia.cat5e: MediaSpec(max_length_m=100.0, ns_per_m=5.56),
    CableMedia.cat6: MediaSpec(max_length_m=100.0, ns_per_m=5.56),
    CableMedia.cat6a: MediaSpec(max_length_m=100.0, ns_per_m=5.56),
    CableMedia.mmf_om3: MediaSpec(max_length_m=300.0, ns_per_m=4.90),
    CableMedia.mmf_om4: MediaSpec(max_length_m=400.0, ns_per_m=4.90),
    CableMedia.smf_os2: MediaSpec(max_length_m=10_000.0, ns_per_m=4.90),
    CableMedia.dac: MediaSpec(max_length_m=10.0, ns_per_m=4.30),
    CableMedia.coax: MediaSpec(max_length_m=500.0, ns_per_m=4.55),
    CableMedia.gpon_drop: MediaSpec(max_length_m=20_000.0, ns_per_m=4.90),
}

# One-way propagation delay per metre, in milliseconds (schema Link.delay unit).
_NS_TO_MS = 1e-6


@dataclass(frozen=True)
class LinkPhysical:
    """The physical verdict for one logical link."""

    total_length_m: float
    added_delay_ms: float
    over_length: bool
    # media of the run that blew its length budget, for a human-readable reason
    over_media: CableMedia | None = None


def link_effects(cables: list[Cable]) -> LinkPhysical:
    """Fold one link's cable runs into a propagation delay + over-length verdict."""
    total_len = 0.0
    delay_ms = 0.0
    over = False
    over_media: CableMedia | None = None
    for c in cables:
        spec = CABLE_SPECS[c.media]
        total_len += c.length_m
        delay_ms += c.length_m * spec.ns_per_m * _NS_TO_MS
        if c.length_m > spec.max_length_m:
            over = True
            over_media = over_media or c.media
    return LinkPhysical(
        total_length_m=round(total_len, 3),
        added_delay_ms=round(delay_ms, 6),
        over_length=over,
        over_media=over_media,
    )


def plant_report(topo: Topology) -> dict[str, LinkPhysical]:
    """Per-link physical verdicts, keyed by link id (for TD / diagnostics)."""
    by_link: dict[str, list[Cable]] = defaultdict(list)
    for c in topo.cables:
        by_link[c.link_id].append(c)
    return {lid: link_effects(cs) for lid, cs in by_link.items()}


def unracked_nodes_by_site(topo: Topology) -> dict[str, list[Node]]:
    """Nodes with a site but no rack (outdoor placement, keputusan Surya
    2026-09-06 — ``Node.mount``), keyed by ``site_id``.

    The 2.5D elevation view (``plantAdapter.ts``) only ever iterates
    ``topo.racks``, so a node placed directly on a site (no rack) has never
    rendered anywhere and had no UI to edit its ``mount``. This is the read
    side of that gap — a plain per-site listing, not a 3D placement.
    """
    by_site: dict[str, list[Node]] = defaultdict(list)
    for n in topo.nodes:
        if n.rack_id is None and n.site_id is not None:
            by_site[n.site_id].append(n)
    return dict(by_site)


def apply_physical(topo: Topology) -> Topology:
    """Return a copy of ``topo`` with cable physics folded into its links.

    Adds propagation delay and escalates an ``up`` link to ``errored`` when any
    of its cable runs exceeds the media's rated max length. A link the operator
    already took ``down``/``admin_down`` is left as-is — physics doesn't override
    an explicit human choice. No-op (and no copy) when there are no cables.
    """
    if not topo.cables:
        return topo
    by_link: dict[str, list[Cable]] = defaultdict(list)
    for c in topo.cables:
        by_link[c.link_id].append(c)

    new_links = []
    for link in topo.links:
        cables = by_link.get(link.id)
        if not cables:
            new_links.append(link)
            continue
        eff = link_effects(cables)
        # ``status`` is a plain string here (models use ``use_enum_values``);
        # keep it a string so every consumer's ``str(status)`` check agrees.
        status = link.status
        if eff.over_length and status == LinkStatus.up:
            status = LinkStatus.errored.value
        new_links.append(
            link.model_copy(
                update={
                    "delay": round(link.delay + eff.added_delay_ms, 6),
                    "status": status,
                }
            )
        )
    return topo.model_copy(update={"links": new_links})
