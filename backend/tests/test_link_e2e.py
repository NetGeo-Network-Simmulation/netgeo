"""N-3: PodmanAdaptor.wire_link/destroy_link against two real FRR containers.

Same cross-verification discipline as ``test_podman_adaptor.py``: never trust
the adaptor's own return value, shell out to ``podman network``/``podman ps``
to prove the network and attachments actually exist, and prove the link is
live with a real ``ping`` between the two containers.
"""
import json
import subprocess
import time

import pytest
from engine.emulation.ip_alloc import link_subnet
from engine.emulation.podman_adaptor import (
    CONTAINER_PREFIX,
    LINK_NETWORK_PREFIX,
    PodmanAdaptor,
    socket_reachable,
)
from engine.model import InterfaceModel, LinkModel
from engine.netstack.device import Device

pytestmark = pytest.mark.podman

skip_no_podman = pytest.mark.skipif(
    not socket_reachable(), reason="podman.socket unreachable — see PodmanSocketUnreachable"
)

LINK_ID = "n3-test-link"
NODE_A = "n3-test-r1"
NODE_B = "n3-test-r2"
NETWORK_NAME = f"{LINK_NETWORK_PREFIX}{LINK_ID}"


def _network_exists(name: str) -> bool:
    return subprocess.run(
        ["podman", "network", "inspect", name], capture_output=True, check=False
    ).returncode == 0


def _container_networks(container_name: str) -> dict:
    """``NetworkSettings.Networks`` of a container — stable across podman versions,
    unlike ``podman network inspect``'s ``containers`` field (schema/timing varies,
    see CI regression this helper replaced)."""
    out = subprocess.run(
        ["podman", "inspect", container_name, "--format", "{{json .NetworkSettings.Networks}}"],
        capture_output=True,
        text=True,
        check=False,
    )
    return json.loads(out.stdout or "{}")


def _wait_for_attachment(container_name: str, network_name: str, timeout: float = 10.0) -> bool:
    """Poll until ``network.connect()`` is visible on the container (handles slower
    CI runners), rather than a blind sleep."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if network_name in _container_networks(container_name):
            return True
        time.sleep(0.5)
    return False


def _container_state(container_name: str) -> str | None:
    out = subprocess.run(
        ["podman", "ps", "-a", "--filter", f"name=^{container_name}$", "--format", "{{.State}}"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return out or None


def _ping(from_container: str, dest_ip: str) -> subprocess.CompletedProcess:
    """Sync helper (not called directly from async test bodies — ASYNC221)."""
    return subprocess.run(
        ["podman", "exec", from_container, "ping", "-c1", "-W2", dest_ip],
        capture_output=True,
        check=False,
    )


@pytest.fixture
def adaptor():
    a = PodmanAdaptor()
    yield a
    # Teardown must run even if the test body failed mid-way — network first
    # (it references the containers), then the containers themselves.
    subprocess.run(["podman", "network", "rm", "-f", NETWORK_NAME], capture_output=True, check=False)
    for node_id in (NODE_A, NODE_B):
        subprocess.run(
            ["podman", "rm", "-f", f"{CONTAINER_PREFIX}{node_id}"], capture_output=True, check=False
        )


async def _spawn_pair(a: PodmanAdaptor) -> tuple[Device, Device]:
    dev_a = Device(name=NODE_A, node_id=NODE_A, nos="frr", mode="emul")
    dev_b = Device(name=NODE_B, node_id=NODE_B, nos="frr", mode="emul")
    await a.spawn(dev_a)
    await a.spawn(dev_b)
    return dev_a, dev_b


@skip_no_podman
async def test_wire_link_then_destroy(adaptor):
    a = adaptor
    await _spawn_pair(a)

    _, _, _ip_a, ip_b = link_subnet(LINK_ID)
    link = LinkModel(id=LINK_ID, a_iface="if-a", b_iface="if-b")
    iface_a = InterfaceModel(id="if-a", node_id=NODE_A, name="eth1")
    iface_b = InterfaceModel(id="if-b", node_id=NODE_B, name="eth1")

    await a.wire_link(link, iface_a, iface_b)

    assert _network_exists(NETWORK_NAME)
    assert _wait_for_attachment(f"{CONTAINER_PREFIX}{NODE_A}", NETWORK_NAME)
    assert _wait_for_attachment(f"{CONTAINER_PREFIX}{NODE_B}", NETWORK_NAME)

    # The link must actually pass traffic between the two real containers.
    ping = _ping(f"{CONTAINER_PREFIX}{NODE_A}", ip_b)
    assert ping.returncode == 0, ping.stdout.decode() + ping.stderr.decode()

    await a.destroy_link(LINK_ID)

    assert not _network_exists(NETWORK_NAME)
    # Containers themselves survive destroy_link — only the network is torn down.
    assert _container_state(f"{CONTAINER_PREFIX}{NODE_A}") == "running"
    assert _container_state(f"{CONTAINER_PREFIX}{NODE_B}") == "running"


@skip_no_podman
async def test_destroy_link_is_idempotent_on_missing_network(adaptor):
    a = adaptor
    await a.destroy_link("never-wired-link-id")  # must not raise
