# NetGeo — Installation & Operations Guide

Version: v1.2.36

---

## Overview

NetGeo is distributed as a Docker Compose stack. The installer (`install.sh` / `install.ps1`) handles Docker installation, secret generation, image build, and stack start in a single command. No manual configuration is required for a standard install.

The stack runs 5 containers behind a single nginx gateway:

| Container | Image | Role |
|---|---|---|
| `postgres` | postgres:16-alpine | Persistent storage (topologies, projects, auth) |
| `redis` | redis:7-alpine | Realtime state, WebSocket pub/sub fan-out |
| `backend` | built locally | FastAPI (Python 3.12) on :8000 |
| `frontend` | node:20-alpine (Vite dev) | React 18 UI on :5180 |
| `gateway` | nginx:1.27-alpine | Single-origin entry on HTTP\_PORT (:8090) |

The gateway port is `8090` by default — chosen to avoid conflicts with SecureOps (:80) and StorageHub (:8080) when all three apps run on the same host.

---

## Prerequisites

- **Git** (to clone the repo)
- **Docker + Docker Compose** — the Linux installer auto-installs these; on Windows and macOS install Docker Desktop first
- Free port **8090** (or set `HTTP_PORT` to another value)

---

## Quick Install (Linux / macOS)

Clone and install in one command:

```bash
curl -fsSL https://raw.githubusercontent.com/NetGeo-Network-Simmulation/netgeo/main/bootstrap.sh | bash
```

Or clone first, then run the installer:

```bash
git clone https://github.com/NetGeo-Network-Simmulation/netgeo.git
cd netgeo
./install.sh
```

**Windows (PowerShell):**

```powershell
git clone https://github.com/NetGeo-Network-Simmulation/netgeo.git
cd netgeo
.\install.ps1
```

After the stack is up, the installer prints:

```
On this machine  ->  http://localhost:8090
On the network   ->  http://<LAN-IP>:8090
API docs         ->  http://<LAN-IP>:8090/docs
```

---

## Docker Auto-Install (Linux)

On Linux, if Docker is not present, `install.sh` tries three methods in order:

1. **get.docker.com** convenience script (upstream, most reliable)
2. Distro packages — `moby-engine` (Fedora/RHEL/dnf), `docker.io` (Debian/Ubuntu/apt), `docker`+`docker-compose` (zypper, pacman)
3. `podman` + `podman-docker` shim (Fedora fallback)

On WSL, the installer detects WSL and warns that Docker Desktop with WSL integration is the smoothest path; it still attempts an in-WSL engine install as a fallback.

---

## Install Options

| Command | Effect |
|---|---|
| `./install.sh` | Build + start (dev stack + nginx gateway on :8090) |
| `./install.sh --rebuild` | Force no-cache rebuild, then start |
| `./install.sh --no-build` | Start without rebuilding |
| `./install.sh --down` | Stop the stack |
| `./install.sh --reset` | Stop and delete all data (Postgres + Redis volumes) |
| `./install.sh --prod` | Use the production stack (`docker-compose.prod.yml`) |
| `./install.sh --tailscale` | Install Tailscale and use its VPN IP |
| `./install.sh --public` | Auto-detect public IP and add to CORS |
| `./install.sh --no-updater` | Skip installing the in-app update watcher service |
| `HTTP_PORT=9000 ./install.sh` | Use a different gateway port |

---

## First-Run Admin Setup

NetGeo has **no default password**. On a fresh install, when `NETGEO_ADMIN_PASSWORD` is not set and no auth store file exists, the backend enters first-run setup mode.

The login page detects this (via `GET /api/auth/setup`) and shows a setup form. The first visitor creates the admin account by submitting their chosen username and password (`POST /api/auth/setup`). This endpoint hard-fails with `409 Conflict` once any account exists, so it is only callable once.

Alternatively, seed the admin at deploy time via environment variable:

```bash
NETGEO_ADMIN_PASSWORD=yourpassword ./install.sh
```

Password hashes set through the UI are persisted to `/var/lib/netgeo/auth.json` (bind-mounted into the backend container) and survive container recreates.

---

## In-App Update

`install.sh` installs a host-side watcher service (`netgeo-updater`, systemd unit `/etc/systemd/system/netgeo-updater.service`) that monitors `/var/lib/netgeo/update.request`. When the dashboard's "Check for updates" action triggers a release download, the backend writes that sentinel file and the watcher runs `scripts/self-update.sh --apply`, which:

1. `git fetch` + fast-forward to the latest release tag
2. Re-runs `install.sh` with the same flags as the original install (persisted in `/var/lib/netgeo/install.flags`)

On systems without systemd the watcher is not installed; run it manually:

```bash
bash scripts/self-update.sh --watch
```

**Manual update (always works):**

```bash
git pull
./install.sh --rebuild
```

---

## Uninstall

```bash
./uninstall.sh           # stop + remove containers; KEEP data and system config
./uninstall.sh --purge   # full clean: also delete Postgres + Redis volumes,
                         # local images, the netgeo-updater service, the
                         # firewall rule for HTTP_PORT, and /var/lib/netgeo
```

`--purge` works even if the repo directory has already been deleted — it finds NetGeo's Docker footprint by compose-project label and name prefix:

```bash
curl -fsSL https://raw.githubusercontent.com/NetGeo-Network-Simmulation/netgeo/main/uninstall.sh \
  | sudo bash -s -- --purge --yes
```

Deliberately NOT removed by `--purge`: the Docker engine itself and Tailscale (shared host-level dependencies).

---

## Firewall

`install.sh` opens TCP `HTTP_PORT` in the host firewall automatically (idempotent, best-effort):

- **firewalld** (Fedora/RHEL)
- **ufw** (Debian/Ubuntu)
- **nftables** (nft)
- **iptables** (fallback)

`uninstall.sh --purge` reverses the same rule.

**WSL:** On mirrored-networking WSL2, the installer adds a `New-NetFirewallHyperVRule` rule on the Windows side (requires one UAC approval). On NAT-mode WSL it adds a `netsh portproxy` forwarding rule.

---

## Versioning

The running version is a code constant (`APP_VERSION = "1.2.34"` in `backend/app/core/config.py`). It is never stamped into environment variables or compose files — this ensures self-updated installs always report the version they actually run, not the version they were first installed at. The `/api/health` endpoint and the dashboard's update panel both read this constant directly.

---

## Development Setup

Run the backend and frontend directly (no Docker required):

```bash
# Backend — uses in-memory store by default (no Postgres/Redis needed)
cd backend
python3.12 -m venv .venv && source .venv/bin/activate   # Linux/macOS
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000   # http://localhost:8000/docs
pytest -q                                   # test suite

# Frontend
cd frontend
npm install
npm run dev                                 # http://localhost:5180
```

The backend defaults to an in-memory store (`app/store/memory.py`), so it runs without a live database. Vite proxies `/api` and `/ws` to `http://localhost:8000`, so the browser talks to a single origin.

To use PostgreSQL in dev, start only the database container:

```bash
docker compose -f infra/docker-compose.yml up -d postgres redis
```

Then set `DATABASE_URL` in `backend/.env` and restart uvicorn.

---

## Compose File Layout

| File | Purpose |
|---|---|
| `infra/docker-compose.yml` | Core dev stack: postgres, redis, backend (hot-reload), frontend (Vite) |
| `infra/docker-compose.lan.yml` | LAN gateway overlay: adds the nginx `gateway` container on `HTTP_PORT` |
| `infra/docker-compose.prod.yml` | Production stack (immutable images, no source mounts) |

`install.sh` runs `dev + lan` by default; `--prod` switches to `prod` only.

---

## Supported Platforms

**Docker install (recommended):** Linux (Fedora, Ubuntu, Debian, RHEL, Arch, openSUSE), macOS (Docker Desktop), Windows (Docker Desktop or WSL2).

**Direct dev run:** any OS with Python 3.12+ and Node 20+.
