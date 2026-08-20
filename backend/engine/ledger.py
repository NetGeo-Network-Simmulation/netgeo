"""Event ledger — deterministic record of every dispatched event.

Foundation for NG-SIM-01 (simulation-mode event list) and NG-NFR-01 (replay
determinism): attached as a plain scheduler observer, it keeps a ring buffer
of recent records for UIs and an *incremental* SHA-256 over the full event
stream so two runs can be compared byte-for-byte no matter how long they ran.
"""
from __future__ import annotations

import hashlib
from collections import deque
from typing import Any

from engine.events import SimEvent
from engine.scheduler import Scheduler


def _payload_fields(payload: Any) -> dict:
    """Rich per-frame fields when the event carries a FrameContext
    (duck-typed via ``ledger_fields`` to avoid importing the netstack)."""
    fields = getattr(payload, "ledger_fields", None)
    if callable(fields):
        return fields()
    return {"info": "" if payload is None else type(payload).__name__}


class Ledger:
    """Ring-buffered event records + incremental replay hash."""

    __slots__ = ("_hash", "records", "seq")

    def __init__(self, maxlen: int = 100_000) -> None:
        self.records: deque[dict] = deque(maxlen=maxlen)
        self.seq = 0
        self._hash = hashlib.sha256()

    def attach(self, scheduler: Scheduler) -> Ledger:
        scheduler.add_observer(self._on_event)
        return self

    def _on_event(self, now: float, event: SimEvent) -> None:
        self.seq += 1
        record = {
            "seq": self.seq,
            "t": round(now, 9),
            "type": event.type.name,
            "node": event.node_id or "",
            **_payload_fields(event.payload),
        }
        self.records.append(record)
        self._hash.update(
            f'{record["seq"]}|{record["t"]:.9f}|{record["type"]}|'
            f'{record["node"]}|{record.get("link", "")}|{record.get("info", "")}\n'.encode()
        )

    def hash(self) -> str:
        """Hex digest over every event recorded so far (not just the ring)."""
        return self._hash.copy().hexdigest()

    def tail(
        self,
        from_seq: int = 0,
        limit: int = 500,
        type_prefix: str | None = None,
        node: str | None = None,
    ) -> list[dict]:
        """Records after ``from_seq``, oldest first, optionally filtered."""
        out: list[dict] = []
        for r in self.records:
            if r["seq"] <= from_seq:
                continue
            if type_prefix and not r["type"].startswith(type_prefix):
                continue
            if node and r["node"] != node:
                continue
            out.append(r)
            if len(out) >= limit:
                break
        return out
