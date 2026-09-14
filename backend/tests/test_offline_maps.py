"""Test: offline map tiles (OFFLINE-MAP-1).

Two paths must both work:
  1. No local MBTiles file (test default, see conftest) -> status reports
     unavailable and the tile endpoint 404s, i.e. today's online-only
     behavior is untouched.
  2. A valid local MBTiles file is configured -> status reports it and the
     tile endpoint serves bytes straight out of the file.
Plus: a corrupt file must fail honestly (unavailable), never a silent blank
tile.
"""
from __future__ import annotations

import sqlite3

from app.core.config import get_settings

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_FAKE_TILE = _PNG_MAGIC + b"fake-tile-bytes"


def _make_mbtiles(path, *, with_tile=True):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE metadata (name TEXT, value TEXT)")
    conn.execute("CREATE TABLE tiles (zoom_level INTEGER, tile_column INTEGER, tile_row INTEGER, tile_data BLOB)")
    conn.executemany(
        "INSERT INTO metadata (name, value) VALUES (?, ?)",
        [("name", "Test Region"), ("attribution", "Test Attribution"), ("minzoom", "0"), ("maxzoom", "10")],
    )
    if with_tile:
        # XYZ (3, 2, 5) <-> MBTiles TMS row = 2**3 - 1 - 5 = 2
        conn.execute(
            "INSERT INTO tiles (zoom_level, tile_column, tile_row, tile_data) VALUES (3, 2, 2, ?)",
            (_FAKE_TILE,),
        )
    conn.commit()
    conn.close()


async def test_no_local_file_falls_back_to_online(client):
    """Test default: NETGEO_OFFLINE_MAP_PATH="" -> unavailable, tile 404s."""
    resp = await client.get("/api/maps/status")
    assert resp.status_code == 200
    assert resp.json() == {"available": False, "path": None, "region": None, "attribution": None}

    tile_resp = await client.get("/api/maps/tiles/3/2/5")
    assert tile_resp.status_code == 404


async def test_valid_local_file_is_served(client, tmp_path, monkeypatch):
    mbtiles = tmp_path / "region.mbtiles"
    _make_mbtiles(mbtiles)
    monkeypatch.setattr(get_settings(), "NETGEO_OFFLINE_MAP_PATH", str(mbtiles))

    status = (await client.get("/api/maps/status")).json()
    assert status["available"] is True
    assert status["region"] == "Test Region"
    assert status["attribution"] == "Test Attribution"
    assert status["min_zoom"] == 0
    assert status["max_zoom"] == 10

    tile_resp = await client.get("/api/maps/tiles/3/2/5")
    assert tile_resp.status_code == 200
    assert tile_resp.content == _FAKE_TILE
    assert tile_resp.headers["content-type"] == "image/png"


async def test_missing_tile_in_valid_file_404s(client, tmp_path, monkeypatch):
    mbtiles = tmp_path / "region.mbtiles"
    _make_mbtiles(mbtiles, with_tile=False)
    monkeypatch.setattr(get_settings(), "NETGEO_OFFLINE_MAP_PATH", str(mbtiles))

    tile_resp = await client.get("/api/maps/tiles/3/2/5")
    assert tile_resp.status_code == 404


async def test_corrupt_file_falls_back_honestly(client, tmp_path, monkeypatch):
    """Not-a-database file must report unavailable, never a blank 200 tile."""
    corrupt = tmp_path / "broken.mbtiles"
    corrupt.write_bytes(b"this is not a sqlite database")
    monkeypatch.setattr(get_settings(), "NETGEO_OFFLINE_MAP_PATH", str(corrupt))

    status = (await client.get("/api/maps/status")).json()
    assert status["available"] is False

    tile_resp = await client.get("/api/maps/tiles/3/2/5")
    assert tile_resp.status_code == 404


async def test_configured_path_that_does_not_exist_is_unavailable(client, tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "NETGEO_OFFLINE_MAP_PATH", str(tmp_path / "nope.mbtiles"))

    status = (await client.get("/api/maps/status")).json()
    assert status["available"] is False
    assert status["path"] is None


# --------------------------------------------------------------------------
# Install endpoints (OFFLINE-MAP-3) — upload / download-by-URL / remove.
# The .rpm/.deb install path has no interactive step, so these let the
# in-app first-run screen (and Settings, later) install a region file
# instead of only the CLI installer flags.
# --------------------------------------------------------------------------


def _mbtiles_bytes() -> bytes:
    """A minimal-but-valid MBTiles file, as raw bytes (for upload)."""
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".mbtiles") as f:
        _make_mbtiles(f.name)
        return open(f.name, "rb").read()


async def test_upload_installs_and_status_reflects_it(client, tmp_path, monkeypatch):
    target = tmp_path / "installed.mbtiles"
    monkeypatch.setattr(get_settings(), "NETGEO_OFFLINE_MAP_PATH", str(target))

    resp = await client.post(
        "/api/maps/offline-map/upload",
        files={"file": ("region.mbtiles", _mbtiles_bytes(), "application/octet-stream")},
    )
    assert resp.status_code == 200
    assert resp.json()["available"] is True
    assert target.is_file()

    status = (await client.get("/api/maps/status")).json()
    assert status["available"] is True
    assert status["region"] == "Test Region"


async def test_upload_rejects_non_mbtiles_and_leaves_nothing_behind(client, tmp_path, monkeypatch):
    target = tmp_path / "installed.mbtiles"
    monkeypatch.setattr(get_settings(), "NETGEO_OFFLINE_MAP_PATH", str(target))

    resp = await client.post(
        "/api/maps/offline-map/upload",
        files={"file": ("garbage.mbtiles", b"not a sqlite file", "application/octet-stream")},
    )
    assert resp.status_code == 422
    assert not target.exists()
    # No leftover .part temp files either.
    assert list(tmp_path.iterdir()) == []


async def test_download_rejects_non_http_url(client, tmp_path, monkeypatch):
    target = tmp_path / "installed.mbtiles"
    monkeypatch.setattr(get_settings(), "NETGEO_OFFLINE_MAP_PATH", str(target))

    resp = await client.post("/api/maps/offline-map/download", json={"url": "file:///etc/passwd"})
    assert resp.status_code == 422
    assert not target.exists()


async def test_remove_deletes_installed_file(client, tmp_path, monkeypatch):
    target = tmp_path / "installed.mbtiles"
    _make_mbtiles(target)
    monkeypatch.setattr(get_settings(), "NETGEO_OFFLINE_MAP_PATH", str(target))

    assert (await client.get("/api/maps/status")).json()["available"] is True

    resp = await client.delete("/api/maps/offline-map")
    assert resp.status_code == 200
    assert resp.json()["available"] is False
    assert not target.exists()


async def test_remove_is_a_noop_when_nothing_installed(client, tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "NETGEO_OFFLINE_MAP_PATH", str(tmp_path / "nope.mbtiles"))

    resp = await client.delete("/api/maps/offline-map")
    assert resp.status_code == 200
    assert resp.json()["available"] is False
