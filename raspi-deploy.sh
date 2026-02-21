#!/usr/bin/env bash
set -euo pipefail

# =====================================================
# CONFIG
# =====================================================
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UCONN_SRC="${REPO_DIR}"
UCONN_DST="/var/www/uconn"

UCONN_NGINX_SRC="${REPO_DIR}/deploy/nginx/uconn.nginx.conf"
UCONN_NGINX_DST="/etc/nginx/sites-available/uconn"

UCONN_SERVICE_SRC="${REPO_DIR}/deploy/nginx/uconn.service"
UCONN_SERVICE_DST="/etc/systemd/system/uconn.service"

# =====================================================
# HELPERS
# =====================================================
require_dir() {
  [[ -d "$1" ]] || { echo "ERROR: Directory not found: $1" >&2; exit 1; }
}

require_file() {
  [[ -f "$1" ]] || { echo "ERROR: File not found: $1" >&2; exit 1; }
}

require_index() {
  [[ -f "$1/index.html" ]] || { echo "ERROR: Missing index.html in: $1" >&2; exit 1; }
}

deploy_static() {
  local src="$1"
  local dst="$2"

  echo "---- deploy: uconn ----"
  require_dir "$src"
  require_index "$src"

  sudo mkdir -p "$dst"
  sudo rsync -av --delete --exclude ".git/" "${src}/" "${dst}/"

  sudo chown -R www-data:www-data "$dst"
  sudo find "$dst" -type d -exec chmod 755 {} \;
  sudo find "$dst" -type f -exec chmod 644 {} \;

  echo "OK: deployed to $dst"
}

setup_python_venv_at_destination() {
  local dst="$1"

  if [[ -f "$dst/requirements.txt" ]]; then
    echo "---- python venv at destination: $dst ----"

    if [[ ! -d "$dst/.venv" ]]; then
      sudo python3 -m venv "$dst/.venv"
    fi

    sudo "$dst/.venv/bin/pip" install --upgrade pip
    sudo "$dst/.venv/bin/pip" install -r "$dst/requirements.txt"
    sudo chown -R www-data:www-data "$dst/.venv"

    echo "OK: venv ready at $dst/.venv"
  else
    echo "---- skip venv (no requirements.txt): $dst ----"
  fi
}

install_uconn_nginx_conf() {
  echo "---- install nginx conf: uconn ----"
  require_file "$UCONN_NGINX_SRC"

  sudo install -m 0644 "$UCONN_NGINX_SRC" "$UCONN_NGINX_DST"

  if [[ ! -e "/etc/nginx/sites-enabled/uconn" ]]; then
    sudo ln -s "$UCONN_NGINX_DST" "/etc/nginx/sites-enabled/uconn"
  fi

  sudo nginx -t
  sudo systemctl reload nginx
}

install_and_restart_uconn_service() {
  echo "---- install systemd service: uconn ----"
  require_file "$UCONN_SERVICE_SRC"

  sudo install -m 0644 "$UCONN_SERVICE_SRC" "$UCONN_SERVICE_DST"
  sudo systemctl daemon-reload
  sudo systemctl enable --now uconn
  sudo systemctl restart uconn
}

# =====================================================
# MAIN
# =====================================================
require_dir "$REPO_DIR"
require_file "$REPO_DIR/requirements.txt"
require_file "$REPO_DIR/src/server.py"

deploy_static "$UCONN_SRC" "$UCONN_DST"
setup_python_venv_at_destination "$UCONN_DST"
install_uconn_nginx_conf
install_and_restart_uconn_service

echo "---- nginx config test ----"
sudo nginx -t

echo "---- reload nginx ----"
sudo systemctl reload nginx

echo "DONE."
echo "Local:      http://localhost"
echo "Service:    sudo systemctl status uconn"
