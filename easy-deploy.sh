#!/usr/bin/env bash
set -euo pipefail

# =====================================================
# CONFIG
# =====================================================
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_USER="$(id -un)"
SERVICE_GROUP="$(id -gn)"

SERVICE_NAME="uconn-local"
SERVICE_DST="/etc/systemd/system/${SERVICE_NAME}.service"
APP_PORT=8000

# =====================================================
# HELPERS
# =====================================================
require_dir() {
  [[ -d "$1" ]] || { echo "ERROR: Directory not found: $1" >&2; exit 1; }
}

require_file() {
  [[ -f "$1" ]] || { echo "ERROR: File not found: $1" >&2; exit 1; }
}

setup_venv() {
  local repo="$1"

  echo "---- setup python venv in repo ----"
  if [[ ! -d "$repo/.venv" ]]; then
    python3 -m venv "$repo/.venv"
  fi

  "$repo/.venv/bin/pip" install --upgrade pip
  "$repo/.venv/bin/pip" install -r "$repo/requirements.txt"
}

install_service() {
  local repo="$1"
  local tmp_service
  tmp_service="$(mktemp)"

  cat > "$tmp_service" <<EOF
[Unit]
Description=UConn Isomorphic Question LLM (LAN)
After=network.target

[Service]
Type=simple
User=${SERVICE_USER}
Group=${SERVICE_GROUP}
WorkingDirectory=${repo}
Environment=HOST=0.0.0.0
Environment=PORT=${APP_PORT}
EnvironmentFile=-${repo}/.env
ExecStart=${repo}/.venv/bin/uvicorn src.server:app --host \${HOST} --port \${PORT}
Restart=on-failure
RestartSec=3
SyslogIdentifier=${SERVICE_NAME}
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF

  echo "---- install systemd service: ${SERVICE_NAME} ----"
  sudo install -m 0644 "$tmp_service" "$SERVICE_DST"
  rm -f "$tmp_service"

  sudo systemctl daemon-reload
  sudo systemctl enable --now "$SERVICE_NAME"
  sudo systemctl restart "$SERVICE_NAME"
}

open_firewall_if_needed() {
  if command -v ufw >/dev/null 2>&1 && sudo ufw status | grep -q "Status: active"; then
    echo "---- ufw allow ${APP_PORT}/tcp ----"
    sudo ufw allow "${APP_PORT}/tcp"
  fi
}

# =====================================================
# MAIN
# =====================================================
require_dir "$REPO_DIR"
require_file "$REPO_DIR/requirements.txt"
require_file "$REPO_DIR/src/server.py"

setup_venv "$REPO_DIR"
install_service "$REPO_DIR"
open_firewall_if_needed

PI_IP="$(hostname -I | awk '{print $1}')"
echo "DONE."
echo "On Pi:      http://localhost:${APP_PORT}"
echo "On LAN:     http://${PI_IP}:${APP_PORT}"
echo "Service:    sudo systemctl status ${SERVICE_NAME}"
