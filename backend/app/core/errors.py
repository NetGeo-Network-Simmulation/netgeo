"""Exception handlers producing the standard error envelope.

Identical contract to storagehub: every error becomes
``{"success": false, "error": {"code", "message"}}``.
"""
from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.exceptions.base import AppException, SeekUnavailable
from app.utils.response import error

logger = logging.getLogger(__name__)

_STATUS_CODE_MAP = {
    400: "BAD_REQUEST",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    422: "VALIDATION_ERROR",
    429: "RATE_LIMITED",
    500: "INTERNAL_ERROR",
}


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(SeekUnavailable)
    async def seek_unavailable_handler(_: Request, exc: SeekUnavailable):
        # NG-N6: lab.py's endpoints already return flat, un-enveloped dicts on
        # the happy path (no {"success", "data"} wrapper) — this error body
        # matches that local convention instead of the app-wide error()
        # envelope, so the frontend can show `reason` with no code table.
        return JSONResponse(
            status_code=409, content={"error": exc.code, "reason": exc.reason}
        )

    @app.exception_handler(AppException)
    async def app_exception_handler(_: Request, exc: AppException):
        return JSONResponse(
            status_code=exc.status_code, content=error(exc.code, exc.message)
        )

    @app.exception_handler(RequestValidationError)
    async def validation_handler(_: Request, exc: RequestValidationError):
        msg = exc.errors()[0]["msg"] if exc.errors() else "Validation error"
        return JSONResponse(status_code=422, content=error("VALIDATION_ERROR", msg))

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(_: Request, exc: StarletteHTTPException):
        code = _STATUS_CODE_MAP.get(exc.status_code, "ERROR")
        return JSONResponse(
            status_code=exc.status_code, content=error(code, str(exc.detail))
        )

    @app.exception_handler(Exception)
    async def unhandled_handler(_: Request, exc: Exception):
        logger.exception("Unhandled error: %s", exc)
        return JSONResponse(
            status_code=500,
            content=error("INTERNAL_ERROR", "An unexpected error occurred"),
        )
