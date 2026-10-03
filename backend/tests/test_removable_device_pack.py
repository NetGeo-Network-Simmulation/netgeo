import hashlib
import io
import json
import struct
import subprocess
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from app.api import device_types as dt
from app.models import Node
from app.services.device_pack_archive import install_archive
from app.store.memory import MemoryRepository
from fastapi import HTTPException


def test_c9300_pack_build_install_lifecycle(tmp_path, monkeypatch):
    """The shipped builder must produce an installable pack from core sources."""
    import asyncio
    from types import SimpleNamespace

    repo_root = Path(__file__).resolve().parents[2]
    script = repo_root / "scripts/build_c9300_pack.py"
    outputs = [tmp_path / f"build-{number}.ngpack" for number in (1, 2)]
    for output in outputs:
        subprocess.run([sys.executable, str(script), str(output)], check=True)
    assert outputs[0].read_bytes() == outputs[1].read_bytes()
    with zipfile.ZipFile(outputs[0]) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        source = json.loads((repo_root / "network/devices/packs/switches/devices/switches.json").read_text())
        device = json.loads(archive.read("devices/c9300-48p-nm8x.json"))["devices"][0]
        assert device == next(item for item in source["devices"] if item["id"] == device["id"])
        assert device["physical"]["depth_mm"] == 488
        assert device["physical"]["width_mm"] == 445
        assert device["physical"]["height_mm"] == 44
        assert [port["count"] for port in device["ports"]] == [48, 8]
        assert device["network_module"] == "C9300-NM-8X"
        assert all(entry["sha256"] == hashlib.sha256(archive.read(entry["path"])).hexdigest()
                   for entry in manifest["files"])
        assert manifest["licenses"] == [{
            "name": "Apache-2.0",
            "scope": "Original NetGeo schema arrangement and Blender-authored GLB geometry only",
        }]
        assert "does not claim ownership of or relicense these names or facts" in manifest["rights_note"]
        assert all(source["source"].startswith("https://www.cisco.com/") for source in manifest["provenance"])

    root = tmp_path / "installed"
    pack_id = "cisco-c9300-48p-nm8x"
    assert install_archive(outputs[0], root, {"switches"}) == manifest
    monkeypatch.setattr(dt, "_PACKS_DIR", tmp_path / "bundled")
    monkeypatch.setattr(dt, "_USER_PACKS_DIR", root)
    monkeypatch.setattr(dt, "_USER_PACKS_STATE_FILE", root / "enabled.json")
    monkeypatch.setattr(dt, "_PACKS_STATE_FILE", tmp_path / "bundled-state.json")
    monkeypatch.setattr(dt, "get_repo", lambda: SimpleNamespace(_nodes={}))
    device_type_id = f"{pack_id}:cisco-c9300-48p-access"
    def visible():
        return [item for item in asyncio.run(dt.list_device_types()) if item.id == device_type_id]
    assert not visible()
    with pytest.raises(HTTPException) as error:
        asyncio.run(dt.device_pack_asset(pack_id, "c9300-48p-nm8x.glb"))
    assert error.value.status_code == 404
    dt._set_pack_enabled(pack_id, True)
    assert len(visible()) == 1
    assert visible()[0].chassis_asset == device_type_id
    assert asyncio.run(dt.device_pack_asset(pack_id, "c9300-48p-nm8x.glb")).body == (
        repo_root / "frontend/public/3d/brands/cisco/c9300-48p-nm8x.glb").read_bytes()
    dt._set_pack_enabled(pack_id, False)
    assert not visible()
    with pytest.raises(HTTPException):
        asyncio.run(dt.device_pack_asset(pack_id, "c9300-48p-nm8x.glb"))
    assert asyncio.run(dt.uninstall_device_pack(pack_id)) == {"deleted": pack_id}
    assert not (root / pack_id).exists()

DEVICE = {"$schema": "netgeo.device.v2", "devices": [{
    "id": "novel-switch", "display_name": "Novel Switch", "vendor": "Fixture", "kind": "switch",
    "ports": [{"name": "eth0", "type": "eth", "count": 1}],
}]}
GLB_BIN = struct.pack("<9f", 0, 0, 0, 1, 0, 0, 0, 1, 0)
GLB_DOC = {
    "asset": {"version": "2.0"}, "scene": 0,
    "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0}],
    "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}],
    "accessors": [{"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3"}],
    "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": len(GLB_BIN)}],
    "buffers": [{"byteLength": len(GLB_BIN)}],
}


def glb(document):
    encoded = json.dumps(document, separators=(",", ":")).encode()
    encoded += b" " * (-len(encoded) % 4)
    total = 20 + len(encoded) + 8 + len(GLB_BIN)
    return b"glTF" + struct.pack("<II", 2, total) + struct.pack("<I4s", len(encoded), b"JSON") + encoded + struct.pack("<I4s", len(GLB_BIN), b"BIN\x00") + GLB_BIN


GLB = glb(GLB_DOC)


def archive_bytes(*, name="sample", path="devices/sample.json", payload=None, dependency=None,
                  asset=None, asset_kind="chassis-glb", asset_device="novel-switch", asset_path="assets/body.glb", device_count=1):
    payload = json.dumps(DEVICE).encode() if payload is None else payload
    manifest = {
        "schema": "netgeo.pack.v1", "id": name, "name": name, "version": "1.0.0",
        "type": "vendor", "app_compatibility": {"min": "1.0.0"},
        "dependencies": [dependency] if dependency else [],
        "provenance": [{"source": "NetGeo test fixture"}],
        "licenses": [{"name": "Apache-2.0"}],
        "device_count": device_count,
        "files": [{"path": path, "size": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}],
        "assets": [],
    }
    if asset is not None:
        manifest["assets"].append({"kind": asset_kind, "device_id": asset_device, "path": asset_path})
        manifest["files"].append({"path": asset_path, "size": len(asset), "sha256": hashlib.sha256(asset).hexdigest()})
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        archive.writestr(path, payload)
        if asset is not None:
            archive.writestr(asset_path, asset)
    return stream.getvalue()


def test_install_validation_and_atomicity(tmp_path):
    root = tmp_path / "packs"
    source = tmp_path / "pack.zip"
    source.write_bytes(archive_bytes())
    manifest = install_archive(source, root, set())
    assert manifest["id"] == "sample"
    assert (root / "sample/devices/sample.json").is_file()
    with pytest.raises(ValueError, match="already installed"):
        install_archive(source, root, {"sample"})
    source.write_bytes(archive_bytes(name="bad", path="../escape"))
    with pytest.raises(ValueError, match="Unsafe"):
        install_archive(source, root, set())
    assert not (tmp_path / "escape").exists()
    source.write_bytes(archive_bytes(name="bad", dependency="absent"))
    with pytest.raises(ValueError, match="Missing dependencies"):
        install_archive(source, root, set())
    assert not (root / "bad").exists()
    data = archive_bytes(name="tampered")
    with zipfile.ZipFile(io.BytesIO(data)) as source_zip:
        manifest = json.loads(source_zip.read("manifest.json"))
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as zip_out:
        zip_out.writestr("manifest.json", json.dumps(manifest))
        zip_out.writestr("devices/sample.json", b"changed")
    source.write_bytes(stream.getvalue())
    with pytest.raises(ValueError, match="checksum/size mismatch"):
        install_archive(source, root, set())


def test_toggle_and_uninstall_guards(tmp_path, monkeypatch):
    root = tmp_path / "packs"
    source = tmp_path / "pack.zip"
    source.write_bytes(archive_bytes())
    install_archive(source, root, set())
    monkeypatch.setattr(dt, "_PACKS_DIR", tmp_path / "bundled")
    monkeypatch.setattr(dt, "_USER_PACKS_DIR", root)
    monkeypatch.setattr(dt, "_USER_PACKS_STATE_FILE", root / "enabled.json")
    monkeypatch.setattr(dt, "_PACKS_STATE_FILE", tmp_path / "bundled-state.json")
    assert not dt.list_packs()[0]["enabled"]
    dt._set_pack_enabled("sample", True)
    assert dt.list_packs()[0]["enabled"]
    dt._set_pack_enabled("sample", False)
    assert not dt.list_packs()[0]["enabled"]
    dependent = root / "dependent"
    dependent.mkdir()
    (dependent / "manifest.json").write_text(json.dumps({"id": "dependent", "name": "Dependent", "dependencies": ["sample"]}))
    with pytest.raises(HTTPException) as error:
        dt._set_pack_enabled("dependent", True)
    assert error.value.status_code == 409
    dt._set_pack_enabled("sample", True)
    dt._set_pack_enabled("dependent", True)
    with pytest.raises(HTTPException) as error:
        dt._set_pack_enabled("sample", False)
    assert error.value.status_code == 409
    with pytest.raises(HTTPException) as error:
        import asyncio
        asyncio.run(dt.uninstall_device_pack("sample"))
    assert error.value.status_code == 409
    (dependent / "manifest.json").unlink()
    from types import SimpleNamespace
    monkeypatch.setattr(dt, "get_repo", lambda: SimpleNamespace(
        _nodes={"n": SimpleNamespace(device_type_id="sample:stable-device-id")},
    ))
    with pytest.raises(HTTPException) as error:
        asyncio.run(dt.uninstall_device_pack("sample"))
    assert error.value.status_code == 409
    assert (root / "sample").is_dir()
    monkeypatch.setattr(dt, "get_repo", lambda: SimpleNamespace(_nodes={}))
    import asyncio
    assert asyncio.run(dt.uninstall_device_pack("sample")) == {"deleted": "sample"}


def test_asset_served_only_when_declared_and_enabled(tmp_path, monkeypatch):
    import asyncio

    root = tmp_path / "packs"
    source = tmp_path / "pack.zip"
    source.write_bytes(archive_bytes(asset=GLB))
    install_archive(source, root, set())
    monkeypatch.setattr(dt, "_PACKS_DIR", tmp_path / "bundled")
    monkeypatch.setattr(dt, "_USER_PACKS_DIR", root)
    monkeypatch.setattr(dt, "_USER_PACKS_STATE_FILE", root / "enabled.json")
    monkeypatch.setattr(dt, "_PACKS_STATE_FILE", tmp_path / "bundled-state.json")
    with pytest.raises(HTTPException) as error:
        asyncio.run(dt.device_pack_asset("sample", "body.glb"))
    assert error.value.status_code == 404
    dt._set_pack_enabled("sample", True)
    assert asyncio.run(dt.device_pack_asset("sample", "body.glb")).body == GLB
    with pytest.raises(HTTPException) as error:
        asyncio.run(dt.device_pack_asset("sample", "other.glb"))
    assert error.value.status_code == 404
    (root / "sample/assets/body.glb").write_bytes(b"fake glb")
    with pytest.raises(HTTPException) as error:
        asyncio.run(dt.device_pack_asset("sample", "body.glb"))
    assert error.value.status_code == 404
    manifest = asyncio.run(dt.list_device_packs())[0]
    assert manifest.assets["sample:novel-switch"]["body"] == "/api/device-packs/sample/assets/body.glb"
    device = next(d for d in asyncio.run(dt.list_device_types()) if d.id == "sample:novel-switch")
    assert device.chassis_asset == "sample:novel-switch"


@pytest.mark.parametrize(("kwargs", "message"), [
    ({"payload": b"not json"}, "Unreadable device JSON"),
    ({"payload": b'{"$schema":"netgeo.device.v2","devices":[]}'}, "Empty device list"),
    ({"payload": b'{"$schema":"other","devices":[{}]}'}, "Invalid device schema"),
    ({"payload": json.dumps({**DEVICE, "pack_id": "other"}).encode()}, "Mismatched pack id"),
    ({"payload": json.dumps({"$schema": "netgeo.device.v2", "devices": [DEVICE["devices"][0]] * 2}).encode(), "device_count": 2}, "Duplicate device id"),
    ({"device_count": 2}, "device_count"),
    ({"asset": b"fake glb"}, "Invalid GLB header"),
    ({"asset": glb({**GLB_DOC, "scenes": []})}, "Invalid GLB renderable scene"),
    ({"asset": glb({**GLB_DOC, "buffers": [{"byteLength": len(GLB_BIN), "uri": "https://example.test/data.bin"}]})}, "Invalid GLB external URI"),
    ({"asset": glb({**GLB_DOC, "images": [{"uri": "https://example.test/logo.png"}]})}, "Invalid GLB external URI"),
    ({"asset": glb({**GLB_DOC, "scenes": [{"nodes": [99]}]})}, "Invalid GLB root node index"),
    ({"asset": GLB[:4] + (1).to_bytes(4, "little") + GLB[8:]}, "Invalid GLB version/length"),
    ({"asset": GLB[:8] + (999).to_bytes(4, "little") + GLB[12:]}, "Invalid GLB version/length"),
    ({"asset": GLB[:16] + b"BIN\x00" + GLB[20:]}, "Invalid GLB JSON chunk"),
    ({"asset": GLB, "asset_device": "missing"}, "Invalid asset kind or device binding"),
    ({"asset": GLB, "asset_kind": "image"}, "Invalid asset kind or device binding"),
    ({"asset": GLB, "asset_path": "assets/body.txt"}, "Invalid GLB asset path"),
])
def test_invalid_pack_rejected_without_install(tmp_path, kwargs, message):
    source = tmp_path / "pack.zip"
    source.write_bytes(archive_bytes(**kwargs))
    with pytest.raises(ValueError, match=message):
        install_archive(source, tmp_path / "packs", set())
    assert not (tmp_path / "packs/sample").exists()


def test_concurrent_pack_toggles_keep_every_override(tmp_path, monkeypatch):
    root = tmp_path / "packs"
    for index in range(20):
        path = root / f"pack-{index}"
        path.mkdir(parents=True)
        (path / "manifest.json").write_text(json.dumps({"id": path.name, "name": path.name}))
    monkeypatch.setattr(dt, "_PACKS_DIR", tmp_path / "bundled")
    monkeypatch.setattr(dt, "_USER_PACKS_DIR", root)
    monkeypatch.setattr(dt, "_USER_PACKS_STATE_FILE", root / "enabled.json")
    monkeypatch.setattr(dt, "_PACKS_STATE_FILE", tmp_path / "bundled-state.json")
    original = dt._load_pack_overrides
    def slow_read():
        value = original()
        time.sleep(0.003)
        return value
    monkeypatch.setattr(dt, "_load_pack_overrides", slow_read)
    with ThreadPoolExecutor(max_workers=10) as pool:
        list(pool.map(lambda index: dt._set_pack_enabled(f"pack-{index}", True), range(20)))
    data = json.loads((root / "enabled.json").read_text())
    assert len(data["overrides"]) == 20
    assert all(data["overrides"].values())
    assert not list(root.glob(".enabled.json.*.tmp"))


def test_uninstall_guard_sees_reloaded_persisted_project(tmp_path, monkeypatch):
    import asyncio

    root = tmp_path / "packs"
    source = tmp_path / "pack.zip"
    source.write_bytes(archive_bytes())
    install_archive(source, root, set())
    state = tmp_path / "state.json"
    repository = MemoryRepository(state_path=state)
    project = asyncio.run(repository.create_project("Persisted"))
    asyncio.run(repository.add_node(Node(id="node-1", project_id=project.id, name="Switch", device_type_id="sample:novel-switch")))
    reloaded = MemoryRepository(state_path=state)
    monkeypatch.setattr(dt, "_PACKS_DIR", tmp_path / "bundled")
    monkeypatch.setattr(dt, "_USER_PACKS_DIR", root)
    monkeypatch.setattr(dt, "_USER_PACKS_STATE_FILE", root / "enabled.json")
    monkeypatch.setattr(dt, "_PACKS_STATE_FILE", tmp_path / "bundled-state.json")
    monkeypatch.setattr(dt, "get_repo", lambda: reloaded)
    with pytest.raises(HTTPException) as error:
        asyncio.run(dt.uninstall_device_pack("sample"))
    assert error.value.status_code == 409
    assert (root / "sample").is_dir()
