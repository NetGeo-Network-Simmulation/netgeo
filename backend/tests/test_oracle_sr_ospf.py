"""Oracle harness: OSPF-SR (RFC 8665) run against BOTH our sim engine and
real FRR 10.7.0 containers on the identical pe1-p-pe2 line topology,
comparing only what RFC 8665 actually mandates -- the SRGB each router
advertises, the Prefix-SID index for its loopback, the no-PHP (NP) flag on
that Prefix-SID, and the SID-to-label formula (``srgb_base + index``, sec
3.1) both engines derive from that same advertised data. CLI formatting and
FRR's own adjacency-SID extension (sec 3.3: LAN-Adj-SID *pairs*, one
protection-eligible and one not) are never compared -- adjacency-SIDs are a
documented FRR extension beyond what RFC 8665 pins down, and this engine
only ever allocates one per neighbor (see ``SrProcess._alloc_adj_sids``),
so there is nothing meaningful to assert equal.

** Kernel MPLS dataplane is NOT available in this rootless-podman lab **,
verified experimentally while building this test: a stock FRR 10.7.0
container here reports ``show mpls status`` -> "MPLS support enabled: no
(mpls kernel extensions not detected)", and ``show mpls table json``
returns ``{}`` regardless of a correct SR config -- loading the kernel's
``mpls_router``/``mpls_iptunnel`` modules needs real root, which neither
this rootless podman setup nor (by the same constraint) the
ubuntu-22.04 GitHub Actions runner the podman-emulation-ci job uses has.
This is an environment capability gap, not a protocol mismatch: RFC 8665's
SID-to-label formula is still fully checkable without a live LFIB, by
comparing what each engine *computes* from the identical SRGB +
Prefix-SID-index inputs against the sim's actually-installed
``router.lfib`` -- see the label-derivation block below.

Marked ``oracle`` (registered in pytest.ini), deliberately NOT ``podman``:
the CI podman-smoke required merge gate runs ``pytest -m podman -q`` and
does not select these -- see ORACLE_HARNESS.md for why, and how to run this
file on purpose (``pytest -m oracle``). ``skip_no_podman`` still applies
underneath, same as every other oracle test.
"""
from __future__ import annotations

import json
import subprocess
import time

import pytest

from engine.emulation.ip_alloc import link_subnet
from engine.emulation.podman_adaptor import (
    CONTAINER_PREFIX,
    PodmanAdaptor,
    socket_reachable,
)
from engine.model import InterfaceModel, LinkModel
from engine.netstack import Network
from engine.netstack.device import Device as EmulatedDevice
from engine.netstack.protocols.ospf import OspfProcess
from engine.netstack.protocols.sr import SrProcess
from engine.netstack.routing import Router
from tests.test_oracle_ospf import (
    _DAEMONS_OSPF_ON,
    _iface_for_ip,
    _ospf_neighbor_state,
    _put_text,
)

pytestmark = pytest.mark.oracle

skip_no_podman = pytest.mark.skipif(
    not socket_reachable(), reason="podman.socket unreachable — see PodmanSocketUnreachable"
)

NODES = ("oracle-sr-pe1", "oracle-sr-p", "oracle-sr-pe2")
ROUTER_IDS = {"oracle-sr-pe1": "1.1.1.1", "oracle-sr-p": "2.2.2.2", "oracle-sr-pe2": "3.3.3.3"}
LOOPBACKS = {"oracle-sr-pe1": "10.255.0.1", "oracle-sr-p": "10.255.0.2", "oracle-sr-pe2": "10.255.0.3"}
NODE_SIDS = {"oracle-sr-pe1": 101, "oracle-sr-p": 100, "oracle-sr-pe2": 102}
NO_PHP = {"oracle-sr-pe1": False, "oracle-sr-p": False, "oracle-sr-pe2": True}
SRGB_BASE = 16000
SRGB_RANGE = 8000  # -> [16000, 23999], matches FRR ospfd's own default global-block

LINK_PE1_P = "oracle-sr-pe1-p"
LINK_P_PE2 = "oracle-sr-p-pe2"
LINKS = ((LINK_PE1_P, "oracle-sr-pe1", "oracle-sr-p"), (LINK_P_PE2, "oracle-sr-p", "oracle-sr-pe2"))

RID_TO_SIM_NAME = {"1.1.1.1": "pe1", "2.2.2.2": "p", "3.3.3.3": "pe2"}


@pytest.fixture
def sr_cluster():
    a = PodmanAdaptor()
    client = a._get_client()
    try:
        yield a, client
    finally:
        for link_id, _na, _nb in LINKS:
            subprocess.run(
                ["podman", "network", "rm", "-f", f"netgeo-link-{link_id}"],
                capture_output=True, check=False,
            )
        for node_id in NODES:
            subprocess.run(
                ["podman", "rm", "-f", f"{CONTAINER_PREFIX}{node_id}"],
                capture_output=True, check=False,
            )


async def _spawn_and_wire_sr(a: PodmanAdaptor, client):
    """Spawns the 3 oracle-sr-* FRR containers, enables ospfd (one restart,
    done before any link is wired -- see ORACLE_HARNESS.md step 4), then
    wires pe1-p and p-pe2 as their own point-to-point podman networks
    (deterministic /29 from ``link_subnet``, same per-link-bridge pattern
    test_oracle_bgp.py uses -- an SR line topology needs distinct subnets
    per hop, not one shared broadcast LAN)."""
    for node_id in NODES:
        await a.spawn(EmulatedDevice(name=node_id, node_id=node_id, nos="frr", mode="emul"))
    containers = {n: client.containers.get(f"{CONTAINER_PREFIX}{n}") for n in NODES}
    for c in containers.values():
        _put_text(c, "/etc/frr", "daemons", _DAEMONS_OSPF_ON)
        c.restart(timeout=5)
    link_info: dict[str, tuple[str, str, str]] = {}  # link_id -> (cidr, ip_a, ip_b)
    for link_id, na, nb in LINKS:
        cidr, _gw, ip_a, ip_b = link_subnet(link_id)
        link_info[link_id] = (cidr, ip_a, ip_b)
        await a.wire_link(
            LinkModel(id=link_id, a_iface=f"{link_id}-a", b_iface=f"{link_id}-b"),
            InterfaceModel(id=f"{link_id}-a", node_id=na, name="ethX"),
            InterfaceModel(id=f"{link_id}-b", node_id=nb, name="ethX"),
        )
    return containers, link_info


def _frr_sr_conf(
    hostname: str, router_id: str, loopback: str, node_sid: int, no_php: bool,
    iface_subnets: list[tuple[str, str]],
) -> str:
    """``iface_subnets`` is ``[(real_iface_name, link_cidr), ...]`` -- one
    entry per p2p link this node sits on (1 for pe1/pe2, 2 for p). FRR
    auto-adds ``router-info area`` once ``segment-routing on`` is set
    (verified experimentally -- never typed here)."""
    lines = [
        "frr version 10.7", "frr defaults traditional", f"hostname {hostname}", "!",
        "interface lo", f" ip address {loopback}/32", "!",
    ]
    for ifn, _cidr in iface_subnets:
        lines += [f"interface {ifn}", " ip ospf hello-interval 1", " ip ospf dead-interval 4", "!"]
    lines += ["router ospf", f" ospf router-id {router_id}", f" network {loopback}/32 area 0"]
    for _ifn, cidr in iface_subnets:
        lines.append(f" network {cidr} area 0")
    lines += [
        " capability opaque",
        " segment-routing on",
        f" segment-routing global-block {SRGB_BASE} {SRGB_BASE + SRGB_RANGE - 1}",
    ]
    npflag = " no-php-flag" if no_php else ""
    lines.append(f" segment-routing prefix {loopback}/32 index {node_sid}{npflag}")
    lines += ["!", "line vty", "!"]
    return "\n".join(lines) + "\n"


def _configure_sr(
    container, hostname: str, router_id: str, loopback: str, node_sid: int, no_php: bool,
    iface_ips: list[tuple[str, str]],
) -> None:
    """``iface_ips`` = ``[(ip, link_cidr), ...]`` this node owns, one per
    link -- the real container NIC name is discovered fresh per IP (podman
    assigns eth1/eth2/... by attach order, never assumed, same discipline
    as ``_iface_for_ip``'s own docstring)."""
    iface_subnets = [(_iface_for_ip(container, ip), cidr) for ip, cidr in iface_ips]
    conf = _frr_sr_conf(hostname, router_id, loopback, node_sid, no_php, iface_subnets)
    _put_text(container, "/etc/frr", "frr.conf", conf)
    container.exec_run(["chown", "frr:frr", "/etc/frr/frr.conf"])
    container.exec_run(["chmod", "640", "/etc/frr/frr.conf"])
    container.exec_run(["vtysh", "-b"])


def _wait_full(container, peer_router_id: str, timeout: float = 20.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = _ospf_neighbor_state(container, peer_router_id)
        if state and state.startswith("Full"):
            return True
        time.sleep(1)
    return False


def _frr_opaque_json(container) -> dict:
    _ec, (out, _err) = container.exec_run(
        ["vtysh", "-c", "show ip ospf database opaque-area json"], demux=True
    )
    return json.loads((out or b"").decode() or "{}")


def _frr_sr_facts(container, timeout: float = 15.0) -> tuple[dict[str, tuple[int, int]], dict[str, dict]]:
    """Polls until this router's LSDB carries all 3 routers' RI-LSA (SRGB)
    and Extended-Prefix-LSA (Prefix-SID) -- flooding can lag a beat behind
    Full -- then returns ``({router_id: (srgb_lower, srgb_upper)},
    {router_id: {"address", "plen", "index", "flags"}})``, read straight
    from FRR's own ``json`` command output, never parsed off CLI text."""
    deadline = time.monotonic() + timeout
    srgb: dict[str, tuple[int, int]] = {}
    psid: dict[str, dict] = {}
    while time.monotonic() < deadline:
        data = _frr_opaque_json(container)
        entries = data.get("areaLocalOpaqueLsa", {}).get("areas", {}).get("0.0.0.0", [])
        srgb, psid = {}, {}
        for e in entries:
            rid = e["advertisingRouter"]
            if e["opaqueType"] == "Router Information LSA":
                sr = e["opaqueValues"]["routerInformation"].get("segmentRouting")
                if sr:
                    srgb[rid] = (sr["srgb"]["lowerBound"], sr["srgb"]["upperBound"])
            elif e["opaqueType"] == "Extended Prefix Opaque LSA":
                ep = e["opaqueValues"]["extendedPrefix"]
                sid = ep.get("prefixSID")
                if sid:
                    psid[rid] = {
                        "address": ep["address"], "plen": ep["prefixLength"],
                        "index": sid["index"], "flags": sid["flags"],
                    }
        if len(srgb) == 3 and len(psid) == 3:
            return srgb, psid
        time.sleep(1)
    return srgb, psid


def _sim_sr_topology() -> tuple[Network, dict[str, Router], dict[str, OspfProcess]]:
    """Identical pe1-p-pe2 line as the FRR side: same loopbacks, node-SIDs,
    SRGB and pe2's no-PHP flag (mirrors test_sr_ospf_native.py's ``_lab()``
    shape, minus the host leg the oracle comparison doesn't need)."""
    net = Network(seed=53)
    pe1 = net.add_device(Router("pe1"))
    p = net.add_device(Router("p"))
    pe2 = net.add_device(Router("pe2"))
    net.connect("l1p", net.add_iface(pe1, "eth0", ["10.0.12.1/30"]),
                net.add_iface(p, "eth0", ["10.0.12.2/30"]))
    net.connect("lp2", net.add_iface(p, "eth1", ["10.0.23.1/30"]),
                net.add_iface(pe2, "eth0", ["10.0.23.2/30"]))
    net.add_iface(pe1, "lo0", ["10.255.0.1/32"])
    net.add_iface(p, "lo0", ["10.255.0.2/32"])
    net.add_iface(pe2, "lo0", ["10.255.0.3/32"])
    ospf_pe1 = OspfProcess(pe1, router_id="1.1.1.1", hello_interval=1.0)
    ospf_p = OspfProcess(p, router_id="2.2.2.2", hello_interval=1.0)
    ospf_pe2 = OspfProcess(pe2, router_id="3.3.3.3", hello_interval=1.0)
    SrProcess(pe1, ospf_pe1, node_sid=101, srgb_base=SRGB_BASE, srgb_range=SRGB_RANGE)
    SrProcess(p, ospf_p, node_sid=100, srgb_base=SRGB_BASE, srgb_range=SRGB_RANGE)
    SrProcess(pe2, ospf_pe2, node_sid=102, srgb_base=SRGB_BASE, srgb_range=SRGB_RANGE, no_php=True)
    net.start()
    net.run(until=30.0)
    return net, {"pe1": pe1, "p": p, "pe2": pe2}, {"pe1": ospf_pe1, "p": ospf_p, "pe2": ospf_pe2}


# ---------------------------------------------------------------------------
# Oracle case: SRGB, Prefix-SID index/NP-flag and the label they derive to,
# all matching between our sim and real FRR on the identical topology.
# ---------------------------------------------------------------------------

@skip_no_podman
async def test_oracle_sr_ospf_srgb_and_prefix_sid(sr_cluster):
    a, client = sr_cluster
    containers, link_info = await _spawn_and_wire_sr(a, client)
    cidr1, ip_pe1, ip_p1 = link_info[LINK_PE1_P]
    cidr2, ip_p2, ip_pe2 = link_info[LINK_P_PE2]

    _configure_sr(containers["oracle-sr-pe1"], "oracle-sr-pe1", ROUTER_IDS["oracle-sr-pe1"],
                  LOOPBACKS["oracle-sr-pe1"], NODE_SIDS["oracle-sr-pe1"], NO_PHP["oracle-sr-pe1"],
                  [(ip_pe1, cidr1)])
    _configure_sr(containers["oracle-sr-p"], "oracle-sr-p", ROUTER_IDS["oracle-sr-p"],
                  LOOPBACKS["oracle-sr-p"], NODE_SIDS["oracle-sr-p"], NO_PHP["oracle-sr-p"],
                  [(ip_p1, cidr1), (ip_p2, cidr2)])
    _configure_sr(containers["oracle-sr-pe2"], "oracle-sr-pe2", ROUTER_IDS["oracle-sr-pe2"],
                  LOOPBACKS["oracle-sr-pe2"], NODE_SIDS["oracle-sr-pe2"], NO_PHP["oracle-sr-pe2"],
                  [(ip_pe2, cidr2)])

    assert _wait_full(containers["oracle-sr-pe1"], ROUTER_IDS["oracle-sr-p"]), \
        "pe1-p OSPF adjacency never reached Full"
    assert _wait_full(containers["oracle-sr-pe2"], ROUTER_IDS["oracle-sr-p"]), \
        "p-pe2 OSPF adjacency never reached Full"

    frr_srgb, frr_psid = _frr_sr_facts(containers["oracle-sr-pe1"])
    assert len(frr_srgb) == 3, f"FRR LSDB missing RI-LSAs (SRGB) from some router: {frr_srgb}"
    assert len(frr_psid) == 3, f"FRR LSDB missing Extended-Prefix-LSAs from some router: {frr_psid}"

    _net, routers, ospfs = _sim_sr_topology()
    sim_srgb: dict[str, tuple[int, int]] = {}
    sim_psid: dict[str, dict] = {}
    for row in ospfs["pe1"].opaque_rows():
        rid = row["router_id"]
        if row["opaque_type"] == 4:
            sim_srgb[rid] = (row["tlvs"]["srgb_base"], row["tlvs"]["srgb_base"] + row["tlvs"]["srgb_range"] - 1)
        elif row["opaque_type"] == 7:
            sim_psid[rid] = {
                "prefix": row["tlvs"]["prefix"], "index": row["tlvs"]["sid_index"],
                "no_php": row["tlvs"]["flags"]["NP"],
            }

    # 1. SRGB advertisement (RFC 8665 sec 3.1): same [base, base+range-1] range.
    for rid, frr_range in frr_srgb.items():
        assert frr_range == sim_srgb[rid], (
            f"router {rid}: FRR SRGB {frr_range} vs sim SRGB {sim_srgb[rid]}"
        )

    # 2. Prefix-SID index (sec 4.1) + NP no-PHP flag (sec 4.2) per loopback.
    for rid, frr_e in frr_psid.items():
        sim_e = sim_psid[rid]
        frr_prefix = f"{frr_e['address']}/{frr_e['plen']}"
        assert frr_prefix == sim_e["prefix"], (
            f"router {rid}: FRR Prefix-SID prefix {frr_prefix} vs sim {sim_e['prefix']}"
        )
        assert frr_e["index"] == sim_e["index"], (
            f"router {rid}: FRR Prefix-SID index {frr_e['index']} vs sim {sim_e['index']}"
        )
        frr_no_php = bool(int(frr_e["flags"], 16) & 0x40)  # NP bit, RFC 8665 sec 4.2
        assert frr_no_php == sim_e["no_php"], (
            f"router {rid}: FRR NP flag {frr_no_php} vs sim no_php {sim_e['no_php']}"
        )

    # 3. Derived label = srgb_base + index (sec 3.1 formula) matches what the
    # sim actually installed in router.lfib -- see module docstring for why
    # this stands in for a real kernel LFIB compare in this environment.
    for rid, frr_e in frr_psid.items():
        label = frr_srgb[rid][0] + frr_e["index"]
        owner = RID_TO_SIM_NAME[rid]
        for name, router in routers.items():
            if name == owner:
                assert router.lfib[label].action == "pop", (
                    f"{name}'s own node-SID label {label} should pop (UHP), "
                    f"got {router.lfib[label].action}"
                )
            else:
                assert label in router.lfib, (
                    f"{name} has no LFIB entry for {rid}'s derived label {label} "
                    f"(FRR SRGB base {frr_srgb[rid][0]} + Prefix-SID index {frr_e['index']})"
                )
