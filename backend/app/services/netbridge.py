"""Host network bridge — discover the machine's real ethernet adapters and
internet reachability so a NetGeo **cloud node** can attach the simulation to
the outside world (LAN / internet), GNS3-cloud style.

This module is read-only and side-effect free: it *reports* what the host has.
Actually wiring an emulated node onto a real NIC (macvlan/bridge) is the job of
an emulation adaptor (`engine/emulation/`), which consumes the adapter name a
cloud node is bound to (`Node.intent["uplink"]["adapter"]`).

Uses ``psutil`` when available and degrades gracefully to the standard library,
so the API never hard-fails just because psutil isn't installed.
"""
from __future__ import annotations

import socket
import time

try:  # psutil gives rich per-NIC data; optional so we never hard-depend on it.
    import psutil  # type: ignore
except Exception:  # pragma: no cover - psutil is listed in requirements
    psutil = None  # type: ignore

# NIC name fragments that are almost always virtual/internal, not a real uplink.
_VIRTUAL_HINTS = (
    "lo", "loopback", "docker", "veth", "br-", "virbr", "vmnet", "vboxnet",
    "tailscale", "wg", "tun", "tap", "zt", "hyper-v", "vethernet", "isatap",
    "bluetooth", "ppp",
)


def _primary_outbound_ip() -> str | None:
    """The source IP the OS would use to reach the internet (no traffic sent)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 53))
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


def _looks_virtual(name: str) -> bool:
    low = name.lower()
    return any(h in low for h in _VIRTUAL_HINTS)


def list_interfaces() -> list[dict]:
    """All host network adapters with addresses, link state, speed and MTU.

    The adapter carrying the primary outbound route is flagged ``is_primary``;
    likely-virtual adapters (docker/veth/loopback/VPN) are flagged ``is_virtual``
    so the UI can default to a real ethernet uplink.
    """
    primary_ip = _primary_outbound_ip()
    out: list[dict] = []

    if psutil is None:
        # Minimal fallback: hostname's primary address only.
        host = socket.gethostname()
        try:
            ip = socket.gethostbyname(host)
        except OSError:
            ip = primary_ip or "127.0.0.1"
        return [
            {
                "name": host or "primary",
                "mac": None,
                "ipv4": [ip],
                "ipv6": [],
                "is_up": True,
                "speed_mbps": 0,
                "mtu": 1500,
                "is_virtual": False,
                "is_primary": ip == primary_ip,
            }
        ]

    addrs = psutil.net_if_addrs()
    stats = psutil.net_if_stats()
    for name, addr_list in addrs.items():
        ipv4, ipv6, mac = [], [], None
        for a in addr_list:
            if a.family == socket.AF_INET:
                ipv4.append(a.address)
            elif a.family == socket.AF_INET6:
                ipv6.append(a.address.split("%")[0])  # strip zone id
            elif getattr(socket, "AF_PACKET", None) and a.family == socket.AF_PACKET or getattr(psutil, "AF_LINK", None) and a.family == psutil.AF_LINK:
                mac = a.address
        st = stats.get(name)
        out.append(
            {
                "name": name,
                "mac": mac,
                "ipv4": ipv4,
                "ipv6": ipv6,
                "is_up": bool(st.isup) if st else False,
                "speed_mbps": int(st.speed) if st and st.speed else 0,
                "mtu": int(st.mtu) if st else 1500,
                "is_virtual": _looks_virtual(name),
                "is_primary": bool(primary_ip and primary_ip in ipv4),
            }
        )

    # Real, up, non-virtual adapters first; primary on top.
    out.sort(key=lambda i: (not i["is_primary"], i["is_virtual"], not i["is_up"], i["name"]))
    return out


def internet_status(probe_host: str = "1.1.1.1", probe_port: int = 53,
                     timeout: float = 2.0) -> dict:
    """Check internet reachability by opening a TCP socket to a public resolver.

    Returns ``{online, latency_ms, via, source_ip}``. No DNS lookup or HTTP
    request is made, so it stays fast and works on locked-down hosts.
    """
    source_ip = _primary_outbound_ip()
    start = time.perf_counter()
    online = False
    try:
        with socket.create_connection((probe_host, probe_port), timeout=timeout):
            online = True
    except OSError:
        online = False
    latency_ms = round((time.perf_counter() - start) * 1000, 1) if online else None
    return {
        "online": online,
        "latency_ms": latency_ms,
        "via": f"{probe_host}:{probe_port}",
        "source_ip": source_ip,
    }
