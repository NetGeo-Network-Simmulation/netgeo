import hashlib
import io
import json
import zipfile

import pytest
from app.api import device_types as dt
from app.services.device_pack_archive import install_archive
from fastapi import HTTPException

DEVICE = {"$schema": "netgeo.device.v2", "devices": [{
    "id": "novel-switch", "display_name": "Novel Switch", "vendor": "Fixture", "kind": "switch",
    "ports": [{"name": "eth0", "type": "eth", "count": 1}],
}]}
GLB_JSON = b'{"asset":{"version":"2.0"}}'
GLB_JSON += b" " * (-len(GLB_JSON) % 4)
GLB = b"glTF" + (2).to_bytes(4, "little") + (20 + len(GLB_JSON)).to_bytes(4, "little") + len(GLB_JSON).to_bytes(4, "little") + b"JSON" + GLB_JSON


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
