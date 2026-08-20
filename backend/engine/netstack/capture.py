"""Per-link packet capture — the sim's built-in Wireshark.

Every frame transmitted or received on a link is recorded into a bounded ring
buffer (per link) so the UI can show a live capture inspector without the
memory footprint growing with simulation length.
"""
from __future__ import annotations

from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from engine.netstack.frames import EthernetFrame


@dataclass(slots=True)
class CaptureRecord:
    time: float
    link_id: str
    iface: str            # "<device>:<iface>" the event was observed on
    direction: str        # "tx" | "rx" | "drop"
    frame_id: int
    size: int
    info: str             # human summary line
    layers: dict          # structured layer dict for the inspector
    wire: bytes = b""     # synthesized on-wire bytes (pcapng export, NG-CAP-01)

    def as_dict(self) -> dict:
        return {
            "t": round(self.time, 9),
            "link_id": self.link_id,
            "iface": self.iface,
            "dir": self.direction,
            "frame_id": self.frame_id,
            "size": self.size,
            "info": self.info,
            "layers": self.layers,
        }


class CaptureManager:
    """Bounded per-link capture buffers."""

    def __init__(self, per_link_limit: int = 2000) -> None:
        self.per_link_limit = per_link_limit
        self._buffers: dict[str, deque[CaptureRecord]] = {}
        self.total_records = 0

    def record(
        self,
        time: float,
        link_id: str,
        iface: str,
        direction: str,
        frame: EthernetFrame,
    ) -> None:
        buf = self._buffers.get(link_id)
        if buf is None:
            buf = deque(maxlen=self.per_link_limit)
            self._buffers[link_id] = buf
        # Wire bytes are synthesized *now* (like the layers snapshot) because
        # forwarding mutates packets in place — TTL/NAT edits after delivery
        # must not rewrite history.
        from engine.netstack.pcapng import frame_bytes

        try:
            wire = frame_bytes(frame)
        except Exception:  # never let export cosmetics break the sim
            wire = bytes(frame.size_bytes)
        buf.append(
            CaptureRecord(
                time=time,
                link_id=link_id,
                iface=iface,
                direction=direction,
                frame_id=frame.id,
                size=frame.size_bytes,
                info=frame.summary(),
                layers=frame.layers(),
                wire=wire,
            )
        )
        self.total_records += 1

    def records(self, link_id: str | None = None, limit: int = 200) -> list[CaptureRecord]:
        """Latest records, newest last. ``link_id=None`` merges all links."""
        if link_id is not None:
            items: Iterable[CaptureRecord] = self._buffers.get(link_id, ())
            out = list(items)
        else:
            out = [r for buf in self._buffers.values() for r in buf]
            out.sort(key=lambda r: (r.time, r.frame_id))
        return out[-limit:]

    def clear(self) -> None:
        self._buffers.clear()
        self.total_records = 0
