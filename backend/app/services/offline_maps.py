"""Offline map tiles (OFFLINE-MAP-1).

Serves raster basemap tiles from a local MBTiles file when the operator has
one installed, so the native full-offline distribution variant (see memory
netgeo-distribusi-lima-bentuk, variant #1) doesn't need internet access for
its basemap. When no valid local file is present, ``get_status`` reports
``available: false`` and the frontend keeps using its existing online tile
providers (frontend/src/config/mapTiles.ts) — this module never talks to
Esri/OSM itself, it only tells the caller which basemap is live.

Format: MBTiles (SQLite, https://github.com/mapbox/mbtiles-spec) — chosen
over PMTiles because MBTiles is readable with the Python standard library's
``sqlite3`` module alone. Reading PMTiles would need either a third-party
parser package or hand-rolled binary directory/varint/gzip decoding for its
custom layout — MBTiles is "just a SQLite file" and every common region-tile
builder (mbutil, tippecanoe, planetiler, QGIS export) can produce one.
Stdlib beats a new dependency here.

Tiles are read per-request via SQL — the file (can be hundreds of MB) is
never loaded into memory as a whole.
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from app.core.config import get_settings

logger = logging.getLogger(__name__)


def _resolve_path() -> Path | None:
    raw = get_settings().NETGEO_OFFLINE_MAP_PATH
    if not raw:
        return None
    path = Path(raw).expanduser()
    return path if path.is_file() else None


def _connect(path: Path) -> sqlite3.Connection:
    # Read-only URI connection: never mutates the file, safe alongside other
    # readers/writers of it.
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def _metadata(conn: sqlite3.Connection) -> dict[str, str]:
    try:
        return dict(conn.execute("SELECT name, value FROM metadata").fetchall())
    except sqlite3.Error:
        return {}


def get_status() -> dict:
    """What the frontend needs to decide online vs. offline basemap."""
    path = _resolve_path()
    if path is None:
        return {"available": False, "path": None, "region": None, "attribution": None}

    try:
        conn = _connect(path)
        try:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            if "tiles" not in tables:
                raise sqlite3.DatabaseError("missing 'tiles' table")
            meta = _metadata(conn)
        finally:
            conn.close()
    except sqlite3.Error as exc:
        # Corrupt/incomplete file: fall back honestly instead of serving a
        # blank map — log it so the operator notices, report unavailable.
        logger.warning(
            "Offline map %s is unreadable, falling back to online tiles: %s", path, exc
        )
        return {"available": False, "path": str(path), "region": None, "attribution": None}

    return {
        "available": True,
        "path": str(path),
        "region": meta.get("name"),
        "attribution": meta.get("attribution"),
        "min_zoom": int(meta["minzoom"]) if meta.get("minzoom", "").isdigit() else None,
        "max_zoom": int(meta["maxzoom"]) if meta.get("maxzoom", "").isdigit() else None,
    }


def _content_type(data: bytes) -> str:
    """Sniff the tile's own magic bytes rather than trust metadata claims."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "application/octet-stream"


def get_tile(z: int, x: int, y: int) -> tuple[bytes, str] | None:
    """Return (tile_bytes, content_type) for XYZ (slippy-map) coords, or None."""
    path = _resolve_path()
    if path is None:
        return None

    tms_y = (2**z) - 1 - y  # MBTiles stores tiles TMS-style (y flipped vs. XYZ)
    try:
        conn = _connect(path)
        try:
            row = conn.execute(
                "SELECT tile_data FROM tiles WHERE zoom_level=? AND tile_column=? AND tile_row=?",
                (z, x, tms_y),
            ).fetchone()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        logger.warning(
            "Offline map %s is unreadable, falling back to online tiles: %s", path, exc
        )
        return None

    if row is None:
        return None
    return row[0], _content_type(row[0])
