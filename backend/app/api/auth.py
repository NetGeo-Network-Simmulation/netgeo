"""Authentication endpoints.

POST /api/auth/login           — exchange credentials for a JWT token (public)
GET  /api/auth/setup           — is first-run setup still pending?    (public)
POST /api/auth/setup           — create the admin account, one time   (public)
GET  /api/auth/me              — current user's identity        (requires auth)
POST /api/auth/change-password — change own password             (requires auth)

The public endpoints intentionally have no Depends(get_current_user) — the
router is included in api/__init__.py WITHOUT the auth dependency so login and
first-run setup are always reachable.  /setup hard-fails with 409 once any
account exists, so it is only ever usable on a fresh install.

The authenticated endpoints apply get_current_user at the handler level, which
lets the auth contract file and the frontend clearly see that every call must
carry a valid Bearer token.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app.api.deps import get_current_user
from app.core.config import get_settings
from app.core.security import (
    MIN_PASSWORD_LENGTH,
    authenticate_user,
    change_password,
    check_rate_limit,
    create_access_token,
    create_initial_admin,
    is_setup_required,
)
from app.exceptions.base import Conflict, Unauthorized, ValidationError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class SetupStatusResponse(BaseModel):
    setup_required: bool


class SetupRequest(BaseModel):
    # Username defaults to the configured admin name when omitted.
    username: str | None = None
    password: str = Field(min_length=MIN_PASSWORD_LENGTH)


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=MIN_PASSWORD_LENGTH)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, request: Request) -> TokenResponse:
    """Exchange username + password for a signed HS256 JWT access token.

    Rate-limited: 10 requests per minute per source IP (enforced by
    RateLimitMiddleware in main.py; this handler adds a second layer via
    check_rate_limit for defence in depth when the middleware is bypassed
    in unit tests).

    On success returns:
        {"access_token": "<jwt>", "token_type": "bearer", "expires_in": <seconds>}

    On failure always returns 401 UNAUTHORIZED with a generic message — the
    response never reveals whether the username or the password was wrong.
    """
    # In-handler rate-limit guard (defence in depth; middleware is the primary layer)
    client_ip = request.client.host if request.client else "unknown"
    if not check_rate_limit(f"login:{client_ip}", max_calls=10, window_seconds=60.0):
        # Re-use Unauthorized so the response envelope stays consistent.
        # 429 is handled by the middleware; here we just abort the handler.
        raise Unauthorized("Too many login attempts. Please wait and try again.")

    user = authenticate_user(body.username, body.password)
    if user is None:
        logger.warning(
            "Failed login attempt for username=%r from %s", body.username, client_ip
        )
        # Generic message — never reveal whether it's the username or password.
        raise Unauthorized("Invalid username or password")

    settings = get_settings()
    expires_in = settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60
    token = create_access_token(
        sub=user["sub"],
        role=user["role"],
        secret=settings.SECRET_KEY,
        expires_in=expires_in,
    )
    logger.info("Successful login for username=%r from %s", body.username, client_ip)
    return TokenResponse(access_token=token, token_type="bearer", expires_in=expires_in)


@router.get("/setup", response_model=SetupStatusResponse)
async def setup_status() -> SetupStatusResponse:
    """Report whether first-run setup is still pending (no account exists yet).

    Public — the login page calls this to decide whether to show the
    "create admin password" form instead of the sign-in form.
    """
    return SetupStatusResponse(setup_required=is_setup_required())


@router.post("/setup", response_model=TokenResponse)
async def setup(body: SetupRequest, request: Request) -> TokenResponse:
    """First-run setup: create the admin account and sign in immediately.

    Only available while no account exists — returns 409 CONFLICT once setup
    has been completed (or an admin was seeded via NETGEO_ADMIN_PASSWORD).
    The chosen password is persisted to NETGEO_AUTH_STORE so it survives
    restarts.
    """
    client_ip = request.client.host if request.client else "unknown"
    if not check_rate_limit(f"setup:{client_ip}", max_calls=10, window_seconds=60.0):
        raise Unauthorized("Too many attempts. Please wait and try again.")

    if not is_setup_required():
        raise Conflict("Setup has already been completed")

    settings = get_settings()
    username = (body.username or settings.NETGEO_ADMIN_USER).strip()
    try:
        user = create_initial_admin(username, body.password)
    except ValueError as exc:
        raise ValidationError(str(exc))

    logger.info(
        "First-run setup completed: admin user %r created from %s", username, client_ip
    )
    expires_in = settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60
    token = create_access_token(
        sub=user["sub"],
        role=user["role"],
        secret=settings.SECRET_KEY,
        expires_in=expires_in,
    )
    return TokenResponse(access_token=token, token_type="bearer", expires_in=expires_in)


@router.post("/change-password")
async def change_own_password(
    body: ChangePasswordRequest,
    request: Request,
    current_user: Annotated[dict, Depends(get_current_user)],
) -> dict:
    """Change the authenticated user's password.

    Requires the current password (re-authentication) so a stolen token alone
    cannot rotate credentials.  The new password is persisted to
    NETGEO_AUTH_STORE.  Existing tokens stay valid until they expire.
    """
    client_ip = request.client.host if request.client else "unknown"
    if not check_rate_limit(f"chpass:{client_ip}", max_calls=10, window_seconds=60.0):
        raise Unauthorized("Too many attempts. Please wait and try again.")

    try:
        change_password(current_user["sub"], body.current_password, body.new_password)
    except ValueError as exc:
        # Wrong current password → 401; policy violations → 422.
        if "incorrect" in str(exc).lower():
            logger.warning(
                "Failed password change for username=%r from %s",
                current_user["sub"],
                client_ip,
            )
            raise Unauthorized(str(exc))
        raise ValidationError(str(exc))

    return {"success": True, "message": "Password updated"}


@router.get("/me")
async def me(current_user: Annotated[dict, Depends(get_current_user)]) -> dict:
    """Return the identity of the currently authenticated user.

    Requires:  Authorization: Bearer <access_token>
    """
    return {
        "username": current_user["sub"],
        "role": current_user["role"],
    }
