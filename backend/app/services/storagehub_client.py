"""StorageHub ingest client (C-2): pushes a NetGeo project archive into a
StorageHub instance via its log-ingest endpoint.

Fresh implementation against StorageHub's documented contract — no code
shared with the secureops StorageHub client (different license):

    POST {BASE}/api/v1/ingest/logs
    header: X-API-Key: <key>
    multipart: file=(filename, bytes, "application/gzip"), form: source=<str>
    200 -> {"success": true, "data": {"path", "size"}, "message": ...}
"""
from __future__ import annotations

import httpx

from app.core.config import settings, storagehub_enabled
from app.exceptions.base import AppException

_INGEST_PATH = "/api/v1/ingest/logs"


class StorageHubError(AppException):
    """StorageHub is not configured, unreachable, or rejected the upload."""
    status_code = 502
    code = "STORAGEHUB_ERROR"


async def upload(content: bytes, filename: str, source: str) -> dict:
    """POST ``content`` to StorageHub's log-ingest endpoint, return its parsed
    JSON body. Raises :class:`StorageHubError` (503 if unconfigured, 502 for
    any connection/auth/HTTP failure)."""
    if not storagehub_enabled():
        raise StorageHubError("StorageHub backup not configured", status_code=503)

    url = settings.NETGEO_STORAGEHUB_URL.rstrip("/") + _INGEST_PATH
    headers = {"X-API-Key": settings.NETGEO_STORAGEHUB_API_KEY}
    files = {"file": (filename, content, "application/gzip")}

    try:
        async with httpx.AsyncClient(
            timeout=60, verify=settings.NETGEO_STORAGEHUB_VERIFY_TLS
        ) as client:
            resp = await client.post(
                url, headers=headers, files=files, data={"source": source}
            )
    except httpx.HTTPError as exc:
        raise StorageHubError(f"could not reach StorageHub: {exc}") from exc

    if resp.status_code in (401, 403):
        raise StorageHubError("StorageHub rejected the API key")
    if resp.status_code >= 400:
        raise StorageHubError(
            f"StorageHub returned {resp.status_code}: {resp.text[:200]}"
        )
    return resp.json()


__all__ = ["StorageHubError", "upload"]
