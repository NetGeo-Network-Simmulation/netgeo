"""In-app update endpoints.

GET  /api/update/check   — read-only; compare running version with GitHub.
GET  /api/update/status  — progress reported by scripts/self-update.sh.
POST /api/update/apply   — trigger pull+rebuild+restart.

The whole router is mounted behind the admin-JWT dependency (RB-05), so every
endpoint here already requires an authenticated session. ``UPDATE_TOKEN`` is an
*optional* second factor: when set, POST /apply additionally requires a matching
``X-Update-Token`` header; when empty, the admin session alone is sufficient.

Handlers are intentionally **synchronous** ``def`` functions. ``updater.check``
and ``updater.apply`` perform *blocking* network/subprocess I/O; declaring the
routes ``async`` would run that I/O directly on the asyncio event loop and stall
every other in-flight request (which is exactly how the UI ends up reporting the
backend as unreachable). FastAPI runs plain ``def`` handlers in a threadpool, so
the blocking work never starves the event loop.
"""
from __future__ import annotations

import hmac
import logging

from fastapi import APIRouter, Header, HTTPException, status

from app.core.config import get_settings
from app.services import updater

logger = logging.getLogger(__name__)

router = APIRouter(tags=["update"])


@router.get("/update/check")
def update_check() -> dict:
    # check() is defensive and degrades gracefully, but guard the route anyway so
    # an unexpected failure surfaces as a structured payload instead of a 500 —
    # the frontend distinguishes "GitHub unreachable" from "backend unreachable".
    try:
        return updater.check()
    except Exception:
        logger.exception("update_check failed unexpectedly")
        return {
            "current": get_settings().APP_VERSION,
            "latest": None,
            "update_available": False,
            "error": "Update check failed unexpectedly on the backend.",
        }


@router.get("/update/status")
def update_status() -> dict:
    return updater.status()


@router.post("/update/apply")
def update_apply(x_update_token: str | None = Header(default=None)) -> dict:
    settings = get_settings()
    # The admin JWT is enforced by the router dependency. UPDATE_TOKEN, when
    # configured, is an additional shared secret. Constant-time compare so the
    # token cannot be recovered byte-by-byte via a response-timing side channel
    # (consistent with core.security's hmac usage).
    if settings.UPDATE_TOKEN and (
        x_update_token is None or not hmac.compare_digest(x_update_token, settings.UPDATE_TOKEN)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid update token.",
        )
    return updater.apply()
