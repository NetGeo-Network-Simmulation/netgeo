"""Event-ledger tests — NG-SIM-01 foundation + NG-NFR-01 replay determinism.

The contract: same topology + same seed → byte-identical event stream,
verified through the ledger's incremental hash. Also covers the HTTP surface
(/api/lab/{id}/ledger) and the /api/v2 alias (NG-NFR-04).
"""
from __future__ import annotations

from engine.netstack import Network
from engine.netstack.device import Host
from engine.netstack.switching import Switch


def _lan(seed: int, loss: float = 0.0) -> Network:
    net = Network(seed=seed)
    h1 = net.add_device(Host("h1"))
    h2 = net.add_device(Host("h2"))
    sw = net.add_device(Switch("sw1"))
    i1 = net.add_iface(h1, "eth0", ["10.0.0.1/24"])
    i2 = net.add_iface(h2, "eth0", ["10.0.0.2/24"])
    net.connect("l1", i1, net.add_iface(sw, "gi0/1"), delay=0.0001, loss=loss)
    net.connect("l2", i2, net.add_iface(sw, "gi0/2"), delay=0.0001, loss=loss)
    net.ping("h1", "10.0.0.2", count=4)
    return net


def test_replay_same_seed_identical_hash():
    a, b = _lan(seed=42), _lan(seed=42)
    assert a.ledger.seq > 0
    assert a.ledger.seq == b.ledger.seq
    assert a.ledger.hash() == b.ledger.hash()


def test_replay_with_loss_still_deterministic():
    # Stochastic loss draws come from the seeded RNG — still replayable.
    a, b = _lan(seed=7, loss=0.3), _lan(seed=7, loss=0.3)
    assert a.ledger.hash() == b.ledger.hash()
    # A different seed must produce a different stream under loss.
    c = _lan(seed=8, loss=0.3)
    assert c.ledger.hash() != a.ledger.hash()


def test_tail_filters_and_pagination():
    net = _lan(seed=42)
    led = net.ledger
    page1 = led.tail(from_seq=0, limit=5)
    assert [r["seq"] for r in page1] == [1, 2, 3, 4, 5]
    page2 = led.tail(from_seq=page1[-1]["seq"], limit=5)
    assert page2[0]["seq"] == 6
    only_rx = led.tail(limit=10_000, type_prefix="PACKET_RX")
    assert only_rx and all(r["type"] == "PACKET_RX" for r in only_rx)


def test_hash_covers_more_than_ring_window():
    # Ring keeps 8 records, but the hash is over the full stream.
    a, b = _lan(seed=42), _lan(seed=42)
    a.ledger.records = type(a.ledger.records)(a.ledger.records, 8)
    assert len(a.ledger.records) <= 8 < a.ledger.seq
    assert a.ledger.hash() == b.ledger.hash()


# ---------------------------------------------------------------------------
# HTTP surface + /api/v2 alias
# ---------------------------------------------------------------------------

async def test_api_v2_alias_serves_same_routes(client, anon_client):
    v1 = await anon_client.get("/api/health")
    v2 = await anon_client.get("/api/v2/health")
    assert v2.status_code == 200 and v2.json() == v1.json()
    # Protected route parity — auth still enforced through the alias.
    assert (await anon_client.get("/api/v2/projects")).status_code == 401
    assert (await client.get("/api/v2/projects")).status_code == 200


async def test_ledger_endpoint(client):
    resp = await client.post("/api/v2/projects", json={"name": "LedgerLab"})
    assert resp.status_code == 201
    pid = resp.json()["id"]
    for name, ip in (("h1", "10.0.0.1/24"), ("h2", "10.0.0.2/24")):
        resp = await client.post(
            "/api/v2/nodes",
            json={
                "project_id": pid,
                "name": name,
                "kind": "host",
                "interfaces": [{"id": "", "node_id": "", "name": "eth0", "ip": [ip]}],
            },
        )
        assert resp.status_code == 201, resp.text
    resp = await client.post(
        "/api/v2/links", json={"project_id": pid, "a_iface": "h1", "b_iface": "h2"}
    )
    assert resp.status_code == 201, resp.text

    ping = await client.post(
        f"/api/v2/lab/{pid}/ping", json={"src": "h1", "dst": "10.0.0.2", "count": 2}
    )
    assert ping.status_code == 200, ping.text

    led = (await client.get(f"/api/v2/lab/{pid}/ledger?limit=10")).json()
    assert led["total"] > 0 and len(led["hash"]) == 64
    assert led["records"][0]["seq"] == 1
    filtered = (
        await client.get(f"/api/v2/lab/{pid}/ledger?type_prefix=PACKET_RX&limit=5")
    ).json()
    assert all(r["type"] == "PACKET_RX" for r in filtered["records"])
