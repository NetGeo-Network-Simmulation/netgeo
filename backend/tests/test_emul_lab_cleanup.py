"""Startup failure tears down even partially created emulation resources."""
from __future__ import annotations

import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from app.models import Interface, Link, Node, Project, Topology
from app.services.emul_lab import EmulationLeaseUnavailable, EmulLabManager


@pytest.fixture(autouse=True)
def _isolated_lease(monkeypatch, tmp_path):
    monkeypatch.setattr("app.services.emul_lab.LEASE_PATH", tmp_path / "netgeo-emulation.lock")


async def test_failed_wire_cleans_partial_network_and_nodes(monkeypatch):
    events = []

    class FakeAdaptor:
        def __init__(self, **kwargs):
            pass

        async def spawn(self, device):
            events.append(("spawn", device.node_id))

        async def wire_link(self, link, a, b):
            events.append(("wire", link.id))
            raise RuntimeError("network connect failed")

        async def destroy_link(self, link_id):
            events.append(("unlink", link_id))

        async def destroy(self, node_id):
            events.append(("destroy", node_id))

    monkeypatch.setattr("app.services.emul_lab.PodmanAdaptor", FakeAdaptor)
    monkeypatch.setattr("app.services.emul_lab.socket_reachable", lambda: False)
    manager = EmulLabManager()
    topo = Topology(
        project=Project(id="p", name="P", mode="pure-emul"),
        nodes=[
            Node(id="a", project_id="p", name="a", kind="router", nos="frr", mode="emul",
                 interfaces=[Interface(id="ia", node_id="a", name="eth1")]),
            Node(id="b", project_id="p", name="b", kind="router", nos="frr", mode="emul",
                 interfaces=[Interface(id="ib", node_id="b", name="eth1")]),
        ],
        links=[Link(id="l", project_id="p", a_iface="ia", b_iface="ib")],
    )
    with pytest.raises(RuntimeError, match="network connect failed"):
        await manager.ping(topo, "a", "b", "l", 1)
    assert events == [
        ("spawn", "a"), ("spawn", "b"), ("wire", "l"),
        ("unlink", "l"), ("destroy", "b"), ("destroy", "a"),
    ]
    assert not manager._labs
    manager.release_lease()


async def test_shutdown_attempts_every_project_after_failure():
    events = []

    class FakeAdaptor:
        async def destroy_link(self, link_id):
            events.append(("link", link_id))
            if link_id == "bad-link":
                raise RuntimeError("unavailable")

        async def destroy(self, node_id):
            events.append(("node", node_id))

    manager = EmulLabManager()
    manager._labs = {
        "bad": ("fp", FakeAdaptor(), ["bad-node"], ["bad-link"]),
        "good": ("fp", FakeAdaptor(), ["good-node"], ["good-link"]),
    }
    with pytest.raises(Exception, match="bad:.*unavailable"):
        await manager.shutdown_all()
    assert events == [("link", "bad-link"), ("node", "bad-node"),
                      ("link", "good-link"), ("node", "good-node")]
    assert "good" not in manager._labs


async def test_reconciler_only_touches_labeled_netgeo_resources(monkeypatch):
    events = []
    owned = {"netgeo.managed": "true", "netgeo.project": "p", "netgeo.lab": "lab"}
    networks = [
        SimpleNamespace(name="netgeo-link-owned", attrs={"labels": {**owned, "netgeo.link": "owned"}}),
        SimpleNamespace(name="netgeo-link-other", attrs={"labels": {"netgeo.managed": "true"}}),
    ]
    containers = [
        SimpleNamespace(name="netgeo-owned", labels={**owned, "netgeo.kind": "frr"}),
        SimpleNamespace(name="netgeo-other", labels={"netgeo.managed": "true"}),
    ]

    class FakeAdaptor:
        def __init__(self):
            pass

        def _get_client(self):
            return SimpleNamespace(
                networks=SimpleNamespace(list=lambda: networks),
                containers=SimpleNamespace(list=lambda all: containers),
            )

        async def destroy_link(self, link_id):
            events.append(("link", link_id))

        async def destroy(self, node_id):
            events.append(("node", node_id))

    monkeypatch.setattr("app.services.emul_lab.socket_reachable", lambda: True)
    monkeypatch.setattr("app.services.emul_lab.PodmanAdaptor", FakeAdaptor)
    manager = EmulLabManager()
    await manager.reconcile_stale()
    assert events == [("link", "owned"), ("node", "owned")]
    await manager.shutdown_all()


async def test_second_manager_cannot_reconcile_or_spawn_until_lease_released(monkeypatch):
    calls = []
    monkeypatch.setattr("app.services.emul_lab.socket_reachable", lambda: True)

    class FakeAdaptor:
        def __init__(self, **kwargs):
            calls.append("adaptor")

        def _get_client(self):
            return SimpleNamespace(
                networks=SimpleNamespace(list=list),
                containers=SimpleNamespace(list=lambda all: []),
            )

    monkeypatch.setattr("app.services.emul_lab.PodmanAdaptor", FakeAdaptor)
    first, second = EmulLabManager(), EmulLabManager()
    await first.reconcile_stale()
    with pytest.raises(EmulationLeaseUnavailable, match="another NetGeo backend"):
        await second.reconcile_stale()
    topo = Topology(project=Project(id="p", name="P", mode="pure-emul"))
    with pytest.raises(EmulationLeaseUnavailable):
        await second._get(topo)
    second._labs["p"] = ("fp", SimpleNamespace(), ["node"], ["link"])
    with pytest.raises(EmulationLeaseUnavailable):
        await second.destroy("p")
    second._labs.clear()
    assert calls == ["adaptor"]
    first.release_lease()
    await second.reconcile_stale()
    assert calls == ["adaptor", "adaptor"]
    second.release_lease()


def test_lease_excludes_a_second_process(tmp_path):
    owner = EmulLabManager()
    owner.acquire_lease()
    env = {**os.environ, "XDG_RUNTIME_DIR": str(tmp_path)}
    probe = [sys.executable, "-c",
             "from app.services.emul_lab import EmulLabManager; EmulLabManager().acquire_lease()"]
    try:
        blocked = subprocess.run(probe, env=env, capture_output=True, text=True, check=False)
        assert blocked.returncode != 0
        assert "another NetGeo backend" in blocked.stderr
    finally:
        owner.release_lease()
    available = subprocess.run(probe, env=env, capture_output=True, text=True, check=False)
    assert available.returncode == 0, available.stderr
