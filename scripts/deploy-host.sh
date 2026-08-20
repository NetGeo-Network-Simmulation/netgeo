#!/usr/bin/env bash
# Deploy local main to the live host: bundle -> ff-merge -> rebuild backend -> restart frontend+gateway -> health check.
# Usage: NETGEO_HOST=<host> NETGEO_SSH_USER=<user> NETGEO_SUDO_PW=... scripts/deploy-host.sh
#        (HOST + SSH_USER are required; prompts for the sudo password if unset)
set -euo pipefail

HOST="${NETGEO_HOST:?set NETGEO_HOST to your deploy target (host/IP)}"
SSH_USER="${NETGEO_SSH_USER:?set NETGEO_SSH_USER to the ssh user}"
REPO="${NETGEO_REPO:-$HOME/mini-project/netgeo}"

if [[ -z "${NETGEO_SUDO_PW:-}" ]]; then
  read -rsp "sudo password for ${SSH_USER}@${HOST}: " NETGEO_SUDO_PW; echo
fi

# Reachability is probed on the SSH port, not ICMP: hosts on a tailnet (or
# behind a firewall) routinely drop ping while SSH stays up.
timeout 8 bash -c "cat < /dev/null > /dev/tcp/${HOST}/22" 2>/dev/null \
  || { echo "host $HOST unreachable on ssh/22" >&2; exit 1; }

# No key auth on the host; the account password doubles as the sudo password.
# sshpass -e reads SSHPASS from env so it never appears in argv.
export SSHPASS="$NETGEO_SUDO_PW"
SSH="sshpass -e ssh -o StrictHostKeyChecking=accept-new"
SCP="sshpass -e scp -o StrictHostKeyChecking=accept-new"

BUNDLE="$(mktemp /tmp/netgeo-XXXXXX.bundle)"
trap 'rm -f "$BUNDLE"' EXIT
git -C "$REPO" bundle create "$BUNDLE" main
$SCP -q "$BUNDLE" "${SSH_USER}@${HOST}:/tmp/netgeo.bundle"

# Password rides stdin (never argv/disk). sudo_() feeds it to sudo -S per call.
{
  printf 'PW=%q\n' "$NETGEO_SUDO_PW"
  cat <<'EOF'
set -euo pipefail
sudo_() { printf '%s\n' "$PW" | sudo -S -p '' "$@"; }
cd ~/netgeo
# root watcher leaves root-owned files in .git; dev container rewrites the lockfile
sudo_ chown -R "$USER:$USER" .git
git checkout -- frontend/package-lock.json 2>/dev/null || true
git checkout -q main
git fetch /tmp/netgeo.bundle +main:main-in
git merge --ff-only main-in
rm -f /tmp/netgeo.bundle
sudo_ docker compose -p netgeo-dev -f infra/docker-compose.yml -f infra/docker-compose.lan.yml build backend
sudo_ docker compose -p netgeo-dev -f infra/docker-compose.yml -f infra/docker-compose.lan.yml up -d
# frontend must restart to re-read package.json (vite dev + bind mount); gateway rides along
sudo_ docker compose -p netgeo-dev -f infra/docker-compose.yml -f infra/docker-compose.lan.yml restart frontend gateway
sleep 5
echo "HEAD: $(git log --oneline -1)"
curl -fsS http://127.0.0.1:8090/api/health && echo
EOF
} | $SSH "${SSH_USER}@${HOST}" bash
