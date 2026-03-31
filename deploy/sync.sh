#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# sync.sh — Pull latest repo, rebuild, and restart
#
# Usage:
#   bash deploy/sync.sh                 # One-time sync
#   bash deploy/sync.sh --install-timer # Install systemd timer for auto-sync
#   bash deploy/sync.sh --remove-timer  # Remove the auto-sync timer
#
# The sync does a git pull on the repo, rebuilds the frontend, rsyncs to
# /var/www/uconn (preserving .env, outputs, uploads, .venv), and restarts
# the backend service. It does NOT delete local-only data.
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
DEPLOY_DIR="${DEPLOY_DIR:-/var/www/uconn}"
BRANCH="${GIT_BRANCH:-main}"

TIMER_SERVICE="uconn-sync"
TIMER_INTERVAL="60"  # seconds

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

info() { echo -e "${GREEN}[sync]${NC} $1"; }
warn() { echo -e "${YELLOW}[sync]${NC} $1"; }
err()  { echo -e "${RED}[sync]${NC} $1" >&2; }

# =========================================================================
# --install-timer: systemd timer for auto-sync every 60s
# =========================================================================
install_timer() {
  info "Installing auto-sync systemd timer (every ${TIMER_INTERVAL}s)..."

  # Service that runs the sync
  sudo tee "/etc/systemd/system/${TIMER_SERVICE}.service" >/dev/null <<EOF
[Unit]
Description=UConn repo auto-sync
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/bin/bash ${SCRIPT_DIR}/sync.sh
StandardOutput=journal
StandardError=journal
SyslogIdentifier=${TIMER_SERVICE}

# Run as the repo owner so git pull works
User=$(stat -c '%U' "$REPO_DIR")
Group=$(stat -c '%G' "$REPO_DIR")

Environment=DEPLOY_DIR=${DEPLOY_DIR}
EOF

  # Timer that triggers the service
  sudo tee "/etc/systemd/system/${TIMER_SERVICE}.timer" >/dev/null <<EOF
[Unit]
Description=UConn repo auto-sync timer (every ${TIMER_INTERVAL}s)

[Timer]
OnBootSec=30
OnUnitActiveSec=${TIMER_INTERVAL}s
AccuracySec=5s

[Install]
WantedBy=timers.target
EOF

  sudo systemctl daemon-reload
  sudo systemctl enable --now "${TIMER_SERVICE}.timer"

  info "Auto-sync timer installed and started."
  info "Check status:  sudo systemctl status ${TIMER_SERVICE}.timer"
  info "View logs:     sudo journalctl -u ${TIMER_SERVICE} -f"
  exit 0
}

# =========================================================================
# --remove-timer: remove the auto-sync timer
# =========================================================================
remove_timer() {
  info "Removing auto-sync timer..."

  if systemctl is-active --quiet "${TIMER_SERVICE}.timer" 2>/dev/null; then
    sudo systemctl stop "${TIMER_SERVICE}.timer"
  fi
  if systemctl is-enabled --quiet "${TIMER_SERVICE}.timer" 2>/dev/null; then
    sudo systemctl disable "${TIMER_SERVICE}.timer"
  fi

  sudo rm -f "/etc/systemd/system/${TIMER_SERVICE}.service"
  sudo rm -f "/etc/systemd/system/${TIMER_SERVICE}.timer"
  sudo systemctl daemon-reload

  info "Auto-sync timer removed."
  exit 0
}

# =========================================================================
# Parse arguments
# =========================================================================
for arg in "$@"; do
  case "$arg" in
    --install-timer) install_timer ;;
    --remove-timer)  remove_timer ;;
    --help|-h)
      echo "Usage: bash sync.sh [--install-timer | --remove-timer]"
      echo ""
      echo "  (no args)        One-time sync: pull, build, deploy, restart"
      echo "  --install-timer  Install systemd timer for auto-sync every 60s"
      echo "  --remove-timer   Remove the auto-sync timer"
      exit 0
      ;;
  esac
done

# =========================================================================
# One-time sync
# =========================================================================

# Verify repo
if [[ ! -d "$REPO_DIR/.git" ]]; then
  err "Not a git repository: $REPO_DIR"
  exit 1
fi

cd "$REPO_DIR"

# -------------------------------------------------------------------------
# 1. Fetch and check for updates
# -------------------------------------------------------------------------
info "Fetching latest from origin/$BRANCH..."
git fetch origin "$BRANCH" --quiet

LOCAL_SHA="$(git rev-parse HEAD)"
REMOTE_SHA="$(git rev-parse "origin/$BRANCH")"

if [[ "$LOCAL_SHA" == "$REMOTE_SHA" ]]; then
  info "Already up to date ($LOCAL_SHA)."
  # Even if up to date, still check if deploy dir is synced
  if [[ -f "$DEPLOY_DIR/dist/index.html" ]] && systemctl is-active --quiet uconn 2>/dev/null; then
    info "Deploy is current. Nothing to do."
    exit 0
  fi
  info "Deploy dir may need refresh — continuing..."
fi

# -------------------------------------------------------------------------
# 2. Pull latest (fast-forward only, safe for clean deploys)
# -------------------------------------------------------------------------
info "Pulling latest commits..."
git pull --ff-only origin "$BRANCH"

NEW_SHA="$(git rev-parse --short HEAD)"
info "Now at commit: $NEW_SHA"

# -------------------------------------------------------------------------
# 3. Rsync to deploy dir (preserve local-only data)
# -------------------------------------------------------------------------
if [[ -d "$DEPLOY_DIR" ]]; then
  info "Syncing to $DEPLOY_DIR (preserving local data)..."

  sudo rsync -av --delete \
    --exclude ".git/" \
    --exclude ".venv/" \
    --exclude ".env" \
    --exclude "node_modules/" \
    --exclude "frontend/node_modules/" \
    --exclude "frontend/.vite/" \
    --exclude "backend/outputs/" \
    --exclude "backend/uploads/" \
    --exclude "__pycache__/" \
    "${REPO_DIR}/" "${DEPLOY_DIR}/"

  sudo chown -R www-data:www-data "$DEPLOY_DIR"

  # Sync .env from repo if destination doesn't have one
  if [[ -f "$REPO_DIR/.env" ]] && [[ ! -f "$DEPLOY_DIR/.env" ]]; then
    sudo install -m 640 -o www-data -g www-data "$REPO_DIR/.env" "$DEPLOY_DIR/.env"
    info "Copied .env to deploy dir."
  fi
else
  warn "$DEPLOY_DIR doesn't exist. Run deploy.sh first for initial setup."
  exit 1
fi

# -------------------------------------------------------------------------
# 4. Rebuild frontend + reinstall Python deps if needed
# -------------------------------------------------------------------------
export DEPLOY_DIR
export REPO_DIR
bash "$SCRIPT_DIR/packages.sh"

# -------------------------------------------------------------------------
# 5. Restart the backend service
# -------------------------------------------------------------------------
if systemctl is-enabled --quiet uconn 2>/dev/null; then
  info "Restarting UConn backend service..."
  sudo systemctl restart uconn

  # Wait a moment and check
  sleep 2
  if systemctl is-active --quiet uconn; then
    info "Backend service restarted successfully."
  else
    warn "Backend service may have failed. Check: sudo journalctl -u uconn -n 20"
  fi
else
  warn "uconn.service not installed. Run live.sh to set it up."
fi

# -------------------------------------------------------------------------
# 6. Reload nginx (in case config changed)
# -------------------------------------------------------------------------
if systemctl is-active --quiet nginx 2>/dev/null; then
  if sudo nginx -t 2>&1; then
    sudo systemctl reload nginx
    info "Nginx reloaded."
  else
    warn "Nginx config test failed — skipping reload."
  fi
fi

echo ""
info "Sync complete! ($NEW_SHA)"
info "Site: https://${HOSTNAME:-uconn.lindimismaili.com}"
