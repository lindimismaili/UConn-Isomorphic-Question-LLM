#!/usr/bin/env bash
set -euo pipefail

# =====================================================
# CONFIG
# =====================================================
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UCONN_SRC="${REPO_DIR}"
UCONN_DST="/var/www/uconn"

UCONN_NGINX_DST="/etc/nginx/sites-available/uconn"
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

ensure_git_pull_deploy_hooks() {
  local src="$1"
  local desired_hooks_path=".githooks"

  if [[ "${DEPLOY_HOOK_RUNNING:-0}" == "1" ]]; then
    echo "---- git hooks bootstrap (skipped: running from hook) ----"
    return
  fi

  echo "---- git hooks bootstrap ----"
  require_dir "$src/.githooks"

  if ! command -v git >/dev/null 2>&1; then
    echo "WARN: git not found; cannot configure hooks path automatically."
    return
  fi

  if ! git -C "$src" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    echo "WARN: $src is not a git work tree; skipping hooks bootstrap."
    return
  fi

  local current_hooks_path
  current_hooks_path="$(git -C "$src" config --local --get core.hooksPath || true)"
  if [[ "$current_hooks_path" != "$desired_hooks_path" ]]; then
    git -C "$src" config --local core.hooksPath "$desired_hooks_path"
    echo "Set git hooksPath -> ${desired_hooks_path}"
  else
    echo "git hooksPath already set -> ${desired_hooks_path}"
  fi

  local hook_files=(
    "$src/.githooks/post-merge"
    "$src/.githooks/post-rewrite"
    "$src/raspi-deploy.sh"
  )

  for hook in "${hook_files[@]}"; do
    if [[ ! -f "$hook" ]]; then
      echo "WARN: Missing hook file: $hook"
      continue
    fi
    # Normalize Windows CRLF to LF only when needed.
    if grep -q $'\r' "$hook"; then
      sed -i 's/\r$//' "$hook"
    fi
    if [[ ! -x "$hook" ]]; then
      chmod +x "$hook"
    fi
  done

  echo "OK: git pull deploy hooks ready."
}

ensure_frontend_dist() {
  local src="$1"

  echo "---- frontend build ----"
  require_dir "$src"
  require_dir "$src/frontend"
  require_file "$src/frontend/package.json"

  if [[ "${SKIP_FRONTEND_BUILD:-0}" == "1" ]]; then
    echo "SKIP_FRONTEND_BUILD=1 set; skipping frontend build."
    require_index "$src/dist"
    echo "OK: using existing $src/dist"
    return
  fi

  if ! command -v npm >/dev/null 2>&1; then
    echo "ERROR: npm is required to build frontend. Install Node.js/npm on this host." >&2
    exit 1
  fi

  if [[ ! -d "$src/node_modules" ]]; then
    echo "Installing Node dependencies at repo root..."
    if [[ -f "$src/package-lock.json" ]]; then
      (cd "$src" && npm ci --no-audit --no-fund)
    else
      (cd "$src" && npm install --no-audit --no-fund)
    fi
  fi

  if [[ ! -x "$src/frontend/node_modules/.bin/vite" ]]; then
    echo "Installing frontend dependencies..."
    if [[ -f "$src/frontend/package-lock.json" ]]; then
      (cd "$src/frontend" && npm ci --no-audit --no-fund)
    else
      (cd "$src/frontend" && npm install --no-audit --no-fund)
    fi
  fi

  echo "Building frontend (outputs to $src/dist)..."
  (cd "$src" && npm run frontend:build)
  require_index "$src/dist"
  echo "OK: frontend build ready at $src/dist"
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
  require_index "$src/dist"

  sudo mkdir -p "$dst"
  sudo rsync -av --delete \
    --exclude ".git/" \
    --exclude ".venv/" \
    --exclude ".env" \
    --exclude "frontend/" \
    "${src}/" "${dst}/"

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

  sudo tee "$UCONN_NGINX_DST" >/dev/null <<'NGINX_EOF'
server {
    listen 80;
    server_name uconn.lindimismaili.com;

    root /var/www/uconn/dist;
    index index.html;

    client_max_body_size 100m;
    client_body_timeout 300s;
    send_timeout 300s;

    location / {
        try_files $uri $uri/ /index.html;
    }

    location = /api {
        return 301 /api/;
    }

    location /api/ {
        proxy_pass http://127.0.0.1:3002;
        proxy_http_version 1.1;

        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        proxy_set_header CF-Connecting-IP $http_cf_connecting_ip;
        proxy_set_header CF-Ray $http_cf_ray;

        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";

        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
        proxy_connect_timeout 60s;
        proxy_buffering off;
        proxy_request_buffering off;
    }
}
NGINX_EOF

  sudo chmod 0644 "$UCONN_NGINX_DST"

  if [[ ! -e "/etc/nginx/sites-enabled/uconn" ]]; then
    sudo ln -s "$UCONN_NGINX_DST" "/etc/nginx/sites-enabled/uconn"
  fi

  sudo nginx -t
  sudo systemctl reload nginx
}

install_and_restart_uconn_service() {
  echo "---- install systemd service: uconn ----"

  sudo tee "$UCONN_SERVICE_DST" >/dev/null <<'SERVICE_EOF'
[Unit]
Description=UConn Isomorphic Question LLM (FastAPI)
After=network.target

[Service]
Type=simple

User=www-data
Group=www-data

WorkingDirectory=/var/www/uconn

Environment=HOST=127.0.0.1
Environment=PORT=3002
EnvironmentFile=-/var/www/uconn/.env

ExecStart=/var/www/uconn/.venv/bin/uvicorn src.server:app --host ${HOST} --port ${PORT}
Restart=on-failure
RestartSec=3

SyslogIdentifier=uconn

NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=true

[Install]
WantedBy=multi-user.target
SERVICE_EOF

  sudo chmod 0644 "$UCONN_SERVICE_DST"
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

ensure_git_pull_deploy_hooks "$UCONN_SRC"
ensure_frontend_dist "$UCONN_SRC"
deploy_static "$UCONN_SRC" "$UCONN_DST"
setup_python_venv_at_destination "$UCONN_DST"
write_version_metadata "$UCONN_SRC" "$UCONN_DST/dist"
install_uconn_nginx_conf
install_and_restart_uconn_service

echo "---- nginx config test ----"
sudo nginx -t

echo "---- reload nginx ----"
sudo systemctl reload nginx

echo "DONE."
echo "Local:      http://localhost"
echo "Service:    sudo systemctl status uconn"
