"""Offline glyph (font PBF) serving for vector-basemap text labels
(OFFLINE-MAP-5).

MapLibre needs a glyph server (``{fontstack}/{range}.pbf``) to render
`symbol` layers on a vector source — OFFLINE-MAP-4 shipped the vector
basemap without labels because there was no local glyph source, and calling
a public glyph CDN would break the "zero external requests while offline"
contract (frontend/src/config/mapTiles.ts). This module serves them from
files bundled with the app instead, the same way offline_maps.py serves
basemap tiles from a local file — no runtime dependency, just static bytes
read off disk.

Fonts: Noto Sans Regular/Bold, SIL Open Font License 1.1 (see
``data/glyphs/OFL.txt``, shipped alongside the PBFs it covers). Only
codepoint range 0-255 (Basic Latin + Latin-1 Supplement) was generated:
Indonesian place/road/water names use no characters outside it, so ~156KB
total covers every label NetGeo renders — a general-purpose glyph server
covering the full BMP would need ~30x that many range files per weight.
Regenerating: ``fontnik`` (native addon, prebuilt binary via npm, no build
tools needed) `.range({font, start: 0, end: 255}, cb)` per weight — see the
git history of this file's commit for the exact one-off script used.
"""
from __future__ import annotations

from pathlib import Path

_GLYPH_DIR = Path(__file__).resolve().parent.parent / "data" / "glyphs"

# Every range actually generated, per weight folder under _GLYPH_DIR.
_RANGE = "0-255"


def resolve_weight(requested_fontstack: str) -> str:
    """Map any requested fontstack — including a comma-joined fallback list
    MapLibre builds from a style's `text-font`, e.g. "Noto Sans Regular,
    Arial Unicode MS Regular" — to one of the two weights actually bundled.
    Bold wins if any requested name mentions it; Regular is the default.
    Covers both what this app's own style requests (config/mapTiles.ts
    `LABEL_FONT_REGULAR`/`LABEL_FONT_BOLD`) and anything a foreign
    OpenMapTiles-style JSON might ask for."""
    return "noto-sans-bold" if "bold" in requested_fontstack.lower() else "noto-sans-regular"


def get_range(fontstack: str, start: int, end: int) -> bytes | None:
    """Return the glyph PBF bytes for the resolved fontstack + codepoint
    range, or None if that exact range wasn't generated (only 0-255 is)."""
    if f"{start}-{end}" != _RANGE:
        return None
    path = _GLYPH_DIR / resolve_weight(fontstack) / f"{_RANGE}.pbf"
    return path.read_bytes() if path.is_file() else None
