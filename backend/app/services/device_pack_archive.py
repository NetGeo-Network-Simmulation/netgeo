"""Validation and staged installation of removable device packs."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import zipfile
from pathlib import Path

from app.core.config import APP_VERSION

_ID = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
_HASH = re.compile(r"^[0-9a-f]{64}$")
_VERSION = re.compile(r"^\d+\.\d+\.\d+$")
_TYPES = {"core", "vendor", "accessory", "nos"}
MAX_ARCHIVE = 64 * 1024 * 1024
MAX_UNPACKED = 128 * 1024 * 1024


def _safe_path(name: str) -> bool:
    return bool(name) and not name.startswith("/") and "\\" not in name and all(
        part not in ("", ".", "..") for part in name.split("/")
    )


def validate_manifest(manifest: dict, files: dict[str, tuple[int, str]]) -> None:
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
    assets = manifest.get("assets", {})
    if not isinstance(assets, dict):
        raise TypeError("Invalid assets")
    for value in assets.values():
        if not isinstance(value, dict):
            raise TypeError("Invalid asset")
        for asset in value.values():
            prefix = f"/api/device-packs/{pack_id}/"
            if not isinstance(asset, str) or not asset.startswith(prefix):
                raise ValueError("Asset URL must reference this pack")
            if asset.removeprefix(prefix) not in declared:
                raise ValueError("Asset file not declared")


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
        for info in infos:
            if info.filename == "manifest.json":
                continue
            digest = hashlib.sha256()
            with archive.open(info) as stream:
                while chunk := stream.read(1024 * 1024):
                    digest.update(chunk)
            files[info.filename] = (info.file_size, digest.hexdigest())
        validate_manifest(manifest, files)
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
