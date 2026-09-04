"""N-2: PodmanAdaptor spawns/destroys a real FRR container.

Cross-verifies against ``podman ps`` in a subprocess rather than trusting the
adaptor's own return value — the adaptor could lie about state, ``podman``
can't lie about its own containers.
"""
import subprocess

import pytest

from engine.emulation.adaptor import EmulationStatus
from engine.emulation.podman_adaptor import (
    CONTAINER_PREFIX,
    PodmanAdaptor,
    socket_reachable,
)
from engine.netstack.device import Device

pytestmark = pytest.mark.podman

skip_no_podman = pytest.mark.skipif(
    not socket_reachable(), reason="podman.socket unreachable — see PodmanSocketUnreachable"
)


def _podman_state(container_name: str) -> str | None:
    """State of a container per ``podman ps -a``, or None if it doesn't exist."""
    out = subprocess.run(
        ["podman", "ps", "-a", "--filter", f"name=^{container_name}$", "--format", "{{.State}}"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return out or None


@pytest.fixture
def adaptor():
    a = PodmanAdaptor()
    node_id = "n2-test-r1"
    yield a, node_id
    # Teardown must run even if the test body failed mid-way — never leave an
    # orphaned container on the dev machine.
    subprocess.run(
        ["podman", "rm", "-f", f"{CONTAINER_PREFIX}{node_id}"],
        capture_output=True,
        check=False,
    )


@skip_no_podman
async def test_spawn_then_destroy_real_container(adaptor):
    a, node_id = adaptor
    device = Device(name=node_id, node_id=node_id, nos="frr", mode="emul")
    container_name = f"{CONTAINER_PREFIX}{node_id}"

    handle = await a.spawn(device)
    assert handle.status == EmulationStatus.RUNNING
    assert _podman_state(container_name) == "running"

    await a.destroy(node_id)
    assert _podman_state(container_name) is None


@skip_no_podman
async def test_destroy_is_idempotent_on_missing_container(adaptor):
    a, node_id = adaptor
    await a.destroy(node_id)  # never spawned — must not raise
