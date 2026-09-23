"""Test: offline glyph (font PBF) serving (OFFLINE-MAP-5).

The bundled fonts are real (backend/app/data/glyphs/), so these hit the
actual files rather than a fixture — matching offline_maps.py's own approach
of reading real bytes off disk rather than mocking the filesystem.
"""
from __future__ import annotations


async def test_regular_range_is_served(client):
    resp = await client.get("/api/maps/fonts/Noto%20Sans%20Regular/0-255.pbf")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/x-protobuf"
    assert len(resp.content) > 0


async def test_bold_range_is_served(client):
    resp = await client.get("/api/maps/fonts/Noto%20Sans%20Bold/0-255.pbf")
    assert resp.status_code == 200
    assert resp.content != (await client.get("/api/maps/fonts/Noto%20Sans%20Regular/0-255.pbf")).content


async def test_unknown_fontstack_falls_back_to_regular_weight():
    from app.services import glyphs

    assert glyphs.resolve_weight("Some Random Font Nobody Has") == "noto-sans-regular"


async def test_comma_joined_fallback_stack_picks_bold_if_any_part_says_so():
    from app.services import glyphs

    # MapLibre builds this exact shape from a style's `text-font` array.
    assert glyphs.resolve_weight("Noto Sans Bold,Arial Unicode MS Regular") == "noto-sans-bold"
    assert glyphs.resolve_weight("Noto Sans Regular,Arial Unicode MS Regular") == "noto-sans-regular"


async def test_ungenerated_range_404s(client):
    resp = await client.get("/api/maps/fonts/Noto%20Sans%20Regular/256-511.pbf")
    assert resp.status_code == 404


async def test_malformed_range_400s(client):
    resp = await client.get("/api/maps/fonts/Noto%20Sans%20Regular/not-a-range.pbf")
    assert resp.status_code == 400


async def test_malformed_extension_400s(client):
    resp = await client.get("/api/maps/fonts/Noto%20Sans%20Regular/0-255.txt")
    assert resp.status_code == 400
