"""Request-body size cap (resource-exhaustion guard)."""
from __future__ import annotations

from app.main import _MAX_BODY_BYTES, MaxBodySizeMiddleware


async def _run(headers: list[tuple[bytes, bytes]]) -> list[dict]:
    """Drive the middleware with a fake downstream app and capture what it sends."""
    sent: list[dict] = []
    passed_through = []

    async def app(scope, receive, send):
        passed_through.append(True)

    async def send(msg):
        sent.append(msg)

    await MaxBodySizeMiddleware(app)({"type": "http", "headers": headers}, None, send)
    return sent, passed_through


async def test_oversized_content_length_rejected_413():
    sent, passed = await _run([(b"content-length", str(_MAX_BODY_BYTES + 1).encode())])
    assert not passed  # never reached the handler
    assert sent[0]["status"] == 413


async def test_normal_body_passes_through():
    sent, passed = await _run([(b"content-length", b"2048")])
    assert passed and sent == []


async def test_garbage_content_length_passes_through():
    # A non-numeric header must not crash the guard.
    _sent, passed = await _run([(b"content-length", b"not-a-number")])
    assert passed
