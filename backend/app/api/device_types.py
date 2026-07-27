"""Device-type registry endpoints.

Provides a catalog of known device types (AP, CPE, Tower, Switch, Router, …)
that the frontend map-mode uses to populate node-creation palettes and set
sensible defaults. Built-in types are read-only; operators may extend the
registry with custom entries at runtime.

Endpoints
---------
GET    /api/device-types              — list all device types (built-in + pack + custom)
POST   /api/device-types              — add a custom device type
DELETE /api/device-types/{id}         — remove a custom device type (built-ins protected)
POST   /api/device-types/upload-iso   — parse ISO 9660 image and register device type
GET    /api/device-packs              — list device library packs (id, enabled, device_count)
POST   /api/device-packs/{id}/enable  — enable a pack (its devices join GET /device-types)
POST   /api/device-packs/{id}/disable — disable a pack (its devices disappear, no restart)

Device library packs (NG-DL-02 / docs/design/15-DEVICE-LIBRARY-PACKS.md)
--------------------------------------------------------------------------
A pack is a folder under ``network/devices/packs/<pack-id>/`` holding a
``manifest.json`` plus ``devices/*.json`` (schema ``netgeo.device.v2`` —
additive extension of the existing ``netgeo.device.v1`` library schema with
optional ``snmp_oids``/``layout``/``rf``). Packs are scanned and their device
JSON re-parsed on every call — no import-time filesystem walk, no in-memory
cache, no plugin engine: enable/disable is just an entry in
``network/devices/packs_enabled.json`` and takes effect on the next request.
"""
from __future__ import annotations

import json
import logging
import struct
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, HTTPException, UploadFile, status
from pydantic import BaseModel, Field

from app.utils.ids import new_id

router = APIRouter(tags=["device-types"])
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class DeviceType(BaseModel):
    id: str
    name: str
    category: str = Field(
        default="custom",
        description="Logical group: wireless, wired, infrastructure, custom, …",
    )
    icon: str | None = Field(
        default=None,
        description="Icon identifier used by the frontend (e.g. 'ap', 'tower')",
    )
    description: str = ""
    builtin: bool = Field(default=False, description="True for read-only built-in types")
    # C-4/Rack#3: optional power + monitoring metadata. Backward-compatible —
    # older catalog entries without these fields remain valid.
    power_watts_idle: float | None = Field(
        default=None, description="Typical idle power draw in watts"
    )
    power_watts_max: float | None = Field(
        default=None, description="Maximum rated power draw in watts"
    )
    # Device console (docs/design/stitch-html/clay/device-console): the PoE
    # budget available to *connected* devices — distinct from this chassis's
    # own typical/max draw above. None when the pack's vendor datasheet never
    # published one (e.g. non-PoE switches) — the console must show "PoE
    # unavailable" for those, never a fabricated number.
    poe_budget_w: float | None = Field(
        default=None, description="Total PoE wattage this device can deliver to connected ports"
    )
    snmp_oids: dict[str, str] | None = Field(
        default=None, description="Named SNMP OIDs for monitoring this device type"
    )


class DeviceTypeCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    category: str = "custom"
    icon: str | None = None
    description: str = ""


# ---------------------------------------------------------------------------
# Built-in catalog  (immutable; returned in every GET response)
# ---------------------------------------------------------------------------

# Generic IF-MIB / RFC1213 OIDs, standard across vendors — enough for basic
# up/down + traffic polling without a per-vendor MIB library.
_IF_MIB_OIDS = {
    "sysUpTime": "1.3.6.1.2.1.1.3.0",
    "ifOperStatus": "1.3.6.1.2.1.2.2.1.8",
    "ifHCInOctets": "1.3.6.1.2.1.31.1.1.1.6",
}

# Power figures are representative per device *class* (idle / max rated draw,
# watts) drawn from typical datasheet ranges — not per-SKU precision. Good
# enough for rack power/heat rollups; see docs/research/rack-device-ui.md.
_BUILTIN: list[DeviceType] = [
    DeviceType(id="builtin-ap",      name="Access Point",   category="wireless",        icon="ap",      description="802.11 wireless access point",                builtin=True, power_watts_idle=7,   power_watts_max=15,  snmp_oids=_IF_MIB_OIDS),
    DeviceType(id="builtin-cpe",     name="CPE",            category="wireless",        icon="cpe",     description="Customer-premises equipment (wireless)",       builtin=True, power_watts_idle=6,   power_watts_max=12,  snmp_oids=_IF_MIB_OIDS),
    DeviceType(id="builtin-tower",   name="Tower",          category="wireless",        icon="tower",   description="Cellular / backhaul tower",                   builtin=True, power_watts_idle=150, power_watts_max=400, snmp_oids=_IF_MIB_OIDS),
    DeviceType(id="builtin-switch",  name="Switch",         category="wired",           icon="switch",  description="Layer-2 Ethernet switch",                     builtin=True, power_watts_idle=20,  power_watts_max=60,  snmp_oids=_IF_MIB_OIDS),
    DeviceType(id="builtin-router",  name="Router",         category="wired",           icon="router",  description="Layer-3 IP router",                           builtin=True, power_watts_idle=40,  power_watts_max=120, snmp_oids=_IF_MIB_OIDS),
    DeviceType(id="builtin-olt",     name="OLT",            category="fiber",           icon="olt",     description="Optical Line Terminal (PON/GPON headend)",    builtin=True, power_watts_idle=150, power_watts_max=350, snmp_oids=_IF_MIB_OIDS),
    DeviceType(id="builtin-onu",     name="ONU/ONT",        category="fiber",           icon="onu",     description="Optical Network Unit / Terminal (subscriber)", builtin=True, power_watts_idle=5,   power_watts_max=12,  snmp_oids=_IF_MIB_OIDS),
    DeviceType(id="builtin-fw",      name="Firewall",       category="security",        icon="fw",      description="Stateful packet-filter / NGFW",               builtin=True, power_watts_idle=30,  power_watts_max=90,  snmp_oids=_IF_MIB_OIDS),
    DeviceType(id="builtin-server",  name="Server",         category="infrastructure",  icon="server",  description="Physical or virtual server",                  builtin=True, power_watts_idle=150, power_watts_max=450, snmp_oids=_IF_MIB_OIDS),
    DeviceType(id="builtin-cloud",   name="Cloud / Internet", category="infrastructure", icon="cloud",  description="Internet gateway / cloud-uplink node",        builtin=True, power_watts_idle=0,   power_watts_max=0),
]

# Runtime-mutable store for operator-added custom types
_custom: dict[str, DeviceType] = {}


# ---------------------------------------------------------------------------
# Device library packs (drop-in JSON folders, see module docstring)
# ---------------------------------------------------------------------------

# repo-root/network/devices  (backend/app/api/ -> ../../../network/devices)
_DEVICES_DIR = Path(__file__).resolve().parents[3] / "network" / "devices"
_PACKS_DIR = _DEVICES_DIR / "packs"
_PACKS_STATE_FILE = _DEVICES_DIR / "packs_enabled.json"

# node.kind -> DeviceType.category, matching the styling _BUILTIN already uses.
# Unknown kinds fall back to the kind string itself — the frontend's category
# color map defaults unknown values to a neutral color, so this never breaks
# rendering.
_KIND_CATEGORY = {
    "router": "wired", "switch": "wired", "ap": "wireless",
    "olt": "fiber", "onu": "fiber",
    "firewall": "security", "server": "infrastructure", "cloud": "infrastructure",
}


def _iter_pack_manifests() -> list[tuple[Path, dict]]:
    """Scan ``packs/*/manifest.json``, skipping unreadable/invalid ones."""
    if not _PACKS_DIR.is_dir():
        return []
    found: list[tuple[Path, dict]] = []
    for manifest_path in sorted(_PACKS_DIR.glob("*/manifest.json")):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("device pack %s: invalid manifest.json: %s", manifest_path.parent.name, exc)
            continue
        manifest.setdefault("id", manifest_path.parent.name)
        found.append((manifest_path.parent, manifest))
    return found


def _load_pack_overrides() -> dict[str, bool]:
    """Explicit enable/disable overrides on top of each manifest's default."""
    if not _PACKS_STATE_FILE.exists():
        return {}
    try:
        data = json.loads(_PACKS_STATE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("packs_enabled.json: unreadable, ignoring overrides: %s", exc)
        return {}
    return data.get("overrides", {})


def _save_pack_overrides(overrides: dict[str, bool]) -> None:
    _PACKS_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _PACKS_STATE_FILE.write_text(json.dumps({"overrides": overrides}, indent=2) + "\n", encoding="utf-8")


def _pack_enabled(pack_id: str, manifest: dict, overrides: dict[str, bool]) -> bool:
    return overrides.get(pack_id, bool(manifest.get("enabled_by_default", False)))


def list_packs() -> list[dict]:
    """Every discovered pack's manifest plus its resolved enabled state."""
    overrides = _load_pack_overrides()
    return [
        {**manifest, "enabled": _pack_enabled(manifest["id"], manifest, overrides)}
        for _, manifest in _iter_pack_manifests()
    ]


def _device_json_to_type(pack_id: str, dev: dict) -> DeviceType:
    kind = dev.get("kind", "custom")
    power = dev.get("power") or {}
    return DeviceType(
        id=f"{pack_id}:{dev['id']}",
        name=dev.get("display_name", dev["id"]),
        category=_KIND_CATEGORY.get(kind, kind),
        icon=kind,
        description=dev.get("datasheet_note", ""),
        builtin=True,  # read-only, like _BUILTIN — managed via pack enable/disable, not DELETE
        power_watts_idle=power.get("typical_w"),
        power_watts_max=power.get("max_w"),
        poe_budget_w=power.get("poe_budget_w"),
        snmp_oids=dev.get("snmp_oids") or _IF_MIB_OIDS,
    )


def _load_enabled_pack_devices() -> list[DeviceType]:
    """Parse ``devices/*.json`` for every currently-enabled pack.

    On-demand, not cached: a disabled pack costs nothing (never parsed); an
    enabled pack is re-read every call. Fine at this scale (a handful of
    packs, tens of devices) — add a cache only if profiling says otherwise.
    """
    overrides = _load_pack_overrides()
    out: list[DeviceType] = []
    for pack_dir, manifest in _iter_pack_manifests():
        pack_id = manifest["id"]
        if not _pack_enabled(pack_id, manifest, overrides):
            continue
        for device_file in sorted((pack_dir / "devices").glob("*.json")):
            try:
                data = json.loads(device_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("device pack %s: invalid %s: %s", pack_id, device_file.name, exc)
                continue
            for dev in data.get("devices", []):
                if not dev.get("id") or not dev.get("kind"):
                    logger.warning(
                        "device pack %s: skipping device missing id/kind in %s",
                        pack_id, device_file.name,
                    )
                    continue
                out.append(_device_json_to_type(pack_id, dev))
    return out


class DevicePack(BaseModel):
    id: str
    name: str
    version: str = "0.0.0"
    categories: list[str] = []
    device_count: int = 0
    enabled_by_default: bool = False
    source_note: str = ""
    enabled: bool


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/device-types", response_model=list[DeviceType])
async def list_device_types() -> list[DeviceType]:
    """Return all device types: built-ins, then enabled pack devices, then custom."""
    return _BUILTIN + _load_enabled_pack_devices() + list(_custom.values())


@router.get("/device-packs", response_model=list[DevicePack])
async def list_device_packs() -> list[DevicePack]:
    """List every discovered device library pack and its enabled state."""
    return [DevicePack(**p) for p in list_packs()]


@router.post("/device-packs/{pack_id}/enable", response_model=DevicePack)
async def enable_device_pack(pack_id: str) -> DevicePack:
    return _set_pack_enabled(pack_id, True)


@router.post("/device-packs/{pack_id}/disable", response_model=DevicePack)
async def disable_device_pack(pack_id: str) -> DevicePack:
    return _set_pack_enabled(pack_id, False)


def _set_pack_enabled(pack_id: str, enabled: bool) -> DevicePack:
    manifests = {m["id"]: m for _, m in _iter_pack_manifests()}
    if pack_id not in manifests:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Device pack '{pack_id}' not found.")
    overrides = _load_pack_overrides()
    overrides[pack_id] = enabled
    _save_pack_overrides(overrides)
    return DevicePack(**manifests[pack_id], enabled=enabled)


@router.post("/device-types", response_model=DeviceType, status_code=status.HTTP_201_CREATED)
async def create_device_type(body: DeviceTypeCreate) -> DeviceType:
    """Register a new custom device type.

    The name must be unique across built-ins and existing custom types.
    """
    all_names = {dt.name.lower() for dt in _BUILTIN} | {dt.name.lower() for dt in _custom.values()}
    if body.name.lower() in all_names:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A device type named '{body.name}' already exists.",
        )
    dt = DeviceType(
        id=new_id(),
        name=body.name,
        category=body.category,
        icon=body.icon,
        description=body.description,
        builtin=False,
    )
    _custom[dt.id] = dt
    return dt


@router.delete("/device-types/{device_type_id}", status_code=status.HTTP_200_OK)
async def delete_device_type(device_type_id: str) -> dict:
    """Delete a custom device type.

    Built-in types cannot be deleted (returns 403).
    Non-existent IDs return 404.
    """
    # Check built-ins first
    if any(dt.id == device_type_id for dt in _BUILTIN):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Built-in device types cannot be deleted.",
        )
    if device_type_id not in _custom:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Device type '{device_type_id}' not found.",
        )
    del _custom[device_type_id]
    return {"deleted": device_type_id}


# ---------------------------------------------------------------------------
# ISO 9660 upload — parse label, size, architecture and register device type
# ---------------------------------------------------------------------------

class ISODeviceType(BaseModel):
    """Metadata extracted from an ISO 9660 image."""
    id:            str
    name:          str
    label:         str   = Field(description="ISO 9660 Volume Identifier")
    system_id:     str   = Field(description="ISO 9660 System Identifier field")
    architecture:  str   = Field(description="Inferred CPU architecture (x86_64, arm64, …)")
    size_bytes:    int   = Field(description="Declared image size in bytes")
    size_mb:       float = Field(description="Image size in megabytes (rounded 2 dp)")
    category:      str   = "iso"
    builtin:       bool  = False


# ISO 9660 Primary Volume Descriptor layout
# Absolute file offsets (sector 16 = byte 32768, each sector = 2048 bytes)
_PVD_OFFSET       = 32768   # byte offset of Primary Volume Descriptor in ISO
_PVD_TYPE_OFFSET  = 0       # relative offset: descriptor type (1 byte)
_PVD_ID_OFFSET    = 1       # relative: standard identifier "CD001" (5 bytes)
_PVD_SYS_ID_OFF   = 8       # relative: system identifier (32 bytes)
_PVD_VOL_ID_OFF   = 40      # relative: volume identifier / label (32 bytes)
_PVD_SPACE_OFF    = 80      # relative: volume space size LE+BE (8 bytes)
_PVD_BLOCK_SZ_OFF = 128     # relative: logical block size LE+BE (4 bytes)
_PVD_TYPE_PRIMARY = 1

_MIN_ISO_SIZE = _PVD_OFFSET + 512  # must have at least enough bytes to read PVD


def _parse_iso_9660(data: bytes) -> dict:
    """Extract label, size, and architecture from raw ISO 9660 bytes.

    Reads the Primary Volume Descriptor at byte offset 32768 (sector 16).

    Args:
        data: Raw bytes of the ISO image (at least 33280 bytes required).

    Returns:
        Dict with keys: label, system_id, architecture, size_bytes, size_mb.

    Raises:
        ValueError: If the data is too short or not a valid ISO 9660 image.
    """
    if len(data) < _MIN_ISO_SIZE:
        raise ValueError(
            f"File too small to be a valid ISO 9660 image "
            f"(got {len(data)} bytes, need ≥ {_MIN_ISO_SIZE})"
        )

    pvd = data[_PVD_OFFSET:]

    # Verify PVD signature
    pvd_type = pvd[_PVD_TYPE_OFFSET]
    std_id   = pvd[_PVD_ID_OFFSET: _PVD_ID_OFFSET + 5].decode("ascii", errors="replace")
    if pvd_type != _PVD_TYPE_PRIMARY or std_id != "CD001":
        raise ValueError(
            f"Not a valid ISO 9660 Primary Volume Descriptor "
            f"(type={pvd_type:#x}, id={std_id!r})"
        )

    # System Identifier (32 bytes, a-characters, space-padded)
    system_id = pvd[_PVD_SYS_ID_OFF: _PVD_SYS_ID_OFF + 32].decode(
        "ascii", errors="replace"
    ).strip()

    # Volume Identifier / Label (32 bytes, d-characters, space-padded)
    volume_id = pvd[_PVD_VOL_ID_OFF: _PVD_VOL_ID_OFF + 32].decode(
        "ascii", errors="replace"
    ).strip()

    # Volume Space Size: little-endian 4 bytes at offset 80
    volume_space_size_le = struct.unpack_from("<I", pvd, _PVD_SPACE_OFF)[0]

    # Logical Block Size: little-endian 2 bytes at offset 128
    logical_block_size = struct.unpack_from("<H", pvd, _PVD_BLOCK_SZ_OFF)[0]
    if logical_block_size == 0:
        logical_block_size = 2048  # ISO 9660 standard default

    size_bytes = volume_space_size_le * logical_block_size

    # Infer architecture from system identifier and volume label heuristics
    architecture = _infer_architecture(system_id, volume_id)

    return {
        "label":        volume_id or "UNKNOWN",
        "system_id":    system_id or "",
        "architecture": architecture,
        "size_bytes":   size_bytes,
        "size_mb":      round(size_bytes / (1024 * 1024), 2),
    }


_ARCH_KEYWORDS: list[tuple[list[str], str]] = [
    (["x86_64", "amd64", "x64", "64-bit"],              "x86_64"),
    (["i386", "i686", "x86", "32-bit"],                  "x86"),
    (["arm64", "aarch64", "armv8"],                       "arm64"),
    (["armv7", "armhf", "arm32"],                         "armv7"),
    (["mips64", "mips"],                                  "mips"),
    (["riscv64", "riscv"],                                "riscv64"),
    (["ppc64le", "powerpc", "ppc"],                       "ppc64le"),
    (["s390x"],                                           "s390x"),
]


def _infer_architecture(system_id: str, volume_id: str) -> str:
    """Best-effort architecture inference from ISO identifier strings."""
    combined = (system_id + " " + volume_id).lower()
    for keywords, arch in _ARCH_KEYWORDS:
        for kw in keywords:
            if kw in combined:
                return arch
    return "unknown"


@router.post(
    "/device-types/upload-iso",
    response_model=ISODeviceType,
    status_code=status.HTTP_201_CREATED,
    summary="Upload ISO image and register device type",
    description=(
        "Parse an ISO 9660 disc image (multipart/form-data), extract the "
        "volume label, declared size, and inferred CPU architecture from the "
        "Primary Volume Descriptor, then register the result as a custom "
        "device type in the registry."
    ),
)
async def upload_iso_device_type(
    file: Annotated[UploadFile, File(description="ISO 9660 disc image (.iso)")],
) -> ISODeviceType:
    """Parse ISO 9660 header and register device type from disc image.

    Reads up to the first 2 MB of the file (enough to cover the PVD at
    byte 32 768 plus margin). Registers the extracted metadata as a custom
    device type named after the volume label.

    Returns 400 if the file is not a valid ISO 9660 image.
    Returns 409 if a device type with the same label already exists.
    """
    # Read enough bytes to cover the PVD (sector 16 + slack)
    raw = await file.read(2 * 1024 * 1024)  # 2 MB cap

    try:
        meta = _parse_iso_9660(raw)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid ISO 9660 image: {exc}",
        ) from exc

    label = meta["label"]
    name = label.replace("_", " ").replace("-", " ").title()

    # Collision check against built-ins and existing custom types
    all_names = {dt.name.lower() for dt in _BUILTIN} | {
        dt.name.lower() for dt in _custom.values()
    }
    if name.lower() in all_names:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A device type named '{name}' (from ISO label '{label}') already exists.",
        )

    device_id = new_id()
    iso_dt = ISODeviceType(
        id=device_id,
        name=name,
        label=label,
        system_id=meta["system_id"],
        architecture=meta["architecture"],
        size_bytes=meta["size_bytes"],
        size_mb=meta["size_mb"],
        category="iso",
        builtin=False,
    )

    # Also store a plain DeviceType entry so it appears in the main listing
    _custom[device_id] = DeviceType(
        id=device_id,
        name=name,
        category="iso",
        icon="server",
        description=(
            f"ISO: {label} | arch={meta['architecture']} | "
            f"size={meta['size_mb']} MiB"
        ),
        builtin=False,
    )

    return iso_dt
