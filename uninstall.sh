#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# NetGeo — uninstaller (Docker stack)
#
# Run before a major update or to remove NetGeo from a machine.
#
# Usage:
#   bash uninstall.sh           # stop + remove containers, KEEP data + system config
#   bash uninstall.sh --purge   # FULL clean: also delete data volumes, local images,
#                               #   the update-watcher service, the firewall rule this
#                               #   installer opened, and /var/lib/netgeo — restoring
#                               #   the host to its pre-install state.
#   bash uninstall.sh --yes     # no confirmation prompt
#
# Already deleted the repo? --purge still works from anywhere (it finds NetGeo's
# Docker footprint by label/name, no compose files needed). Fetch + run:
#   curl -fsSL https://raw.githubusercontent.com/NetGeo-Network-Simmulation/netgeo/main/uninstall.sh \
#     | sudo bash -s -- --purge --yes
#
# Deliberately NOT touched (shared, host-level — not created for NetGeo alone):
#   the Docker engine itself (and docker0), and Tailscale (tailscale0). Remove
#   those by hand if you truly want them gone.
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail
cd "$(dirname "$0")"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
say()  { echo -e "${GREEN}==> $*${NC}"; }
warn() { echo -e "${YELLOW}!! $*${NC}"; }

PURGE=0; ASSUME_YES=0
for a in "$@"; do case "$a" in
  --purge) PURGE=1 ;;
  --yes|-y) ASSUME_YES=1 ;;
esac; done

# State dir + recorded install env (mirrors install.sh). install.env records the
# HTTP_PORT that was actually opened, so we reverse the right firewall rule.
STATE_DIR="${NETGEO_STATE_DIR:-/var/lib/netgeo}"
_ENV_HTTP_PORT="${HTTP_PORT:-}"
# shellcheck disable=SC1090,SC1091
[ -f "$STATE_DIR/install.env" ] && . "$STATE_DIR/install.env" 2>/dev/null || true
HTTP_PORT="${_ENV_HTTP_PORT:-${HTTP_PORT:-8090}}"

if [[ "$ASSUME_YES" != "1" ]]; then
  echo "This will stop and remove NetGeo from this machine."
  if [[ "$PURGE" == "1" ]]; then
    echo -e "${RED}--purge: DELETES the database (Postgres), Redis data and Docker volumes,${NC}"
    echo -e "${RED}         removes local NetGeo images, the netgeo-updater service, the TCP ${HTTP_PORT}${NC}"
    echo -e "${RED}         firewall rule, and ${STATE_DIR}. The Docker engine and Tailscale are kept.${NC}"
  fi
  read -r -p "Type 'yes' to continue: " c; [[ "$c" == "yes" ]] || { echo "Aborted."; exit 0; }
fi

# Compose files live in infra/ — cover dev (+LAN gateway) and prod stacks.
DEV_CF="infra/docker-compose.yml"
LAN_CF="infra/docker-compose.lan.yml"
PROD_CF="infra/docker-compose.prod.yml"

COMPOSE=""
if docker compose version >/dev/null 2>&1; then COMPOSE="docker compose"
elif sudo docker compose version >/dev/null 2>&1; then COMPOSE="sudo docker compose"
elif command -v docker-compose >/dev/null 2>&1; then COMPOSE="docker-compose"; fi

# --purge extras: -v drops named volumes, --rmi local drops the images this
# stack built locally (compose down leaves them otherwise).
DOWN_FLAGS="--remove-orphans"
[[ "$PURGE" == "1" ]] && DOWN_FLAGS="$DOWN_FLAGS -v --rmi local"

if [[ -z "$COMPOSE" ]]; then
  warn "Docker Compose not found — skipping container teardown."
else
  # Production stack (uses infra/.env.prod if present).
  if [[ -f "$PROD_CF" ]]; then
    say "Stopping production stack (if running)…"
    ENVOPT=""; [[ -f infra/.env.prod ]] && ENVOPT="--env-file infra/.env.prod"
    HTTP_PORT="$HTTP_PORT" $COMPOSE $ENVOPT -f "$PROD_CF" down $DOWN_FLAGS 2>/dev/null || true
  fi
  # Development stack + LAN gateway overlay.
  if [[ -f "$DEV_CF" ]]; then
    say "Stopping development stack + LAN gateway (if running)…"
    CF="-f $DEV_CF"; [[ -f "$LAN_CF" ]] && CF="$CF -f $LAN_CF"
    HTTP_PORT="$HTTP_PORT" $COMPOSE $CF down $DOWN_FLAGS 2>/dev/null || true
  fi
fi

# ── System-config cleanup (--purge only): undo what install.sh provisioned ──
is_wsl() { grep -qiE "(microsoft|wsl)" /proc/version 2>/dev/null; }

# Repo-independent teardown: find NetGeo's Docker footprint by compose-project
# label and name prefix, so a --purge still works even when the repo directory
# (and its compose files) has already been deleted — the exact orphan case where
# `docker compose down` above finds nothing to do.
docker_purge_leftovers() {
  local DK=""
  if docker info >/dev/null 2>&1; then DK="docker"
  elif sudo docker info >/dev/null 2>&1; then DK="sudo docker"
  else return 0; fi

  local ids vols nets imgs
  ids="$($DK ps -aq --filter 'label=com.docker.compose.project=netgeo' 2>/dev/null; \
         $DK ps -aq --filter 'name=netgeo' 2>/dev/null | sort -u)"
  # shellcheck disable=SC2086
  [[ -n "$ids" ]] && { $DK rm -f $ids >/dev/null 2>&1 || true; say "Removed NetGeo containers."; }

  vols="$($DK volume ls -q 2>/dev/null | grep -E '^netgeo[-_]' || true)"
  # shellcheck disable=SC2086
  [[ -n "$vols" ]] && { $DK volume rm -f $vols >/dev/null 2>&1 || true; say "Removed NetGeo volumes."; }

  nets="$($DK network ls --format '{{.Name}}' 2>/dev/null | grep -E '^netgeo[-_]' || true)"
  for n in $nets; do $DK network rm "$n" >/dev/null 2>&1 || true; done
  [[ -n "$nets" ]] && say "Removed NetGeo networks."

  imgs="$($DK images --format '{{.Repository}}:{{.Tag}}' 2>/dev/null | grep -E '(^|/)netgeo[-_/]' || true)"
  # shellcheck disable=SC2086
  [[ -n "$imgs" ]] && { $DK rmi -f $imgs >/dev/null 2>&1 || true; say "Removed NetGeo images."; }
}

remove_updater_service() {
  command -v systemctl >/dev/null 2>&1 || return 0
  local unit="/etc/systemd/system/netgeo-updater.service"
  if [[ -f "$unit" ]] || systemctl list-unit-files 2>/dev/null | grep -q '^netgeo-updater'; then
    sudo systemctl disable --now netgeo-updater.service >/dev/null 2>&1 || true
    sudo rm -f "$unit" >/dev/null 2>&1 || true
    sudo systemctl daemon-reload >/dev/null 2>&1 || true
    say "Removed the update-watcher service (netgeo-updater)."
  fi
}

close_firewall() {  # reverse install.sh's open_firewall for the recorded port
  local port="$1" removed=""
  if command -v firewall-cmd >/dev/null 2>&1 && sudo firewall-cmd --state >/dev/null 2>&1; then
    sudo firewall-cmd --permanent --remove-port="${port}/tcp" >/dev/null 2>&1 || true
    sudo firewall-cmd --reload >/dev/null 2>&1 || true
    removed="firewalld"
  elif command -v ufw >/dev/null 2>&1 && sudo ufw status 2>/dev/null | grep -qi active; then
    sudo ufw delete allow "${port}/tcp" >/dev/null 2>&1 || true
    removed="ufw"
  elif command -v iptables >/dev/null 2>&1; then
    # Drop every copy we may have inserted across re-runs.
    while sudo iptables -C INPUT -p tcp --dport "${port}" -j ACCEPT >/dev/null 2>&1; do
      sudo iptables -D INPUT -p tcp --dport "${port}" -j ACCEPT >/dev/null 2>&1 || break
    done
    removed="iptables"
  fi
  [[ -n "$removed" ]] && say "Closed TCP ${port} in the host firewall (${removed})."
}

remove_wsl_firewall() {  # reverse the Windows-side rule + portproxy on WSL
  is_wsl || return 0
  command -v powershell.exe >/dev/null 2>&1 || return 0
  local port="$1" ps enc
  ps="netsh interface portproxy delete v4tov4 listenaddress=0.0.0.0 listenport=${port} 2>\$null | Out-Null; Remove-NetFirewallRule -DisplayName 'NetGeo ${port}' -ErrorAction SilentlyContinue; Remove-NetFirewallHyperVRule -Name 'NetGeo-${port}' -ErrorAction SilentlyContinue"
  enc="$(powershell.exe -NoProfile -Command "[Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes('${ps}'))" 2>/dev/null | tr -d '\r')"
  if [[ -n "$enc" ]] && powershell.exe -NoProfile -Command "Start-Process powershell -Verb RunAs -Wait -ArgumentList '-NoProfile','-EncodedCommand','${enc}'" >/dev/null 2>&1; then
    say "Removed the Windows firewall rule + portproxy for TCP ${port}."
  fi
}

if [[ "$PURGE" == "1" ]]; then
  say "Purging system configuration installed by NetGeo…"
  docker_purge_leftovers          # catch stragglers even if the repo is gone
  remove_updater_service
  close_firewall "$HTTP_PORT"
  remove_wsl_firewall "$HTTP_PORT"
  if [[ -d "$STATE_DIR" ]]; then
    sudo rm -rf "$STATE_DIR" 2>/dev/null || rm -rf "$STATE_DIR" 2>/dev/null || true
    say "Removed state directory ${STATE_DIR}."
  fi
  warn "Data volumes + local images deleted."
  warn "Kept (shared, not NetGeo-only): the Docker engine (docker0) and Tailscale (tailscale0)."
else
  say "Volumes, images and system config kept. Use --purge for a full clean."
fi

say "NetGeo uninstall complete."
