<div align="center">

# NetGeo

**Self-hosted network simulation, planning & digital-twin platform**

*Packet-realistic simulation · RF/fiber planning · config-import digital twin — one cross-platform app.*

[![CI](https://github.com/suryaex/netgeo/actions/workflows/backend.yml/badge.svg)](https://github.com/suryaex/netgeo/actions)
![License](https://img.shields.io/badge/license-Apache--2.0-blue)
![Version](https://img.shields.io/badge/version-1.2.37-brightgreen)
![Channel](https://img.shields.io/badge/channel-beta-blueviolet)
![Python](https://img.shields.io/badge/python-3.12+-blue)
![React](https://img.shields.io/badge/react-18-61dafb)

</div>

---

## Installation

**Prerequisites:** Git, Docker + Docker Compose, and a free port **8090** (override with `HTTP_PORT`).
On Linux the installer auto-installs Docker (Fedora, Ubuntu, Debian, RHEL, Arch); on Windows/macOS install Docker Desktop first.

**One command (Linux / macOS)** — clones the repo and runs the installer:

```bash
curl -fsSL https://raw.githubusercontent.com/suryaex/netgeo/main/bootstrap.sh | bash
```

**Manual (all platforms)**:

```bash
git clone https://github.com/suryaex/netgeo.git
cd netgeo
./install.sh          # Linux / macOS
.\install.ps1         # Windows PowerShell
```

The installer generates secrets, builds the full stack (PostgreSQL + FastAPI backend + React frontend behind an nginx gateway), waits for `/api/health`, and prints the URLs:

```
On this machine  ->  http://localhost:8090
On the network   ->  http://<LAN-IP>:8090
API docs         ->  http://<LAN-IP>:8090/docs
```

**Common options:**

| Command | Effect |
|---|---|
| `./install.sh` | Build + start |
| `./install.sh --rebuild` | Force rebuild (no cache) |
| `./install.sh --down` | Stop the stack |
| `./install.sh --reset` | Stop and delete all data |
| `HTTP_PORT=9000 ./install.sh` | Use a different port |
| `./uninstall.sh` | Uninstall (keep data + system config) |
| `./uninstall.sh --purge` | Full clean — also remove data volumes, local images, the update-watcher service, the firewall rule, and `/var/lib/netgeo` (Docker engine & Tailscale kept) |

> Already deleted the repo folder? `--purge` still cleans up — it finds NetGeo's
> Docker footprint by name, no compose files needed:
> ```bash
> curl -fsSL https://raw.githubusercontent.com/suryaex/netgeo/main/uninstall.sh | sudo bash -s -- --purge --yes
> ```

<details>
<summary>Run backend / frontend directly (development)</summary>

```bash
# Backend
cd backend
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000   # http://localhost:8000/docs
pytest -q

# Frontend
cd frontend
npm install
npm run dev                                 # http://localhost:5180
```

</details>

---

## Features

A pure-Python engine (no native dependencies — runs on Linux, Windows, and ARM) drives a desktop-class web UI.

- **Packet-realistic simulation** — a deterministic discrete-event netstack: L2 (MAC learning, 802.1Q VLANs, STP, LAG/LACP), L3 (longest-prefix routing, NAT44, ACLs, DHCP, DNS), dynamic routing (**OSPF** multi-area, **BGP** with route-reflectors & communities), VRRP, and full **IPv4 + IPv6** dual-stack. Every link is captured for packet inspection and every run is bit-for-bit reproducible for a given seed.

- **Live CLI & diagnostics** — each device speaks a Cisco-like or MikroTik-like CLI over its real tables (`show ip route`, `show ip ospf neighbor`, `ping`, `traceroute`, …). Ping / traceroute / capture APIs, pcapng export with a display-filter inspector, and a **simulation mode** with an event ledger you can replay and step back through.

- **Digital twin** — import a running device config (**Cisco IOS-like** or **MikroTik RouterOS**, including OSPF/BGP) into the model; link inference wires devices that share a subnet into a connected twin; the **reachability engine** answers *"can A reach B?"* with evidence — ping result, traceroute path, and the source router's routing decision.

- **Map deploy** — click a point on the GIS map and choose **Wireless** (AP / CPE) or **Cabled** (router / switch / OLT): the device is created as a real topology node at those coordinates, auto-linked to its nearest upstream, and rack-mounted kinds are slotted into the site rack automatically. Map markers and link lines render straight from the live topology.

- **RF planning** — FSPL / Hata / COST-231 propagation, coverage rasters, and point-to-point / point-to-multipoint link budgets with automatic product selection.

- **Fiber & FTTH** — GPON loss-budget planner with splitter tables, plus bill-of-materials and HTML report generation.

- **Physical plant** — sites, racks, and cabling with rack-unit placement, a rack elevation view, and cable-length-driven propagation delay.

- **Education** — author lab activities and auto-grade a student's topology (interface addressing, VLANs, OSPF adjacency, reachability), with timed and shareable labs.

- **Addressing & config** — auto-addressing wizard with a dry-run preview step (shows planned assignments before committing), one-click dual-stack (IPv4 + IPv6 ULA) assignment, whole-project vendor config export, and a config regeneration diff view.

- **Traffic & QoS** — DSCP classify/mark with three strict-priority classes (EF/AF/BE), per-class queueing that shapes delay and drop as a link saturates, and per-class counters surfaced in the event ledger and `show qos` CLI.

- **Workspace UI** — an n8n/Figma-style topology canvas (floating bezier edges, port dots, hover-to-connect, drag-to-reconnect edge endpoints, live dashed connection preview, minimap) plus dedicated workspaces — Projects Portal, Config Center, Problem Center, and Reports Center — in matching dark & light themes. Device icons are customizable: import your own SVG/PNG symbols (GNS3-style) and assign them per node.

- **Projects** — multi-project workspace with export/import archives, real-time collaboration channel, JWT + WebSocket auth, and in-app self-update from GitHub releases.

Designed to start in under 3 seconds and idle below 300 MB RAM.

---

## Tech stack

**Backend:** Python 3.12+, FastAPI (async), Pydantic, PostgreSQL, Pytest.
**Frontend:** React 18 + TypeScript, Vite, Zustand, React Flow, Tailwind CSS.
**Infra:** Docker + Docker Compose behind an nginx gateway.

---

## Roadmap (post-1.0)

Picked by demand, not order: IS-IS / MPLS / Segment Routing / EVPN-VXLAN, twin drift-diff & telemetry overlay, RF interference & Monte-Carlo, a GNS3-class emulation bridge, and an MCP-compatible AI assistant.

---

## Contributing

Pull requests are welcome. Start with **[CONTRIBUTING.md](CONTRIBUTING.md)** — it covers local setup,
the commands CI runs, and the branch/PR workflow.

In short: work on a branch (`<scope>/<intent>`), keep `main` green, and open a PR. `main` is protected —
direct pushes are rejected, and the `test` (backend lint + pytest) and `build` (frontend typecheck +
build) checks must pass before a PR can merge.

Bug reports and feature ideas go to [Issues](https://github.com/suryaex/netgeo/issues) using the
templates provided.

---

## License

[Apache-2.0](LICENSE) © Muhammad Surya Ragasin — Politeknik Negeri Sriwijaya, D4 Teknik Telekomunikasi.
