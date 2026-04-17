#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# packages.sh — Install system packages, Python venv, Node deps, build frontend
#
# Runs against the deploy directory (/var/www/uconn by default).
# Expects env var:
#   DEPLOY_DIR — deploy target, e.g. /var/www/uconn
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="${REPO_DIR:-$(cd "${SCRIPT_DIR}/.." && pwd)}"
DEPLOY_CONFIG_FILE="$REPO_DIR/.deploy.env"

if [[ -f "$DEPLOY_CONFIG_FILE" ]]; then
  # shellcheck source=/dev/null
  source "$DEPLOY_CONFIG_FILE"
fi

DEPLOY_DIR="${DEPLOY_DIR:-/var/www/uconn}"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

info() { echo -e "${GREEN}[packages]${NC} $1"; }
warn() { echo -e "${YELLOW}[packages]${NC} $1"; }
err()  { echo -e "${RED}[packages]${NC} $1" >&2; }

APT_UPDATED=0
NODE_MIN_VERSION="20.19.0"
NODE_LTS_VERSION="22.x"

apt_install_if_missing() {
  local package_name="$1"
  local command_name="${2:-$1}"

  if command -v "$command_name" >/dev/null 2>&1; then
    return
  fi

  if [[ "$APT_UPDATED" -eq 0 ]]; then
    info "Updating apt package list..."
    sudo apt-get update -qq
    APT_UPDATED=1
  fi

  info "Installing $package_name..."
  sudo apt-get install -y -qq "$package_name"
}

node_version_is_supported() {
  if ! command -v node >/dev/null 2>&1; then
    return 1
  fi

  local version
  local major
  local minor
  local patch

  version="$(node --version 2>/dev/null || true)"
  version="${version#v}"
  IFS='.' read -r major minor patch <<< "$version"
  patch="${patch%%[^0-9]*}"

  [[ "$major" =~ ^[0-9]+$ ]] || return 1
  [[ "$minor" =~ ^[0-9]+$ ]] || minor=0
  [[ "$patch" =~ ^[0-9]+$ ]] || patch=0

  if (( major > 22 )); then
    return 0
  fi

  if (( major == 22 && minor >= 12 )); then
    return 0
  fi

  if (( major == 20 && minor >= 19 )); then
    return 0
  fi

  return 1
}

install_node_from_nodesource() {
  local setup_script

  apt_install_if_missing curl
  apt_install_if_missing ca-certificates update-ca-certificates

  setup_script="$(mktemp)"
  info "Installing Node.js ${NODE_LTS_VERSION} from NodeSource..."
  curl -fsSL "https://deb.nodesource.com/setup_${NODE_LTS_VERSION}" -o "$setup_script"
  sudo -E bash "$setup_script"
  rm -f "$setup_script"

  sudo apt-get install -y -qq nodejs
  hash -r
}

ensure_supported_node() {
  if node_version_is_supported; then
    info "Node.js $(node --version 2>&1) satisfies Vite requirements."
  else
    if command -v node >/dev/null 2>&1; then
      warn "Node.js $(node --version 2>&1) is too old for Vite. Installing Node.js ${NODE_LTS_VERSION}..."
    else
      info "Node.js is not installed. Installing Node.js ${NODE_LTS_VERSION}..."
    fi
    install_node_from_nodesource
  fi

  if ! node_version_is_supported; then
    err "Node.js $(node --version 2>&1 || echo 'missing') does not satisfy Vite. Need Node ${NODE_MIN_VERSION}+ within 20.x, or Node 22.12.0+."
    exit 1
  fi

  if ! command -v npm >/dev/null 2>&1; then
    warn "npm was not found. Reinstalling Node.js ${NODE_LTS_VERSION} from NodeSource..."
    install_node_from_nodesource
  fi

  if ! command -v npm >/dev/null 2>&1; then
    err "npm was not found after installing Node.js. Re-run deploy/packages.sh after checking the NodeSource install logs."
    exit 1
  fi
}

# -------------------------------------------------------------------------
# 1. System packages
# -------------------------------------------------------------------------
install_system_packages() {
  info "Checking system packages..."

  apt_install_if_missing git
  apt_install_if_missing rsync
  apt_install_if_missing nginx
  apt_install_if_missing python3
  apt_install_if_missing python3-venv
  apt_install_if_missing python3-pip pip3
  apt_install_if_missing curl
  ensure_supported_node

  info "System packages OK."
  info "  python3: $(python3 --version 2>&1)"
  info "  node:    $(node --version 2>&1)"
  info "  npm:     $(npm --version 2>&1)"
  info "  nginx:   $(nginx -v 2>&1 || true)"
}

# -------------------------------------------------------------------------
# 2. Python virtual environment + dependencies
# -------------------------------------------------------------------------
setup_python_venv() {
  local req_file="$DEPLOY_DIR/backend/requirements.txt"
  local venv_dir="$DEPLOY_DIR/.venv"
  local pip_bin="$venv_dir/bin/pip"
  local hash_file="$venv_dir/.requirements.sha256"

  if [[ ! -f "$req_file" ]]; then
    warn "No backend/requirements.txt found in $DEPLOY_DIR — skipping venv."
    return
  fi

  info "Setting up Python virtual environment..."

  local needs_install=0

  # Create venv if missing
  if [[ ! -d "$venv_dir" ]]; then
    info "Creating venv at $venv_dir..."
    sudo python3 -m venv "$venv_dir"
    needs_install=1
  fi

  # Ensure pip exists
  if [[ ! -x "$pip_bin" ]]; then
    warn "pip missing in venv — recreating..."
    sudo python3 -m venv "$venv_dir"
    needs_install=1
  fi

  # Check if requirements.txt changed
  local current_hash=""
  local previous_hash=""

  if command -v sha256sum >/dev/null 2>&1; then
    current_hash="$(sha256sum "$req_file" | awk '{print $1}')"
  elif command -v shasum >/dev/null 2>&1; then
    current_hash="$(shasum -a 256 "$req_file" | awk '{print $1}')"
  else
    current_hash="force"
  fi

  if [[ -f "$hash_file" ]]; then
    previous_hash="$(tr -d '[:space:]' < "$hash_file" || true)"
  fi

  if [[ "${FORCE_PIP_SYNC:-0}" == "1" ]]; then
    info "FORCE_PIP_SYNC=1 — forcing pip install."
    needs_install=1
  elif [[ "$current_hash" != "$previous_hash" ]]; then
    info "requirements.txt changed — syncing dependencies."
    needs_install=1
  fi

  if [[ "$needs_install" -eq 1 ]]; then
    sudo "$pip_bin" install --upgrade pip
    sudo "$pip_bin" install -r "$req_file"
    printf "%s\n" "$current_hash" | sudo tee "$hash_file" >/dev/null
    sudo chmod 644 "$hash_file"
    info "Python dependencies installed."
  else
    info "Python dependencies up to date (requirements.txt unchanged)."
  fi

  sudo chown -R www-data:www-data "$venv_dir"
  info "Python venv ready at $venv_dir"
}

# -------------------------------------------------------------------------
# 3. Node.js dependencies + frontend build
# -------------------------------------------------------------------------
build_frontend() {
  local frontend_dir="$DEPLOY_DIR/frontend"
  local dist_dir="$DEPLOY_DIR/dist"

  if [[ ! -f "$frontend_dir/package.json" ]]; then
    warn "No frontend/package.json found — skipping frontend build."
    return
  fi

  info "Installing frontend Node.js dependencies..."

  # npm + vite need write access to frontend/ and dist/ (at repo root)
  sudo chown -R "$(whoami)" "$frontend_dir"
  sudo mkdir -p "$dist_dir"
  sudo chown -R "$(whoami)" "$dist_dir"

  if [[ -f "$frontend_dir/package-lock.json" ]]; then
    (cd "$frontend_dir" && npm ci --no-audit --no-fund)
  else
    (cd "$frontend_dir" && npm install --no-audit --no-fund)
  fi

  info "Building frontend..."
  (cd "$frontend_dir" && npm run build)

  # Verify build output
  if [[ ! -f "$dist_dir/index.html" ]]; then
    err "Frontend build failed — no index.html in $dist_dir"
    exit 1
  fi

  # Restore ownership
  sudo chown -R www-data:www-data "$DEPLOY_DIR"

  info "Frontend built at $dist_dir"
}

# -------------------------------------------------------------------------
# 4. Write version metadata
# -------------------------------------------------------------------------
write_version() {
  local dist_dir="$DEPLOY_DIR/dist"

  if [[ ! -d "$dist_dir" ]]; then
    return
  fi

  # Try to get git info from the repo source (not deploy dir)
  local repo_dir="${REPO_DIR:-$DEPLOY_DIR}"
  local commit_sha="unknown"
  local commit_short="unknown"
  local commit_time="unknown"

  if command -v git >/dev/null 2>&1 && git -C "$repo_dir" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    commit_sha="$(git -C "$repo_dir" rev-parse HEAD 2>/dev/null || echo 'unknown')"
    commit_short="$(git -C "$repo_dir" rev-parse --short HEAD 2>/dev/null || echo 'unknown')"
    commit_time="$(git -C "$repo_dir" log -1 --format=%cI 2>/dev/null || echo 'unknown')"
  fi

  local published_at
  published_at="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

  sudo tee "$dist_dir/version.json" >/dev/null <<EOF
{
  "version": "${commit_short}",
  "commit": "${commit_sha}",
  "commit_time": "${commit_time}",
  "published_at": "${published_at}"
}
EOF

  sudo chown www-data:www-data "$dist_dir/version.json"
  sudo chmod 644 "$dist_dir/version.json"
  info "Version metadata written ($commit_short)"
}

# =========================================================================
# MAIN
# =========================================================================

if [[ ! -d "$DEPLOY_DIR" ]]; then
  err "Deploy directory not found: $DEPLOY_DIR"
  err "Run cloudflare.sh first to sync the repo."
  exit 1
fi

install_system_packages
setup_python_venv
build_frontend
write_version

echo ""
info "All packages installed and frontend built."
