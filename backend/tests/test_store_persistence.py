"""S2 PERSIST-01: MemoryRepository must survive a backend restart.

Uses an explicit ``state_path`` (never the real ``~/.config/netgeo/``) so
these tests stay hermetic — see conftest's NETGEO_STATE_STORE="" default too.
"""
from __future__ import annotations

import json
import os

from app.models import Node, Site
from app.store.memory import MemoryRepository


async def test_restart_reloads_identical_entities(tmp_path):
    """Create project/site/node, drop the repo, reload from the same file."""
    state_file = tmp_path / "state.json"
    repo1 = MemoryRepository(state_path=state_file)

    proj = await repo1.create_project("Lab", "desc")
    site = await repo1.add_site(Site(id="s1", project_id=proj.id, name="HQ"))
    node = await repo1.add_node(Node(id="n1", project_id=proj.id, name="Edge1", site_id=site.id))

    # Simulate a process restart: throw away the instance, load a fresh one
    # pointed at the same file.
    repo2 = MemoryRepository(state_path=state_file)

    got_proj = await repo2.get_project(proj.id)
    got_site = await repo2.get_site(site.id)
    got_node = await repo2.get_node(node.id)
    assert got_proj.id == proj.id and got_proj.name == "Lab"
    assert got_site.id == site.id and got_site.name == "HQ"
    assert got_node.id == node.id and got_node.site_id == site.id


async def test_state_file_is_atomic_and_0600(tmp_path):
    state_file = tmp_path / "state.json"
    repo = MemoryRepository(state_path=state_file)
    await repo.create_project("P")

    assert state_file.is_file()
    assert not state_file.with_suffix(".tmp").exists()  # no half-written temp left behind
    assert (os.stat(state_file).st_mode & 0o777) == 0o600


async def test_corrupt_state_file_starts_empty_and_warns(tmp_path, caplog):
    state_file = tmp_path / "state.json"
    state_file.write_text("{not valid json", encoding="utf-8")

    with caplog.at_level("WARNING"):
        repo = MemoryRepository(state_path=state_file)

    assert await repo.list_projects() == []
    assert any("corrupt" in r.message for r in caplog.records)
    # corrupt file is rescued to <state>.corrupt, not silently overwritten later
    corrupt_file = state_file.with_suffix(".corrupt")
    assert not state_file.exists()
    assert corrupt_file.read_text(encoding="utf-8") == "{not valid json"

    # A later mutation writes a fresh state.json without touching the rescue copy.
    await repo.create_project("P")
    assert state_file.is_file()
    assert corrupt_file.read_text(encoding="utf-8") == "{not valid json"


async def test_corrupt_state_file_rescue_overwrites_stale_corrupt_copy(tmp_path):
    state_file = tmp_path / "state.json"
    corrupt_file = state_file.with_suffix(".corrupt")
    corrupt_file.write_text("stale leftover", encoding="utf-8")
    state_file.write_text("{also not valid", encoding="utf-8")

    MemoryRepository(state_path=state_file)

    assert corrupt_file.read_text(encoding="utf-8") == "{also not valid"


async def test_partial_load_failure_discards_everything(tmp_path):
    """One bad entity in an otherwise-valid file must not leave a half-loaded repo."""
    state_file = tmp_path / "state.json"
    state_file.write_text(
        json.dumps(
            {
                "_projects": [{"id": "p1", "name": "Lab", "description": ""}],
                "_nodes": [{"id": "n1", "not_a_real_field": "boom"}],
            }
        ),
        encoding="utf-8",
    )

    repo = MemoryRepository(state_path=state_file)

    assert await repo.list_projects() == []


async def test_missing_state_file_starts_empty_silently(tmp_path, caplog):
    state_file = tmp_path / "does-not-exist.json"
    with caplog.at_level("WARNING"):
        repo = MemoryRepository(state_path=state_file)
    assert await repo.list_projects() == []
    assert caplog.records == []


async def test_state_path_none_disables_persistence(tmp_path):
    """Empty NETGEO_STATE_STORE (test default) means no file is ever written."""
    repo = MemoryRepository(state_path="")
    await repo.create_project("P")
    assert list(tmp_path.iterdir()) == []
