"""Lab API tests — packet-level diagnostics over HTTP.

Builds a routed topology through the real REST surface (projects/nodes/links,
including node-id link endpoints that exercise interface auto-provisioning),
then drives /api/lab/* : ping, traceroute, CLI, captures, tables and the
auto-addressing wizard.
"""
from __future__ import annotations

import asyncio

import pytest

from app.services.netlab import get_lab_manager


@pytest.fixture(autouse=True)
def _fresh_labs():
    """Labs are keyed by project id; clear between tests."""
    get_lab_manager()._labs.clear()
    yield
    get_lab_manager()._labs.clear()


async def _mk_project(client) -> str:
    resp = await client.post("/api/projects", json={"name": "LabTest"})
    assert resp.status_code == 201
    return resp.json()["id"]


async def _mk_node(client, pid: str, name: str, kind: str, ifaces=None, intent=None):
    body = {
        "project_id": pid,
        "name": name,
        "kind": kind,
        "interfaces": ifaces or [],
    }
    if intent is not None:
        body["intent"] = intent
    resp = await client.post("/api/nodes", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _iface(name: str, ips: list[str]):
    return {"id": "", "node_id": "", "name": name, "ip": ips}


async def _mk_link(client, pid: str, a: str, b: str, **kw):
    body = {"project_id": pid, "a_iface": a, "b_iface": b, **kw}
    resp = await client.post("/api/links", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _routed_topology(client):
    """h1 -- r1 -- r2 -- h2, addressed and statically routed via intent."""
    pid = await _mk_project(client)
    h1 = await _mk_node(
        client, pid, "h1", "host",
        ifaces=[_iface("eth0", ["192.168.1.10/24"])],
        intent={"gateway": "192.168.1.1"},
    )
    r1 = await _mk_node(
        client, pid, "r1", "router",
        ifaces=[_iface("eth0", ["192.168.1.1/24"]), _iface("eth1", ["10.0.12.1/30"])],
        intent={"static_routes": [{"prefix": "192.168.2.0/24", "next_hop": "10.0.12.2"}]},
    )
    r2 = await _mk_node(
        client, pid, "r2", "router",
        ifaces=[_iface("eth0", ["192.168.2.1/24"]), _iface("eth1", ["10.0.12.2/30"])],
        intent={"static_routes": [{"prefix": "192.168.1.0/24", "next_hop": "10.0.12.1"}]},
    )
    h2 = await _mk_node(
        client, pid, "h2", "host",
        ifaces=[_iface("eth0", ["192.168.2.10/24"])],
        intent={"gateway": "192.168.2.1"},
    )

    def iface_id(node, name):
        return next(i["id"] for i in node["interfaces"] if i["name"] == name)

    await _mk_link(client, pid, iface_id(h1, "eth0"), iface_id(r1, "eth0"))
    await _mk_link(client, pid, iface_id(r1, "eth1"), iface_id(r2, "eth1"))
    await _mk_link(client, pid, iface_id(r2, "eth0"), iface_id(h2, "eth0"))
    return pid


async def test_lab_ping_end_to_end(client):
    pid = await _routed_topology(client)
    resp = await client.post(
        f"/api/lab/{pid}/ping", json={"src": "h1", "dst": "192.168.2.10", "count": 3}
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["received"] == 3
    assert data["loss_pct"] == 0.0
    assert data["avg_ms"] is not None


async def test_lab_ping_accepts_node_ref_as_destination(client):
    pid = await _routed_topology(client)
    resp = await client.post(
        f"/api/lab/{pid}/ping", json={"src": "h1", "dst": "h2", "count": 2}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["received"] == 2


async def test_lab_traceroute_lists_hops(client):
    pid = await _routed_topology(client)
    resp = await client.post(
        f"/api/lab/{pid}/traceroute", json={"src": "h1", "dst": "192.168.2.10"}
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["reached"] is True
    addrs = [h["address"] for h in data["hops"]]
    assert addrs == ["192.168.1.1", "10.0.12.2", "192.168.2.10"]


async def test_lab_cli_show_and_ping(client):
    pid = await _routed_topology(client)
    resp = await client.post(
        f"/api/lab/{pid}/cli", json={"node": "r1", "command": "show ip route"}
    )
    assert resp.status_code == 200, resp.text
    out = resp.json()
    assert "192.168.2.0/24" in out["output"]
    assert out["prompt"] == "r1>"

    resp2 = await client.post(
        f"/api/lab/{pid}/cli", json={"node": "h1", "command": "ping 192.168.2.10 2"}
    )
    assert "Success rate 100% (2/2)" in resp2.json()["output"]


async def test_lab_captures_and_tables(client):
    pid = await _routed_topology(client)
    await client.post(
        f"/api/lab/{pid}/ping", json={"src": "h1", "dst": "192.168.2.10", "count": 1}
    )
    caps = await client.get(f"/api/lab/{pid}/captures", params={"limit": 200})
    assert caps.status_code == 200
    infos = " | ".join(r["info"] for r in caps.json()["records"])
    assert "echo-request" in infos

    tables = await client.get(f"/api/lab/{pid}/tables/r1")
    assert tables.status_code == 200
    data = tables.json()
    assert any(r["source"] == "static" for r in data["routes"])
    assert any(r["source"] == "connected" for r in data["routes"])
    assert data["arp"]  # ARP learned during the ping


async def test_lab_status_and_rebuild(client):
    pid = await _routed_topology(client)
    status = await client.get(f"/api/lab/{pid}/status")
    assert status.status_code == 200
    assert status.json()["stats"]["devices"] == 4
    rebuilt = await client.post(f"/api/lab/{pid}/rebuild")
    assert rebuilt.status_code == 200
    assert rebuilt.json()["stats"]["devices"] == 4


async def test_link_create_auto_provisions_interfaces(client):
    """Frontend fallback: link endpoints given as *node ids* must grow ports."""
    pid = await _mk_project(client)
    a = await _mk_node(client, pid, "alpha", "router")
    b = await _mk_node(client, pid, "beta", "router")
    link = await _mk_link(client, pid, a["id"], b["id"])

    na = (await client.get(f"/api/nodes/{a['id']}")).json()
    nb = (await client.get(f"/api/nodes/{b['id']}")).json()
    assert [i["name"] for i in na["interfaces"]] == ["eth0"]
    assert [i["name"] for i in nb["interfaces"]] == ["eth0"]
    assert na["interfaces"][0]["peer_link_id"] == link["id"]
    assert link["a_iface"] == na["interfaces"][0]["id"]

    # Deleting the link releases the ports.
    resp = await client.delete(f"/api/links/{link['id']}")
    assert resp.status_code == 200
    na2 = (await client.get(f"/api/nodes/{a['id']}")).json()
    assert na2["interfaces"][0]["peer_link_id"] is None


async def test_auto_address_wizard_makes_topology_pingable(client):
    """Unaddressed routers + hosts + switch -> wizard -> working pings."""
    pid = await _mk_project(client)
    r1 = await _mk_node(client, pid, "r1", "router")
    r2 = await _mk_node(client, pid, "r2", "router")
    sw = await _mk_node(client, pid, "sw", "switch")
    h1 = await _mk_node(client, pid, "h1", "host")

    await _mk_link(client, pid, r1["id"], r2["id"])   # p2p /30
    await _mk_link(client, pid, r1["id"], sw["id"])   # LAN
    await _mk_link(client, pid, h1["id"], sw["id"])   # LAN

    resp = await client.post(f"/api/lab/{pid}/auto-address")
    assert resp.status_code == 200, resp.text
    plan = resp.json()
    assert plan["nodes_updated"] >= 3

    # The host must now reach its gateway router.
    r1_node = (await client.get(f"/api/nodes/{r1['id']}")).json()
    lan_ip = next(
        (i["ip"][0] for i in r1_node["interfaces"] if i["ip"] and i["ip"][0].endswith("/24")),
        None,
    )
    assert lan_ip is not None
    gw = lan_ip.split("/")[0]
    ping = await client.post(
        f"/api/lab/{pid}/ping", json={"src": "h1", "dst": gw, "count": 2}
    )
    assert ping.status_code == 200, ping.text
    assert ping.json()["received"] == 2

    # And the two routers can reach each other over the /30.
    r2_node = (await client.get(f"/api/nodes/{r2['id']}")).json()
    p2p_ip = next(
        (i["ip"][0] for i in r2_node["interfaces"] if i["ip"] and i["ip"][0].endswith("/30")),
        None,
    )
    assert p2p_ip is not None
    ping2 = await client.post(
        f"/api/lab/{pid}/ping", json={"src": "r1", "dst": p2p_ip.split("/")[0], "count": 2}
    )
    assert ping2.json()["received"] == 2

    # NG-TD-03: the wizard is dual-stack — the same p2p link also got a ULA /64,
    # and the routers reach each other over IPv6 too.
    p2p_ip6 = next(
        (ip for i in r2_node["interfaces"] for ip in i["ip"] if ip.endswith("/64")),
        None,
    )
    assert p2p_ip6 is not None and p2p_ip6.startswith("fd00:")
    ping6 = await client.post(
        f"/api/lab/{pid}/ping", json={"src": "r1", "dst": p2p_ip6.split("/")[0], "count": 2}
    )
    assert ping6.json()["received"] == 2
    # The host learned an IPv6 default gateway.
    h1_node = (await client.get(f"/api/nodes/{h1['id']}")).json()
    assert (h1_node["intent"] or {}).get("gateway6", "").startswith("fd00:")


async def test_auto_address_dry_run_is_zero_persist(client):
    """dry_run=true returns a summarized plan but MUST NOT touch any node."""
    pid = await _mk_project(client)
    r1 = await _mk_node(client, pid, "r1", "router")
    r2 = await _mk_node(client, pid, "r2", "router")
    sw = await _mk_node(client, pid, "sw", "switch")
    h1 = await _mk_node(client, pid, "h1", "host")
    await _mk_link(client, pid, r1["id"], r2["id"])
    await _mk_link(client, pid, r1["id"], sw["id"])
    await _mk_link(client, pid, h1["id"], sw["id"])

    # Snapshot every node's addressing + intent before the dry run.
    before = {
        n["id"]: ([i["ip"] for i in n["interfaces"]], n.get("intent"))
        for n in (await client.get(f"/api/projects/{pid}/topology")).json()["nodes"]
    }

    resp = await client.post(f"/api/lab/{pid}/auto-address", params={"dry_run": True})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["dry_run"] is True
    assert body["nodes_updated"] == 0
    # The summary carries preview rows the wizard renders.
    summary = body["summary"]
    assert summary["ipv4"], "dry run should preview at least one IPv4 domain"
    assert summary["p2p_links"] >= 1 and summary["lan_domains"] >= 1
    for row in summary["ipv4"]:
        assert "/" in row["subnet"] and row["gateway"] and row["hosts"] >= 0

    # Nothing persisted: every node's interfaces + intent are byte-for-byte the same.
    after = {
        n["id"]: ([i["ip"] for i in n["interfaces"]], n.get("intent"))
        for n in (await client.get(f"/api/projects/{pid}/topology")).json()["nodes"]
    }
    assert after == before


async def test_lab_endpoints_require_auth(anon_client):
    resp = await anon_client.post(
        "/api/lab/some-project/ping", json={"src": "a", "dst": "b"}
    )
    assert resp.status_code == 401


async def test_lab_unknown_project_is_404(client):
    resp = await client.post(
        "/api/lab/nope/ping", json={"src": "a", "dst": "10.0.0.1"}
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# F62 — /api/lab/* runs each request's engine work on a real OS thread via
# run_in_threadpool. Two concurrent requests against the *same* project's
# Lab share one Network/Ledger with no synchronization, so their dispatch
# loops can interleave and corrupt the shared ledger seq counter.
#
# The race window is forced deterministically (no timing luck): the first
# ledger event dispatched after warm-up captures the pre-increment seq, then
# sleeps — long enough for a genuinely concurrent second request to run its
# whole ping (many ledger events) if nothing serializes the two requests.
# Resuming with the *stale* seq reproduces the classic lost-update. With the
# per-project lock in place, the second request can't even enter its work()
# closure during that sleep (blocked acquiring the lock), so nothing races
# and the sleep just costs wall-clock time — deterministically green.
# ---------------------------------------------------------------------------

def _install_ledger_race(lab):
    """Replace the ledger's dispatch observer (registered once at Network
    build time, so a class-level monkeypatch can't reach the already-bound
    callback the scheduler holds) with one that stalls its first invocation.

    Mutating ``scheduler._observers`` in place — not ``Ledger``/``engine``
    source — keeps this a test-only injection, not an engine change.
    """
    import threading
    import time

    ledger = lab.net.ledger
    observers = lab.net.scheduler._observers

    PAUSE_S = 0.5
    state = {"paused": False}
    guard = threading.Lock()

    def patched(now, event):
        with guard:
            first = not state["paused"]
            state["paused"] = True
        record_seq = ledger.seq
        if first:
            time.sleep(PAUSE_S)  # let a real concurrent request run freely here
        # Reproduce Ledger._on_event's `self.seq += 1` — for the stalled
        # call this uses the seq captured *before* sleeping, exactly what
        # two racing OS threads can produce on an unsynchronized
        # read-modify-write.
        ledger.seq = record_seq + 1
        ledger.records.append({
            "seq": ledger.seq,
            "t": round(now, 9),
            "type": event.type.name,
            "node": event.node_id or "",
        })

    for i, obs in enumerate(observers):
        if getattr(obs, "__self__", None) is ledger:
            observers[i] = patched


async def test_concurrent_pings_on_same_project_do_not_corrupt_ledger(client):
    """Two /lab/{id}/ping calls racing on one project must not desync the
    shared ledger's seq counter (F62) — proves the per-project lock actually
    serializes engine mutation, not just API-layer bookkeeping."""
    pid = await _routed_topology(client)

    # Build + settle the lab *before* installing the race hook, so this test
    # isolates the do_ping race from the separate first-build race.
    warm = await client.post(
        f"/api/lab/{pid}/ping", json={"src": "h1", "dst": "h2", "count": 1}
    )
    assert warm.status_code == 200, warm.text

    _install_ledger_race(get_lab_manager().peek(pid))

    r1, r2 = await asyncio.gather(
        client.post(f"/api/lab/{pid}/ping", json={"src": "h1", "dst": "h2", "count": 3}),
        client.post(f"/api/lab/{pid}/ping", json={"src": "h1", "dst": "h2", "count": 3}),
    )
    assert r1.status_code == 200, r1.text
    assert r2.status_code == 200, r2.text

    lab = get_lab_manager().peek(pid)
    seqs = [rec["seq"] for rec in lab.net.ledger.records]
    assert len(seqs) == len(set(seqs)), (
        f"ledger seq corrupted by concurrent /lab/ping on one project (F62): "
        f"duplicate seq values in {seqs}"
    )
