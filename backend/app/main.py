"""NetGeo backend entrypoint.

FastAPI app assembling the §4 REST surface, the WebSocket endpoints, the shared
error envelope, CORS, JWT authentication (RB-01), and in-process rate limiting
(RB-14) — mirroring the secureops/storagehub app factory style.

Run:  uvicorn app.main:app --reload --port 8000 --ws websockets
Smoke: python -c "from app.main import app; print('netgeo backend imports OK')"
"""
from __future__ import annotations

import json
import logging
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import api_router
from app.api.ws import router as ws_router
from app.core.config import APP_CHANNEL, get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging
from app.core.security import check_rate_limit, configure_auth_store, init_admin_user, is_setup_required

logger = logging.getLogger(__name__)

settings = get_settings()


# ---------------------------------------------------------------------------
# RB-14: In-process rate-limit middleware
# ---------------------------------------------------------------------------

# Rules: (path, method, max_calls_per_window, window_seconds)
_RATE_LIMIT_RULES: list[tuple[str, str, int, float]] = [
    ("/api/auth/login", "POST", 10, 60.0),    # brute-force guard on login
    ("/api/update/apply", "POST", 5, 60.0),   # update trigger guard (RB-05)
]

_RATE_LIMITED_BODY = json.dumps(
    {"success": False, "error": {"code": "RATE_LIMITED", "message": "Too many requests. Please try again later."}}
).encode()


class RateLimitMiddleware:
    """Per-IP sliding-window rate limiter implemented as pure ASGI middleware.

    No external dependencies (no Redis, no slowapi). Uses the in-process
    sliding-window log from ``app.core.security.check_rate_limit``.

    Applied only to the endpoints listed in _RATE_LIMIT_RULES to avoid
    adding overhead to every request.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            path = scope.get("path", "")
            method = scope.get("method", "")
            for rule_path, rule_method, max_calls, window in _RATE_LIMIT_RULES:
                if path == rule_path and method == rule_method:
                    client_ip = self._client_ip(scope)
                    key = f"rl:{rule_path}:{client_ip}"
                    if not check_rate_limit(key, max_calls, window):
                        await self._send_429(send)
                        return
                    break
        await self.app(scope, receive, send)

    @staticmethod
    def _client_ip(scope: dict) -> str:
        """Best-effort source IP from the ASGI scope.

        Uses the direct TCP peer address for rate-limiting so that spoofed
        X-Forwarded-For headers cannot be used to bypass limits.
        """
        client = scope.get("client")
        if client:
            return client[0]
        return "unknown"

    @staticmethod
    async def _send_429(send: Any) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": 429,
                "headers": [
                    [b"content-type", b"application/json"],
                    [b"retry-after", b"60"],
                ],
            }
        )
        await send({"type": "http.response.body", "body": _RATE_LIMITED_BODY})


# ---------------------------------------------------------------------------
# Request body size cap (resource-exhaustion guard)
# ---------------------------------------------------------------------------

# Every write path takes a bounded JSON body; a project archive is the largest
# legitimate one. 16 MiB comfortably fits a big project while stopping an
# OOM-scale paste/upload before any handler parses it.
_MAX_BODY_BYTES = 16 * 1024 * 1024

_BODY_TOO_LARGE = json.dumps(
    {"success": False, "error": {"code": "BODY_TOO_LARGE", "message": "Request body too large."}}
).encode()


class MaxBodySizeMiddleware:
    """Reject an over-large request body with 413 before it is read (pure ASGI).

    Header-based: a declared ``Content-Length`` above the cap is refused.
    ponytail: chunked uploads without a Content-Length aren't capped here — add a
    receive-wrapper byte counter if a streaming upload path ever appears (today
    every write endpoint takes a bounded JSON body).
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            for key, value in scope.get("headers", []):
                if key == b"content-length":
                    try:
                        too_big = int(value) > _MAX_BODY_BYTES
                    except ValueError:
                        too_big = False
                    if too_big:
                        await send({
                            "type": "http.response.start",
                            "status": 413,
                            "headers": [[b"content-type", b"application/json"]],
                        })
                        await send({"type": "http.response.body", "body": _BODY_TOO_LARGE})
                        return
                    break
        await self.app(scope, receive, send)


# ---------------------------------------------------------------------------
# Security response headers
# ---------------------------------------------------------------------------

# Static headers applied to every response. HSTS is added conditionally below
# because it must only be sent over HTTPS (settings.ENABLE_HSTS).
_SECURITY_HEADERS: list[tuple[bytes, bytes]] = [
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"cross-origin-opener-policy", b"same-origin"),
]


class ApiV2AliasMiddleware:
    """NG-NFR-04: serve ``/api/v2/*`` from the same handlers as ``/api/*``.

    Pure path rewrite (HTTP + WebSocket) so v2 clients and v1 clients hit
    identical routes; runs outermost so rate limiting sees the v1 path.
    ponytail: duplicate routers when v2 needs to *diverge* from v1, not before.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        path = scope.get("path", "")
        if scope["type"] in ("http", "websocket") and path.startswith("/api/v2/"):
            scope = dict(scope)
            scope["path"] = "/api/" + path[len("/api/v2/"):]
        await self.app(scope, receive, send)


class SecurityHeadersMiddleware:
    """Inject hardening headers on every HTTP response (pure ASGI, no deps).

    Wires up the previously-unused ``ENABLE_HSTS`` setting and adds the baseline
    headers (anti-MIME-sniffing, clickjacking, referrer leakage) recommended by
    OWASP. WebSocket scopes are passed through untouched.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: dict) -> None:
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                headers.extend(_SECURITY_HEADERS)
                if settings.ENABLE_HSTS:
                    headers.append(
                        (b"strict-transport-security", b"max-age=31536000; includeSubDomains")
                    )
            await send(message)

        await self.app(scope, receive, send_with_headers)


# ---------------------------------------------------------------------------
# Admin user initialisation helper
# ---------------------------------------------------------------------------

def _init_admin_from_settings() -> None:
    """Initialise the admin account.  Called once from the lifespan hook.

    Resolution order:
    1. Load users persisted by the UI (first-run setup / change-password)
       from NETGEO_AUTH_STORE — these always win over env vars, so a password
       changed in the UI survives restarts even when the env var is stale.
    2. Otherwise, if NETGEO_ADMIN_PASSWORD is set: seed the admin from env.
    3. Otherwise: enter first-run setup mode — the login page prompts the
       first visitor to create the admin password (POST /api/auth/setup).
    """
    configure_auth_store(settings.NETGEO_AUTH_STORE or None)

    if not is_setup_required():
        return  # loaded from the auth store

    if settings.NETGEO_ADMIN_PASSWORD:
        init_admin_user(settings.NETGEO_ADMIN_USER, settings.NETGEO_ADMIN_PASSWORD)
        return

    logger.warning(
        "\n\n[FIRST-RUN SETUP] No admin account is configured.\n"
        "Open the NetGeo web UI to create the admin password (one-time setup),\n"
        "or set NETGEO_ADMIN_PASSWORD in your .env file before startup.\n",
    )


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(_: FastAPI):
    configure_logging()
    _init_admin_from_settings()
    logger.info("NetGeo backend started (environment=%s)", settings.ENVIRONMENT)
    yield
    logger.info("NetGeo backend shutting down.")


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        description="NetGeo — open-source large-scale network simulation platform.",
        lifespan=lifespan,
    )

    # RB-14: in-process rate limiter (outermost middleware so it runs before auth)
    app.add_middleware(RateLimitMiddleware)

    # Reject oversized request bodies before any handler parses them.
    app.add_middleware(MaxBodySizeMiddleware)

    # RB-09: baseline security response headers (+ HSTS when ENABLE_HSTS=1)
    app.add_middleware(SecurityHeadersMiddleware)

    # RB-09: tightened CORS — explicit methods and headers instead of wildcards
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-API-Key", "X-Request-ID"],
    )

    # NG-NFR-04: /api/v2 alias — added last so it runs outermost (rewrites
    # the path before rate limiting and routing see it).
    app.add_middleware(ApiV2AliasMiddleware)

    register_exception_handlers(app)
    app.include_router(api_router)   # /api/*
    app.include_router(ws_router)    # /ws/*

    @app.get("/api/health", tags=["meta"])
    async def health() -> dict:
        """Public health check — no authentication required."""
        return {
            "status": "ok",
            "app": settings.APP_NAME,
            "version": settings.APP_VERSION,
            "channel": APP_CHANNEL,
        }

    return app


app = create_app()
