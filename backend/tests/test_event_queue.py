"""Unit test terisolasi untuk EventQueue/SimEvent (engine/events.py).

Kontrak urutan sesuai docstring & kode di engine/events.py:
    key = (time, type, seq)
  - time menang atas segalanya.
  - pada time sama, EventType (IntEnum) jadi prioritas -- nilai lebih kecil
    duluan. Control-plane (LINK_UP=1, LINK_DOWN=2, NODE_UP=3, NODE_DOWN=4)
    memang bernilai lebih kecil dari data-plane (PACKET_ENQUEUE=5,
    PACKET_TX=6, PACKET_RX=7), jadi klaim "control-plane sebelum data-plane
    pada timestamp identik" nyata ada di kode, bukan asumsi.
  - pada time+type sama, seq (counter monotonik dari push()) menjaga FIFO.
"""
from __future__ import annotations

import pytest

from engine.events import EventQueue, EventType, SimEvent


def test_time_wins_over_everything():
    q = EventQueue()
    q.push(SimEvent(time=5.0, type=EventType.SIM_END))
    q.push(SimEvent(time=1.0, type=EventType.PACKET_RX))
    q.push(SimEvent(time=3.0, type=EventType.SIM_START))
    order = [q.pop().time for _ in range(3)]
    assert order == [1.0, 3.0, 5.0]


def test_control_plane_before_data_plane_at_same_time():
    """Pada timestamp identik, LINK_DOWN (control-plane) harus keluar duluan
    dari PACKET_TX (data-plane) -- ini kontrak nyata di EventType (nilai
    LINK_DOWN=2 < PACKET_TX=6), bukan sekadar klaim di komentar."""
    q = EventQueue()
    q.push(SimEvent(time=2.0, type=EventType.PACKET_TX))
    q.push(SimEvent(time=2.0, type=EventType.LINK_DOWN))
    first, second = q.pop(), q.pop()
    assert first.type is EventType.LINK_DOWN
    assert second.type is EventType.PACKET_TX


def test_seq_keeps_fifo_for_ties():
    """Time+type identik -> urutan push dipertahankan lewat seq."""
    q = EventQueue()
    events = [SimEvent(time=1.0, type=EventType.TIMER, payload=i) for i in range(5)]
    for e in events:
        q.push(e)
    popped_payloads = [q.pop().payload for _ in range(5)]
    assert popped_payloads == [0, 1, 2, 3, 4]


def test_seq_is_required_because_simevent_is_not_orderable():
    """Kalau seq TIDAK ikut dalam sort key, heapq akan membandingkan SimEvent
    langsung begitu (time, type) seri -- dan SimEvent (dataclass tanpa
    order=True) tidak punya __lt__, jadi heapq meledak dengan TypeError.
    Ini alasan konkret kenapa seq wajib ada di key, bukan cuma buat estetika.
    """
    a = SimEvent(time=1.0, type=EventType.TIMER)
    b = SimEvent(time=1.0, type=EventType.TIMER)
    with pytest.raises(TypeError):
        a < b  # noqa: B015 - membuktikan SimEvent memang tak orderable sendiri


def test_stats_reflect_push_and_pop_counts():
    q = EventQueue()
    q.push(SimEvent(time=0.0, type=EventType.SIM_START))
    q.push(SimEvent(time=0.0, type=EventType.SIM_END))
    q.pop()
    assert q.stats == {"pushed": 2, "popped": 1, "pending": 1}
