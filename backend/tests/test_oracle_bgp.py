"""Oracle harness: BGP best-path selection (RFC 4271 §9.1.2.2) run against
BOTH our sim engine and real FRR 10.7.0 containers on identical topologies,
comparing only which next-hop wins a prefix -- read from FRR's own
``show ip bgp <prefix> json`` (the ``bestpath.overall`` path's next-hop) and
from the sim's ``BgpProcess.best_paths()``. CLI formatting, UPDATE ordering
and timing are never compared -- see ORACLE_HARNESS.md.

Criteria compared, each isolated so a mismatch points at exactly one RFC
tie-break step:
- (b) shortest AS-path wins, all else tied
      -> test_oracle_bgp_prefers_shorter_as_path
- (e) eBGP beats iBGP, all else tied
      -> test_oracle_bgp_prefers_ebgp_over_ibgp

Criteria SKIPPED, and why -- a real engine gap, not a harness shortcoming
(see the field comments on ``BgpAttrs`` in engine/netstack/protocols/bgp.py):
- (a) local-pref, (c) origin type, (d) MED: this engine has no route-map /
  redistribute config surface that ever produces a non-default value for
  these three attributes through a real message flow -- local_pref is
  always 100 on origination, origin is always "igp", med is always 0. The
  only way to hand the sim side a different value is to poke ``BgpAttrs``/
  ``rib_in`` directly, exactly like ``test_bgp_bestpath.py``'s pure
  decision-process unit tests already do. Doing that here while FRR gets a
  real route-map on the wire would compare two different things dressed up
  as one -- config surface on FRR's side, hand-injected attributes on the
  sim's -- which is exactly the "berpura-pura membandingkan" this harness
  is told to avoid. Skipped rather than faked; revisit once the sim grows
  real route-map support.
- Re-checked 2026-09-12 per an explicit request to add local-pref and origin
  oracle cases (treating MED alone as the risky one): all three hit the
  identical wall, not just MED. ``add_neighbor``/``advertise_network`` take
  no local-pref/origin/MED override of any kind (grepped -- confirmed), so
  none of the three can be produced on the sim side without the same
  hand-injection this file already refuses to do for the other two.

FRR knobs pinned explicitly in every node's config (not left at whatever the
image defaults to):
- ``no bgp always-compare-med``: off, matching ``BgpProcess.always_compare_med``
  default (False) -- explicit so a future FRR default flip can't silently
  change what's under test.
- ``bgp bestpath compare-routerid``: ON. The sim's tie-break ladder always
  compares router-id at step (h), unconditionally (see ``_better``'s
  docstring) -- FRR's default (off) instead falls back to "oldest received
  path" at that step, which the sim has no equivalent of. Neither test below
  actually reaches step (h) (both resolve earlier), but this is pinned so a
  future test that does reach it isn't silently decided by FRR's non-RFC
  "oldest path" default instead of router-id.
- ``bgp deterministic-med``: ON, so MED-adjacent comparisons don't inherit
  FRR's arrival-order artifact from its off-mode grouping (irrelevant to the
  two scenarios here -- neither sets a MED -- pinned for the same
  future-proofing reason as compare-routerid above).
- ``no bgp ebgp-requires-policy`` / ``no bgp network import-check``: not
  outcome knobs -- without them FRR's RFC 8212 default refuses to exchange
  routes with no attached route-map at all, and ``network`` refuses to
  advertise a prefix with no matching RIB entry. Neither knob is part of
  RFC 4271 §9.1.2.2; both exist purely so the topology comes up at all,
  mirroring the sim's own ``advertise_network()`` which has no such gates.
"""
from __future__ import annotations

import json
import subprocess
import time
from ipaddress import IPv4Network

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
from engine.netstack.protocols.bgp import BgpProcess
from engine.netstack.routing import Router
from tests.test_oracle_ospf import _iface_for_ip, _put_text

pytestmark = pytest.mark.oracle

skip_no_podman = pytest.mark.skipif(
    not socket_reachable(), reason="podman.socket unreachable — see PodmanSocketUnreachable"
)

PFX = "203.0.113.0/24"

# bgpd on, everything else off (same "flip one flag" discipline as
# test_oracle_ospf.py's _DAEMONS_OSPF_ON).
_DAEMONS_BGP_ON = (
    "bgpd=yes\nospfd=no\nospf6d=no\nripd=no\nripngd=no\nisisd=no\npimd=no\n"
    "pim6d=no\nldpd=no\nnhrpd=no\neigrpd=no\nbabeld=no\nsharpd=no\npbrd=no\n"
    "bfdd=no\nfabricd=no\nvrrpd=no\npathd=no\nvtysh_enable=yes\n"
)

# Shared bestpath-comparison knobs -- see module docstring for why each one
# is pinned rather than left at the image default.
_BESTPATH_KNOBS = (
    " no bgp ebgp-requires-policy\n"
    " no bgp network import-check\n"
    " no bgp always-compare-med\n"
    " bgp bestpath compare-routerid\n"
    " bgp deterministic-med\n"
)


def _bgp_conf(
    hostname: str,
    asn: int,
    router_id: str,
    iface_ips: list[tuple[str, str]],
    neighbors: list[tuple[str, int]],
    networks: list[str],
) -> str:
    lines = ["frr version 10.7", "frr defaults traditional", f"hostname {hostname}", "!"]
    for iface, ip in iface_ips:
        lines += [f"interface {iface}", f" ip address {ip}/29", "!"]
    lines.append(f"router bgp {asn}")
    lines.append(f" bgp router-id {router_id}")
    lines.append(_BESTPATH_KNOBS.rstrip("\n"))
    for peer_ip, remote_asn in neighbors:
        lines.append(f" neighbor {peer_ip} remote-as {remote_asn}")
    for net_ in networks:
        lines.append(f" network {net_}")
    lines += ["!", "line vty", "!"]
    return "\n".join(lines) + "\n"


@pytest.fixture
def bgp_cluster():
    """Yields (adaptor, client); tears down every container/network this
    file could have created, unconditionally, in the post-yield block pytest
    runs even when the test body raises -- same discipline as
    test_oracle_ospf.py's ``frr_cluster``. Node/link ids are namespaced
    ``oracle-bgp-*`` so this can never collide with the OSPF oracle's
    ``oracle-*`` or a developer's own containers."""
    a = PodmanAdaptor()
    client = a._get_client()
    try:
        yield a, client
    finally:
        for link_id in (
            "oracle-bgp-aspath-hub-up1",
            "oracle-bgp-aspath-hub-up2",
            "oracle-bgp-aspath-up2-up3",
            "oracle-bgp-ebgp-hub-r2",
            "oracle-bgp-ebgp-hub-up1",
            "oracle-bgp-ebgp-r2-up2",
        ):
            subprocess.run(
                ["podman", "network", "rm", "-f", f"netgeo-link-{link_id}"],
                capture_output=True, check=False,
            )
        for node_id in (
            "oracle-bgp-hub", "oracle-bgp-up1", "oracle-bgp-up2", "oracle-bgp-up3",
            "oracle-bgp-r2",
        ):
            subprocess.run(
                ["podman", "rm", "-f", f"{CONTAINER_PREFIX}{node_id}"],
                capture_output=True, check=False,
            )


async def _spawn_and_wire_bgp(
    a: PodmanAdaptor, client, asns: dict[str, int], links: list[tuple[str, str, str]]
) -> tuple[dict, dict[str, tuple[str, str]]]:
    """Spawns one FRR container per name in ``asns``, enables bgpd with the
    one restart this needs *before* any link is wired (netavark reassigns a
    container's IP on every restart -- see ORACLE_HARNESS.md step 4), then
    wires each ``(link_id, node_a, node_b)`` as its own point-to-point
    podman network via ``PodmanAdaptor.wire_link`` (deterministic /29 from
    ``link_subnet`` -- the same per-link-bridge pattern test_link_e2e.py
    uses, not the OSPF oracle's single shared broadcast LAN, because BGP
    peers sit on distinct point-to-point subnets, not one segment)."""
    for node_id in asns:
        await a.spawn(EmulatedDevice(name=node_id, node_id=node_id, nos="frr", mode="emul"))
    containers = {n: client.containers.get(f"{CONTAINER_PREFIX}{n}") for n in asns}
    for c in containers.values():
        _put_text(c, "/etc/frr", "daemons", _DAEMONS_BGP_ON)
        c.restart(timeout=5)
    link_ips: dict[str, tuple[str, str]] = {}
    for link_id, na, nb in links:
        _cidr, _gw, ip_a, ip_b = link_subnet(link_id)
        link_ips[link_id] = (ip_a, ip_b)
        await a.wire_link(
            LinkModel(id=link_id, a_iface=f"{link_id}-a", b_iface=f"{link_id}-b"),
            InterfaceModel(id=f"{link_id}-a", node_id=na, name="ethX"),
            InterfaceModel(id=f"{link_id}-b", node_id=nb, name="ethX"),
        )
    return containers, link_ips


def _push_bgp_conf(
    container,
    hostname: str,
    asn: int,
    router_id: str,
    iface_ips: list[tuple[str, str]],
    neighbors: list[tuple[str, int]],
    networks: list[str],
) -> None:
    conf = _bgp_conf(hostname, asn, router_id, iface_ips, neighbors, networks)
    _put_text(container, "/etc/frr", "frr.conf", conf)
    container.exec_run(["chown", "frr:frr", "/etc/frr/frr.conf"])
    container.exec_run(["chmod", "640", "/etc/frr/frr.conf"])
    container.exec_run(["vtysh", "-b"])
    # Forces a fresh outbound announce under the just-loaded policy knobs
    # instead of trusting session timing -- verified necessary experimentally
    # (a session that reached Established under stricter, pre-config
    # defaults can otherwise sit on a stale empty End-of-RIB).
    container.exec_run(["vtysh", "-c", "clear bgp * out"])


def _frr_bgp_best_nexthop(container, prefix: str, timeout: float = 20.0) -> str | None:
    """The next-hop of FRR's own bestpath pick for ``prefix``, straight from
    ``show ip bgp <prefix> json``'s ``bestpath.overall`` path -- never
    parsed off table-formatted CLI text (see module docstring)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        _ec, (out, _err) = container.exec_run(
            ["vtysh", "-c", f"show ip bgp {prefix} json"], demux=True
        )
        data = json.loads((out or b"").decode() or "{}")
        for path in data.get("paths", []):
            if path.get("bestpath", {}).get("overall"):
                nh = path.get("nexthops") or [{}]
                return nh[0].get("ip")
        time.sleep(1)
    return None


# ---------------------------------------------------------------------------
# Oracle case 1: shortest AS-path wins, all else tied (RFC 4271 §9.1.2.2 (b)).
#
#   hub (65000) --- up1 (65001, originates PFX)              AS-path len 1
#   hub (65000) --- up2 (65002) --- up3 (65003, originates PFX)  AS-path len 2
#
# Both routes reach hub with identical local-pref/origin/MED/eBGP-status;
# only AS-path length differs. hub must pick up1's route.
# ---------------------------------------------------------------------------

@skip_no_podman
async def test_oracle_bgp_prefers_shorter_as_path(bgp_cluster):
    a, client = bgp_cluster
    asns = {
        "oracle-bgp-hub": 65000, "oracle-bgp-up1": 65001,
        "oracle-bgp-up2": 65002, "oracle-bgp-up3": 65003,
    }
    links = [
        ("oracle-bgp-aspath-hub-up1", "oracle-bgp-hub", "oracle-bgp-up1"),
        ("oracle-bgp-aspath-hub-up2", "oracle-bgp-hub", "oracle-bgp-up2"),
        ("oracle-bgp-aspath-up2-up3", "oracle-bgp-up2", "oracle-bgp-up3"),
    ]
    containers, link_ips = await _spawn_and_wire_bgp(a, client, asns, links)
    ip_hub_up1, ip_up1 = link_ips["oracle-bgp-aspath-hub-up1"]
    ip_hub_up2, ip_up2 = link_ips["oracle-bgp-aspath-hub-up2"]
    ip_up2b, ip_up3 = link_ips["oracle-bgp-aspath-up2-up3"]

    iface_hub_up1 = _iface_for_ip(containers["oracle-bgp-hub"], ip_hub_up1)
    iface_hub_up2 = _iface_for_ip(containers["oracle-bgp-hub"], ip_hub_up2)
    iface_up1 = _iface_for_ip(containers["oracle-bgp-up1"], ip_up1)
    iface_up2_hub = _iface_for_ip(containers["oracle-bgp-up2"], ip_up2)
    iface_up2_up3 = _iface_for_ip(containers["oracle-bgp-up2"], ip_up2b)
    iface_up3 = _iface_for_ip(containers["oracle-bgp-up3"], ip_up3)

    _push_bgp_conf(
        containers["oracle-bgp-hub"], "hub", 65000, "9.9.9.9",
        [(iface_hub_up1, ip_hub_up1), (iface_hub_up2, ip_hub_up2)],
        [(ip_up1, 65001), (ip_up2, 65002)], [],
    )
    _push_bgp_conf(
        containers["oracle-bgp-up1"], "up1", 65001, "1.1.1.1",
        [(iface_up1, ip_up1)], [(ip_hub_up1, 65000)], [PFX],
    )
    _push_bgp_conf(
        containers["oracle-bgp-up2"], "up2", 65002, "2.2.2.2",
        [(iface_up2_hub, ip_up2), (iface_up2_up3, ip_up2b)],
        [(ip_hub_up2, 65000), (ip_up3, 65003)], [],
    )
    _push_bgp_conf(
        containers["oracle-bgp-up3"], "up3", 65003, "3.3.3.3",
        [(iface_up3, ip_up3)], [(ip_up2b, 65002)], [PFX],
    )

    frr_best = _frr_bgp_best_nexthop(containers["oracle-bgp-hub"], PFX)
    assert frr_best == ip_up1, (
        f"FRR picked next-hop {frr_best!r}, expected the shorter-AS-path "
        f"route via up1 ({ip_up1!r}) — real RFC 4271 §9.1.2.2(b) "
        "non-conformance if this ever legitimately disagrees"
    )

    net, up1_ip = _sim_aspath_topology()
    _attrs, sim_nh = net.devices["hub"].processes[0].best_paths()[IPv4Network(PFX)]
    assert str(sim_nh) == up1_ip, f"sim picked {sim_nh}, expected up1 {up1_ip}"


def _link(net: Network, a: Router, an: str, aip: str, b: Router, bn: str, bip: str):
    net.connect(f"{a.name}-{b.name}", net.add_iface(a, an, [aip]), net.add_iface(b, bn, [bip]))


def _sim_aspath_topology() -> tuple[Network, str]:
    """Same topology/shape as the FRR side above, sim's own IP scheme."""
    net = Network(seed=41)
    hub = net.add_device(Router("hub"))
    up1 = net.add_device(Router("up1"))
    up2 = net.add_device(Router("up2"))
    up3 = net.add_device(Router("up3"))
    _link(net, hub, "eth0", "10.0.1.2/30", up1, "eth0", "10.0.1.1/30")
    _link(net, hub, "eth1", "10.0.2.2/30", up2, "eth0", "10.0.2.1/30")
    _link(net, up2, "eth1", "10.0.3.1/30", up3, "eth0", "10.0.3.2/30")
    phub = BgpProcess(hub, asn=65000, router_id="9.9.9.9", keepalive_interval=1.0)
    phub.add_neighbor("10.0.1.1", 65001)
    phub.add_neighbor("10.0.2.1", 65002)
    p1 = BgpProcess(up1, asn=65001, router_id="1.1.1.1", keepalive_interval=1.0)
    p1.add_neighbor("10.0.1.2", 65000)
    p1.advertise_network(PFX)
    p2 = BgpProcess(up2, asn=65002, router_id="2.2.2.2", keepalive_interval=1.0)
    p2.add_neighbor("10.0.2.2", 65000)
    p2.add_neighbor("10.0.3.2", 65003)
    p3 = BgpProcess(up3, asn=65003, router_id="3.3.3.3", keepalive_interval=1.0)
    p3.add_neighbor("10.0.3.1", 65002)
    p3.advertise_network(PFX)
    net.start()
    net.run(until=20.0)
    return net, "10.0.1.1"


# ---------------------------------------------------------------------------
# Oracle case 2: eBGP beats iBGP, all else tied (RFC 4271 §9.1.2.2 (e)).
#
#   hub (65000) --- up1 (65001, originates PFX)     direct eBGP, AS-path len 1
#   hub (65000) --- r2 (65000, iBGP) --- up2 (65002, originates PFX)
#                                                    iBGP-relayed, AS-path len 1
#
# AS-path length ties (iBGP never adds a hop), origin ties (both "igp" via a
# plain network statement), MED ties (both unset/0) -- only eBGP-vs-iBGP
# status differs. hub must pick up1's direct eBGP route.
# ---------------------------------------------------------------------------

@skip_no_podman
async def test_oracle_bgp_prefers_ebgp_over_ibgp(bgp_cluster):
    a, client = bgp_cluster
    asns = {
        "oracle-bgp-hub": 65000, "oracle-bgp-r2": 65000,
        "oracle-bgp-up1": 65001, "oracle-bgp-up2": 65002,
    }
    links = [
        ("oracle-bgp-ebgp-hub-r2", "oracle-bgp-hub", "oracle-bgp-r2"),
        ("oracle-bgp-ebgp-hub-up1", "oracle-bgp-hub", "oracle-bgp-up1"),
        ("oracle-bgp-ebgp-r2-up2", "oracle-bgp-r2", "oracle-bgp-up2"),
    ]
    containers, link_ips = await _spawn_and_wire_bgp(a, client, asns, links)
    ip_hub_r2, ip_r2_hub = link_ips["oracle-bgp-ebgp-hub-r2"]
    ip_hub_up1, ip_up1 = link_ips["oracle-bgp-ebgp-hub-up1"]
    ip_r2_up2, ip_up2 = link_ips["oracle-bgp-ebgp-r2-up2"]

    iface_hub_r2 = _iface_for_ip(containers["oracle-bgp-hub"], ip_hub_r2)
    iface_hub_up1 = _iface_for_ip(containers["oracle-bgp-hub"], ip_hub_up1)
    iface_r2_hub = _iface_for_ip(containers["oracle-bgp-r2"], ip_r2_hub)
    iface_r2_up2 = _iface_for_ip(containers["oracle-bgp-r2"], ip_r2_up2)
    iface_up1 = _iface_for_ip(containers["oracle-bgp-up1"], ip_up1)
    iface_up2 = _iface_for_ip(containers["oracle-bgp-up2"], ip_up2)

    _push_bgp_conf(
        containers["oracle-bgp-hub"], "hub", 65000, "9.9.9.9",
        [(iface_hub_r2, ip_hub_r2), (iface_hub_up1, ip_hub_up1)],
        [(ip_r2_hub, 65000), (ip_up1, 65001)], [],
    )
    _push_bgp_conf(
        containers["oracle-bgp-r2"], "r2", 65000, "8.8.8.8",
        [(iface_r2_hub, ip_r2_hub), (iface_r2_up2, ip_r2_up2)],
        [(ip_hub_r2, 65000), (ip_up2, 65002)], [],
    )
    _push_bgp_conf(
        containers["oracle-bgp-up1"], "up1", 65001, "1.1.1.1",
        [(iface_up1, ip_up1)], [(ip_hub_up1, 65000)], [PFX],
    )
    _push_bgp_conf(
        containers["oracle-bgp-up2"], "up2", 65002, "2.2.2.2",
        [(iface_up2, ip_up2)], [(ip_r2_up2, 65000)], [PFX],
    )

    frr_best = _frr_bgp_best_nexthop(containers["oracle-bgp-hub"], PFX)
    assert frr_best == ip_up1, (
        f"FRR picked next-hop {frr_best!r}, expected the direct-eBGP route "
        f"via up1 ({ip_up1!r}) over the iBGP-relayed one — real RFC 4271 "
        "§9.1.2.2(e) non-conformance if this ever legitimately disagrees"
    )

    net, up1_ip = _sim_ebgp_over_ibgp_topology()
    _attrs, sim_nh = net.devices["hub"].processes[0].best_paths()[IPv4Network(PFX)]
    assert str(sim_nh) == up1_ip, f"sim picked {sim_nh}, expected up1 {up1_ip}"


def _sim_ebgp_over_ibgp_topology() -> tuple[Network, str]:
    net = Network(seed=41)
    hub = net.add_device(Router("hub"))
    r2 = net.add_device(Router("r2"))
    up1 = net.add_device(Router("up1"))
    up2 = net.add_device(Router("up2"))
    _link(net, hub, "eth0", "10.0.1.2/30", r2, "eth0", "10.0.1.1/30")
    _link(net, hub, "eth1", "10.0.2.2/30", up1, "eth0", "10.0.2.1/30")
    _link(net, r2, "eth1", "10.0.3.1/30", up2, "eth0", "10.0.3.2/30")
    phub = BgpProcess(hub, asn=65000, router_id="9.9.9.9", keepalive_interval=1.0)
    phub.add_neighbor("10.0.1.1", 65000)
    phub.add_neighbor("10.0.2.1", 65001)
    pr2 = BgpProcess(r2, asn=65000, router_id="8.8.8.8", keepalive_interval=1.0)
    pr2.add_neighbor("10.0.1.2", 65000)
    pr2.add_neighbor("10.0.3.2", 65002)
    p1 = BgpProcess(up1, asn=65001, router_id="1.1.1.1", keepalive_interval=1.0)
    p1.add_neighbor("10.0.2.2", 65000)
    p1.advertise_network(PFX)
    p2 = BgpProcess(up2, asn=65002, router_id="2.2.2.2", keepalive_interval=1.0)
    p2.add_neighbor("10.0.3.1", 65000)
    p2.advertise_network(PFX)
    net.start()
    net.run(until=20.0)
    return net, "10.0.2.1"
