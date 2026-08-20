"""Self-update endpoint tests.

Covers the v0.4.1 fixes:
  * APP_VERSION is a code constant — a stale env var must NOT override it
    (the "Current: 0.1 forever" bug from stamped .env files).
  * POST /update/apply works with just the admin session when UPDATE_TOKEN
    is unset, and enforces the X-Update-Token header when it is set.
  * /update/check reports can_apply/token_required for the UI.
"""
from __future__ import annotations

import pytest

from app.core import config as config_mod
from app.core.config import Settings, get_settings
from app.services import updater

# ---- Version source of truth -------------------------------------------------

def test_app_version_ignores_environment(monkeypatch):
    """A stamped APP_VERSION env var (old install.sh behaviour) is ignored."""
    monkeypatch.setenv("APP_VERSION", "0.1")
    fresh = Settings(_env_file=None)
    assert fresh.APP_VERSION == config_mod.APP_VERSION
    assert fresh.APP_VERSION != "0.1"


def test_check_uses_code_version(monkeypatch):
    """updater.check() compares against the code constant."""
    monkeypatch.setattr(
        updater, "_latest_release",
        lambda repo: {"version": "v99.0.0", "notes": "", "url": "", "published_at": ""},
    )
    payload = updater.check()
    assert payload["current"] == config_mod.APP_VERSION
    assert payload["update_available"] is True
    assert payload["can_apply"] is True
    assert payload["token_required"] is False


def test_check_reports_token_required(monkeypatch):
    monkeypatch.setattr(get_settings(), "UPDATE_TOKEN", "sekrit")
    monkeypatch.setattr(
        updater, "_latest_release",
        lambda repo: {"version": "v99.0.0", "notes": "", "url": "", "published_at": ""},
    )
    payload = updater.check()
    assert payload["can_apply"] is True
    assert payload["token_required"] is True


# ---- POST /update/apply guards ------------------------------------------------

@pytest.fixture
def _mock_apply(monkeypatch):
    """Never run the real updater (it would hit GitHub / spawn processes)."""
    monkeypatch.setattr(updater, "apply", lambda: {"state": "queued", "message": "mocked"})


async def test_apply_requires_auth(anon_client, _mock_apply):
    resp = await anon_client.post("/api/update/apply")
    assert resp.status_code == 401


async def test_apply_allowed_with_session_when_no_token_configured(client, _mock_apply):
    """UPDATE_TOKEN unset → the admin session alone is sufficient (no more 403)."""
    resp = await client.post("/api/update/apply")
    assert resp.status_code == 200, resp.text
    assert resp.json()["state"] == "queued"


async def test_apply_enforces_token_when_configured(client, _mock_apply, monkeypatch):
    monkeypatch.setattr(get_settings(), "UPDATE_TOKEN", "sekrit")

    resp = await client.post("/api/update/apply")
    assert resp.status_code == 401

    resp = await client.post("/api/update/apply", headers={"X-Update-Token": "wrong"})
    assert resp.status_code == 401

    resp = await client.post("/api/update/apply", headers={"X-Update-Token": "sekrit"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["state"] == "queued"
