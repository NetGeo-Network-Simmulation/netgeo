"""Project report + bill of materials (NG-CFG-02 / NG-FI-04).

Pure aggregation over a project's stored data. Reuses ``services.fiber`` and
``services.physical`` for the optical + rack math — this module only tallies and
renders. The HTML is a stdlib ``string.Template`` (no templating dependency),
self-contained and print-friendly.
"""
from __future__ import annotations

from collections import Counter
from html import escape
from string import Template

from app.models import BomItem, FiberPath, LossBudget, Topology
from app.services.fiber import loss_budget

# ponytail: per-kind wattage estimate (mirrors the rack view's KIND_WATTS) —
# swap for real chassis watts when the device library (NG-DL-01) exposes them.
_KIND_WATTS = {
    "router": 250, "switch": 150, "firewall": 200, "olt": 300,
    "server": 400, "host": 100, "ap": 20, "cloud": 0,
}


def _watts(kind: str) -> int:
    return _KIND_WATTS.get(kind, 150)


def build_bom(topo: Topology, fiber_paths: list[FiberPath]) -> list[BomItem]:
    """Tally a project's bill of materials, deterministically ordered."""
    items: list[BomItem] = []

    for kind, n in sorted(Counter(node.kind for node in topo.nodes).items()):
        items.append(BomItem(category="Devices", item=str(kind), qty=n, unit="pcs"))

    # cables by media: count + total length
    cable_len: dict[str, float] = {}
    cable_qty: Counter = Counter()
    for c in topo.cables:
        cable_qty[c.media] += 1
        cable_len[c.media] = cable_len.get(c.media, 0.0) + c.length_m
    for media in sorted(cable_qty):
        items.append(BomItem(
            category="Cabling", item=str(media), qty=cable_qty[media], unit="run",
            notes=f"{round(cable_len[media], 1)} m total",
        ))

    # fiber passive elements across all paths
    splitters: Counter = Counter()
    connectors = splices = 0
    fiber_len = 0.0
    for p in fiber_paths:
        for e in p.elements:
            if e.kind == "splitter":
                splitters[e.split_ratio] += 1
            elif e.kind == "connector":
                connectors += 1
            elif e.kind == "splice":
                splices += 1
            elif e.kind == "fiber":
                fiber_len += e.length_m
    for ratio in sorted(splitters):
        items.append(BomItem(category="Fiber plant", item=f"1:{ratio} splitter", qty=splitters[ratio], unit="pcs"))
    if connectors:
        items.append(BomItem(category="Fiber plant", item="Connector", qty=connectors, unit="pcs"))
    if splices:
        items.append(BomItem(category="Fiber plant", item="Splice", qty=splices, unit="pcs"))
    if fiber_len:
        items.append(BomItem(category="Fiber plant", item="Fiber", qty=round(fiber_len, 1), unit="m"))

    if topo.sites:
        items.append(BomItem(category="Facilities", item="Site", qty=len(topo.sites), unit="pcs"))
    if topo.racks:
        items.append(BomItem(category="Facilities", item="Rack", qty=len(topo.racks), unit="pcs"))

    return items


def _rows(rows: list[list[str]]) -> str:
    return "".join("<tr>" + "".join(f"<td>{escape(str(c))}</td>" for c in r) + "</tr>" for r in rows)


def _section(title: str, body: str) -> str:
    return f"<section><h2>{escape(title)}</h2>{body}</section>"


def _none(msg: str = "none") -> str:
    return f'<p class="none">{escape(msg)}</p>'


_PAGE = Template(
    """<!doctype html><html><head><meta charset="utf-8"><title>$title</title>
<style>
@page { size: A4; margin: 18mm; }
body { font: 13px/1.5 -apple-system, Segoe UI, Roboto, sans-serif; color: #1a1a1a; }
h1 { font-size: 22px; margin: 0 0 2px; } h2 { font-size: 15px; border-bottom: 2px solid #333; padding-bottom: 3px; margin-top: 22px; }
table { border-collapse: collapse; width: 100%; margin: 6px 0; } th, td { border: 1px solid #ccc; padding: 4px 7px; text-align: left; }
th { background: #f0f0f0; } .none { color: #888; font-style: italic; } .sub { color: #666; margin: 0 0 12px; }
</style></head><body>
<h1>$title</h1><p class="sub">$subtitle</p>
$sections
</body></html>"""
)


def render_report_html(
    project, topo: Topology, budgets: list[tuple[FiberPath, LossBudget]], bom: list[BomItem]
) -> str:
    """Assemble the print-ready project report (NG-CFG-02)."""
    secs: list[str] = []

    # summary
    secs.append(_section("Summary", "<table>" + _rows([
        ["Devices", str(len(topo.nodes))], ["Links", str(len(topo.links))],
        ["Sites", str(len(topo.sites))], ["Racks", str(len(topo.racks))],
        ["Cables", str(len(topo.cables))], ["Fiber paths", str(len(budgets))],
    ]) + "</table>"))

    # device inventory
    inv = _rows([[n.name, n.kind, n.nos, f"{_watts(n.kind)} W"] for n in topo.nodes]) if topo.nodes else ""
    secs.append(_section("Device inventory",
        f"<table><tr><th>Name</th><th>Kind</th><th>NOS</th><th>Power</th></tr>{inv}</table>" if inv else _none()))

    # rack sheets + power/heat per rack
    if topo.racks:
        blocks = []
        for rack in topo.racks:
            placed = sorted((n for n in topo.nodes if n.rack_id == rack.id),
                            key=lambda n: n.ru_start or 0)
            watts = sum(_watts(n.kind) for n in placed)
            rows = _rows([[f"U{n.ru_start}-U{(n.ru_start or 0) + (n.ru_span or 1) - 1}", n.name, n.kind] for n in placed])
            body = (f"<table><tr><th>RU</th><th>Device</th><th>Kind</th></tr>{rows}</table>"
                    if placed else _none("empty rack"))
            blocks.append(f"<h3>{escape(rack.name)} ({rack.ru_height}U) — {watts} W · {round(watts * 3.412)} BTU/hr</h3>{body}")
        secs.append(_section("Rack sheets", "".join(blocks)))
    else:
        secs.append(_section("Rack sheets", _none()))

    # fiber loss budgets
    if budgets:
        rows = _rows([[p.name, p.gpon_class, f"{b.total_loss_db} dB", f"{b.budget_db} dB",
                       f"{b.margin_db} dB", "PASS" if b.passed else "FAIL"] for p, b in budgets])
        secs.append(_section("Fiber loss budgets",
            f"<table><tr><th>Path</th><th>Class</th><th>Loss</th><th>Budget</th><th>Margin</th><th>Result</th></tr>{rows}</table>"))
    else:
        secs.append(_section("Fiber loss budgets", _none()))

    # BOM
    if bom:
        rows = _rows([[i.category, i.item, i.qty, i.unit, i.notes] for i in bom])
        secs.append(_section("Bill of materials",
            f"<table><tr><th>Category</th><th>Item</th><th>Qty</th><th>Unit</th><th>Notes</th></tr>{rows}</table>"))
    else:
        secs.append(_section("Bill of materials", _none()))

    # RF: placeholder until the coverage overlay lands
    secs.append(_section("RF studies", _none("RF coverage maps pending the map overlay (deferred)")))

    return _PAGE.substitute(
        title=escape(f"NetGeo Project Report — {project.name}"),
        subtitle=escape(f"Project {project.id} · {len(topo.nodes)} devices"),
        sections="".join(secs),
    )


def project_report(project, topo: Topology, fiber_paths: list[FiberPath]) -> tuple[list[BomItem], str]:
    """BOM + rendered HTML for a project (reuses fiber loss_budget)."""
    bom = build_bom(topo, fiber_paths)
    budgets = [(p, loss_budget(p)) for p in fiber_paths]
    return bom, render_report_html(project, topo, budgets, bom)


__all__ = ["build_bom", "project_report", "render_report_html"]
