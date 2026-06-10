#!/usr/bin/env bash
set -euo pipefail

# Install and configure the site on Raspberry Pi OS or Debian-based Linux.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
DEPLOY_CONFIG_FILE="${REPO_DIR}/.deploy.env"

DEPLOY_DIR="${DEPLOY_DIR:-/var/www/uconn-isomorphic}"
SERVICE_NAME="${SERVICE_NAME:-uconn-isomorphic}"
BACKEND_PORT="${BACKEND_PORT:-3002}"
CLOUDFLARED_SYSTEM_DIR="/etc/cloudflared"
CLOUDFLARED_USER_DIR="${HOME}/.cloudflared"
NODE_LTS_VERSION="22.x"
APT_UPDATED=0

if [[ -f "${DEPLOY_CONFIG_FILE}" ]]; then
  # shellcheck source=/dev/null
  source "${DEPLOY_CONFIG_FILE}"
fi

NGINX_CONF="/etc/nginx/sites-available/${SERVICE_NAME}"
NGINX_LINK="/etc/nginx/sites-enabled/${SERVICE_NAME}"
CLOUDFLARED_SERVICE_NAME="${CLOUDFLARED_SERVICE_NAME:-${SERVICE_NAME}-cloudflared}"
CLOUDFLARED_CONFIG_FILE="${CLOUDFLARED_CONFIG_FILE:-${CLOUDFLARED_SYSTEM_DIR}/${SERVICE_NAME}.yml}"

DEVICE_HOSTNAME="$(hostname -s 2>/dev/null || hostname 2>/dev/null || true)"
DEVICE_HOSTNAME="${DEVICE_HOSTNAME%%.*}"
if [[ -z "${DEVICE_HOSTNAME}" ]]; then
  DEVICE_HOSTNAME="raspberrypi"
fi
LOCAL_HOSTNAME="${DEVICE_HOSTNAME}"
LOCAL_DOMAIN="${LOCAL_HOSTNAME}.local"

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
  value="$(trim_value "${value}")"
  printf '%s\n' "${value:-$default_value}"
}

trim_value() {
  local value="$1"

  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  printf '%s\n' "${value}"
}

is_valid_port() {
  [[ "$1" =~ ^[0-9]+$ ]] && (( $1 >= 1 && $1 <= 65535 ))
}

is_valid_service_name() {
  [[ "$1" =~ ^[A-Za-z0-9_.@-]+$ ]]
}

is_valid_short_hostname() {
  [[ "$1" =~ ^[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?$ ]]
}

normalize_hostname_input() {
  local hostname="$1"

  hostname="${hostname#http://}"
  hostname="${hostname#https://}"
  hostname="${hostname%%/*}"
  hostname="${hostname%%:*}"
  hostname="${hostname%.}"
  printf '%s\n' "${hostname}"
}

is_valid_dns_hostname() {
  local hostname="$1"
  local label
  local labels=()

  [[ -n "${hostname}" ]] || return 1
  [[ "${hostname}" != *" "* ]] || return 1
  [[ "${hostname}" == *.* ]] || return 1
  [[ "${#hostname}" -le 253 ]] || return 1

  IFS='.' read -ra labels <<< "${hostname}"
  for label in "${labels[@]}"; do
    [[ "${label}" =~ ^[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?$ ]] || return 1
  done
}

validate_deploy_settings() {
  if ! is_valid_port "${BACKEND_PORT}"; then
    err "BACKEND_PORT must be an integer from 1 to 65535; got ${BACKEND_PORT}."
    exit 1
  fi

  if ! is_valid_service_name "${SERVICE_NAME}"; then
    err "SERVICE_NAME contains unsafe characters: ${SERVICE_NAME}"
    exit 1
  fi

  if ! is_valid_service_name "${CLOUDFLARED_SERVICE_NAME}"; then
    err "CLOUDFLARED_SERVICE_NAME contains unsafe characters: ${CLOUDFLARED_SERVICE_NAME}"
    exit 1
  fi

  if ! is_valid_short_hostname "${LOCAL_HOSTNAME}"; then
    err "Device hostname is not safe for local DNS/server_name: ${LOCAL_HOSTNAME}"
    exit 1
  fi

  if [[ "${DEPLOY_DIR}" != /* ]]; then
    err "DEPLOY_DIR must be an absolute path; got ${DEPLOY_DIR}."
    exit 1
  fi

  if [[ "${DEPLOY_DIR}" == *[[:space:]]* ]]; then
    err "DEPLOY_DIR cannot contain whitespace because systemd/nginx paths are generated from it: ${DEPLOY_DIR}"
    exit 1
  fi

  if [[ "${CLOUDFLARED_CONFIG_FILE}" != "${CLOUDFLARED_SYSTEM_DIR}/"* ]] || [[ "${CLOUDFLARED_CONFIG_FILE}" == *[[:space:]]* ]]; then
    err "CLOUDFLARED_CONFIG_FILE must live under ${CLOUDFLARED_SYSTEM_DIR} and contain no whitespace."
    exit 1
  fi

  if [[ "${DEPLOY_DIR}" == "/" ]] || [[ "${DEPLOY_DIR}" == "${REPO_DIR}" ]] || [[ "${DEPLOY_DIR}" == "${REPO_DIR}/"* ]]; then
    err "Refusing unsafe DEPLOY_DIR=${DEPLOY_DIR}."
    err "Use a dedicated directory outside the git checkout, for example /var/www/uconn-isomorphic."
    exit 1
  fi

  if [[ -n "${PUBLIC_HOSTNAME:-}" ]]; then
    PUBLIC_HOSTNAME="$(normalize_hostname_input "${PUBLIC_HOSTNAME}")"
    if ! is_valid_dns_hostname "${PUBLIC_HOSTNAME}"; then
      err "PUBLIC_HOSTNAME must be a DNS hostname without a path or port; got ${PUBLIC_HOSTNAME}."
      exit 1
    fi
  fi

  if [[ -n "${TUNNEL_UUID:-}" ]] && ! is_tunnel_uuid "${TUNNEL_UUID}"; then
    warn "Ignoring invalid saved TUNNEL_UUID=${TUNNEL_UUID}."
    unset TUNNEL_UUID
  fi
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
  sync_etc_hosts
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
  apt_install_if_missing iproute2 ss
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

  info "Using existing device hostname ${LOCAL_HOSTNAME}."
  info "Local site address will be http://${LOCAL_DOMAIN}"
}

sync_etc_hosts() {
  # Keep /etc/hosts in step with the current hostname. Without this, sudo can
  # print "unable to resolve host <name>" when the hostname and hosts file drift.
  local hosts_line="127.0.1.1	${LOCAL_HOSTNAME} ${LOCAL_DOMAIN}"

  if grep -qE "^127\.0\.1\.1[[:space:]]+${LOCAL_HOSTNAME}([[:space:]]+${LOCAL_DOMAIN})?([[:space:]]|$)" /etc/hosts; then
    info "/etc/hosts already maps 127.0.1.1 to ${LOCAL_HOSTNAME}."
  elif grep -qE '^127\.0\.1\.1[[:space:]]' /etc/hosts; then
    info "Updating the 127.0.1.1 entry in /etc/hosts to ${LOCAL_HOSTNAME}."
    sudo sed -i -E "s/^127\.0\.1\.1[[:space:]].*/${hosts_line}/" /etc/hosts
  else
    info "Adding ${LOCAL_HOSTNAME} to /etc/hosts."
    printf '%s\n' "${hosts_line}" | sudo tee -a /etc/hosts >/dev/null
  fi
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

backend_port_pids() {
  if command -v ss >/dev/null 2>&1; then
    sudo ss -H -ltnp "sport = :${BACKEND_PORT}" 2>/dev/null \
      | sed -nE 's/.*pid=([0-9]+).*/\1/p' \
      | sort -u
  elif command -v lsof >/dev/null 2>&1; then
    sudo lsof -tiTCP:"${BACKEND_PORT}" -sTCP:LISTEN 2>/dev/null | sort -u
  fi
}

pid_is_this_backend() {
  local pid="$1"
  local args

  args="$(ps -p "${pid}" -o args= 2>/dev/null || true)"
  [[ "${args}" == *"backend.src.server:app"* ]] || [[ "${args}" == *"${DEPLOY_DIR}/.venv/bin/uvicorn"* ]]
}

stop_existing_backend_service() {
  if systemctl cat "${SERVICE_NAME}" >/dev/null 2>&1; then
    info "Stopping any existing ${SERVICE_NAME} service before restart."
    sudo systemctl stop "${SERVICE_NAME}" || true
    sudo systemctl reset-failed "${SERVICE_NAME}" || true
  fi
}

ensure_backend_port_available() {
  local pids pid

  mapfile -t pids < <(backend_port_pids)
  if [[ "${#pids[@]}" -eq 0 ]]; then
    return
  fi

  for pid in "${pids[@]}"; do
    if [[ -z "${pid}" ]]; then
      continue
    fi

    if pid_is_this_backend "${pid}"; then
      warn "Stopping stale backend process ${pid} on port ${BACKEND_PORT}."
      sudo kill "${pid}" 2>/dev/null || true
    else
      err "Port ${BACKEND_PORT} is already in use by a non-deploy process:"
      sudo ss -ltnp "sport = :${BACKEND_PORT}" || true
      err "Stop that process or rerun with BACKEND_PORT=<free-port> bash deploy/deploy.sh."
      exit 1
    fi
  done

  sleep 1
  mapfile -t pids < <(backend_port_pids)
  if [[ "${#pids[@]}" -eq 0 ]]; then
    return
  fi

  for pid in "${pids[@]}"; do
    if [[ -n "${pid}" ]] && pid_is_this_backend "${pid}"; then
      warn "Force-stopping stale backend process ${pid} on port ${BACKEND_PORT}."
      sudo kill -9 "${pid}" 2>/dev/null || true
    fi
  done

  sleep 1
  mapfile -t pids < <(backend_port_pids)
  if [[ "${#pids[@]}" -gt 0 ]]; then
    err "Port ${BACKEND_PORT} is still busy:"
    sudo ss -ltnp "sport = :${BACKEND_PORT}" || true
    exit 1
  fi
}

install_backend_service() {
  banner "Backend Service"

  local service_file="/etc/systemd/system/${SERVICE_NAME}.service"
  local allowed_origins="http://${LOCAL_DOMAIN},http://127.0.0.1"
  if [[ -n "${PUBLIC_HOSTNAME:-}" ]]; then
    allowed_origins="${allowed_origins},https://${PUBLIC_HOSTNAME},http://${PUBLIC_HOSTNAME}"
  fi

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
EnvironmentFile=-${DEPLOY_DIR}/.env
Environment=HOST=127.0.0.1
Environment=PORT=${BACKEND_PORT}
Environment=ALLOWED_ORIGINS=${allowed_origins}
ExecStart=${DEPLOY_DIR}/.venv/bin/uvicorn backend.src.server:app --host 127.0.0.1 --port ${BACKEND_PORT}
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

  stop_existing_backend_service
  ensure_backend_port_available

  sudo systemctl restart "${SERVICE_NAME}"
  sleep 2
  if ! systemctl is-active --quiet "${SERVICE_NAME}"; then
    err "${SERVICE_NAME} did not stay running after restart."
    err "Check: sudo journalctl -u ${SERVICE_NAME} -n 50"
    exit 1
  fi

  if ! curl -fsS "http://127.0.0.1:${BACKEND_PORT}/api/health" >/dev/null; then
    err "${SERVICE_NAME} restarted but did not pass health check on port ${BACKEND_PORT}."
    err "Check: sudo journalctl -u ${SERVICE_NAME} -n 50"
    exit 1
  fi

  info "Backend service ${SERVICE_NAME} is installed."
}

install_nginx_config() {
  banner "Nginx"

  local server_names="${LOCAL_DOMAIN} ${LOCAL_HOSTNAME}"
  local had_conf=0
  local had_link=0
  local conf_backup=""
  local link_target=""
  local default_link="/etc/nginx/sites-enabled/default"
  local had_default_link=0
  local default_link_target=""
  if [[ -n "${PUBLIC_HOSTNAME:-}" ]]; then
    server_names="${server_names} ${PUBLIC_HOSTNAME}"
  fi

  if [[ -f "${NGINX_CONF}" ]]; then
    had_conf=1
    conf_backup="$(mktemp)"
    sudo cp "${NGINX_CONF}" "${conf_backup}"
  fi

  if [[ -L "${NGINX_LINK}" ]]; then
    had_link=1
    link_target="$(readlink "${NGINX_LINK}")"
  fi

  if [[ -L "${default_link}" ]]; then
    had_default_link=1
    default_link_target="$(readlink "${default_link}")"
  fi

  disable_conflicting_nginx_sites
  sudo rm -f "${default_link}"

  # Serve as the default vhost so requests addressed by IP or localhost (for
  # example the local health check) reach this site instead of whichever
  # enabled site happens to sort first. Skip the flag if another enabled site
  # already claims default_server to keep nginx -t passing.
  local default_server_flag=" default_server"
  if grep -Rlsq "default_server" /etc/nginx/sites-enabled/ 2>/dev/null \
    && ! grep -lsq "default_server" "${NGINX_CONF}" 2>/dev/null; then
    warn "Another nginx site already declares default_server; this site will only answer for: ${server_names}."
    default_server_flag=""
  fi

  sudo tee "${NGINX_CONF}" >/dev/null <<EOF
server {
    listen 80${default_server_flag};
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

  if ! sudo nginx -t; then
    err "Generated nginx config failed validation; restoring the previous ${SERVICE_NAME} config."
    if [[ "${had_conf}" -eq 1 ]]; then
      sudo cp "${conf_backup}" "${NGINX_CONF}"
      sudo chmod 644 "${NGINX_CONF}"
    else
      sudo rm -f "${NGINX_CONF}"
    fi

    if [[ "${had_link}" -eq 1 ]]; then
      sudo ln -sf "${link_target}" "${NGINX_LINK}"
    else
      sudo rm -f "${NGINX_LINK}"
    fi

    if [[ "${had_default_link}" -eq 1 ]]; then
      sudo ln -sf "${default_link_target}" "${default_link}"
    fi

    rm -f "${conf_backup}"
    exit 1
  fi

  rm -f "${conf_backup}"
  sudo systemctl enable --now nginx
  sudo systemctl reload nginx
  info "Nginx is serving http://${LOCAL_DOMAIN}."
}

disable_conflicting_nginx_sites() {
  local site

  if [[ -z "${PUBLIC_HOSTNAME:-}" ]]; then
    return
  fi

  for site in /etc/nginx/sites-enabled/*; do
    if [[ ! -e "${site}" ]] || [[ "${site}" == "${NGINX_LINK}" ]]; then
      continue
    fi

    if sudo grep -Fq "${PUBLIC_HOSTNAME}" "${site}" 2>/dev/null; then
      warn "Disabling existing nginx site $(basename "${site}") because it already claims ${PUBLIC_HOSTNAME}."
      sudo rm -f "${site}"
    fi
  done
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

  if ! curl -fsS "http://127.0.0.1:${BACKEND_PORT}/api/health" >/dev/null; then
    err "Backend health check failed at http://127.0.0.1:${BACKEND_PORT}/api/health."
    err "Check: sudo journalctl -u ${SERVICE_NAME} -n 50"
    exit 1
  fi

  if ! curl -fsS --resolve "${LOCAL_DOMAIN}:80:127.0.0.1" "http://${LOCAL_DOMAIN}/api/health" >/dev/null; then
    err "Local nginx health check failed for http://${LOCAL_DOMAIN}/api/health."
    err "Check: sudo nginx -T | grep -A20 'server_name ${LOCAL_DOMAIN}'"
    exit 1
  fi

  if curl -fsS "http://127.0.0.1/api/health" >/dev/null; then
    info "Site is confirmed through localhost and http://${LOCAL_DOMAIN}"
  else
    warn "The site is healthy for http://${LOCAL_DOMAIN}, but plain http://127.0.0.1 reached another nginx server block."
  fi
}

save_deploy_config() {
  : > "${DEPLOY_CONFIG_FILE}"
  save_config_var "LOCAL_HOSTNAME" "${LOCAL_HOSTNAME}"
  save_config_var "LOCAL_DOMAIN" "${LOCAL_DOMAIN}"
  save_config_var "DEPLOY_DIR" "${DEPLOY_DIR}"
  save_config_var "SERVICE_NAME" "${SERVICE_NAME}"
  save_config_var "BACKEND_PORT" "${BACKEND_PORT}"
  save_config_var "CLOUDFLARED_SERVICE_NAME" "${CLOUDFLARED_SERVICE_NAME}"
  save_config_var "CLOUDFLARED_CONFIG_FILE" "${CLOUDFLARED_CONFIG_FILE}"
  if [[ -n "${TUNNEL_NAME:-}" ]]; then
    save_config_var "TUNNEL_NAME" "${TUNNEL_NAME}"
  fi
  if [[ -n "${PUBLIC_HOSTNAME:-}" ]]; then
    save_config_var "PUBLIC_HOSTNAME" "${PUBLIC_HOSTNAME}"
  fi
  if [[ -n "${TUNNEL_UUID:-}" ]]; then
    save_config_var "TUNNEL_UUID" "${TUNNEL_UUID}"
  fi
  chmod 600 "${DEPLOY_CONFIG_FILE}"
}

save_config_var() {
  local name="$1"
  local value="$2"

  printf '%s=%q\n' "${name}" "${value}" >> "${DEPLOY_CONFIG_FILE}"
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
  if [[ -r "${CLOUDFLARED_USER_DIR}/cert.pem" ]] || [[ -r "${CLOUDFLARED_SYSTEM_DIR}/cert.pem" ]]; then
    info "Cloudflare credentials already exist."
    return
  fi

  if [[ -f "${CLOUDFLARED_SYSTEM_DIR}/cert.pem" ]]; then
    warn "${CLOUDFLARED_SYSTEM_DIR}/cert.pem exists but is not readable by this user."
    warn "Logging in again will create ${CLOUDFLARED_USER_DIR}/cert.pem for tunnel management."
  fi

  warn "Cloudflare login is required. A browser URL will be shown."
  cloudflared tunnel login

  if [[ ! -r "${CLOUDFLARED_USER_DIR}/cert.pem" ]]; then
    err "Cloudflare login did not create ${CLOUDFLARED_USER_DIR}/cert.pem."
    exit 1
  fi
}

cloudflared_origin_cert_file() {
  if [[ -r "${CLOUDFLARED_USER_DIR}/cert.pem" ]]; then
    printf '%s\n' "${CLOUDFLARED_USER_DIR}/cert.pem"
  elif [[ -r "${CLOUDFLARED_SYSTEM_DIR}/cert.pem" ]]; then
    printf '%s\n' "${CLOUDFLARED_SYSTEM_DIR}/cert.pem"
  fi
}

cloudflared_with_cert() {
  local cert_file
  cert_file="$(cloudflared_origin_cert_file)"

  if [[ -n "${cert_file}" ]]; then
    TUNNEL_ORIGIN_CERT="${cert_file}" cloudflared "$@"
  else
    cloudflared "$@"
  fi
}

list_active_tunnels() {
  if cloudflared_with_cert tunnel list -o json 2>/dev/null | python3 -c '
import json, sys
try:
    data = json.load(sys.stdin)
except Exception:
    raise SystemExit(1)
if isinstance(data, dict):
    tunnels = data.get("result") or data.get("tunnels") or []
else:
    tunnels = data
if not isinstance(tunnels, list):
    raise SystemExit(1)
for tunnel in tunnels:
    if not isinstance(tunnel, dict):
        continue
    if tunnel.get("deleted_at"):
        continue
    tunnel_id = tunnel.get("id") or tunnel.get("uuid") or ""
    tunnel_name = tunnel.get("name") or ""
    if tunnel_id and tunnel_name:
        print(f"{tunnel_id}\t{tunnel_name}")
' 2>/dev/null; then
    return
  fi

  cloudflared_with_cert tunnel list 2>/dev/null | awk 'NR > 1 && $1 ~ /^[[:xdigit:]-]+$/ && $2 != "" { print $1 "\t" $2 }' || true
}

extract_tunnel_uuid() {
  sed -nE \
    -e 's/.*Created tunnel .* with id ([[:xdigit:]-]{36}).*/\1/p' \
    -e 's|.*Tunnel credentials written to .*/([[:xdigit:]-]{36})\.json.*|\1|p' \
    -e 's/^[[:space:]]*(ID|id|Tunnel ID|TunnelID)[^[:xdigit:]]*([[:xdigit:]-]{36}).*/\2/p' \
    -e 's/.*"([[:xdigit:]-]{36})".*/\1/p' \
    | tail -n 1
}

is_tunnel_uuid() {
  [[ "$1" =~ ^[[:xdigit:]]{8}-[[:xdigit:]]{4}-[[:xdigit:]]{4}-[[:xdigit:]]{4}-[[:xdigit:]]{12}$ ]]
}

find_tunnel_uuid_from_info() {
  local tunnel_name="$1"
  local info_output uuid

  if info_output="$(cloudflared_with_cert tunnel info "${tunnel_name}" -o json 2>/dev/null)"; then
    uuid="$(printf '%s\n' "${info_output}" | python3 -c '
import json, sys
try:
    data = json.load(sys.stdin)
except Exception:
    raise SystemExit(1)
for key in ("id", "uuid", "ID", "TunnelID"):
    value = data.get(key)
    if value:
        print(value)
        break
' 2>/dev/null || true)"
    if is_tunnel_uuid "${uuid}"; then
      printf '%s\n' "${uuid}"
      return
    fi
  fi

  info_output="$(cloudflared_with_cert tunnel info "${tunnel_name}" 2>/dev/null || true)"
  printf '%s\n' "${info_output}" | extract_tunnel_uuid
}

find_single_local_tunnel_credential_uuid() {
  local credential_file credential_name
  local credential_uuids=()

  for credential_file in "${CLOUDFLARED_USER_DIR}"/*.json "${CLOUDFLARED_SYSTEM_DIR}"/*.json; do
    if [[ ! -f "${credential_file}" ]]; then
      continue
    fi

    credential_name="$(basename "${credential_file}" .json)"
    if is_tunnel_uuid "${credential_name}"; then
      credential_uuids+=("${credential_name}")
    fi
  done

  if [[ "${#credential_uuids[@]}" -eq 0 ]]; then
    return
  fi

  mapfile -t credential_uuids < <(printf '%s\n' "${credential_uuids[@]}" | sort -u)
  if [[ "${#credential_uuids[@]}" -eq 1 ]]; then
    printf '%s\n' "${credential_uuids[0]}"
  fi
}

find_newest_local_tunnel_credential_uuid() {
  local credential_file credential_name

  credential_file="$(
    {
      find "${CLOUDFLARED_USER_DIR}" -maxdepth 1 -type f -name '*.json' -printf '%T@ %p\n' 2>/dev/null || true
      sudo find "${CLOUDFLARED_SYSTEM_DIR}" -maxdepth 1 -type f -name '*.json' -printf '%T@ %p\n' 2>/dev/null || true
    } | sort -nr | awk 'NR == 1 { $1=""; sub(/^ /, ""); print }'
  )"

  if [[ -z "${credential_file}" ]]; then
    return
  fi

  credential_name="$(basename "${credential_file}" .json)"
  if is_tunnel_uuid "${credential_name}"; then
    printf '%s\n' "${credential_name}"
  fi
}

extract_tunnel_uuid_from_config() {
  local config_file="$1"
  local uuid=""

  uuid="$(sudo sed -nE 's/^[[:space:]]*tunnel:[[:space:]]*([[:xdigit:]-]{36})[[:space:]]*$/\1/p' "${config_file}" 2>/dev/null | head -1)"
  if is_tunnel_uuid "${uuid}"; then
    printf '%s\n' "${uuid}"
    return
  fi

  uuid="$(sudo sed -nE 's|^[[:space:]]*credentials-file:[[:space:]]*.*/([[:xdigit:]-]{36})\.json[[:space:]]*$|\1|p' "${config_file}" 2>/dev/null | head -1)"
  if is_tunnel_uuid "${uuid}"; then
    printf '%s\n' "${uuid}"
  fi
}

find_tunnel_uuid_from_hostname_configs() {
  local config_file uuid

  if [[ -z "${PUBLIC_HOSTNAME:-}" ]]; then
    return
  fi

  for config_file in "${CLOUDFLARED_CONFIG_FILE}" "${CLOUDFLARED_SYSTEM_DIR}"/*.yml "${CLOUDFLARED_SYSTEM_DIR}"/config.yaml "${CLOUDFLARED_SYSTEM_DIR}"/config.yml "${CLOUDFLARED_USER_DIR}"/*.yml "${CLOUDFLARED_USER_DIR}"/config.yaml "${CLOUDFLARED_USER_DIR}"/config.yml; do
    if [[ ! -f "${config_file}" ]]; then
      continue
    fi

    if sudo grep -Fq "${PUBLIC_HOSTNAME}" "${config_file}" 2>/dev/null; then
      uuid="$(extract_tunnel_uuid_from_config "${config_file}")"
      if is_tunnel_uuid "${uuid}"; then
        printf '%s\n' "${uuid}"
        return
      fi
    fi
  done
}

cloudflared_service_units() {
  (systemctl list-unit-files --type=service --no-legend '*cloudflared*.service' 2>/dev/null || true) \
    | awk '{ print $1 }' \
    | sort -u
}

cloudflared_service_config_paths() {
  local unit="$1"

  systemctl cat "${unit}" 2>/dev/null \
    | sed -nE 's/.*--config[= ]([^ ]+).*/\1/p' \
    | sort -u
}

cloudflared_service_conflicts_with_target() {
  local unit="$1"
  local config_file

  if systemctl cat "${unit}" 2>/dev/null | grep -Fq "${PUBLIC_HOSTNAME:-__missing_public_hostname__}"; then
    return 0
  fi

  if [[ -n "${TUNNEL_UUID:-}" ]] && systemctl cat "${unit}" 2>/dev/null | grep -Fq "${TUNNEL_UUID}"; then
    return 0
  fi

  while IFS= read -r config_file; do
    if [[ -z "${config_file}" ]] || [[ ! -f "${config_file}" ]]; then
      continue
    fi

    if [[ -n "${PUBLIC_HOSTNAME:-}" ]] && sudo grep -Fq "${PUBLIC_HOSTNAME}" "${config_file}" 2>/dev/null; then
      return 0
    fi

    if [[ -n "${TUNNEL_UUID:-}" ]] && sudo grep -Fq "${TUNNEL_UUID}" "${config_file}" 2>/dev/null; then
      return 0
    fi
  done < <(cloudflared_service_config_paths "${unit}")

  return 1
}

stop_conflicting_cloudflared_services() {
  local unit

  while IFS= read -r unit; do
    if [[ -z "${unit}" ]] || [[ "${unit}" == "${CLOUDFLARED_SERVICE_NAME}.service" ]]; then
      continue
    fi

    if cloudflared_service_conflicts_with_target "${unit}"; then
      warn "Stopping ${unit}; it already manages ${PUBLIC_HOSTNAME:-${TUNNEL_UUID}}."
      sudo systemctl stop "${unit}" || true
      sudo systemctl disable "${unit}" || true
      sudo systemctl reset-failed "${unit}" || true
    fi
  done < <(cloudflared_service_units)
}

print_existing_tunnels() {
  local tunnels="$1"
  local tunnel_uuid tunnel_name

  info "Existing Cloudflare tunnels found:"
  while IFS=$'\t' read -r tunnel_uuid tunnel_name; do
    if [[ -n "${tunnel_uuid}" ]] && [[ -n "${tunnel_name}" ]]; then
      printf '  - %s (%s)\n' "${tunnel_name}" "${tunnel_uuid}"
    fi
  done <<< "${tunnels}"
}

find_tunnel_uuid() {
  local tunnel_name="$1"
  local uuid=""

  uuid="$(list_active_tunnels | awk -F '\t' -v name="${tunnel_name}" '$2 == name { print $1; exit }')"
  if [[ -z "${uuid}" ]]; then
    uuid="$(cloudflared_with_cert tunnel list --name "${tunnel_name}" 2>/dev/null | awk -v name="${tunnel_name}" '$1 ~ /^[[:xdigit:]-]{36}$/ && $0 ~ name { print $1; exit }' || true)"
  fi
  if [[ -z "${uuid}" ]]; then
    uuid="$(find_tunnel_uuid_from_info "${tunnel_name}")"
  fi

  printf '%s\n' "${uuid}"
}

select_cloudflare_tunnel_name() {
  local configured_name="${TUNNEL_NAME:-}"
  local tunnels matching_uuid tunnel_count first_tunnel_name selected_name

  tunnels="$(list_active_tunnels || true)"

  if [[ -n "${configured_name}" ]]; then
    if [[ -n "${tunnels}" ]]; then
      matching_uuid="$(awk -F '\t' -v name="${configured_name}" '$2 == name { print $1; exit }' <<< "${tunnels}")"
      if [[ -n "${matching_uuid}" ]]; then
        info "Using configured existing Cloudflare tunnel ${configured_name} (${matching_uuid})."
        TUNNEL_NAME="${configured_name}"
        return
      fi

      warn "Configured Cloudflare tunnel ${configured_name} was not found in this account."
    else
      info "Using configured Cloudflare tunnel name ${configured_name}; it will be created if needed."
      TUNNEL_NAME="${configured_name}"
      return
    fi
  fi

  if [[ -z "${tunnels}" ]]; then
    TUNNEL_NAME="$(prompt_with_default "Cloudflare tunnel name" "${configured_name:-uconn_isomorphic}")"
    return
  fi

  print_existing_tunnels "${tunnels}"
  tunnel_count="$(awk 'END { print NR + 0 }' <<< "${tunnels}")"
  first_tunnel_name="$(awk -F '\t' 'NR == 1 { print $2; exit }' <<< "${tunnels}")"

  if [[ "${tunnel_count}" -eq 1 ]]; then
    info "Using existing Cloudflare tunnel ${first_tunnel_name}."
    TUNNEL_NAME="${first_tunnel_name}"
    return
  fi

  selected_name="$(prompt_with_default "Cloudflare tunnel name" "${first_tunnel_name}")"
  TUNNEL_NAME="${selected_name}"
}

ensure_cloudflare_tunnel() {
  local tunnel_name="$1"
  local create_output uuid

  if [[ -n "${TUNNEL_UUID:-}" ]] && is_tunnel_uuid "${TUNNEL_UUID}"; then
    info "Using configured Cloudflare tunnel ${tunnel_name} (${TUNNEL_UUID})."
    return
  fi

  uuid="$(find_tunnel_uuid "${tunnel_name}")"
  if [[ -z "${uuid}" ]]; then
    uuid="$(find_tunnel_uuid_from_hostname_configs)"
  fi

  if [[ -n "${uuid}" ]]; then
    info "Using existing Cloudflare tunnel ${tunnel_name} (${uuid})."
    TUNNEL_UUID="${uuid}"
    return
  fi

  info "Creating Cloudflare tunnel ${tunnel_name}."
  if ! create_output="$(cloudflared_with_cert tunnel create "${tunnel_name}" 2>&1)"; then
    printf '%s\n' "${create_output}"
    uuid="$(find_tunnel_uuid "${tunnel_name}")"
    if [[ -z "${uuid}" ]]; then
      uuid="$(find_tunnel_uuid_from_hostname_configs)"
    fi
    if [[ -z "${uuid}" ]] && [[ "${create_output}" == *"tunnel with name already exists"* ]]; then
      uuid="$(find_single_local_tunnel_credential_uuid)"
    fi
    if [[ -z "${uuid}" ]] && [[ "${create_output}" == *"tunnel with name already exists"* ]]; then
      uuid="$(find_newest_local_tunnel_credential_uuid)"
    fi
    if [[ -n "${uuid}" ]]; then
      info "Tunnel ${tunnel_name} already exists; using existing Cloudflare tunnel (${uuid})."
      TUNNEL_UUID="${uuid}"
      return
    fi

    err "Could not create Cloudflare tunnel ${tunnel_name}."
    exit 1
  fi

  printf '%s\n' "${create_output}"
  uuid="$(printf '%s\n' "${create_output}" | extract_tunnel_uuid)"

  if [[ -z "${uuid}" ]]; then
    uuid="$(find_tunnel_uuid "${tunnel_name}")"
  fi

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
    err "Expected ${CLOUDFLARED_USER_DIR}/${TUNNEL_UUID}.json or ${CLOUDFLARED_SYSTEM_DIR}/${TUNNEL_UUID}.json."
    err "If this tunnel was created on another machine, copy that JSON credential file here or choose a new tunnel name."
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
  local tunnel_ref="${TUNNEL_UUID:-$tunnel_name}"

  if ! is_tunnel_uuid "${TUNNEL_UUID:-}"; then
    err "Cannot write cloudflared config without a valid tunnel UUID."
    exit 1
  fi

  sudo tee "${CLOUDFLARED_CONFIG_FILE}" >/dev/null <<EOF
tunnel: ${tunnel_ref}
credentials-file: ${CLOUDFLARED_SYSTEM_DIR}/${TUNNEL_UUID}.json

ingress:
  - hostname: ${public_hostname}
    service: http://127.0.0.1:80
  - service: http_status:404
EOF

  sudo chmod 644 "${CLOUDFLARED_CONFIG_FILE}"
}

route_cloudflare_dns() {
  local tunnel_name="$1"
  local public_hostname="$2"
  local tunnel_ref="${TUNNEL_UUID:-$tunnel_name}"
  local route_output

  info "Creating Cloudflare DNS route for ${public_hostname}."
  if route_output="$(cloudflared_with_cert tunnel route dns "${tunnel_ref}" "${public_hostname}" 2>&1)"; then
    printf '%s\n' "${route_output}"
    return
  fi

  printf '%s\n' "${route_output}"
  warn "DNS route already exists or could not be created normally. Trying overwrite."
  if route_output="$(cloudflared_with_cert tunnel route dns --overwrite-dns "${tunnel_ref}" "${public_hostname}" 2>&1)"; then
    printf '%s\n' "${route_output}"
    return
  fi

  printf '%s\n' "${route_output}"
  err "Cloudflare DNS route could not be created for ${public_hostname}."
  err "Check that the hostname is in your Cloudflare account and the login has DNS edit permission."
  exit 1
}

stop_legacy_cloudflared_service_if_ours() {
  if [[ "${CLOUDFLARED_SERVICE_NAME}" == "cloudflared" ]]; then
    return
  fi

  if ! systemctl cat cloudflared >/dev/null 2>&1; then
    return
  fi

  if [[ -f "${CLOUDFLARED_SYSTEM_DIR}/config.yml" ]] \
    && sudo grep -q "${TUNNEL_UUID}" "${CLOUDFLARED_SYSTEM_DIR}/config.yml"; then
    warn "Stopping legacy cloudflared.service for this tunnel; ${CLOUDFLARED_SERVICE_NAME}.service will manage it."
    sudo systemctl stop cloudflared || true
    sudo systemctl disable cloudflared || true
    sudo systemctl reset-failed cloudflared || true
  fi
}

install_cloudflared_service() {
  local cloudflared_bin
  local service_file="/etc/systemd/system/${CLOUDFLARED_SERVICE_NAME}.service"
  cloudflared_bin="$(command -v cloudflared)"

  stop_legacy_cloudflared_service_if_ours
  stop_conflicting_cloudflared_services

  sudo tee "${service_file}" >/dev/null <<EOF
[Unit]
Description=cloudflared tunnel for ${SERVICE_NAME}
After=network-online.target
Wants=network-online.target

[Service]
Type=notify
ExecStart=${cloudflared_bin} --no-autoupdate --config ${CLOUDFLARED_CONFIG_FILE} tunnel run
Restart=on-failure
RestartSec=5s

[Install]
WantedBy=multi-user.target
EOF

  sudo chmod 644 "${service_file}"
  sudo systemctl daemon-reload
  sudo systemctl enable --now "${CLOUDFLARED_SERVICE_NAME}"
  sudo systemctl restart "${CLOUDFLARED_SERVICE_NAME}"

  sleep 2
  if ! systemctl is-active --quiet "${CLOUDFLARED_SERVICE_NAME}"; then
    err "${CLOUDFLARED_SERVICE_NAME}.service did not stay running."
    err "Check: sudo journalctl -u ${CLOUDFLARED_SERVICE_NAME} -n 50"
    exit 1
  fi
}

setup_cloudflare() {
  banner "Cloudflare Tunnel"

  local public_hostname

  install_cloudflared
  ensure_cloudflare_authenticated
  select_cloudflare_tunnel_name

  public_hostname="$(prompt_with_default "Cloudflare hostname to route to this Pi, for example uconn.example.com" "${PUBLIC_HOSTNAME:-}")"
  public_hostname="$(normalize_hostname_input "${public_hostname}")"

  if [[ -z "${public_hostname}" ]]; then
    err "A public hostname is required for Cloudflare tunnel routing."
    exit 1
  fi

  if ! is_valid_dns_hostname "${public_hostname}"; then
    err "Cloudflare hostname must be a DNS hostname without a path or port; got ${public_hostname}."
    exit 1
  fi

  PUBLIC_HOSTNAME="${public_hostname}"

  ensure_cloudflare_tunnel "${TUNNEL_NAME}"
  copy_cloudflare_credentials
  write_cloudflare_config "${TUNNEL_NAME}" "${PUBLIC_HOSTNAME}"
  route_cloudflare_dns "${TUNNEL_NAME}" "${PUBLIC_HOSTNAME}"
  install_backend_service
  install_nginx_config
  install_cloudflared_service
  save_deploy_config

  info "Cloudflare tunnel is configured for https://${PUBLIC_HOSTNAME}"
}

main() {
  banner "UConn Isomorphic Question LLM Installer"
  select_platform

  echo "This script will install and create everything necessary to run this site on a Raspberry Pi."
  echo "It will install system packages, create ${DEPLOY_DIR}, build the frontend, install the backend service, configure nginx, and use the existing local address ${LOCAL_DOMAIN}."
  echo ""

  if ! ask_to_continue "Continue with Raspberry Pi setup?"; then
    info "Closed without making changes."
    exit 0
  fi

  require_linux
  require_repo
  validate_deploy_settings
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
