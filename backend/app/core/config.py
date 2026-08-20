"""Application configuration loaded from environment variables.

Mirrors the secureops/storagehub settings style (pydantic-settings + a cached
``settings`` singleton) so the three projects share a baseline. NetGeo adds
Redis (realtime state / job queue) and engine knobs.
"""
from __future__ import annotations

import sys
from functools import lru_cache

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Single source of truth for the running version. Deliberately NOT a Settings
# field: install.sh used to stamp APP_VERSION into .env / compose env, and that
# stale value overrode the code default forever — self-updated installs kept
# reporting the version they were first installed at (e.g. "0.1"), so the
# updater always saw an update available. The env var is now ignored.
APP_VERSION = "1.2.77"
# Release channel (alpha/beta/stable). Kept out of APP_VERSION so the updater's
# numeric version comparison against GitHub releases keeps working.
APP_CHANNEL = "beta"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # App
    APP_NAME: str = "NetGeo"
    ENVIRONMENT: str = "development"
    SECRET_KEY: str = "change-me"
    CORS_ORIGINS: str = "http://localhost,http://localhost:5173,http://localhost:3000"

    # URLs
    FRONTEND_URL: str = "http://localhost:5173"
    BACKEND_URL: str = "http://localhost:8000"
    API_V1_PREFIX: str = "/api"

    # Database (PostgreSQL per MASTER_SPEC §2; async driver)
    DATABASE_URL: str = "postgresql+asyncpg://netgeo:netgeo@localhost:5432/netgeo"

    # Redis — realtime state, pub/sub for WS fan-out, job queue
    REDIS_URL: str = "redis://localhost:6379/0"

    # Simulation engine
    SIM_MAX_NODES: int = 5000          # soft guard for a single in-process run
    SIM_DEFAULT_SEED: int = 0
    SIM_MAX_EVENTS: int = 5_000_000    # runaway guard
    SIM_METRIC_INTERVAL: float = 1.0

    # Emulation backend: "null" | "containerlab" | "docker"
    EMULATION_BACKEND: str = "null"

    # Security headers
    ENABLE_HSTS: bool = False

    # Auth — JWT
    # HS256 with SECRET_KEY is the chosen algorithm (single-node; no RS256 keypair files needed).
    # ACCESS_TOKEN_EXPIRE_MINUTES controls JWT lifetime.
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60

    # Auth — admin seed account (RB-01)
    # NETGEO_ADMIN_USER and NETGEO_ADMIN_PASSWORD are read once at startup.
    # If NETGEO_ADMIN_PASSWORD is empty (and no auth store file exists yet),
    # the web UI enters first-run setup mode: the first visitor creates the
    # admin password via POST /api/auth/setup.
    NETGEO_ADMIN_USER: str = "admin"
    NETGEO_ADMIN_PASSWORD: str = ""
    # JSON file where password hashes set via the UI (first-run setup /
    # change-password) are persisted across restarts. Empty = disabled.
    NETGEO_AUTH_STORE: str = "~/.config/netgeo/auth.json"
    # JSON file where MemoryRepository state (projects/nodes/links/...) is
    # persisted across restarts (S2 PERSIST-01). Empty = disabled (in-memory only).
    NETGEO_STATE_STORE: str = "~/.config/netgeo/state.json"

    # In-app self-update (see app/services/updater.py + scripts/self-update.sh).
    # GITHUB_REPO is the "owner/name" slug whose releases we compare against.
    GITHUB_REPO: str = "suryaex/netgeo"
    UPDATE_BRANCH: str = "main"
    # Optional extra guard for POST /api/update/apply. The endpoint always
    # requires an authenticated admin session (RB-05); when UPDATE_TOKEN is
    # set, the X-Update-Token header must ALSO match (defence in depth).
    UPDATE_TOKEN: str = ""
    # Sentinel + status files exchanged with the host-side scripts/self-update.sh.
    UPDATE_TRIGGER_FILE: str = "/var/lib/netgeo/update.request"
    UPDATE_STATUS_FILE: str = "/var/lib/netgeo/update.status"
    # When "1" the backend may run scripts/self-update.sh itself (needs the repo +
    # docker socket mounted). Otherwise it only drops the trigger file for a
    # host-side watcher to pick up — the safer default in containers.
    UPDATE_INPROC: bool = False

    # StorageHub backup connector (C-2): POST /projects/{id}/backup-to-storagehub
    # pushes a project archive into a StorageHub instance's log-ingest endpoint.
    # Default OFF — both URL and API key must be set to enable it.
    NETGEO_STORAGEHUB_URL: str = ""
    NETGEO_STORAGEHUB_API_KEY: str = ""
    NETGEO_STORAGEHUB_SOURCE: str = "netgeo"
    NETGEO_STORAGEHUB_VERIFY_TLS: bool = True

    # ---------------------------------------------------------------------------
    # Validators
    # ---------------------------------------------------------------------------

    @field_validator(
        "ENABLE_HSTS", "UPDATE_INPROC", "NETGEO_STORAGEHUB_VERIFY_TLS", mode="before"
    )
    @classmethod
    def _parse_bool(cls, v):
        if isinstance(v, str):
            return v.strip().lower() in {"1", "true", "yes", "on"}
        return v

    @model_validator(mode="after")
    def _check_secret_key(self) -> Settings:
        """RB-07: Refuse to start in production with the default SECRET_KEY.

        In non-production environments, print a prominent warning to stderr so
        the developer sees it even before the logging system is configured.
        """
        if self.SECRET_KEY == "change-me":
            if self.ENVIRONMENT == "production":
                raise ValueError(
                    "[FATAL] SECRET_KEY is set to the insecure default 'change-me'. "
                    "This is not allowed in production. "
                    "Set SECRET_KEY to a cryptographically random string of at least "
                    "32 characters in your environment or .env file before deploying."
                )
            # Non-production: warn loudly — stderr is always visible.
            print(
                "\n"
                "*** SECURITY WARNING ***\n"
                "SECRET_KEY is 'change-me' (the insecure default).\n"
                "Any party who knows this default value can forge JWT tokens.\n"
                "Set SECRET_KEY to a unique random string before deploying.\n"
                "Example: SECRET_KEY=$(python -c 'import secrets; print(secrets.token_hex(32))')\n"
                "*** SECURITY WARNING ***\n",
                file=sys.stderr,
                flush=True,
            )
        return self

    # ---------------------------------------------------------------------------
    # Properties
    # ---------------------------------------------------------------------------

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def APP_VERSION(self) -> str:
        """Running version — always the code constant, never the environment."""
        return APP_VERSION


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()


def storagehub_enabled() -> bool:
    """True once both the StorageHub URL and API key are configured."""
    return bool(settings.NETGEO_STORAGEHUB_URL and settings.NETGEO_STORAGEHUB_API_KEY)
