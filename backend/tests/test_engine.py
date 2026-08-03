"""Test kernel discrete-event: determinisme & korektnya forwarding.

Membangun ``NetworkModel`` engine secara langsung (tanpa lapisan web/DB) lalu
memverifikasi:
  1. paket benar-benar terkirim end-to-end melalui shortest-path,
  2. dua run dengan model+seed sama menghasilkan hasil identik (reproducibility,
     kontrak MASTER_SPEC §1/§5),
  3. model loss menjatuhkan paket secara deterministik mengikuti RNG ber-seed.
"""
from __future__ import annotations

from engine import (
    InterfaceModel,
    LinkModel,
    NetworkModel,
    NodeModel,
    Packet,
    Simulation,
    SimulationConfig,
)


def _line_topology(n: int, loss: float = 0.0) -> NetworkModel:
    """Bangun rantai linear n0 - n1 - ... - n(n-1)."""
    model = NetworkModel()
    for i in range(n):
        model.add_node(
            NodeModel(
                id=f"n{i}",
                name=f"n{i}",
                interfaces=[
                    InterfaceModel(id=f"n{i}-L", node_id=f"n{i}", name="L"),
                    InterfaceModel(id=f"n{i}-R", node_id=f"n{i}", name="R"),
                ],
            )
        )
    for i in range(n - 1):
        model.add_link(
            LinkModel(
                id=f"l{i}",
                a_iface=f"n{i}-R",
                b_iface=f"n{i+1}-L",
                bandwidth=1_000_000_000,
                delay=0.001,
                loss=loss,
            )
        )
    return model


def test_packet_delivered_end_to_end():
    model = _line_topology(4)
    sim = Simulation(model, SimulationConfig(seed=1))
    sim.inject(Packet(src="n0", dst="n3", proto="icmp"))
    result = sim.run()
    assert result.delivered == 1
    assert result.dropped == 0
    # 3 hop * (serialization + 1ms propagasi) > 0
    assert result.sim_time > 0.0
    assert result.avg_latency > 0.0


def test_run_is_deterministic():
    """Model + seed identik -> hasil identik (bit-for-bit pada metrik)."""
    def run():
        model = _line_topology(5, loss=0.2)
        sim = Simulation(model, SimulationConfig(seed=42))
        for k in range(20):
            sim.inject(Packet(src="n0", dst="n4", proto="udp", id=1000 + k))
        return sim.run().as_dict()

    a = run()
    b = run()
    assert a == b, "run dengan seed sama harus deterministik"


def test_seed_changes_loss_outcome():
    """RNG ber-seed benar-benar mempengaruhi jalur loss.

    Dua properti yang keduanya deterministik (nol flaky):
      1. seed sama -> delivered count identik (jaminan keras).
      2. di antara banyak seed yang dicoba, setidaknya ada satu pasang yang
         beda hasil -- membuktikan RNG memang dipakai di jalur loss, bukan
         seluruh loss=0.5 diabaikan/di-hardcode. Memakai banyak seed (bukan
         cuma 1 vs 999) supaya assert tidak bergantung pada satu pasangan
         seed yang kebetulan sama.
    """
    def delivered_for(seed: int) -> int:
        model = _line_topology(5, loss=0.5)
        sim = Simulation(model, SimulationConfig(seed=seed))
        for k in range(50):
            sim.inject(Packet(src="n0", dst="n4", id=2000 + k))
        return sim.run().delivered

    # 1. seed sama -> hasil identik, tak pernah flaky.
    assert delivered_for(7) == delivered_for(7)

    # 2. RNG benar mempengaruhi hasil: seed sama dua kali dulu untuk jangkar,
    # lalu cari sepasang seed berbeda di antara banyak kandidat yang hasilnya
    # beda. Kalau jalur RNG dicabut (loss jadi deterministik), semua seed
    # akan memberi delivered count yang sama dan assert ini gagal.
    outcomes = {seed: delivered_for(seed) for seed in range(20)}
    assert len(set(outcomes.values())) > 1, (
        f"20 seed berbeda semuanya memberi delivered count sama: {outcomes} "
        "-- RNG sepertinya tidak lagi mempengaruhi jalur loss"
    )


def test_down_link_breaks_delivery():
    """Sebuah link yang di-set down harus benar-benar memutus trafik.

    Regresi: dulu status link diabaikan saat membangun model engine sehingga
    paket tetap "terkirim" lewat link yang sudah dimatikan.
    """
    model = _line_topology(3)
    model.links["l1"].up = False  # putuskan n1<->n2
    sim = Simulation(model, SimulationConfig(seed=0))
    sim.inject(Packet(src="n0", dst="n2"))
    result = sim.run()
    assert result.delivered == 0
    assert result.dropped == 1


def test_down_link_reroutes_when_alternate_exists():
    """Jika ada jalur sehat alternatif, trafik harus dibelokkan, bukan di-drop.

    Topologi berlian: n0-n1-n3 dan n0-n2-n3. Matikan n1<->n3, paket n0->n3
    harus tetap terkirim lewat n2.
    """
    model = NetworkModel()
    for nid in ("n0", "n1", "n2", "n3"):
        model.add_node(
            NodeModel(
                id=nid,
                name=nid,
                interfaces=[
                    InterfaceModel(id=f"{nid}-a", node_id=nid, name="a"),
                    InterfaceModel(id=f"{nid}-b", node_id=nid, name="b"),
                ],
            )
        )
    edges = [("e01", "n0-a", "n1-a"), ("e13", "n1-b", "n3-a"),
             ("e02", "n0-b", "n2-a"), ("e23", "n2-b", "n3-b")]
    for lid, a, b in edges:
        model.add_link(LinkModel(id=lid, a_iface=a, b_iface=b, delay=0.001))
    model.links["e13"].up = False  # matikan satu sisi berlian

    sim = Simulation(model, SimulationConfig(seed=0))
    sim.inject(Packet(src="n0", dst="n3"))
    result = sim.run()
    assert result.delivered == 1
    assert result.dropped == 0
    # rute yang dipakai harus melewati n2, bukan n1 yang putus
    assert model.shortest_path("n0", "n3") == ["n0", "n2", "n3"]


def test_no_route_is_dropped():
    """Node terisolasi -> paket di-drop dengan alasan no_route."""
    model = _line_topology(3)
    # tambah node yatim tanpa link
    model.add_node(
        NodeModel(
            id="orphan",
            name="orphan",
            interfaces=[InterfaceModel(id="orphan-L", node_id="orphan", name="L")],
        )
    )
    sim = Simulation(model, SimulationConfig(seed=0))
    sim.inject(Packet(src="n0", dst="orphan"))
    result = sim.run()
    assert result.delivered == 0
    assert result.dropped == 1
    assert result.drops_by_reason.get("no_route") == 1
