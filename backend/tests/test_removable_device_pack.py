import hashlib
import io
import json
import zipfile

import pytest
from app.api import device_types as dt
from app.services.device_pack_archive import install_archive
from fastapi import HTTPException


def archive_bytes(*, name="sample", path="devices/sample.json", payload=b'{"devices": []}', dependency=None):
    manifest = {
        "schema": "netgeo.pack.v1", "id": name, "name": name, "version": "1.0.0",
        "type": "vendor", "app_compatibility": {"min": "1.0.0"},
        "dependencies": [dependency] if dependency else [],
        "provenance": [{"source": "NetGeo test fixture"}],
        "licenses": [{"name": "Apache-2.0"}],
        "files": [{"path": path, "size": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}],
    }
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        archive.writestr(path, payload)
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
    source.write_bytes(archive_bytes(path="assets/body.glb", payload=b"glTF-test"))
    install_archive(source, root, set())
    monkeypatch.setattr(dt, "_PACKS_DIR", tmp_path / "bundled")
    monkeypatch.setattr(dt, "_USER_PACKS_DIR", root)
    monkeypatch.setattr(dt, "_USER_PACKS_STATE_FILE", root / "enabled.json")
    monkeypatch.setattr(dt, "_PACKS_STATE_FILE", tmp_path / "bundled-state.json")
    with pytest.raises(HTTPException) as error:
        asyncio.run(dt.device_pack_asset("sample", "body.glb"))
    assert error.value.status_code == 404
    dt._set_pack_enabled("sample", True)
    assert asyncio.run(dt.device_pack_asset("sample", "body.glb")).path == root / "sample/assets/body.glb"
    with pytest.raises(HTTPException) as error:
        asyncio.run(dt.device_pack_asset("sample", "other.glb"))
    assert error.value.status_code == 404
