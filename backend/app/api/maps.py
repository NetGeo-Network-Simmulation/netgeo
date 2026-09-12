"""Offline map tile serving (OFFLINE-MAP-1).

GET /api/maps/status            — is a local MBTiles region installed, which
                                   one, what it covers. The frontend (a later
                                   slice) uses this to pick offline vs. online
                                   basemap tiles; this router never contacts
                                   Esri/OSM itself.
GET /api/maps/tiles/{z}/{x}/{y} — raster tile bytes from that file, standard
                                   XYZ (slippy-map) coordinates.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response

from app.services import offline_maps

router = APIRouter(prefix="/maps", tags=["maps"])


@router.get("/status")
async def maps_status() -> dict:
    return offline_maps.get_status()


@router.get("/tiles/{z}/{x}/{y}")
async def maps_tile(z: int, x: int, y: int) -> Response:
    result = offline_maps.get_tile(z, x, y)
    if result is None:
        raise HTTPException(status_code=404, detail="tile not available offline")
    data, content_type = result
    return Response(content=data, media_type=content_type)
