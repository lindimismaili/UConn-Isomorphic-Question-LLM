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

hash_file_sha256() {
  local file="$1"

  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$file" | awk '{print $1}'
    return
  fi

  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$file" | awk '{print $1}'
    return
  fi

  if command -v openssl >/dev/null 2>&1; then
    openssl dgst -sha256 "$file" | awk '{print $NF}'
    return
  fi

  echo "ERROR: No SHA-256 tool found (sha256sum/shasum/openssl)." >&2
  exit 1
}

deploy_static() {
  local src="$1"
  local dst="$2"

  echo "---- deploy: uconn ----"
  require_dir "$src"
  require_index "$src"

  sudo mkdir -p "$dst"
  sudo rsync -av --delete --exclude ".git/" --exclude ".venv/" --exclude ".env" "${src}/" "${dst}/"

  sudo chown -R www-data:www-data "$dst"
  sudo find "$dst" -path "$dst/.venv" -prune -o -path "$dst/.env" -prune -o -type d -exec chmod 755 {} \;
  sudo find "$dst" -path "$dst/.venv" -prune -o -path "$dst/.env" -prune -o -type f -exec chmod 644 {} \;
  if [[ -f "$dst/.env" ]]; then
    sudo chown www-data:www-data "$dst/.env"
    sudo chmod 640 "$dst/.env"
  else
    echo "WARN: $dst/.env is missing. API workflows require OPENAI_API_KEY."
  fi

  echo "OK: deployed to $dst"
}

setup_python_venv_at_destination() {
  local dst="$1"

  if [[ -f "$dst/requirements.txt" ]]; then
    echo "---- python venv at destination: $dst ----"

    local req_file="$dst/requirements.txt"
    local venv_dir="$dst/.venv"
    local pip_bin="$venv_dir/bin/pip"
    local hash_file="$venv_dir/.requirements.sha256"
    local current_hash
    local previous_hash=""
    local needs_install=0

    if [[ ! -d "$dst/.venv" ]]; then
      echo "Creating venv (missing)."
      sudo python3 -m venv "$venv_dir"
      needs_install=1
    fi

    if [[ ! -x "$pip_bin" ]]; then
      echo "pip missing in venv; reinstall required."
      needs_install=1
    fi

    current_hash="$(hash_file_sha256 "$req_file")"
    if [[ -f "$hash_file" ]]; then
      previous_hash="$(tr -d '[:space:]' < "$hash_file" || true)"
    fi

    if [[ "${FORCE_PIP_SYNC:-0}" == "1" ]]; then
      echo "FORCE_PIP_SYNC=1 set; forcing dependency sync."
      needs_install=1
    elif [[ "$current_hash" != "$previous_hash" ]]; then
      echo "requirements.txt changed; syncing dependencies."
      needs_install=1
    else
      echo "requirements.txt unchanged; skipping pip install."
    fi

    if [[ "$needs_install" -eq 1 ]]; then
      sudo "$pip_bin" install --upgrade pip
      sudo "$pip_bin" install -r "$req_file"
      printf "%s\n" "$current_hash" | sudo tee "$hash_file" >/dev/null
      sudo chmod 644 "$hash_file"
    fi

    sudo chown -R www-data:www-data "$venv_dir"

    echo "OK: venv ready at $venv_dir"
  else
    echo "---- skip venv (no requirements.txt): $dst ----"
  fi
}

write_version_metadata() {
  local src="$1"
  local dst="$2"

  echo "---- write version metadata ----"
  require_dir "$src"
  require_dir "$dst"

  local commit_sha
  local commit_short
  local commit_time
  local published_at

  commit_sha="$(git -C "$src" rev-parse HEAD)"
  commit_short="$(git -C "$src" rev-parse --short HEAD)"
  commit_time="$(git -C "$src" log -1 --format=%cI)"
  published_at="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

  sudo tee "$dst/version.json" >/dev/null <<EOF
{
  "version": "${commit_short}",
  "commit": "${commit_sha}",
  "commit_time": "${commit_time}",
  "published_at": "${published_at}"
}
EOF

  sudo chown www-data:www-data "$dst/version.json"
  sudo chmod 644 "$dst/version.json"
  echo "OK: wrote $dst/version.json (${commit_short})"
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
write_version_metadata "$UCONN_SRC" "$UCONN_DST"
install_uconn_nginx_conf
install_and_restart_uconn_service

echo "---- nginx config test ----"
sudo nginx -t

echo "---- reload nginx ----"
sudo systemctl reload nginx

echo "DONE."
echo "Local:      http://localhost"
echo "Service:    sudo systemctl status uconn"
