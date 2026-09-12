"""Oracle harness: OSPF DR/BDR election run against BOTH our sim engine and
a real FRR container on the identical topology, comparing only the outcome
RFC 2328 actually mandates — who becomes DR/BDR/DROther for a given set of
priorities/router-ids, and that the election is non-preemptive (§9.4). CLI
output format, packet order and timing are never compared; those are
implementation detail, not standard.

Why OSPF over BGP: DR/BDR election has a hard deterministic total order
(priority desc, then router-id desc, no randomness — see
``engine/netstack/protocols/ospf.py`` module docstring) so "did both engines
pick the same router" is an objective yes/no, unlike e.g. BGP best-path
which has many equally-valid tie-break knobs.

Marked ``oracle`` (registered in pytest.ini), deliberately NOT ``podman``:
the CI podman-smoke required merge gate runs ``pytest -m podman -q`` and
does not select these — see ORACLE_HARNESS.md (this directory) for why,
and how to run this file on purpose (``pytest -m oracle``).
``skip_no_podman`` still applies underneath, so a machine without podman
just skips cleanly, same pattern as test_podman_adaptor.py.
"""
from __future__ import annotations

import io
import subprocess
import tarfile
import time

import pytest

from engine.emulation.podman_adaptor import (
    CONTAINER_PREFIX,
    PodmanAdaptor,
    socket_reachable,
)
from engine.netstack import Network
from engine.netstack.device import Device as EmulatedDevice
from engine.netstack.protocols.ospf import OspfProcess
from engine.netstack.routing import Router
from engine.netstack.switching import Switch

pytestmark = pytest.mark.oracle

skip_no_podman = pytest.mark.skipif(
    not socket_reachable(), reason="podman.socket unreachable — see PodmanSocketUnreachable"
)

NET_NAME = "netgeo-oracle-ospf-net"
SUBNET = "10.201.97.0/29"
NODES = ("oracle-r1", "oracle-r2", "oracle-r3")
IPS = {"oracle-r1": "10.201.97.2", "oracle-r2": "10.201.97.3", "oracle-r3": "10.201.97.4"}
ROUTER_IDS = {"oracle-r1": "1.1.1.1", "oracle-r2": "2.2.2.2", "oracle-r3": "3.3.3.3"}

# ospfd off by default in the upstream image (verified against the running
# image on this machine) — this file only flips that one flag.
_DAEMONS_OSPF_ON = (
    "bgpd=no\nospfd=yes\nospf6d=no\nripd=no\nripngd=no\nisisd=no\npimd=no\n"
    "pim6d=no\nldpd=no\nnhrpd=no\neigrpd=no\nbabeld=no\nsharpd=no\npbrd=no\n"
    "bfdd=no\nfabricd=no\nvrrpd=no\npathd=no\nvtysh_enable=yes\n"
)


def _put_text(container, dirpath: str, filename: str, content: str) -> None:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        data = content.encode()
        info = tarfile.TarInfo(name=filename)
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    buf.seek(0)
    container.put_archive(dirpath, buf.read())


def _iface_for_ip(container, ip: str, timeout: float = 5.0) -> str:
    """The container's own NIC name carrying ``ip`` — never assumed (podman
    picks eth1/eth2/... by attach order, not something callers should guess).
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        _ec, (out, _err) = container.exec_run(
            ["sh", "-c", f"ip -o -4 addr show | grep {ip}"], demux=True
        )
        line = (out or b"").decode()
        if line.strip():
            return line.split()[1]
        time.sleep(0.3)
    raise RuntimeError(f"no interface carries {ip!r} in {container.name}")


def _frr_conf(hostname: str, router_id: str, iface: str, priority: int, network_cidr: str) -> str:
    return (
        f"frr version 10.7\nfrr defaults traditional\nhostname {hostname}\n!\n"
        f"interface {iface}\n"
        " ip ospf hello-interval 1\n ip ospf dead-interval 4\n"
        f" ip ospf priority {priority}\n!\n"
        f"router ospf\n ospf router-id {router_id}\n network {network_cidr} area 0\n!\nline vty\n!\n"
    )


def _configure_ospf(container, hostname: str, router_id: str, ip: str, priority: int) -> str:
    """Enable ospfd (needs one restart — done before any per-link network is
    attached, so netavark's IP-reassign-on-restart quirk never touches an IP
    a test depends on) and load an OSPF config over the already-wired link.
    Returns the container's interface name for that link."""
    iface = _iface_for_ip(container, ip)
    _put_text(container, "/etc/frr", "frr.conf", _frr_conf(hostname, router_id, iface, priority, SUBNET))
    container.exec_run(["chown", "frr:frr", "/etc/frr/frr.conf"])
    container.exec_run(["chmod", "640", "/etc/frr/frr.conf"])
    # vtysh -b re-applies /etc/frr/frr.conf into the already-running daemons —
    # no container restart needed here, which is what would reshuffle the IP.
    container.exec_run(["vtysh", "-b"])
    return iface


def _ospf_neighbor_state(container, peer_router_id: str) -> str | None:
    _ec, (out, _err) = container.exec_run(["vtysh", "-c", "show ip ospf neighbor"], demux=True)
    for line in (out or b"").decode().splitlines():
        if line.startswith(peer_router_id):
            # e.g. "2.2.2.2  20 Full/Backup  0.32s ..." -> "Full/Backup"
            return line.split()[2]
    return None


def _dr_role(container, iface: str) -> str:
    """DR / Backup / DROther per ``show ip ospf interface`` 'State' field —
    the field FRR itself uses for the role, not something this test derives."""
    _ec, (out, _err) = container.exec_run(
        ["vtysh", "-c", f"show ip ospf interface {iface}"], demux=True
    )
    for line in (out or b"").decode().splitlines():
        # e.g. "  Transmit Delay is 1 sec, State DR, Priority 30" — "State "
        # sits mid-line, not at line start.
        if "State " in line:
            after = line.split("State ", 1)[1]
            return after.split(",")[0].strip()
    raise AssertionError(f"no OSPF interface state line for {iface}")


@pytest.fixture
def frr_cluster():
    """Spawns the 3 oracle-r* FRR containers + their shared broadcast network,
    yields (adaptor, client), and tears every bit of it down in the fixture's
    post-yield block — which pytest runs even when the test body raises,
    exactly like a ``finally``. Verified via ``podman`` CLI in the test body
    itself (never trust the adaptor's own return value alone, same discipline
    as test_podman_adaptor.py / test_link_e2e.py).
    """
    a = PodmanAdaptor()
    client = a._get_client()
    try:
        yield a, client
    finally:
        subprocess.run(
            ["podman", "network", "rm", "-f", NET_NAME], capture_output=True, check=False
        )
        for node_id in NODES:
            subprocess.run(
                ["podman", "rm", "-f", f"{CONTAINER_PREFIX}{node_id}"],
                capture_output=True,
                check=False,
            )


def _container_names_alive() -> list[str]:
    out = subprocess.run(
        ["podman", "ps", "-a", "--format", "{{.Names}}"], capture_output=True, text=True, check=True
    ).stdout
    return [n for n in out.splitlines() if n.startswith(CONTAINER_PREFIX + "oracle-")]


async def _spawn_and_wire(a: PodmanAdaptor, client, node_ids: list[str]):
    for node_id in node_ids:
        await a.spawn(EmulatedDevice(name=node_id, node_id=node_id, nos="frr", mode="emul"))
    containers = {n: client.containers.get(f"{CONTAINER_PREFIX}{n}") for n in node_ids}
    # Enable ospfd BEFORE wiring — the one restart this needs must happen
    # while each container only has its default network, so it can't disturb
    # the per-link static IP assigned afterwards (podman-py .connect()).
    for c in containers.values():
        _put_text(c, "/etc/frr", "daemons", _DAEMONS_OSPF_ON)
        c.restart(timeout=5)
    network = client.networks.create(
        NET_NAME,
        driver="bridge",
        labels={"netgeo.managed": "true"},
        ipam={"Config": [{"Subnet": SUBNET}]},
    )
    for node_id, c in containers.items():
        network.connect(c, ipv4_address=IPS[node_id])
    return containers


def _sim_lan_election(priorities: dict[str, int]) -> tuple[Network, dict[str, OspfProcess]]:
    """Build the identical topology (one broadcast LAN, same router-ids and
    priorities) in the sim engine and run it to convergence."""
    net = Network(seed=97)
    routers = {n: net.add_device(Router(n)) for n in priorities}
    sw = net.add_device(Switch("sw"))
    procs: dict[str, OspfProcess] = {}
    for idx, (name, prio) in enumerate(priorities.items()):
        ip = IPS[name].rsplit(".", 1)[1]
        iface = net.add_iface(routers[name], "eth0", [f"10.0.0.{ip}/29"])
        net.connect(f"l{idx}", iface, net.add_iface(sw, f"gi0/{idx}"))
        procs[name] = OspfProcess(
            routers[name], router_id=ROUTER_IDS[name], hello_interval=1.0, dead_interval=4.0,
            areas={"eth0": 0}, priorities={"eth0": prio},
        )
    net.start()
    net.run(until=15.0)
    return net, procs


def _sim_dr_role(net: Network, procs: dict[str, OspfProcess], name: str) -> str:
    return procs[name]._iface_dr["eth0"].state  # "dr" | "backup" | "drother"


def _wait_for_dr_bdr(containers: dict, ifaces: dict, names, timeout: float = 20.0) -> dict[str, str]:
    """Sync helper (not called directly from async test bodies — ASYNC251,
    same pattern test_link_e2e.py uses for its own blocking polls)."""
    deadline = time.monotonic() + timeout
    roles: dict[str, str] = {}
    while time.monotonic() < deadline:
        roles = {n: _dr_role(containers[n], ifaces[n]) for n in names}
        if list(roles.values()).count("DR") == 1 and list(roles.values()).count("Backup") == 1:
            break
        time.sleep(1)
    return roles


def _wait_for_dr(container, iface: str, timeout: float = 20.0) -> str:
    """Sync helper — see _wait_for_dr_bdr."""
    deadline = time.monotonic() + timeout
    role = ""
    while time.monotonic() < deadline:
        role = _dr_role(container, iface)
        if role == "DR":
            break
        time.sleep(1)
    return role


def _settle(seconds: float) -> None:
    """Sync helper — see _wait_for_dr_bdr."""
    time.sleep(seconds)


# ---------------------------------------------------------------------------
# Oracle case 1: initial election picks DR=highest priority, BDR=runner-up,
# same total order in both engines (RFC 2328 §9.4).
# ---------------------------------------------------------------------------

@skip_no_podman
async def test_oracle_dr_bdr_election_matches_frr(frr_cluster):
    a, client = frr_cluster
    priorities = {"oracle-r1": 30, "oracle-r2": 20, "oracle-r3": 1}  # r1 DR, r2 BDR, r3 DROther

    containers = await _spawn_and_wire(a, client, list(priorities))
    ifaces = {
        n: _configure_ospf(containers[n], n, ROUTER_IDS[n], IPS[n], priorities[n])
        for n in priorities
    }

    frr_roles = _wait_for_dr_bdr(containers, ifaces, priorities)

    net, procs = _sim_lan_election(priorities)
    sim_roles = {n: _sim_dr_role(net, procs, n) for n in priorities}

    # Compare only the standard-mandated outcome, normalising FRR's display
    # spelling ("DR"/"Backup"/"DROther") to the sim's ("dr"/"backup"/"drother").
    frr_normalised = {n: r.lower().replace("backup", "backup") for n, r in frr_roles.items()}
    assert frr_normalised == sim_roles, (
        f"FRR election {frr_roles} vs sim election {sim_roles} — real RFC 2328 "
        "non-conformance if these ever legitimately disagree, not a test bug to paper over"
    )
    assert sim_roles == {"oracle-r1": "dr", "oracle-r2": "backup", "oracle-r3": "drother"}


# ---------------------------------------------------------------------------
# Oracle case 2: election is non-preemptive — raising a DROther's priority
# above the incumbent DR's, live, must NOT trigger a re-election (RFC 2328
# §9.4: "This will not, however, cause the existing Designated Router to be
# replaced"). Proven for FRR in research/spike-frr-podman-2.md §1(a); this
# reproduces that exact scenario instead of trusting the note.
# ---------------------------------------------------------------------------

@skip_no_podman
async def test_oracle_dr_election_non_preemptive_matches_frr(frr_cluster):
    a, client = frr_cluster
    priorities = {"oracle-r1": 20, "oracle-r2": 10}  # r1 DR, r2 BDR initially

    containers = await _spawn_and_wire(a, client, list(priorities))
    ifaces = {
        n: _configure_ospf(containers[n], n, ROUTER_IDS[n], IPS[n], priorities[n])
        for n in priorities
    }

    assert _wait_for_dr(containers["oracle-r1"], ifaces["oracle-r1"]) == "DR"

    # r2 jumps to the highest priority in the segment, live — must not unseat r1.
    containers["oracle-r2"].exec_run(
        ["vtysh", "-c", "configure terminal", "-c", f"interface {ifaces['oracle-r2']}",
         "-c", "ip ospf priority 250"]
    )
    _settle(6.0)  # > one full hello/dead cycle at hello=1s
    frr_dr_after = _dr_role(containers["oracle-r1"], ifaces["oracle-r1"])

    net, procs = _sim_lan_election(priorities)
    assert _sim_dr_role(net, procs, "oracle-r1") == "dr"  # incumbent, sim side

    # Sim side: mirror the same live priority bump — ``iface_priority()``
    # reads ``self.priorities`` fresh on every Hello, so this is the sim's
    # equivalent of FRR's live ``ip ospf priority 250``.
    procs["oracle-r2"].priorities["eth0"] = 250
    net.run_for(6.0)

    assert frr_dr_after == "DR", "FRR unseated its DR on a live priority bump — non-preemptive claim is false"
    assert _sim_dr_role(net, procs, "oracle-r1") == "dr", "sim unseated its DR on a live priority bump"
