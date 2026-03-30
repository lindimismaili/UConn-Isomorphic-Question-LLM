#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# deploy.sh — Master orchestrator
# Run this once after cloning the repo on your Raspberry Pi.
# It calls cloudflare.sh, packages.sh, and live.sh in order.
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Deploy target (where the app runs from)
DEPLOY_DIR="/var/www/uconn"

# Tunnel name and hostname
TUNNEL_NAME="pi-tunnel"
HOSTNAME="uconn.lindimismaili.com"

# Backend port
BACKEND_PORT=3002

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

banner() { echo -e "\n${CYAN}========================================${NC}"; echo -e "${CYAN}  $1${NC}"; echo -e "${CYAN}========================================${NC}\n"; }
info()   { echo -e "${GREEN}[INFO]${NC} $1"; }
warn()   { echo -e "${YELLOW}[WARN]${NC} $1"; }
err()    { echo -e "${RED}[ERROR]${NC} $1" >&2; }

# ---------------------------------------------------------------------------
banner "UConn Isomorphic Question Generator — Deploy"
# ---------------------------------------------------------------------------

info "Repo directory:   $REPO_DIR"
info "Deploy target:    $DEPLOY_DIR"
info "Tunnel name:      $TUNNEL_NAME"
info "Public hostname:  $HOSTNAME"
info "Backend port:     $BACKEND_PORT"
echo ""

# Verify we're running on the Pi (or at least Linux)
if [[ "$(uname -s)" != "Linux" ]]; then
  err "This script is designed for Linux (Raspberry Pi OS). Detected: $(uname -s)"
  exit 1
fi

# Verify repo structure
if [[ ! -f "$REPO_DIR/backend/requirements.txt" ]] || [[ ! -f "$REPO_DIR/backend/src/server.py" ]]; then
  err "Cannot find expected repo files. Run this from the deploy/ folder inside the cloned repo."
  exit 1
fi

# Check for .env
if [[ ! -f "$REPO_DIR/.env" ]]; then
  warn "No .env file found in $REPO_DIR"
  echo ""
  read -rp "Enter your OPENAI_API_KEY (or press Enter to skip): " api_key
  if [[ -n "$api_key" ]]; then
    echo "OPENAI_API_KEY=$api_key" > "$REPO_DIR/.env"
    info "Created $REPO_DIR/.env"
  else
    warn "Skipping .env creation. You must create $DEPLOY_DIR/.env with OPENAI_API_KEY before workflows will work."
  fi
fi

# ---------------------------------------------------------------------------
banner "Step 1/4 — Cloudflare Tunnel Setup"
# ---------------------------------------------------------------------------
export DEPLOY_DIR TUNNEL_NAME HOSTNAME BACKEND_PORT REPO_DIR
bash "$SCRIPT_DIR/cloudflare.sh"

# ---------------------------------------------------------------------------
banner "Step 2/4 — System Packages + Python + Node"
# ---------------------------------------------------------------------------
bash "$SCRIPT_DIR/packages.sh"

# ---------------------------------------------------------------------------
banner "Step 3/4 — Go Live"
# ---------------------------------------------------------------------------
bash "$SCRIPT_DIR/live.sh"

# ---------------------------------------------------------------------------
banner "Step 4/4 — Auto-Sync (optional)"
# ---------------------------------------------------------------------------
echo ""
echo "Would you like to enable automatic repo sync?"
echo "This installs a systemd timer that checks for updates every 60 seconds"
echo "and automatically rebuilds + restarts when new commits are found."
echo ""
read -rp "Enable auto-sync? [y/N]: " enable_sync

if [[ "${enable_sync,,}" == "y" ]]; then
  bash "$SCRIPT_DIR/sync.sh" --install-timer
  info "Auto-sync timer installed and started."
else
  info "Skipped. You can run 'bash deploy/sync.sh' manually to update,"
  info "or 'bash deploy/sync.sh --install-timer' later to enable auto-sync."
fi

# ---------------------------------------------------------------------------
banner "Deploy Complete!"
# ---------------------------------------------------------------------------
echo ""
info "Public URL:       https://$HOSTNAME"
info "Local test:       curl -i http://localhost/api/health"
info "Backend service:  sudo systemctl status uconn"
info "Tunnel service:   sudo systemctl status cloudflared"
info "Nginx:            sudo systemctl status nginx"
echo ""
info "To update manually:  bash $SCRIPT_DIR/sync.sh"
info "To restart:          sudo systemctl restart uconn"
echo ""
