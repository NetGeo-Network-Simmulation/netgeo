"""Startup failure tears down even partially created emulation resources."""
from __future__ import annotations

import pytest

from app.models import Interface, Link, Node, Project, Topology
from app.services.emul_lab import EmulLabManager


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
