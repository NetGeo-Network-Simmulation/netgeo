#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# NetGeo one-command bootstrap.
#
# Safe to pipe straight from the web — it clones the repo (or updates an existing
# checkout), then hands off to ./install.sh. install.sh itself can't be piped to
# bash because it needs the repo's files (infra/ compose, backend/, frontend/).
#
#   curl -fsSL https://raw.githubusercontent.com/NetGeo-Network-Simmulation/netgeo/main/bootstrap.sh | bash
#   curl -fsSL https://raw.githubusercontent.com/NetGeo-Network-Simmulation/netgeo/main/bootstrap.sh | bash -s -- --prod
#
# Env:
#   NETGEO_DIR     where to clone (default: $HOME/netgeo)
#   NETGEO_BRANCH  branch/tag to check out (default: main)
#   NETGEO_REPO_URL  override the clone URL
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

REPO_URL="${NETGEO_REPO_URL:-https://github.com/NetGeo-Network-Simmulation/netgeo.git}"
BRANCH="${NETGEO_BRANCH:-main}"
TARGET_DIR="${NETGEO_DIR:-$HOME/netgeo}"

BLUE='\033[0;34m'; GREEN='\033[0;32m'; RED='\033[0;31m'; NC='\033[0m'
info() { printf "${BLUE}▸${NC} %s\n" "$*"; }
ok()   { printf "${GREEN}✓${NC} %s\n" "$*"; }
err()  { printf "${RED}✗${NC} %s\n" "$*" >&2; }

if ! command -v git >/dev/null 2>&1; then
  err "git is required but not installed."
  err "Install it first, e.g.:  sudo dnf install -y git   |   sudo apt-get install -y git"
  exit 1
fi

if [ -d "$TARGET_DIR/.git" ]; then
  info "Existing checkout found at ${TARGET_DIR} — updating…"
  git -C "$TARGET_DIR" fetch --quiet --tags origin
  git -C "$TARGET_DIR" checkout --quiet "$BRANCH"
  git -C "$TARGET_DIR" pull --ff-only --quiet origin "$BRANCH" || true
else
  info "Cloning ${REPO_URL} → ${TARGET_DIR}…"
  git clone --quiet --branch "$BRANCH" "$REPO_URL" "$TARGET_DIR"
fi
ok "Source ready at ${TARGET_DIR}"

cd "$TARGET_DIR"
[ -f install.sh ] || { err "install.sh not found in ${TARGET_DIR}."; exit 1; }
info "Launching installer: ./install.sh $*"
exec bash ./install.sh "$@"
