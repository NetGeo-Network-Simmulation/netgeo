"""Discrete-event primitives: the event type, the event, and the event queue.

Determinism is a first-class requirement (MASTER_SPEC §1, reproducible runs).
The queue therefore orders events by a stable triple:

    (time, priority, sequence)

``sequence`` is a monotonically increasing tie-breaker so two events scheduled
for the *same* simulation time always pop in insertion order. This makes a run
bit-for-bit reproducible given the same model and seed — essential for
checkpoint/resume and for the "verify in simulation before deploy" promise of
ForgeOS (§5).
"""
from __future__ import annotations

import heapq
import itertools
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any


class EventType(IntEnum):
    """Event kinds, also used as the secondary ordering priority.

    Lower value = higher priority at the same timestamp. Control-plane events
    (link up/down) are processed before data-plane packet movement so topology
    changes take effect for packets scheduled at the same instant.
    """

    SIM_START = 0
    LINK_UP = 1
    LINK_DOWN = 2
    NODE_UP = 3
    NODE_DOWN = 4
    PACKET_ENQUEUE = 5
    PACKET_TX = 6        # packet starts transmitting on a link
    PACKET_RX = 7        # packet fully received at the far end
    TIMER = 8            # protocol timer (hello, keepalive, ...)
    METRIC_SAMPLE = 9    # periodic telemetry sample
    SIM_END = 10


# A handler receives the simulation it runs in and the event being processed.
EventHandler = Callable[["Any", "SimEvent"], None]


@dataclass(slots=True)
class SimEvent:
    """A single thing that happens at a precise point in virtual time.

    Attributes:
        time:     simulation time in seconds (float, monotonic non-decreasing).
        type:     the :class:`EventType`, also the tie-break priority.
        handler:  callable invoked when the event is dispatched. May schedule
                  further events. If ``None`` the event is a pure marker.
        payload:  arbitrary event data (e.g. ``Packet``, node id, link id).
        node_id:  optional originating node, for filtering/telemetry.
    """

    time: float
    type: EventType
    handler: EventHandler | None = None
    payload: Any = None
    node_id: str | None = None
    # set by the queue on push; never set this by hand.
    _seq: int = field(default=-1, compare=False)


class EventQueue:
    """A min-heap priority queue of :class:`SimEvent`.

    Ordering key is ``(time, type, seq)``. ``seq`` guarantees FIFO behaviour
    among events that are otherwise equal, which is what makes runs
    deterministic.
    """

    __slots__ = ("_counter", "_heap", "_popped", "_pushed")

    def __init__(self) -> None:
        self._heap: list[tuple[float, int, int, SimEvent]] = []
        self._counter = itertools.count()
        self._pushed = 0
        self._popped = 0

    def push(self, event: SimEvent) -> None:
        """Schedule an event. Time must not be in the past relative to peek()."""
        seq = next(self._counter)
        event._seq = seq
        heapq.heappush(self._heap, (event.time, int(event.type), seq, event))
        self._pushed += 1

    def pop(self) -> SimEvent:
        """Remove and return the earliest event. Raises if empty."""
        if not self._heap:
            raise IndexError("pop from empty EventQueue")
        self._popped += 1
        return heapq.heappop(self._heap)[3]

    def peek_time(self) -> float | None:
        """Timestamp of the next event without removing it, or None if empty."""
        return self._heap[0][0] if self._heap else None

    def __len__(self) -> int:
        return len(self._heap)

    def __bool__(self) -> bool:
        return bool(self._heap)

    @property
    def stats(self) -> dict[str, int]:
        """Lightweight counters for observability."""
        return {"pushed": self._pushed, "popped": self._popped, "pending": len(self._heap)}
