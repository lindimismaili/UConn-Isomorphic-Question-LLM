#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# live.sh — Start the site (nginx + uvicorn systemd service)
#
# Writes nginx config and systemd unit, then starts/restarts everything.
# Expects env vars (set by deploy.sh, or uses defaults):
#   DEPLOY_DIR    — deploy target, e.g. /var/www/uconn
#   HOSTNAME      — public hostname, e.g. uconn.example.com
#   BACKEND_PORT  — 3002
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="${REPO_DIR:-$(cd "${SCRIPT_DIR}/.." && pwd)}"
DEPLOY_CONFIG_FILE="$REPO_DIR/.deploy.env"

if [[ -f "$DEPLOY_CONFIG_FILE" ]]; then
  # shellcheck source=/dev/null
  source "$DEPLOY_CONFIG_FILE"
fi

DEPLOY_DIR="${DEPLOY_DIR:-/var/www/uconn}"
HOSTNAME="${HOSTNAME:-uconn.lindimismaili.com}"
BACKEND_PORT="${BACKEND_PORT:-3002}"

NGINX_CONF="/etc/nginx/sites-available/uconn"
SYSTEMD_SERVICE="/etc/systemd/system/uconn.service"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

info() { echo -e "${GREEN}[live]${NC} $1"; }
warn() { echo -e "${YELLOW}[live]${NC} $1"; }
err()  { echo -e "${RED}[live]${NC} $1" >&2; }

# -------------------------------------------------------------------------
# 1. Verify deploy directory is ready
# -------------------------------------------------------------------------
preflight_check() {
  if [[ ! -d "$DEPLOY_DIR" ]]; then
    err "Deploy directory not found: $DEPLOY_DIR"
    err "Run deploy.sh or cloudflare.sh first."
    exit 1
  fi

  if [[ ! -f "$DEPLOY_DIR/dist/index.html" ]]; then
    warn "No dist/index.html found — frontend may not have been built."
    warn "Run packages.sh to build the frontend."
  fi

  if [[ ! -d "$DEPLOY_DIR/.venv" ]]; then
    warn "No .venv found — Python dependencies may not be installed."
    warn "Run packages.sh first."
  fi

  if [[ ! -f "$DEPLOY_DIR/.env" ]]; then
    warn "No .env found at $DEPLOY_DIR/.env — OPENAI_API_KEY is required for workflows."
  fi
}

# -------------------------------------------------------------------------
# 2. Write nginx config
# -------------------------------------------------------------------------
install_nginx_config() {
  info "Writing nginx config for $HOSTNAME..."

  sudo tee "$NGINX_CONF" >/dev/null <<EOF
server {
    listen 80;
    server_name ${HOSTNAME};

    root ${DEPLOY_DIR}/dist;
    index index.html;

    client_max_body_size 100m;
    client_body_timeout 300s;
    send_timeout 300s;

    # Frontend — SPA with history fallback
    location / {
        try_files \$uri \$uri/ /index.html;
    }

    # Redirect bare /api to /api/
    location = /api {
        return 301 /api/;
    }

    # Backend API — reverse proxy to uvicorn
    location /api/ {
        proxy_pass http://127.0.0.1:${BACKEND_PORT};
        proxy_http_version 1.1;

        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;

        # Cloudflare headers
        proxy_set_header CF-Connecting-IP \$http_cf_connecting_ip;
        proxy_set_header CF-Ray \$http_cf_ray;

        # WebSocket support
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";

        # Long-running requests (AI workflows)
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
        proxy_connect_timeout 60s;
        proxy_buffering off;
        proxy_request_buffering off;
    }
}
EOF

  sudo chmod 644 "$NGINX_CONF"

  # Enable the site
  if [[ ! -e "/etc/nginx/sites-enabled/uconn" ]]; then
    sudo ln -sf "$NGINX_CONF" "/etc/nginx/sites-enabled/uconn"
  fi

  # Remove default site if present
  if [[ -e "/etc/nginx/sites-enabled/default" ]]; then
    sudo rm -f "/etc/nginx/sites-enabled/default"
    info "Removed default nginx site."
  fi

  # Test config
  if sudo nginx -t 2>&1; then
    info "Nginx config OK."
  else
    err "Nginx config test failed!"
    exit 1
  fi

  # Enable and reload nginx
  sudo systemctl enable --now nginx
  sudo systemctl reload nginx
  info "Nginx reloaded."
}

# -------------------------------------------------------------------------
# 3. Write systemd service for uvicorn backend
# -------------------------------------------------------------------------
install_uconn_service() {
  info "Writing systemd service for UConn backend..."

  sudo tee "$SYSTEMD_SERVICE" >/dev/null <<EOF
[Unit]
Description=UConn Isomorphic Question LLM (FastAPI)
After=network.target

[Service]
Type=simple

User=www-data
Group=www-data

WorkingDirectory=${DEPLOY_DIR}

Environment=HOST=127.0.0.1
Environment=PORT=${BACKEND_PORT}
EnvironmentFile=-${DEPLOY_DIR}/.env

ExecStart=${DEPLOY_DIR}/.venv/bin/uvicorn backend.src.server:app --host \${HOST} --port \${PORT}
Restart=on-failure
RestartSec=3

SyslogIdentifier=uconn

NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=true

[Install]
WantedBy=multi-user.target
EOF

  sudo chmod 644 "$SYSTEMD_SERVICE"
  sudo systemctl daemon-reload
  sudo systemctl enable uconn

  if systemctl is-active --quiet uconn; then
    sudo systemctl restart uconn
    info "UConn backend service restarted."
  else
    sudo systemctl start uconn
    info "UConn backend service started."
  fi
}

# -------------------------------------------------------------------------
# 4. Verify services are running
# -------------------------------------------------------------------------
verify_services() {
  echo ""
  info "Verifying services..."

  local all_ok=1

  if systemctl is-active --quiet nginx; then
    info "  nginx:       RUNNING"
  else
    warn "  nginx:       NOT RUNNING"
    all_ok=0
  fi

  if systemctl is-active --quiet uconn; then
    info "  uconn:       RUNNING"
  else
    warn "  uconn:       NOT RUNNING (check: sudo journalctl -u uconn -n 20)"
    all_ok=0
  fi

  if systemctl is-active --quiet cloudflared; then
    info "  cloudflared: RUNNING"
  else
    warn "  cloudflared: NOT RUNNING (tunnel may not be set up)"
  fi

  echo ""

  if [[ "$all_ok" -eq 1 ]]; then
    info "Site is live!"
    info "  Local:  http://localhost/api/health"
    info "  Public: https://$HOSTNAME"
  else
    warn "Some services are not running. Check logs with:"
    warn "  sudo journalctl -u uconn -n 30"
    warn "  sudo journalctl -u nginx -n 30"
  fi
}

# =========================================================================
# MAIN
# =========================================================================

preflight_check
install_nginx_config
install_uconn_service
verify_services
