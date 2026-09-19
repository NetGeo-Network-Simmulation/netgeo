"""Offline map tile serving (OFFLINE-MAP-1) + install (OFFLINE-MAP-3).

GET    /api/maps/status            — is a local MBTiles region installed,
                                      which one, what it covers. The frontend
                                      uses this to pick offline vs. online
                                      basemap tiles; this router never
                                      contacts Esri/OSM itself.
GET    /api/maps/tiles/{z}/{x}/{y} — raster tile bytes from that file,
                                      standard XYZ (slippy-map) coordinates.
POST   /api/maps/offline-map/upload   — install a user-supplied .mbtiles file.
POST   /api/maps/offline-map/download — download a .mbtiles file from a URL
                                         and install it.
DELETE /api/maps/offline-map          — remove the installed file, revert to
                                         online tiles.

The install endpoints exist because a .rpm/.deb package install has no
interactive step to ask for a map source (see packaging/linux/install.sh and
packaging/windows/netgeo.iss, which DO have one — but only reach installs
done through those installers). The in-app first-run screen and Settings
call these instead, regardless of how NetGeo was installed.
"""
from __future__ import annotations

import gzip
import sqlite3
from typing import Annotated

import httpx
from fastapi import APIRouter, File, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel

from app.exceptions.base import ValidationError
from app.services import offline_maps

router = APIRouter(prefix="/maps", tags=["maps"])


class InstallFromUrlRequest(BaseModel):
    url: str


@router.get("/status")
async def maps_status() -> dict:
    return offline_maps.get_status()


@router.get("/tiles/{z}/{x}/{y}")
async def maps_tile(z: int, x: int, y: int, request: Request) -> Response:
    result = offline_maps.get_tile(z, x, y)
    if result is None:
        raise HTTPException(status_code=404, detail="tile not available offline")
    data, content_type, is_gzip = result
    headers = {}
    if is_gzip:
        # Vector tiles are stored gzip-compressed. Mirror Martin's measured
        # behavior (memory mbtiles-vector-spike-2026-09-19): pass the blob
        # through unchanged with Content-Encoding when the client says it can
        # decompress, otherwise decompress here so a client that can't never
        # gets a mislabeled/undecodable body. Raster tiles never hit this
        # branch (is_gzip is always False for them) so their response is
        # unchanged.
        if "gzip" in request.headers.get("accept-encoding", ""):
            headers["Content-Encoding"] = "gzip"
        else:
            data = gzip.decompress(data)
    return Response(content=data, media_type=content_type, headers=headers)


@router.post("/offline-map/upload")
async def maps_offline_upload(file: Annotated[UploadFile, File()]) -> dict:
    try:
        return offline_maps.install_from_stream(file.file)
    except (ValueError, sqlite3.Error) as exc:
        raise ValidationError(f"could not install {file.filename!r}: {exc}")


@router.post("/offline-map/download")
async def maps_offline_download(body: InstallFromUrlRequest) -> dict:
    try:
        return offline_maps.install_from_url(body.url)
    except (ValueError, sqlite3.Error) as exc:
        raise ValidationError(f"could not install from URL: {exc}")
    except httpx.HTTPError as exc:
        raise ValidationError(f"download failed: {exc}")


@router.delete("/offline-map")
async def maps_offline_remove() -> dict:
    return offline_maps.remove_installed()
