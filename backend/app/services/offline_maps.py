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

``install_from_stream``/``install_from_url`` (OFFLINE-MAP-3) let the operator
supply a region file from the app itself — first-run onboarding, or later via
Settings — instead of only through the CLI installer flags, which never run
for a .rpm/.deb package install (those have no interactive step).
"""
from __future__ import annotations

import json
import logging
import sqlite3
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)

# Sanity cap so a mistyped URL / huge upload can't silently fill the disk.
# Real region packages (a metro area to a small province at street zoom) are
# tens to low hundreds of MB; whole-country extracts can reach a few GB.
MAX_OFFLINE_MAP_BYTES = 4 * 1024**3  # 4 GiB


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


def _has_tiles(conn: sqlite3.Connection) -> bool:
    """Whether ``conn`` has a queryable ``tiles`` relation — table OR view.
    Planetiler's own output (the reference vector-MBTiles builder, see memory
    mbtiles-vector-spike-2026-09-19) stores tiles as ``tiles_shallow`` +
    ``tiles_data`` with a ``tiles`` VIEW joining them for de-duplication, not
    a plain table; a type='table'-only check rejects every such file even
    though ``SELECT ... FROM tiles`` works identically on a view."""
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table', 'view') AND name='tiles'"
    ).fetchall()
    return bool(rows)


def _metadata(conn: sqlite3.Connection) -> dict[str, str]:
    try:
        return dict(conn.execute("SELECT name, value FROM metadata").fetchall())
    except sqlite3.Error:
        return {}


_UNAVAILABLE_STATUS = {
    "available": False,
    "path": None,
    "region": None,
    "attribution": None,
    "format": None,
    "vector_layers": None,
}


def _vector_layers(meta: dict[str, str]) -> list | None:
    """MBTiles' optional TileJSON-shaped ``json`` metadata value carries the
    per-layer schema for vector (pbf) tiles — parse it defensively since it's
    operator-supplied data, not something this app generated."""
    raw = meta.get("json")
    if not raw:
        return None
    try:
        return json.loads(raw).get("vector_layers")
    except (json.JSONDecodeError, AttributeError):
        return None


def get_status() -> dict:
    """What the frontend needs to decide online vs. offline basemap, and
    (OFFLINE-MAP-4) whether that basemap is raster or vector."""
    path = _resolve_path()
    if path is None:
        return dict(_UNAVAILABLE_STATUS)

    try:
        conn = _connect(path)
        try:
            if not _has_tiles(conn):
                raise sqlite3.DatabaseError("missing 'tiles' table/view")
            meta = _metadata(conn)
        finally:
            conn.close()
    except sqlite3.Error as exc:
        # Corrupt/incomplete file: fall back honestly instead of serving a
        # blank map — log it so the operator notices, report unavailable.
        logger.warning(
            "Offline map %s is unreadable, falling back to online tiles: %s", path, exc
        )
        return {**_UNAVAILABLE_STATUS, "path": str(path)}

    return {
        "available": True,
        "path": str(path),
        "region": meta.get("name"),
        "attribution": meta.get("attribution"),
        "min_zoom": int(meta["minzoom"]) if meta.get("minzoom", "").isdigit() else None,
        "max_zoom": int(meta["maxzoom"]) if meta.get("maxzoom", "").isdigit() else None,
        # "pbf" = vector (MapLibre vector source); anything else (png/jpg/webp,
        # or absent on older raster exports) = raster, today's behavior.
        "format": meta.get("format"),
        "vector_layers": _vector_layers(meta),
    }


def _content_type(data: bytes, *, is_gzip: bool) -> str:
    """Sniff the tile's own bytes rather than trust metadata claims. A
    gzip-magic blob is a compressed vector tile (MVT/pbf) — Planetiler/Martin
    convention and the only gzip-wrapped shape MBTiles tiles come in; raster
    formats below are never gzip-wrapped so this check is unambiguous."""
    if is_gzip:
        return "application/x-protobuf"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "application/octet-stream"


def get_tile(z: int, x: int, y: int) -> tuple[bytes, str, bool] | None:
    """Return (tile_bytes, content_type, is_gzip) for XYZ (slippy-map)
    coords, or None. ``is_gzip`` tells the caller whether ``tile_bytes`` is
    still gzip-compressed (vector tiles are stored that way) so it can decide
    the Content-Encoding header the way Martin does (see OFFLINE-MAP-4
    research): pass compressed bytes through when the client accepts gzip,
    decompress first otherwise. Raster tiles are never gzipped -> is_gzip is
    always False for them, so their response is byte-identical to before."""
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
    data = row[0]
    is_gzip = data[:2] == b"\x1f\x8b"
    return data, _content_type(data, is_gzip=is_gzip), is_gzip


def _target_path() -> Path:
    """Where an installed region file lives, regardless of whether it exists
    yet — unlike ``_resolve_path`` this never returns None (needed to know
    where to *write*)."""
    raw = get_settings().NETGEO_OFFLINE_MAP_PATH
    if not raw:
        raise ValueError("no offline-map path is configured (NETGEO_OFFLINE_MAP_PATH)")
    return Path(raw).expanduser()


def _require_tiles_table(path: Path) -> None:
    """Raise ValueError unless ``path`` is a readable MBTiles file (a
    ``tiles`` table or view — see ``_has_tiles``)."""
    conn = _connect(path)
    try:
        ok = _has_tiles(conn)
    finally:
        conn.close()
    if not ok:
        raise ValueError("not a valid MBTiles file (missing 'tiles' table/view)")


def _write_capped(chunks, dest: BinaryIO) -> None:
    """Copy an iterable of byte chunks into ``dest``, aborting past the size
    cap — shared by both the upload (file object) and download (httpx
    iterator) paths."""
    written = 0
    for chunk in chunks:
        written += len(chunk)
        if written > MAX_OFFLINE_MAP_BYTES:
            raise ValueError(f"file exceeds the {MAX_OFFLINE_MAP_BYTES // 1024**3} GiB limit")
        dest.write(chunk)


def _install_atomic(write: Callable[[BinaryIO], None]) -> dict:
    """Write into a temp file next to the target, validate, then atomically
    replace — a crash or invalid file mid-write never corrupts a previously
    working offline map."""
    target = _target_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=target.parent, suffix=".mbtiles.part")
    tmp_path = Path(tmp_name)
    try:
        with open(fd, "wb") as out:
            write(out)
        _require_tiles_table(tmp_path)
        tmp_path.replace(target)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
    return get_status()


def install_from_stream(fileobj: BinaryIO) -> dict:
    """Install an uploaded MBTiles file as the offline map. Raises
    ``ValueError`` (bad/oversized file) or ``sqlite3.Error`` (unreadable)."""
    chunks = iter(lambda: fileobj.read(1024 * 1024), b"")
    return _install_atomic(lambda out: _write_capped(chunks, out))


def install_from_url(url: str) -> dict:
    """Download an MBTiles file from ``url`` and install it. Raises
    ``ValueError``, ``sqlite3.Error``, or ``httpx.HTTPError``."""
    if not url.startswith(("http://", "https://")):
        raise ValueError("url must start with http:// or https://")
    with httpx.stream("GET", url, follow_redirects=True, timeout=60.0) as resp:
        resp.raise_for_status()
        return _install_atomic(lambda out: _write_capped(resp.iter_bytes(1024 * 1024), out))


def remove_installed() -> dict:
    """Delete the installed offline map file, if any — reverting to online
    tiles. A no-op (not an error) when nothing is installed."""
    _target_path().unlink(missing_ok=True)
    return get_status()
