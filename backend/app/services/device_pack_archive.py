"""Validation and staged installation of removable device packs."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import struct
import tempfile
import zipfile
from pathlib import Path

from app.core.config import APP_VERSION

_ID = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
_HASH = re.compile(r"^[0-9a-f]{64}$")
_VERSION = re.compile(r"^\d+\.\d+\.\d+$")
_TYPES = {"core", "vendor", "accessory", "nos"}
_PORT_TYPES = {"eth", "sfp", "sfp+", "sfp28", "qsfp", "wifi", "gpon", "serial", "console", "xfp", "voice"}
MAX_ARCHIVE = 64 * 1024 * 1024
MAX_UNPACKED = 128 * 1024 * 1024


def _safe_path(name: str) -> bool:
    return bool(name) and not name.startswith("/") and "\\" not in name and all(
        part not in ("", ".", "..") for part in name.split("/")
    )


def check_device(pack_id: str, fname: str, dev: object, findings: list[str]) -> None:
    """Canonical device v2 structural checks, shared with repo-pack validation."""
    label = dev.get("id", "?") if isinstance(dev, dict) else "?"
    def bad(message: str) -> None:
        findings.append(f"{pack_id}/{fname}: {label}: {message}")
    if not isinstance(dev, dict):
        bad("device must be an object")
        return
    for key in ("id", "display_name", "vendor", "kind"):
        if not isinstance(dev.get(key), str) or not dev[key].strip():
            bad(f"missing required field '{key}'")
    if isinstance(dev.get("id"), str) and not _ID.fullmatch(dev["id"]):
        bad("invalid stable device id")
    if dev.get("pack_id", pack_id) != pack_id:
        bad("mismatched pack id")
    ports = dev.get("ports", [])
    if not isinstance(ports, list):
        bad("ports must be a list")
        ports = []
    for port in ports:
        if not isinstance(port, dict):
            bad("port must be an object")
            continue
        if not (port.get("pattern") or port.get("name")):
            bad("port without pattern/name")
        if port.get("type") and (not isinstance(port["type"], str) or port["type"] not in _PORT_TYPES):
            bad(f"unknown port type '{port['type']}'")
        if "count" in port and (not isinstance(port["count"], int) or isinstance(port["count"], bool) or port["count"] < 1):
            bad(f"bad port count {port.get('count')!r}")
    chassis = dev.get("chassis")
    if chassis is not None:
        if not isinstance(chassis, dict) or not isinstance(chassis.get("ru"), int) or chassis["ru"] < 0:
            bad("chassis.ru must be int >= 0")
        if isinstance(chassis, dict):
            for key in ("watts", "weight_kg"):
                if key in chassis and not (isinstance(chassis[key], (int, float)) and chassis[key] > 0):
                    bad(f"chassis.{key} must be > 0")
    rf = dev.get("rf")
    if rf is not None:
        rows = rf.get("mcs_table") if isinstance(rf, dict) else None
        if not isinstance(rows, list) or not rows:
            bad("rf block without mcs_table")
        else:
            for row in rows:
                if not isinstance(row, dict) or not all(key in row for key in ("mcs", "snr_db", "mbps")):
                    bad(f"incomplete mcs_table row {row!r}")
    optical = dev.get("optical_budget")
    if optical is not None:
        tx = optical.get("tx_dbm") if isinstance(optical, dict) else None
        if not (isinstance(tx, list) and len(tx) == 2 and all(isinstance(n, (int, float)) for n in tx) and tx[0] <= tx[1]):
            bad("optical_budget.tx_dbm must be [lo, hi]")
        if not isinstance(optical, dict) or "rx_sensitivity_dbm" not in optical:
            bad("optical_budget block without rx_sensitivity_dbm")


def validate_device_documents(pack_id: str, documents: dict[str, bytes], declared_count: object) -> set[str]:
    if not documents:
        raise ValueError("Pack must contain devices/*.json")
    findings: list[str] = []
    ids: set[str] = set()
    count = 0
    for name, raw in documents.items():
        try:
            data = json.loads(raw)
        except (UnicodeError, ValueError) as exc:
            raise ValueError(f"Unreadable device JSON: {name}") from exc
        if not isinstance(data, dict) or data.get("$schema") not in ("netgeo.device.v1", "netgeo.device.v2"):
            raise ValueError(f"Invalid device schema: {name}")
        if data.get("pack_id", pack_id) != pack_id:
            raise ValueError(f"Mismatched pack id: {name}")
        devices = data.get("devices")
        if not isinstance(devices, list) or not devices:
            raise ValueError(f"Empty device list: {name}")
        for dev in devices:
            check_device(pack_id, name, dev, findings)
            if isinstance(dev, dict) and isinstance(dev.get("id"), str):
                if dev["id"] in ids:
                    findings.append(f"Duplicate device id: {dev['id']}")
                ids.add(dev["id"])
            count += 1
    if findings:
        raise ValueError("; ".join(findings))
    if not isinstance(declared_count, int) or isinstance(declared_count, bool) or declared_count != count:
        raise ValueError(f"device_count {declared_count!r} != actual {count}")
    return ids


def validate_glb(raw: bytes, name: str) -> None:
    if len(raw) < 20 or raw[:4] != b"glTF":
        raise ValueError(f"Invalid GLB header: {name}")
    version, total = struct.unpack_from("<II", raw, 4)
    if version != 2 or total != len(raw):
        raise ValueError(f"Invalid GLB version/length: {name}")
    chunk_length, chunk_type = struct.unpack_from("<I4s", raw, 12)
    if chunk_type != b"JSON" or chunk_length == 0 or 20 + chunk_length > len(raw):
        raise ValueError(f"Invalid GLB JSON chunk: {name}")
    cursor = 20 + chunk_length
    if chunk_length % 4 or cursor % 4:
        raise ValueError(f"Unaligned GLB JSON chunk: {name}")
    if cursor < total:
        if cursor + 8 > total:
            raise ValueError(f"Invalid GLB BIN chunk: {name}")
        bin_length, bin_type = struct.unpack_from("<I4s", raw, cursor)
        if bin_type != b"BIN\x00" or bin_length % 4 or cursor + 8 + bin_length != total:
            raise ValueError(f"Invalid GLB BIN chunk: {name}")
    try:
        metadata = json.loads(raw[20:20 + chunk_length])
    except (ValueError, UnicodeError) as exc:
        raise ValueError(f"Invalid GLB JSON: {name}") from exc
    if not isinstance(metadata, dict) or not isinstance(metadata.get("asset"), dict) or metadata["asset"].get("version") != "2.0":
        raise ValueError(f"Invalid GLB asset metadata: {name}")


def validate_manifest(manifest: dict, files: dict[str, tuple[int, str]],
                      documents: dict[str, bytes], asset_bytes: dict[str, bytes]) -> None:
    """Raise ValueError for malformed metadata or undeclared/tampered bytes."""
    if manifest.get("schema") != "netgeo.pack.v1":
        raise ValueError("Unsupported pack manifest schema")
    pack_id = manifest.get("id")
    if not isinstance(pack_id, str) or not _ID.fullmatch(pack_id):
        raise ValueError("Invalid pack id")
    if manifest.get("type") not in _TYPES or not _VERSION.fullmatch(str(manifest.get("version", ""))):
        raise ValueError("Invalid pack type or version")
    if not isinstance(manifest.get("name"), str) or not manifest["name"].strip():
        raise ValueError("Pack name required")
    if manifest.get("enabled_by_default", False) is not False:
        raise ValueError("Removable packs must start disabled")
    compatibility = manifest.get("app_compatibility")
    if not isinstance(compatibility, dict) or not _VERSION.fullmatch(str(compatibility.get("min", ""))):
        raise ValueError("Minimum app version required")
    app = tuple(map(int, APP_VERSION.split(".")[:3]))
    minimum = tuple(map(int, compatibility["min"].split(".")))
    maximum_raw = compatibility.get("max")
    if maximum_raw is not None and not _VERSION.fullmatch(str(maximum_raw)):
        raise ValueError("Invalid maximum app version")
    if app < minimum or (maximum_raw and app > tuple(map(int, maximum_raw.split(".")))):
        raise ValueError("Pack incompatible with this app version")
    dependencies = manifest.get("dependencies")
    if not isinstance(dependencies, list) or any(not isinstance(d, str) or not _ID.fullmatch(d) for d in dependencies):
        raise ValueError("Invalid dependencies")
    if len(dependencies) != len(set(dependencies)) or pack_id in dependencies:
        raise ValueError("Duplicate or self dependency")
    provenance = manifest.get("provenance")
    licenses = manifest.get("licenses")
    if not isinstance(provenance, list) or not provenance or any(
        not isinstance(p, dict) or not isinstance(p.get("source"), str) or not p["source"].strip()
        for p in provenance
    ):
        raise ValueError("Provenance entries required")
    if not isinstance(licenses, list) or not licenses or any(
        not isinstance(p, dict) or not isinstance(p.get("name"), str) or not p["name"].strip()
        for p in licenses
    ):
        raise ValueError("License entries required")
    entries = manifest.get("files")
    if not isinstance(entries, list) or not entries:
        raise ValueError("Declared files required")
    declared: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise TypeError("Invalid file declaration")
        name = entry.get("path")
        if not isinstance(name, str) or not _safe_path(name) or name == "manifest.json" or name in declared:
            raise ValueError("Unsafe or duplicate file path")
        if not isinstance(entry.get("size"), int) or isinstance(entry["size"], bool) or entry["size"] < 0:
            raise ValueError("Invalid file size")
        if not isinstance(entry.get("sha256"), str) or not _HASH.fullmatch(entry["sha256"]):
            raise ValueError("Invalid SHA256")
        if files.get(name) != (entry["size"], entry["sha256"]):
            raise ValueError(f"File checksum/size mismatch: {name}")
        declared.add(name)
    if set(files) != declared:
        raise ValueError("Archive contains undeclared files")
    ids = validate_device_documents(pack_id, documents, manifest.get("device_count"))
    assets = manifest.get("assets", [])
    if not isinstance(assets, list):
        raise TypeError("Invalid assets")
    bound: set[str] = set()
    for asset in assets:
        if not isinstance(asset, dict):
            raise TypeError("Invalid asset")
        device_id, path = asset.get("device_id"), asset.get("path")
        if asset.get("kind") != "chassis-glb" or device_id not in ids or device_id in bound:
            raise ValueError("Invalid asset kind or device binding")
        if not isinstance(path, str) or not path.startswith("assets/") or not path.endswith(".glb") or path not in declared:
            raise ValueError("Invalid GLB asset path")
        validate_glb(asset_bytes[path], path)
        bound.add(device_id)


def install_archive(source: Path, destination: Path, installed_ids: set[str]) -> dict:
    """Install ZIP atomically; destination is the app-managed user pack root."""
    if source.stat().st_size > MAX_ARCHIVE:
        raise ValueError("Archive exceeds size limit")
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(source) as archive:
        infos = archive.infolist()
        if len(infos) > 256 or sum(info.file_size for info in infos) > MAX_UNPACKED:
            raise ValueError("Archive exceeds extraction limit")
        names = [i.filename for i in infos]
        if len(names) != len(set(names)) or "manifest.json" not in names:
            raise ValueError("Duplicate entry or missing manifest")
        if any(i.is_dir() or not _safe_path(i.filename) or (i.external_attr >> 16) & 0o170000 not in (0, 0o100000) for i in infos):
            raise ValueError("Unsafe archive entry")
        manifest = json.loads(archive.read("manifest.json"))
        if not isinstance(manifest, dict):
            raise TypeError("Invalid manifest")
        files = {}
        documents: dict[str, bytes] = {}
        asset_bytes: dict[str, bytes] = {}
        asset_paths = {entry.get("path") for entry in manifest.get("assets", []) if isinstance(entry, dict)}
        for info in infos:
            if info.filename == "manifest.json":
                continue
            digest = hashlib.sha256()
            with archive.open(info) as stream:
                collect = info.filename.startswith("devices/") and info.filename.endswith(".json") or info.filename in asset_paths
                chunks = [] if collect else None
                while chunk := stream.read(1024 * 1024):
                    digest.update(chunk)
                    if chunks is not None:
                        chunks.append(chunk)
            files[info.filename] = (info.file_size, digest.hexdigest())
            if chunks is not None:
                if info.filename in asset_paths:
                    asset_bytes[info.filename] = b"".join(chunks)
                else:
                    documents[info.filename] = b"".join(chunks)
        validate_manifest(manifest, files, documents, asset_bytes)
        pack_id = manifest["id"]
        if pack_id in installed_ids or (destination / pack_id).exists():
            raise ValueError("Pack id already installed")
        if missing := set(manifest["dependencies"]) - installed_ids:
            raise ValueError(f"Missing dependencies: {', '.join(sorted(missing))}")
        stage = Path(tempfile.mkdtemp(prefix=".pack-", dir=destination))
        try:
            for info in infos:
                target = stage / info.filename
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as reader, target.open("wb") as writer:
                    shutil.copyfileobj(reader, writer)
            os.replace(stage, destination / pack_id)
        finally:
            if stage.exists():
                shutil.rmtree(stage)
    return manifest
