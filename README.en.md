*Bahasa: [Indonesia](README.md) | English*

<div align="center">

# NetGeo

**Self-hosted network simulation, RF/GIS planning & digital-twin platform**

*You draw a topology. It runs a real netstack underneath — routing tables, DR/BDR elections,
pcapng captures — and when a real FRR router disagrees with the sim, that's a bug report, not
a footnote.*

[![CI](https://github.com/NetGeo-Network-Simmulation/netgeo/actions/workflows/backend.yml/badge.svg)](https://github.com/NetGeo-Network-Simmulation/netgeo/actions)
![License](https://img.shields.io/badge/license-Apache--2.0-blue)
![Version](https://img.shields.io/badge/version-1.2.124-brightgreen)
![Channel](https://img.shields.io/badge/channel-beta-blueviolet)
![Python](https://img.shields.io/badge/python-3.12+-blue)
![React](https://img.shields.io/badge/react-18-61dafb)

</div>

---

## What this is

A network engineer who has stared at one too many "topology diagrams" that were really just boxes
and lines built NetGeo. It is a simulator, an RF/fiber planner, and a config-import digital twin,
in one app: draw a topology (or import a real Cisco/MikroTik config), watch it run a deterministic
netstack — VLANs, OSPF, BGP, NAT, ACLs — capture the packets, and ask it "can A reach B" and get
an actual answer with evidence, not a guess.

For network students, instructors grading labs, and ISP/network engineers who want to plan or
sanity-check a topology without touching production gear.

---

## Install it

NetGeo ships in five forms. Two work today. Read the table before you pick one — the sizes are
large on purpose (a real native window bundles a real Qt runtime; that is not a bug to "optimize
away").

| # | Form | Frontend | Backend | Map | Status |
|---|---|---|---|---|---|
| 1 | Native, fully offline | native window | local | open-source tiles | **not yet** — window is real, tiles still hit the internet by default |
| 2 | Native + Google Maps | native window | local | Google Maps API | **not built** — no integration exists |
| 3 | Native + remote backend | native window | your server | online | **not built** — launcher always starts a local backend |
| 4 | Headless | browser | local | online or local | **works today** — `--no-window` (see below) |
| 5 | Full online | browser | your server | online | **works today** — this is the Docker install below |

### Download a release (fastest path)

Grab the latest tag's assets from [Releases](https://github.com/NetGeo-Network-Simmulation/netgeo/releases/tag/v1.2.124):

| Asset | Size | What it is |
|---|---|---|
| `NetGeo-x86_64.AppImage` | 231.6 MB | Native Qt window, Linux, no install step — `chmod +x`, run it |
| `netgeo_1.2.124_amd64.deb` | — | Debian/Ubuntu, `apt` installs the Qt dependencies |
| `netgeo-1.2.124-1.x86_64.rpm` | — | Fedora/RHEL, `dnf` installs the Qt dependencies |
| `netgeo-1.2.124-setup.exe` | 157.9 MB | Windows installer, native window — **built, never run-tested on real Windows** |
| `netgeo-linux-x86_64.tar.gz` | 253.5 MB | Onedir bundle, Linux, extract and run `netgeo` |

The AppImage, exe, and tarball are all the native-window path (forms #1/#2/#3 above, minus the
parts not built yet — today they behave like form #4, backend local, map online). See
`packaging/README.md` for what each asset contains and how it's built.

On Fedora, RHEL, Debian, or Ubuntu, prefer the `.deb`/`.rpm` over the AppImage. These packages
declare their dependencies, so `apt`/`dnf` install the Qt/QtWebEngine runtime for you — instead of
bundling everything itself the way the AppImage does.

```
sudo apt install ./netgeo_1.2.124_amd64.deb    # Debian, Ubuntu
sudo dnf install ./netgeo-1.2.124-1.x86_64.rpm # Fedora, RHEL
```

Uninstall anytime with `apt remove netgeo` / `dnf remove netgeo` — both only remove the files they
installed under `/opt` and `/usr`, never touching `~/.config/netgeo/`, so your offline map survives.

### Docker / server install (form #5 — full online)

This is what you want for a shared, browser-accessed instance.

**Prerequisites:** Git, Docker + Docker Compose, a free port **8090** (override with `HTTP_PORT`).
On Linux the installer auto-installs Docker (Fedora, Ubuntu, Debian, RHEL, Arch); on Windows/macOS
install Docker Desktop first.

```bash
curl -fsSL https://raw.githubusercontent.com/NetGeo-Network-Simmulation/netgeo/main/bootstrap.sh | bash
```

or manually:

```bash
git clone https://github.com/NetGeo-Network-Simmulation/netgeo.git
cd netgeo
./install.sh          # Linux / macOS
.\install.ps1         # Windows PowerShell
```

The installer generates secrets, builds the stack (PostgreSQL + FastAPI + React behind nginx),
waits for `/api/health`, and prints:

```
On this machine  ->  http://localhost:8090
On the network   ->  http://<LAN-IP>:8090
API docs         ->  http://<LAN-IP>:8090/docs
```

| Command | Effect |
|---|---|
| `./install.sh --rebuild` | Force rebuild, no cache |
| `./install.sh --down` | Stop the stack |
| `./install.sh --reset` | Stop and delete all data |
| `HTTP_PORT=9000 ./install.sh` | Use a different port |
| `./uninstall.sh` | Uninstall, keep data + system config |
| `./uninstall.sh --purge` | Full clean — data volumes, local images, update-watcher, firewall rule, `/var/lib/netgeo` |

> Deleted the repo folder already? `--purge` still finds NetGeo's Docker footprint by name:
> ```bash
> curl -fsSL https://raw.githubusercontent.com/NetGeo-Network-Simmulation/netgeo/main/uninstall.sh | sudo bash -s -- --purge --yes
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

### Headless mode (form #4)

Backend runs local, UI opens in your regular browser instead of a native window — the honest
choice when you don't want a desktop dependency, or you're on a box with no display at all:

```bash
python packaging/launcher.py --no-window
NETGEO_NO_WINDOW=1 python packaging/launcher.py   # same thing, for systemd units
```

### Offline map region

The backend can serve basemap tiles from a local `.mbtiles` file instead of the internet
(`GET /api/maps/tiles/{z}/{x}/{y}`); the frontend switches to it automatically when one exists and
falls back to online tiles the moment it doesn't — no curated regions are hosted anywhere, so you
bring the file or a URL to fetch it from:

```bash
./install.sh --offline-map=/path/to/region.mbtiles
./install.sh --offline-map-url=https://example.org/region.mbtiles
./install.sh --no-offline-map          # skip, no prompt
```

The Windows installer asks the same question in a wizard page. To add or remove a map after the
fact, just drop or delete the file — no installer needed:

```bash
mkdir -p ~/.config/netgeo
cp my-region.mbtiles ~/.config/netgeo/offline-map.mbtiles   # install
rm ~/.config/netgeo/offline-map.mbtiles                      # remove, back to online tiles
```

Full detail (file locations, fallback rules, how the Windows wizard is wired) is in
[`packaging/README.md`](packaging/README.md).

---

## What makes the engine worth trusting

Most simulators are self-consistent — they only ever check themselves against themselves. NetGeo
also runs an **oracle test**: the identical topology, once on our deterministic Python engine,
once on a real **FRR 10.7.0** container, compared on exactly what the RFC pins down (not CLI
formatting, not timing).

| Comparison | RFC-mandated outcome | Result |
|---|---|---|
| OSPF DR/BDR election (RFC 2328 §9.4/§7.3), non-preemptive | Who gets elected, and that a late higher-priority router doesn't unseat the incumbent | **Match** |
| BGP best-path — shortest AS-path | Route with the shorter AS-path wins | **Match** |
| BGP best-path — eBGP over iBGP | eBGP-learned route preferred over iBGP-learned, all else equal | **Match** |

Zero mismatches on the cases tested so far. See
[`backend/tests/ORACLE_HARNESS.md`](backend/tests/ORACLE_HARNESS.md) for how a case is built.

The remaining tie-breaks — local-pref, ORIGIN, MED — were blocked for a while, and not by the
harness: the engine had no way to set those attributes at all, so best-path logic existed that no
operator could reach. `add_neighbor(..., local_pref_in=N, med_out=N)` and
`advertise_network(..., origin=...)` closed that in v1.2.123, which is why oracle cases for them are
next rather than impossible. Making MED settable immediately exposed a real bug: it leaked across
eBGP boundaries, invisible for as long as its only value was zero.

Beyond that: a pure-Python engine (no native deps — runs on Linux, Windows, ARM) driving L2 (MAC
learning, 802.1Q, STP, LACP), L3 (longest-prefix routing, NAT44, ACLs, DHCP, DNS), OSPF multi-area,
BGP with route-reflectors and communities, VRRP, dual-stack IPv4+IPv6, DSCP-based QoS queueing,
a Cisco/MikroTik-like CLI per device, pcapng export, and a config-import digital twin with a
reachability engine that answers "can A reach B" with the actual routing decision as evidence.
Full feature list: [`dev-docs/ARCHITECTURE.md`](dev-docs/ARCHITECTURE.md).

Starts in under 3 seconds, idles below 300 MB RAM.

---

## What's not here

Said out loud on purpose — a missing feature you can plan around beats a claimed one that breaks
on you:

- No IS-IS, MPLS, Segment Routing, or EVPN.
- No QoS traffic shaping beyond DSCP classify/mark/queue (no policers, no shaper hierarchies).
- No full TCP state machine — the netstack simulates reachability and routing, not a byte-accurate
  transport stack.
- No DNS64/NAT64.
- BGP local-pref/ORIGIN/MED are configurable as of v1.2.123, but not yet cross-checked against FRR —
  the oracle cases for them are written next. Defaults stay local-pref 100, origin IGP, MED 0.
- Windows installer (`netgeo-1.2.123-setup.exe`) has never been run on a real Windows machine —
  built and inspected, not verified end-to-end.
- No multi-tenant isolation on the full-online form (#5) — one shared instance, one set of data.
- Distribution forms #1 (true offline maps), #2 (Google Maps), #3 (remote backend for the native
  window) don't exist yet — see the table above.

---

## Tech stack

**Backend:** Python 3.12+, FastAPI (async), Pydantic, PostgreSQL, Pytest.
**Frontend:** React 18 + TypeScript, Vite, Zustand, React Flow, Tailwind CSS.
**Infra:** Docker + Docker Compose behind nginx; native packaging via PyInstaller + Qt (pywebview).

---

## Contributing

Start with **[CONTRIBUTING.md](CONTRIBUTING.md)** — local setup, the commands CI runs, the branch/PR
workflow. For architecture, the engine's determinism contract, adding a protocol, and the frontend
structure, see **[dev-docs/](dev-docs/)**.

Work on a branch (`<scope>/<intent>`), keep `main` green, open a PR. `main` is protected — the
`test` (backend lint + pytest) and `build` (frontend typecheck + build) checks must pass before
merge.

Bugs and ideas: [Issues](https://github.com/NetGeo-Network-Simmulation/netgeo/issues).

---

## License

[Apache-2.0](LICENSE) © Muhammad Surya Ragasin — Politeknik Negeri Sriwijaya, D4 Teknik Telekomunikasi.
