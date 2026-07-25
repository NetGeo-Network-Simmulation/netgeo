#!/usr/bin/env python3
"""Validate network/devices/packs/*/devices/*.json against schema v2 (NG-DL-01/02).

Schema v2 is v1 plus optional enrichment blocks — existing v1-derived pack
files stay valid. Structural rules enforced here; also imported by
backend/tests/test_device_packs_schema.py so CI runs it as a test.

v2 optional blocks per device:
    chassis:  {"ru": int>=0, "watts": number>0, "weight_kg": number>0}
    personality: str            # NOS personality id (cli dialect + features)
    rf:       {"mcs_table": [{"mcs": int, "snr_db": num, "mbps": num}, ...]}
    optical_budget: {"tx_dbm": [lo, hi], "rx_sensitivity_dbm": num, "class": str}
    (v1 files already use a free-form "optical" key for DWDM notes — untouched)

Also checks each pack's manifest.json: id present and matching its directory
name, and device_count matching the number of devices actually found under
devices/*.json — cheap, real drift checks for a drop-in-JSON mechanism.

Usage: python3 scripts/validate_packs.py   (exit 1 on findings)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PACKS = Path(__file__).resolve().parents[1] / "network" / "devices" / "packs"

_PORT_TYPES = {"eth", "sfp", "sfp+", "sfp28", "qsfp", "wifi", "gpon", "serial", "console", "xfp"}


def _check_device(pack_id: str, fname: str, dev: dict, findings: list[str]) -> None:
    def bad(msg: str) -> None:
        findings.append(f"{pack_id}/{fname}: {dev.get('id', '?')}: {msg}")

    for key in ("id", "display_name", "vendor", "kind"):
        if not dev.get(key):
            bad(f"missing required field '{key}'")
    for port in dev.get("ports", []):
        if not (port.get("pattern") or port.get("name")):
            bad("port without pattern/name")
        if port.get("type") and port["type"] not in _PORT_TYPES:
            bad(f"unknown port type '{port['type']}'")
        if "count" in port and (not isinstance(port["count"], int) or port["count"] < 1):
            bad(f"bad port count {port.get('count')!r}")

    chassis = dev.get("chassis")
    if chassis is not None:
        if not isinstance(chassis.get("ru"), int) or chassis["ru"] < 0:
            bad("chassis.ru must be int >= 0")
        for k in ("watts", "weight_kg"):
            if k in chassis and not (isinstance(chassis[k], (int, float)) and chassis[k] > 0):
                bad(f"chassis.{k} must be > 0")

    rf = dev.get("rf")
    if rf is not None:
        rows = rf.get("mcs_table") or []
        if not rows:
            bad("rf block without mcs_table")
        for row in rows:
            if not all(k in row for k in ("mcs", "snr_db", "mbps")):
                bad(f"incomplete mcs_table row {row!r}")

    optical = dev.get("optical_budget")
    if optical is not None:
        tx = optical.get("tx_dbm")
        if not (isinstance(tx, list) and len(tx) == 2 and tx[0] <= tx[1]):
            bad("optical_budget.tx_dbm must be [lo, hi]")
        if "rx_sensitivity_dbm" not in optical:
            bad("optical_budget block without rx_sensitivity_dbm")


def _check_manifest(pack_dir: Path, findings: list[str]) -> None:
    manifest_path = pack_dir / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        findings.append(f"{pack_dir.name}/manifest.json: invalid JSON: {exc}")
        return

    if manifest.get("id") != pack_dir.name:
        findings.append(
            f"{pack_dir.name}/manifest.json: id {manifest.get('id')!r} "
            f"!= directory name {pack_dir.name!r}"
        )

    device_count = sum(
        len(json.loads(f.read_text(encoding="utf-8")).get("devices", []))
        for f in sorted((pack_dir / "devices").glob("*.json"))
    )
    if manifest.get("device_count") != device_count:
        findings.append(
            f"{pack_dir.name}/manifest.json: device_count {manifest.get('device_count')!r} "
            f"!= actual {device_count}"
        )


def validate() -> list[str]:
    findings: list[str] = []
    pack_dirs = sorted(p for p in PACKS.glob("*") if p.is_dir())
    if not pack_dirs:
        return [f"no device packs found under {PACKS}"]
    for pack_dir in pack_dirs:
        pack_id = pack_dir.name
        _check_manifest(pack_dir, findings)
        for path in sorted((pack_dir / "devices").glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                findings.append(f"{pack_id}/{path.name}: invalid JSON: {exc}")
                continue
            for dev in data.get("devices", []):
                _check_device(pack_id, path.name, dev, findings)
    return findings


if __name__ == "__main__":
    problems = validate()
    for p in problems:
        print(f"FAIL {p}")
    if not problems:
        print(f"OK — packs valid ({PACKS})")
    sys.exit(1 if problems else 0)
