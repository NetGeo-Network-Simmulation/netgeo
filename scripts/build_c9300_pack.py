"""Build the removable C9300-48P + NM-8X pack from NetGeo's canonical sources."""
from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACK_ID = "cisco-c9300-48p-nm8x"
DEVICE_ID = "cisco-c9300-48p-access"
CATALOG = ROOT / "network/devices/packs/switches/devices/switches.json"
GLB = ROOT / "frontend/public/3d/brands/cisco/c9300-48p-nm8x.glb"
TIMESTAMP = (1980, 1, 1, 0, 0, 0)


def canonical_json(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def build(destination: Path) -> None:
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    (device,) = (item for item in catalog["devices"] if item["id"] == DEVICE_ID)
    documents = {
        "devices/c9300-48p-nm8x.json": canonical_json({
            "$schema": "netgeo.device.v2", "pack_id": PACK_ID, "devices": [device],
        }),
        "assets/c9300-48p-nm8x.glb": GLB.read_bytes(),
    }
    manifest = {
        "schema": "netgeo.pack.v1", "id": PACK_ID,
        "name": "Cisco Catalyst C9300-48P + NM-8X", "version": "1.0.0",
        "type": "vendor", "enabled_by_default": False,
        "app_compatibility": {"min": "1.2.125"}, "dependencies": [],
        "device_count": 1,
        "provenance": [
            {"source": "https://www.cisco.com/c/en/us/td/docs/switches/lan/catalyst9300/hardware/install/b_c9300_hig/technical-specs.html", "note": "Reference for chassis dimensions and electrical specifications only."},
            {"source": "https://www.cisco.com/c/en/us/td/docs/switches/lan/catalyst9300/hardware/install/b_c9300_hig/Product-overview.html", "note": "Reference for port, PSU, fan, and chassis arrangement facts only."},
            {"source": "https://www.cisco.com/c/en/us/td/docs/switches/lan/catalyst9300/hardware/install/b_c9300_hig/Installing-a-network-module.html", "note": "Reference for C9300-NM-8X eight SFP+ slots only."},
        ],
        "licenses": [{"name": "Apache-2.0", "scope": "Original NetGeo schema arrangement and Blender-authored GLB geometry only"}],
        "rights_note": "Cisco and Catalyst product names are Cisco trademarks used to identify the device. Technical specifications are factual references attributed in provenance; NetGeo does not claim ownership of or relicense these names or facts. No vendor artwork, CAD, logo, texture, or documentation text is included.",
        "files": [
            {"path": path, "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
            for path, raw in sorted(documents.items())
        ],
        "assets": [{"kind": "chassis-glb", "device_id": DEVICE_ID, "path": "assets/c9300-48p-nm8x.glb"}],
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w") as archive:
        for path, raw in [("manifest.json", canonical_json(manifest)), *sorted(documents.items())]:
            info = zipfile.ZipInfo(path, TIMESTAMP)
            info.compress_type = zipfile.ZIP_STORED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, raw)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="Output .ngpack archive path")
    build(parser.parse_args().output)
