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
sys.path.insert(0, str(PACKS.parents[2] / "backend"))
from app.services.device_pack_archive import validate_device_documents


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

    try:
        documents = {str(path.relative_to(pack_dir)): path.read_bytes()
                     for path in sorted((pack_dir / "devices").rglob("*.json"))}
        validate_device_documents(pack_dir.name, documents, manifest.get("device_count"))
    except (ValueError, OSError) as exc:
        findings.append(f"{pack_dir.name}: {exc}")


def validate() -> list[str]:
    findings: list[str] = []
    pack_dirs = sorted(p for p in PACKS.glob("*") if p.is_dir())
    if not pack_dirs:
        return [f"no device packs found under {PACKS}"]
    for pack_dir in pack_dirs:
        _check_manifest(pack_dir, findings)
    return findings


if __name__ == "__main__":
    problems = validate()
    for p in problems:
        print(f"FAIL {p}")
    if not problems:
        print(f"OK — packs valid ({PACKS})")
    sys.exit(1 if problems else 0)
