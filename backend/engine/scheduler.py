"""The DES scheduler: advances the virtual clock by draining the event queue.

The scheduler is deliberately tiny and synchronous. It pulls the earliest
event, advances ``now`` to that event's time, and dispatches its handler. The
handler may push new events (always at ``time >= now``). The loop ends when the
queue empties, a time horizon is reached, or an external stop is requested.

Real-wall-clock pacing (so a UI can watch the sim "live") is handled one layer
up in :mod:`engine.simulation`; the scheduler itself runs as fast as possible.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from engine.events import EventQueue, SimEvent

logger = logging.getLogger(__name__)

# Called after every dispatched event: (now, event) -> None. Used for telemetry
# streaming and cooperative cancellation checks.
Observer = Callable[[float, SimEvent], None]


class Scheduler:
    """Single-threaded discrete-event scheduler with a monotonic clock."""

    __slots__ = (
        "_dispatched",
        "_observers",
        "_stop",
        "context",
        "dispatch_cap",
        "now",
        "queue",
    )

    def __init__(self, context: Any = None) -> None:
        self.queue = EventQueue()
        self.now: float = 0.0
        self.context = context  # passed to handlers as their first arg
        self._observers: list[Observer] = []
        self._stop = False
        self._dispatched = 0
        # Hard ceiling on lifetime dispatches — replay-to-cursor (NG-SIM-01)
        # sets this to freeze the sim at an exact event, across run() calls.
        self.dispatch_cap: int | None = None

    def schedule(self, event: SimEvent) -> None:
        """Push an event; clamps past-dated events to ``now`` defensively."""
        if event.time < self.now:
            logger.debug("event at t=%.6f < now=%.6f; clamping", event.time, self.now)
            event.time = self.now
        self.queue.push(event)

    def schedule_after(self, delay: float, event: SimEvent) -> None:
        """Convenience: schedule ``delay`` seconds from now."""
        event.time = self.now + max(0.0, delay)
        self.queue.push(event)

    def add_observer(self, observer: Observer) -> None:
        self._observers.append(observer)

    def request_stop(self) -> None:
        """Cooperatively stop the run after the current event."""
        self._stop = True

    def run(self, until: float | None = None, max_events: int | None = None) -> int:
        """Drain the queue until empty, horizon ``until``, or ``max_events``.

        Returns the number of events dispatched. Safe to call repeatedly to
        resume a paused run (the clock and queue persist).
        """
        self._stop = False
        dispatched_this_call = 0
        while self.queue and not self._stop:
            if self.dispatch_cap is not None and self._dispatched >= self.dispatch_cap:
                break
            next_t = self.queue.peek_time()
            if until is not None and next_t is not None and next_t > until:
                self.now = until
                break
            event = self.queue.pop()
            self.now = event.time
            if event.handler is not None:
                try:
                    event.handler(self.context, event)
                except Exception:  # pragma: no cover - a bad handler must not kill the run
                    logger.exception("handler failed for event %s at t=%.6f",
                                     event.type.name, self.now)
            self._dispatched += 1
            dispatched_this_call += 1
            for obs in self._observers:
                obs(self.now, event)
            if max_events is not None and dispatched_this_call >= max_events:
                break
        return dispatched_this_call

    @property
    def dispatched(self) -> int:
        return self._dispatched
