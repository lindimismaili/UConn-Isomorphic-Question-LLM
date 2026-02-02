#!/usr/bin/env bash
set -euo pipefail

# =====================================================
# CONFIG
# =====================================================

# Main site (index.html is in Site/)
MAIN_REPO="/home/triforcelindim/Scripts/LindimsWebsite"
MAIN_SRC="${MAIN_REPO}/Site"
MAIN_DST="/var/www/lindimismaili"

# UConn site (index.html is in repo root)
UCONN_REPO="/home/triforcelindim/Scripts/UConn-IsomorphicQuestionLLM"
UCONN_SRC="${UCONN_REPO}"
UCONN_DST="/var/www/uconn"

# Optional: pin branches (leave empty to use current)
MAIN_BRANCH=""
UCONN_BRANCH=""

# Deployment assets (nginx + systemd)
UCONN_NGINX_SRC="${UCONN_REPO}/deploy/nginx/uconn.nginx.conf"
UCONN_NGINX_DST="/etc/nginx/sites-available/uconn"

UCONN_SERVICE_SRC="${UCONN_REPO}/deploy/nginx/uconn.service"
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

git_pull_if_repo() {
  local repo="$1"
  local branch="$2"

  if [[ -d "$repo/.git" ]]; then
    echo "---- git pull: $repo ----"
    git -C "$repo" reset --hard
    git -C "$repo" clean -fd

    if [[ -n "$branch" ]]; then
      git -C "$repo" fetch --all --prune
      git -C "$repo" checkout "$branch"
      git -C "$repo" pull --ff-only
    else
      git -C "$repo" pull --ff-only
    fi
  else
    echo "---- skip git pull (not a git repo): $repo ----"
  fi
}

deploy_static() {
  local src="$1"
  local dst="$2"
  local name="$3"

  echo "---- deploy: $name ----"
  require_dir "$src"
  require_index "$src"

  sudo mkdir -p "$dst"
  sudo rsync -av --delete "${src}/" "${dst}/"

  sudo chown -R www-data:www-data "$dst"
  sudo find "$dst" -type d -exec chmod 755 {} \;
  sudo find "$dst" -type f -exec chmod 644 {} \;

  echo "OK: $name deployed to $dst"
}

setup_python_venv_at_destination() {
  local dst="$1"

  if [[ -f "$dst/requirements.txt" ]]; then
    echo "---- python venv at destination: $dst ----"

    # Create venv if it doesn't exist
    if [[ ! -d "$dst/.venv" ]]; then
      sudo python3 -m venv "$dst/.venv"
      sudo chown -R www-data:www-data "$dst/.venv"
    fi

    # Install/update requirements
    sudo "$dst/.venv/bin/pip" install --upgrade pip
    sudo "$dst/.venv/bin/pip" install -r "$dst/requirements.txt"

    echo "OK: venv ready at $dst/.venv"
  else
    echo "---- skip venv (no requirements.txt): $dst ----"
  fi
}

install_uconn_nginx_conf() {
  echo "---- install nginx conf: uconn ----"
  require_file "$UCONN_NGINX_SRC"

  # Copy config into sites-available
  sudo install -m 0644 "$UCONN_NGINX_SRC" "$UCONN_NGINX_DST"

  # (Optional but common) ensure it's enabled in sites-enabled
  if [[ ! -e "/etc/nginx/sites-enabled/uconn" ]]; then
    sudo ln -s "$UCONN_NGINX_DST" "/etc/nginx/sites-enabled/uconn"
  fi

  # Test + reload
  sudo nginx -t
  sudo systemctl reload nginx
}

install_and_restart_uconn_service() {
  echo "---- install systemd service: uconn ----"
  require_file "$UCONN_SERVICE_SRC"

  sudo install -m 0644 "$UCONN_SERVICE_SRC" "$UCONN_SERVICE_DST"
  sudo systemctl daemon-reload

  # If you want it to come up on boot, uncomment:
  # sudo systemctl enable uconn

  sudo systemctl restart uconn
}

# =====================================================
# MAIN
# =====================================================

git_pull_if_repo "$MAIN_REPO" "$MAIN_BRANCH"
git_pull_if_repo "$UCONN_REPO" "$UCONN_BRANCH"

deploy_static "$MAIN_SRC" "$MAIN_DST" "lindimismaili.com"
deploy_static "$UCONN_SRC" "$UCONN_DST" "uconn.lindimismaili.com"

# Create venv at destination (after rsync, since --delete wipes it)
setup_python_venv_at_destination "$UCONN_DST"

# New: nginx + systemd steps you requested
install_uconn_nginx_conf
install_and_restart_uconn_service

echo "---- nginx config test ----"
sudo nginx -t

echo "---- reload nginx ----"
sudo systemctl reload nginx

echo "DONE."
