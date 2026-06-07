#!/usr/bin/env bash
set -euo pipefail

# Install and configure the site on Raspberry Pi OS or Debian-based Linux.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
DEPLOY_CONFIG_FILE="${REPO_DIR}/.deploy.env"

LOCAL_HOSTNAME="${LOCAL_HOSTNAME:-isomorphic}"
LOCAL_DOMAIN="${LOCAL_DOMAIN:-isomorphic.local}"
DEPLOY_DIR="${DEPLOY_DIR:-/var/www/uconn-isomorphic}"
SERVICE_NAME="${SERVICE_NAME:-uconn-isomorphic}"
BACKEND_PORT="${BACKEND_PORT:-3002}"
NGINX_CONF="/etc/nginx/sites-available/${SERVICE_NAME}"
NGINX_LINK="/etc/nginx/sites-enabled/${SERVICE_NAME}"
CLOUDFLARED_SYSTEM_DIR="/etc/cloudflared"
CLOUDFLARED_USER_DIR="${HOME}/.cloudflared"
NODE_LTS_VERSION="22.x"
APT_UPDATED=0

if [[ -f "${DEPLOY_CONFIG_FILE}" ]]; then
  # shellcheck source=/dev/null
  source "${DEPLOY_CONFIG_FILE}"
fi

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

banner() { echo -e "\n${CYAN}========================================${NC}\n${CYAN}  $1${NC}\n${CYAN}========================================${NC}\n"; }
info() { echo -e "${GREEN}[INFO]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
err() { echo -e "${RED}[ERROR]${NC} $1" >&2; }

ask_to_continue() {
  local prompt="$1"

  if [[ ! -t 0 ]]; then
    return 1
  fi

  printf "%s (Enter to continue, any other key to close): " "${prompt}"
  local reply=""
  IFS= read -r -n 1 reply || true
  echo ""
  [[ -z "${reply}" ]]
}

prompt_with_default() {
  local prompt="$1"
  local default_value="$2"
  local value=""

  read -rp "${prompt} [${default_value}]: " value
  printf '%s\n' "${value:-$default_value}"
}

select_platform() {
  echo "What system are you running this script on?"
  echo "1) Mac"
  echo "2) Windows"
  echo "3) Raspberry Pi / Linux"
  echo ""

  local platform_choice=""
  if ! read -rp "Choose 1, 2, or 3: " platform_choice; then
    err "Platform selection is required."
    exit 1
  fi

  case "${platform_choice}" in
    1)
      warn "The full deploy installer uses apt, nginx, systemd, and avahi, so it is only for Raspberry Pi / Linux."
      info "For local Mac preview, run: bash deploy/preview.sh --dev"
      exit 0
      ;;
    2)
      warn "The full deploy installer uses apt, nginx, systemd, and avahi, so it is only for Raspberry Pi / Linux."
      info "For Windows, use WSL/Linux and choose option 3, or run the app manually from your normal dev environment."
      exit 0
      ;;
    3)
      return
      ;;
    *)
      err "Invalid choice. Run again and choose 1, 2, or 3."
      exit 1
      ;;
  esac
}

require_linux() {
  if [[ "$(uname -s)" != "Linux" ]]; then
    err "This installer is intended for Raspberry Pi OS or another Debian-based Linux system."
    err "Detected: $(uname -s)"
    exit 1
  fi
}

require_repo() {
  if [[ ! -f "${REPO_DIR}/backend/requirements.txt" ]] || [[ ! -f "${REPO_DIR}/backend/src/server.py" ]] || [[ ! -f "${REPO_DIR}/frontend/package.json" ]]; then
    err "Cannot find expected repo files under ${REPO_DIR}."
    err "Run this from the cloned UConn-IsomorphicQuestionLLM repository."
    exit 1
  fi
}

require_sudo() {
  if ! command -v sudo >/dev/null 2>&1; then
    err "sudo is required for package installation, nginx, systemd, and Cloudflare service setup."
    exit 1
  fi

  sudo -v
}

apt_update_once() {
  if [[ "${APT_UPDATED}" -eq 0 ]]; then
    info "Updating apt package list..."
    sudo apt-get update
    APT_UPDATED=1
  fi
}

apt_install_if_missing() {
  local package_name="$1"
  local command_name="${2:-$1}"

  if command -v "${command_name}" >/dev/null 2>&1; then
    return
  fi

  apt_update_once
  info "Installing ${package_name}..."
  sudo apt-get install -y "${package_name}"
}

node_version_is_supported() {
  command -v node >/dev/null 2>&1 || return 1

  local version major minor patch
  version="$(node --version 2>/dev/null || true)"
  version="${version#v}"
  IFS='.' read -r major minor patch <<< "${version}"
  patch="${patch%%[^0-9]*}"

  [[ "${major}" =~ ^[0-9]+$ ]] || return 1
  [[ "${minor}" =~ ^[0-9]+$ ]] || minor=0
  [[ "${patch}" =~ ^[0-9]+$ ]] || patch=0

  if (( major > 22 )); then return 0; fi
  if (( major == 22 && minor >= 12 )); then return 0; fi
  if (( major == 20 && minor >= 19 )); then return 0; fi
  return 1
}

install_node_from_nodesource() {
  apt_install_if_missing curl
  apt_install_if_missing ca-certificates update-ca-certificates

  local setup_script
  setup_script="$(mktemp)"
  info "Installing Node.js ${NODE_LTS_VERSION} from NodeSource..."
  curl -fsSL "https://deb.nodesource.com/setup_${NODE_LTS_VERSION}" -o "${setup_script}"
  sudo -E bash "${setup_script}"
  rm -f "${setup_script}"
  sudo apt-get install -y nodejs
  hash -r
}

ensure_node() {
  if node_version_is_supported && command -v npm >/dev/null 2>&1; then
    info "Node.js $(node --version) and npm $(npm --version) are installed."
    return
  fi

  if command -v node >/dev/null 2>&1; then
    warn "Node.js $(node --version) is missing npm or is too old for Vite. Installing Node.js ${NODE_LTS_VERSION}."
  else
    warn "Node.js is not installed. Installing Node.js ${NODE_LTS_VERSION}."
  fi

  install_node_from_nodesource

  if ! node_version_is_supported || ! command -v npm >/dev/null 2>&1; then
    err "Node.js/npm installation did not produce a Vite-compatible Node runtime."
    err "Need Node 20.19+ within 20.x, or Node 22.12+."
    exit 1
  fi

  info "Node.js $(node --version) and npm $(npm --version) are ready."
}

install_system_packages() {
  banner "System Packages"

  apt_install_if_missing git
  apt_install_if_missing rsync
  apt_install_if_missing nginx
  apt_install_if_missing curl
  apt_install_if_missing ca-certificates update-ca-certificates
  apt_install_if_missing avahi-daemon avahi-daemon
  apt_install_if_missing python3
  apt_install_if_missing python3-venv
  apt_install_if_missing python3-pip pip3
  ensure_node

  sudo systemctl enable --now avahi-daemon

  info "Python: $(python3 --version 2>&1)"
  info "Node:   $(node --version 2>&1)"
  info "npm:    $(npm --version 2>&1)"
}

configure_local_domain() {
  banner "Local Address"

  local current_hostname
  current_hostname="$(hostname 2>/dev/null || true)"

  if [[ "${current_hostname}" != "${LOCAL_HOSTNAME}" ]]; then
    info "Setting Raspberry Pi hostname to ${LOCAL_HOSTNAME} so ${LOCAL_DOMAIN} resolves through mDNS."
    if command -v hostnamectl >/dev/null 2>&1; then
      sudo hostnamectl set-hostname "${LOCAL_HOSTNAME}"
    else
      printf '%s\n' "${LOCAL_HOSTNAME}" | sudo tee /etc/hostname >/dev/null
      sudo hostname "${LOCAL_HOSTNAME}"
    fi
  else
    info "Hostname is already ${LOCAL_HOSTNAME}."
  fi

  info "Local site address will be http://${LOCAL_DOMAIN}"
}

sync_repo_to_deploy_dir() {
  banner "Deploy Directory"

  info "Creating ${DEPLOY_DIR} and syncing the repository."
  sudo mkdir -p "${DEPLOY_DIR}/backend/outputs" "${DEPLOY_DIR}/backend/uploads"

  sudo rsync -a --delete \
    --exclude ".git/" \
    --exclude ".venv/" \
    --exclude ".env" \
    --exclude "node_modules/" \
    --exclude "frontend/node_modules/" \
    --exclude "frontend/.vite/" \
    --exclude "backend/outputs/" \
    --exclude "backend/uploads/" \
    --exclude "backend/runtime/" \
    --exclude "__pycache__/" \
    "${REPO_DIR}/" "${DEPLOY_DIR}/"

  if [[ -f "${REPO_DIR}/.env" ]]; then
    sudo install -m 640 -o www-data -g www-data "${REPO_DIR}/.env" "${DEPLOY_DIR}/.env"
  elif [[ ! -f "${DEPLOY_DIR}/.env" ]]; then
    warn "No .env file found. OPENAI_API_KEY is required before workflows can run."
  fi

  sudo chown -R www-data:www-data "${DEPLOY_DIR}"
  info "Repository synced to ${DEPLOY_DIR}."
}

warn_if_openai_key_missing() {
  local env_file="${DEPLOY_DIR}/.env"

  if [[ ! -f "${env_file}" ]]; then
    warn "Missing ${env_file}. Create it with OPENAI_API_KEY=... before running workflows."
    return
  fi

  if ! sudo grep -Eq '^OPENAI_API_KEY=.+$' "${env_file}"; then
    warn "${env_file} exists, but OPENAI_API_KEY is missing or empty."
  fi
}

setup_python_venv() {
  banner "Python Environment"

  local req_file="${DEPLOY_DIR}/backend/requirements.txt"
  local venv_dir="${DEPLOY_DIR}/.venv"
  local pip_bin="${venv_dir}/bin/pip"

  if [[ ! -f "${req_file}" ]]; then
    err "Missing ${req_file}."
    exit 1
  fi

  if [[ ! -d "${venv_dir}" ]]; then
    info "Creating Python virtual environment at ${venv_dir}."
    sudo python3 -m venv "${venv_dir}"
  fi

  if [[ ! -x "${pip_bin}" ]]; then
    err "pip is missing from ${venv_dir}; recreating the virtual environment did not succeed."
    exit 1
  fi

  info "Installing Python packages from backend/requirements.txt."
  sudo "${pip_bin}" install --upgrade pip
  sudo "${pip_bin}" install -r "${req_file}"
  sudo chown -R www-data:www-data "${venv_dir}"
}

build_frontend() {
  banner "Frontend Build"

  local frontend_dir="${DEPLOY_DIR}/frontend"
  local dist_dir="${DEPLOY_DIR}/dist"

  sudo chown -R "$(whoami)" "${frontend_dir}"
  sudo mkdir -p "${dist_dir}"
  sudo chown -R "$(whoami)" "${dist_dir}"

  if [[ -f "${frontend_dir}/package-lock.json" ]]; then
    npm -C "${frontend_dir}" ci --no-audit --no-fund
  else
    npm -C "${frontend_dir}" install --no-audit --no-fund
  fi

  npm -C "${frontend_dir}" run build

  if [[ ! -f "${dist_dir}/index.html" ]]; then
    err "Frontend build failed; ${dist_dir}/index.html was not created."
    exit 1
  fi

  write_version
  sudo chown -R www-data:www-data "${DEPLOY_DIR}"
  info "Frontend built at ${dist_dir}."
}

write_version() {
  local dist_dir="${DEPLOY_DIR}/dist"
  local commit_sha="unknown"
  local commit_short="unknown"
  local commit_time="unknown"
  local published_at

  if command -v git >/dev/null 2>&1 && git -C "${REPO_DIR}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    commit_sha="$(git -C "${REPO_DIR}" rev-parse HEAD 2>/dev/null || echo 'unknown')"
    commit_short="$(git -C "${REPO_DIR}" rev-parse --short HEAD 2>/dev/null || echo 'unknown')"
    commit_time="$(git -C "${REPO_DIR}" log -1 --format=%cI 2>/dev/null || echo 'unknown')"
  fi

  published_at="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

  sudo tee "${dist_dir}/version.json" >/dev/null <<EOF
{
  "version": "${commit_short}",
  "commit": "${commit_sha}",
  "commit_time": "${commit_time}",
  "published_at": "${published_at}"
}
EOF
}

install_backend_service() {
  banner "Backend Service"

  local service_file="/etc/systemd/system/${SERVICE_NAME}.service"

  sudo tee "${service_file}" >/dev/null <<EOF
[Unit]
Description=UConn Isomorphic Question LLM
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=www-data
Group=www-data
WorkingDirectory=${DEPLOY_DIR}
Environment=HOST=127.0.0.1
Environment=PORT=${BACKEND_PORT}
Environment=ALLOWED_ORIGINS=http://${LOCAL_DOMAIN}
EnvironmentFile=-${DEPLOY_DIR}/.env
ExecStart=${DEPLOY_DIR}/.venv/bin/uvicorn backend.src.server:app --host \${HOST} --port \${PORT}
Restart=on-failure
RestartSec=3
SyslogIdentifier=${SERVICE_NAME}
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=true

[Install]
WantedBy=multi-user.target
EOF

  sudo chmod 644 "${service_file}"
  sudo systemctl daemon-reload
  sudo systemctl enable "${SERVICE_NAME}"
  sudo systemctl restart "${SERVICE_NAME}"
  info "Backend service ${SERVICE_NAME} is installed."
}

install_nginx_config() {
  banner "Nginx"

  local server_names="${LOCAL_DOMAIN} ${LOCAL_HOSTNAME}"
  if [[ -n "${PUBLIC_HOSTNAME:-}" ]]; then
    server_names="${server_names} ${PUBLIC_HOSTNAME}"
  fi

  sudo tee "${NGINX_CONF}" >/dev/null <<EOF
server {
    listen 80;
    server_name ${server_names};

    root ${DEPLOY_DIR}/dist;
    index index.html;

    client_max_body_size 100m;
    client_body_timeout 300s;
    send_timeout 300s;

    location / {
        try_files \$uri \$uri/ /index.html;
    }

    location = /api {
        return 301 /api/;
    }

    location /api/ {
        proxy_pass http://127.0.0.1:${BACKEND_PORT};
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
        proxy_connect_timeout 60s;
        proxy_buffering off;
        proxy_request_buffering off;
    }
}
EOF

  sudo chmod 644 "${NGINX_CONF}"
  sudo ln -sf "${NGINX_CONF}" "${NGINX_LINK}"
  sudo rm -f /etc/nginx/sites-enabled/default

  sudo nginx -t
  sudo systemctl enable --now nginx
  sudo systemctl reload nginx
  info "Nginx is serving http://${LOCAL_DOMAIN}."
}

verify_local_site() {
  banner "Local Verification"

  sleep 2

  if ! systemctl is-active --quiet "${SERVICE_NAME}"; then
    err "${SERVICE_NAME} is not running. Check: sudo journalctl -u ${SERVICE_NAME} -n 50"
    exit 1
  fi

  if ! systemctl is-active --quiet nginx; then
    err "nginx is not running. Check: sudo journalctl -u nginx -n 50"
    exit 1
  fi

  if ! curl -fsS "http://127.0.0.1/api/health" >/dev/null; then
    err "Local nginx health check failed at http://127.0.0.1/api/health."
    exit 1
  fi

  if curl -fsS "http://${LOCAL_DOMAIN}/api/health" >/dev/null; then
    info "Site is confirmed to run locally at http://${LOCAL_DOMAIN}"
  else
    warn "The service is healthy through localhost, but ${LOCAL_DOMAIN} did not resolve from this shell yet."
    warn "If the hostname was just changed, reconnect or reboot the Pi, then open http://${LOCAL_DOMAIN}"
  fi
}

save_deploy_config() {
  cat > "${DEPLOY_CONFIG_FILE}" <<EOF
LOCAL_HOSTNAME=${LOCAL_HOSTNAME}
LOCAL_DOMAIN=${LOCAL_DOMAIN}
DEPLOY_DIR=${DEPLOY_DIR}
SERVICE_NAME=${SERVICE_NAME}
BACKEND_PORT=${BACKEND_PORT}
EOF
  if [[ -n "${TUNNEL_NAME:-}" ]]; then
    echo "TUNNEL_NAME=${TUNNEL_NAME}" >> "${DEPLOY_CONFIG_FILE}"
  fi
  if [[ -n "${PUBLIC_HOSTNAME:-}" ]]; then
    echo "PUBLIC_HOSTNAME=${PUBLIC_HOSTNAME}" >> "${DEPLOY_CONFIG_FILE}"
  fi
  if [[ -n "${TUNNEL_UUID:-}" ]]; then
    echo "TUNNEL_UUID=${TUNNEL_UUID}" >> "${DEPLOY_CONFIG_FILE}"
  fi
  chmod 600 "${DEPLOY_CONFIG_FILE}"
}

install_cloudflared() {
  if command -v cloudflared >/dev/null 2>&1; then
    info "cloudflared already installed: $(cloudflared --version 2>&1 | head -1)"
    return
  fi

  local arch url tmp_bin
  arch="$(dpkg --print-architecture 2>/dev/null || uname -m)"
  case "${arch}" in
    amd64|x86_64) arch="amd64" ;;
    arm64|aarch64) arch="arm64" ;;
    armhf|armv7l) arch="arm" ;;
    *)
      err "Unsupported architecture for cloudflared: ${arch}"
      exit 1
      ;;
  esac

  apt_install_if_missing curl
  apt_install_if_missing ca-certificates update-ca-certificates

  info "Installing cloudflared."
  curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg | sudo tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null
  echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared stable main" | sudo tee /etc/apt/sources.list.d/cloudflared.list >/dev/null

  if sudo apt-get update && sudo apt-get install -y cloudflared; then
    return
  fi

  warn "Cloudflare apt install failed; downloading the cloudflared binary directly."
  sudo rm -f /etc/apt/sources.list.d/cloudflared.list
  url="https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${arch}"
  tmp_bin="$(mktemp)"
  curl -fsSL -o "${tmp_bin}" "${url}"
  sudo install -m 755 "${tmp_bin}" /usr/local/bin/cloudflared
  rm -f "${tmp_bin}"
}

ensure_cloudflare_authenticated() {
  if [[ -f "${CLOUDFLARED_USER_DIR}/cert.pem" ]] || [[ -f "${CLOUDFLARED_SYSTEM_DIR}/cert.pem" ]]; then
    info "Cloudflare credentials already exist."
    return
  fi

  warn "Cloudflare login is required. A browser URL will be shown."
  cloudflared tunnel login

  if [[ ! -f "${CLOUDFLARED_USER_DIR}/cert.pem" ]]; then
    err "Cloudflare login did not create ${CLOUDFLARED_USER_DIR}/cert.pem."
    exit 1
  fi
}

find_tunnel_uuid() {
  local tunnel_name="$1"
  local uuid=""

  uuid="$(cloudflared tunnel list -o json 2>/dev/null | python3 -c "
import json, sys
try:
    tunnels = json.load(sys.stdin)
except Exception:
    tunnels = []
for tunnel in tunnels:
    if tunnel.get('name') == '${tunnel_name}' and not tunnel.get('deleted_at'):
        print(tunnel.get('id', ''))
        break
" 2>/dev/null || true)"

  if [[ -z "${uuid}" ]]; then
    uuid="$(cloudflared tunnel list 2>/dev/null | awk -v name="${tunnel_name}" '$2 == name { print $1; exit }' || true)"
  fi

  printf '%s\n' "${uuid}"
}

ensure_cloudflare_tunnel() {
  local tunnel_name="$1"
  local uuid

  uuid="$(find_tunnel_uuid "${tunnel_name}")"

  if [[ -n "${uuid}" ]]; then
    info "Using existing Cloudflare tunnel ${tunnel_name} (${uuid})."
    TUNNEL_UUID="${uuid}"
    return
  fi

  info "Creating Cloudflare tunnel ${tunnel_name}."
  cloudflared tunnel create "${tunnel_name}"
  uuid="$(find_tunnel_uuid "${tunnel_name}")"

  if [[ -z "${uuid}" ]]; then
    err "Could not determine the new Cloudflare tunnel UUID."
    exit 1
  fi

  TUNNEL_UUID="${uuid}"
}

copy_cloudflare_credentials() {
  sudo mkdir -p "${CLOUDFLARED_SYSTEM_DIR}"

  if [[ -f "${CLOUDFLARED_USER_DIR}/${TUNNEL_UUID}.json" ]]; then
    sudo cp "${CLOUDFLARED_USER_DIR}/${TUNNEL_UUID}.json" "${CLOUDFLARED_SYSTEM_DIR}/"
    sudo chown root:root "${CLOUDFLARED_SYSTEM_DIR}/${TUNNEL_UUID}.json"
    sudo chmod 600 "${CLOUDFLARED_SYSTEM_DIR}/${TUNNEL_UUID}.json"
  elif [[ ! -f "${CLOUDFLARED_SYSTEM_DIR}/${TUNNEL_UUID}.json" ]]; then
    err "Missing tunnel credential file for ${TUNNEL_UUID}."
    err "Expected ${CLOUDFLARED_USER_DIR}/${TUNNEL_UUID}.json."
    exit 1
  fi

  if [[ -f "${CLOUDFLARED_USER_DIR}/cert.pem" ]] && [[ ! -f "${CLOUDFLARED_SYSTEM_DIR}/cert.pem" ]]; then
    sudo cp "${CLOUDFLARED_USER_DIR}/cert.pem" "${CLOUDFLARED_SYSTEM_DIR}/cert.pem"
    sudo chown root:root "${CLOUDFLARED_SYSTEM_DIR}/cert.pem"
    sudo chmod 600 "${CLOUDFLARED_SYSTEM_DIR}/cert.pem"
  fi
}

write_cloudflare_config() {
  local tunnel_name="$1"
  local public_hostname="$2"

  sudo tee "${CLOUDFLARED_SYSTEM_DIR}/config.yml" >/dev/null <<EOF
tunnel: ${tunnel_name}
credentials-file: ${CLOUDFLARED_SYSTEM_DIR}/${TUNNEL_UUID}.json

ingress:
  - hostname: ${public_hostname}
    service: http://127.0.0.1:80
  - service: http_status:404
EOF

  sudo chmod 644 "${CLOUDFLARED_SYSTEM_DIR}/config.yml"
}

route_cloudflare_dns() {
  local tunnel_name="$1"
  local public_hostname="$2"

  info "Creating Cloudflare DNS route for ${public_hostname}."
  if cloudflared tunnel route dns "${tunnel_name}" "${public_hostname}"; then
    return
  fi

  warn "DNS route already exists or could not be created normally. Trying overwrite."
  cloudflared tunnel route dns --overwrite-dns "${tunnel_name}" "${public_hostname}"
}

install_cloudflared_service() {
  local cloudflared_bin
  cloudflared_bin="$(command -v cloudflared)"

  sudo tee /etc/systemd/system/cloudflared.service >/dev/null <<EOF
[Unit]
Description=cloudflared tunnel
After=network-online.target
Wants=network-online.target

[Service]
Type=notify
ExecStart=${cloudflared_bin} --no-autoupdate --config /etc/cloudflared/config.yml tunnel run
Restart=on-failure
RestartSec=5s

[Install]
WantedBy=multi-user.target
EOF

  sudo chmod 644 /etc/systemd/system/cloudflared.service
  sudo systemctl daemon-reload
  sudo systemctl enable --now cloudflared
  sudo systemctl restart cloudflared
}

setup_cloudflare() {
  banner "Cloudflare Tunnel"

  local tunnel_name public_hostname
  tunnel_name="$(prompt_with_default "Cloudflare tunnel name" "${TUNNEL_NAME:-uconn_isomorphic}")"
  public_hostname="$(prompt_with_default "Cloudflare hostname to route to this Pi, for example uconn.example.com" "${PUBLIC_HOSTNAME:-}")"

  if [[ -z "${public_hostname}" ]]; then
    err "A public hostname is required for Cloudflare tunnel routing."
    exit 1
  fi

  TUNNEL_NAME="${tunnel_name}"
  PUBLIC_HOSTNAME="${public_hostname}"

  install_cloudflared
  ensure_cloudflare_authenticated
  ensure_cloudflare_tunnel "${TUNNEL_NAME}"
  copy_cloudflare_credentials
  write_cloudflare_config "${TUNNEL_NAME}" "${PUBLIC_HOSTNAME}"
  route_cloudflare_dns "${TUNNEL_NAME}" "${PUBLIC_HOSTNAME}"
  install_nginx_config
  install_cloudflared_service
  save_deploy_config

  info "Cloudflare tunnel is configured for https://${PUBLIC_HOSTNAME}"
}

main() {
  banner "UConn Isomorphic Question LLM Installer"
  select_platform

  echo "This script will install and create everything necessary to run this site on a Raspberry Pi."
  echo "It will install system packages, create ${DEPLOY_DIR}, build the frontend, install the backend service, configure nginx, and set up ${LOCAL_DOMAIN}."
  echo ""

  if ! ask_to_continue "Continue with Raspberry Pi setup?"; then
    info "Closed without making changes."
    exit 0
  fi

  require_linux
  require_repo
  require_sudo
  save_deploy_config
  install_system_packages
  configure_local_domain
  sync_repo_to_deploy_dir
  warn_if_openai_key_missing
  setup_python_venv
  build_frontend
  install_backend_service
  install_nginx_config
  verify_local_site

  echo ""
  if ask_to_continue "Attempt to host the site via Cloudflare?"; then
    setup_cloudflare
  else
    info "Skipped Cloudflare hosting. Local site remains available at http://${LOCAL_DOMAIN}"
  fi

  banner "Complete"
  info "Local site: http://${LOCAL_DOMAIN}"
  info "Backend service: sudo systemctl status ${SERVICE_NAME}"
  info "Logs: sudo journalctl -u ${SERVICE_NAME} -f"
  warn_if_openai_key_missing
}

main "$@"
