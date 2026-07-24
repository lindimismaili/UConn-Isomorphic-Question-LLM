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

# DEPLOY_DIR is the release root, not a mutable working tree. The running app
# is selected through this symlink so a completed candidate can be activated
# atomically and the prior release remains available for rollback.
RELEASES_DIR="${DEPLOY_DIR}/releases"
CURRENT_RELEASE="${DEPLOY_DIR}/current"
PERSIST_DIR="${PERSIST_DIR:-/var/lib/${SERVICE_NAME}}"
PERSIST_RUNTIME_DIR="${PERSIST_DIR}/runtime"
DEPLOY_STATUS_FILE="${PERSIST_RUNTIME_DIR}/deployment_status.json"
RELEASE_RETENTION="${RELEASE_RETENTION:-3}"

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

  if [[ "${PERSIST_DIR}" != /* ]] || [[ "${PERSIST_DIR}" == *[[:space:]]* ]]; then
    err "PERSIST_DIR must be an absolute path without whitespace; got ${PERSIST_DIR}."
    exit 1
  fi

  if ! [[ "${RELEASE_RETENTION}" =~ ^[0-9]+$ ]]; then
    err "RELEASE_RETENTION must be a non-negative integer; got ${RELEASE_RETENTION}."
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

  if [[ -n "${TUNNEL_UUID:-}" ]] && ! tunnel_credential_exists "${TUNNEL_UUID}"; then
    warn "Ignoring tunnel UUID ${TUNNEL_UUID}; no local credential JSON exists for it."
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
  if [[ ! -f "${REPO_DIR}/site/backend/requirements.txt" ]] || [[ ! -f "${REPO_DIR}/site/backend/src/server.py" ]] || [[ ! -f "${REPO_DIR}/site/frontend/package.json" ]]; then
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
  sudo mkdir -p "${DEPLOY_DIR}/site/backend/outputs" "${DEPLOY_DIR}/site/backend/uploads"

  sudo rsync -a --delete \
    --exclude ".git/" \
    --exclude ".venv/" \
    --exclude ".env" \
    --exclude "node_modules/" \
    --exclude "site/frontend/node_modules/" \
    --exclude "site/frontend/.vite/" \
    --exclude "site/backend/outputs/" \
    --exclude "site/backend/uploads/" \
    --exclude "__pycache__/" \
    "${REPO_DIR}/" "${DEPLOY_DIR}/"

  if [[ -f "${REPO_DIR}/site/.env" ]]; then
    sudo install -m 640 -o www-data -g www-data "${REPO_DIR}/site/.env" "${DEPLOY_DIR}/site/.env"
  elif [[ ! -f "${DEPLOY_DIR}/site/.env" ]]; then
    warn "No site/.env file found. OPENAI_API_KEY is required before workflows can run."
  fi

  sudo chown -R www-data:www-data "${DEPLOY_DIR}"
  info "Repository synced to ${DEPLOY_DIR}."
}

warn_if_openai_key_missing() {
  local env_file="${DEPLOY_DIR}/site/.env"

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

  local req_file="${DEPLOY_DIR}/site/backend/requirements.txt"
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

  local frontend_dir="${DEPLOY_DIR}/site/frontend"
  local dist_dir="${DEPLOY_DIR}/site/dist"

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
  local dist_dir="${DEPLOY_DIR}/site/dist"
  local commit_sha="${DEPLOY_COMMIT_SHA:-unknown}"
  local commit_short="${DEPLOY_COMMIT_SHORT:-unknown}"
  local commit_time="${DEPLOY_COMMIT_TIME:-unknown}"
  local published_at

  if [[ "${commit_sha}" == "unknown" ]] && command -v git >/dev/null 2>&1 && git -C "${REPO_DIR}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
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
      return 1
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
    return 1
  fi
}

wait_for_backend_health() {
  local attempt

  for attempt in $(seq 1 60); do
    if curl -fsS "http://127.0.0.1:${BACKEND_PORT}/api/health" >/dev/null; then
      return 0
    fi

    if ! systemctl is-active --quiet "${SERVICE_NAME}"; then
      err "${SERVICE_NAME} stopped before it became healthy."
      err "Check: sudo journalctl -u ${SERVICE_NAME} -n 50"
      return 1
    fi

    sleep 1
  done

  err "${SERVICE_NAME} did not pass health check on port ${BACKEND_PORT} within 60 seconds."
  err "Check: sudo journalctl -u ${SERVICE_NAME} -n 50"
  return 1
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
WorkingDirectory=${CURRENT_RELEASE}/site
EnvironmentFile=-${CURRENT_RELEASE}/site/.env
Environment=HOST=127.0.0.1
Environment=PORT=${BACKEND_PORT}
Environment=ALLOWED_ORIGINS=${allowed_origins}
ExecStart=${CURRENT_RELEASE}/.venv/bin/uvicorn backend.src.server:app --host 127.0.0.1 --port ${BACKEND_PORT}
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
  if ! systemctl is-active --quiet "${SERVICE_NAME}"; then
    err "${SERVICE_NAME} did not stay running after restart."
    err "Check: sudo journalctl -u ${SERVICE_NAME} -n 50"
    return 1
  fi

  if ! wait_for_backend_health; then
    return 1
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

    root ${CURRENT_RELEASE}/site/dist;
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
  save_config_var "PERSIST_DIR" "${PERSIST_DIR}"
  save_config_var "RELEASE_RETENTION" "${RELEASE_RETENTION}"
  save_config_var "CLOUDFLARED_SERVICE_NAME" "${CLOUDFLARED_SERVICE_NAME}"
  save_config_var "CLOUDFLARED_CONFIG_FILE" "${CLOUDFLARED_CONFIG_FILE}"
  save_config_var "AUTO_UPDATE" "${AUTO_UPDATE:-0}"
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

tunnel_credential_exists() {
  local uuid="$1"

  [[ -f "${CLOUDFLARED_USER_DIR}/${uuid}.json" ]] || [[ -f "${CLOUDFLARED_SYSTEM_DIR}/${uuid}.json" ]]
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

create_cloudflare_tunnel_named() {
  local tunnel_name="$1"
  local create_output uuid

  info "Creating Cloudflare tunnel ${tunnel_name}."
  if ! create_output="$(cloudflared_with_cert tunnel create "${tunnel_name}" 2>&1)"; then
    printf '%s\n' "${create_output}"
    return 1
  fi

  printf '%s\n' "${create_output}"
  uuid="$(printf '%s\n' "${create_output}" | extract_tunnel_uuid)"

  if [[ -z "${uuid}" ]]; then
    uuid="$(find_tunnel_uuid "${tunnel_name}")"
  fi

  if [[ -z "${uuid}" ]] || ! is_tunnel_uuid "${uuid}"; then
    err "Could not determine the Cloudflare tunnel UUID for ${tunnel_name}."
    return 1
  fi

  TUNNEL_NAME="${tunnel_name}"
  TUNNEL_UUID="${uuid}"
  return 0
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
  local replacement_name uuid

  if [[ -n "${TUNNEL_UUID:-}" ]] && is_tunnel_uuid "${TUNNEL_UUID}"; then
    if tunnel_credential_exists "${TUNNEL_UUID}"; then
      info "Using configured Cloudflare tunnel ${tunnel_name} (${TUNNEL_UUID})."
      return
    fi

    warn "Configured tunnel ${TUNNEL_UUID} has no local credential JSON; creating a replacement tunnel."
    unset TUNNEL_UUID
  fi

  uuid="$(find_tunnel_uuid "${tunnel_name}")"
  if [[ -z "${uuid}" ]]; then
    uuid="$(find_tunnel_uuid_from_hostname_configs)"
  fi

  if [[ -n "${uuid}" ]]; then
    if tunnel_credential_exists "${uuid}"; then
      info "Using existing Cloudflare tunnel ${tunnel_name} (${uuid})."
      TUNNEL_UUID="${uuid}"
      return
    fi

    warn "Existing Cloudflare tunnel ${tunnel_name} (${uuid}) has no local credential JSON; creating a replacement tunnel."
  fi

  if create_cloudflare_tunnel_named "${tunnel_name}"; then
    return
  fi

  replacement_name="${tunnel_name}-${LOCAL_HOSTNAME}"
  warn "Tunnel name ${tunnel_name} is unavailable or unusable locally. Trying ${replacement_name}."
  if create_cloudflare_tunnel_named "${replacement_name}"; then
    return
  fi

  replacement_name="${tunnel_name}-${LOCAL_HOSTNAME}-$(date -u +%Y%m%d%H%M%S)"
  warn "Tunnel name ${replacement_name%-*} is unavailable. Trying ${replacement_name}."
  if create_cloudflare_tunnel_named "${replacement_name}"; then
    return
  fi

  err "Could not create a usable Cloudflare tunnel."
  exit 1
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

write_deployment_status() {
  # Arguments: state stage attempted active previous rollback deferred workflows message
  sudo mkdir -p "${PERSIST_RUNTIME_DIR}"
  sudo env DEPLOY_STATUS_FILE="${DEPLOY_STATUS_FILE}" python3 - "$@" <<'PY'
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

keys = ("status", "stage", "attempted_commit", "active_commit", "previous_commit", "rollback_succeeded", "deferred", "active_workflows", "message")
values = dict(zip(keys, sys.argv[1:], strict=False))
path = Path(os.environ["DEPLOY_STATUS_FILE"])
try:
    old = json.loads(path.read_text(encoding="utf-8"))
except (OSError, ValueError):
    old = {}
events = old.get("events", []) if isinstance(old, dict) and isinstance(old.get("events"), list) else []
now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
rollback = values["rollback_succeeded"].lower()
payload = {
    "status": values["status"], "stage": values["stage"],
    "attempted_commit": values["attempted_commit"], "active_commit": values["active_commit"],
    "previous_commit": values["previous_commit"],
    "rollback_succeeded": True if rollback == "true" else False if rollback == "false" else None,
    "deferred": values["deferred"].lower() == "true",
    "active_workflows": int(values["active_workflows"]) if values["active_workflows"].isdigit() else 0,
    "updated_at": now,
}
events.append({"at": now, "state": values["status"], "stage": values["stage"],
               "commit": values["attempted_commit"], "message": values["message"][:400]})
payload["events"] = events[-50:]
tmp = path.with_suffix(".tmp")
tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
tmp.replace(path)
PY
  sudo chown www-data:www-data "${PERSIST_DIR}" "${PERSIST_RUNTIME_DIR}" "${DEPLOY_STATUS_FILE}"
}

current_release_path() {
  readlink -f "${CURRENT_RELEASE}" 2>/dev/null || true
}

current_release_commit() {
  local current
  current="$(current_release_path)"
  [[ -n "${current}" ]] && basename "${current}" || true
}

active_workflow_count() {
  local payload
  payload="$(curl -fsS --max-time 3 "http://127.0.0.1:${BACKEND_PORT}/api/deployment/status" 2>/dev/null || true)"
  python3 -c 'import json, sys; print(int(json.load(sys.stdin).get("active_workflows", 0)))' <<< "${payload}" 2>/dev/null || printf '%s\n' '-1'
}

link_persistent_data() {
  local release_dir="$1"
  local name
  for name in uploads outputs runtime; do
    sudo rm -rf "${release_dir}/site/backend/${name}"
    sudo ln -s "${PERSIST_DIR}/${name}" "${release_dir}/site/backend/${name}"
  done
}

ensure_release_layout() {
  sudo mkdir -p "${RELEASES_DIR}" "${PERSIST_DIR}/uploads" "${PERSIST_DIR}/outputs" "${PERSIST_RUNTIME_DIR}"
  sudo chown www-data:www-data "${PERSIST_DIR}" "${PERSIST_DIR}/uploads" "${PERSIST_DIR}/outputs" "${PERSIST_RUNTIME_DIR}"
  local name legacy_dir current legacy_sha
  current="$(current_release_path)"
  if [[ -n "${current}" ]]; then
    return 0
  fi

  # One-time migration from the legacy mutable directory. Copy first so a
  # failed migration cannot destroy user content.
  for name in uploads outputs runtime; do
    legacy_dir="${DEPLOY_DIR}/site/backend/${name}"
    if [[ -d "${legacy_dir}" ]]; then
      sudo rsync -a --ignore-existing "${legacy_dir}/" "${PERSIST_DIR}/${name}/"
    fi
  done
  sudo chown -R www-data:www-data "${PERSIST_DIR}"

  if [[ ! -d "${DEPLOY_DIR}/site" ]]; then
    return 0
  fi

  legacy_sha="$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1])).get("commit", ""))' "${DEPLOY_DIR}/site/dist/version.json" 2>/dev/null || true)"
  if ! [[ "${legacy_sha}" =~ ^[0-9a-f]{40}$ ]]; then
    legacy_sha="$(git -C "${REPO_DIR}" rev-parse HEAD 2>/dev/null || true)"
  fi
  [[ "${legacy_sha}" =~ ^[0-9a-f]{40}$ ]] || legacy_sha="legacy-$(date -u +%Y%m%d%H%M%S)"
  current="${RELEASES_DIR}/${legacy_sha}"
  if [[ ! -d "${current}" ]]; then
    sudo mkdir -p "${current}"
    sudo rsync -a --exclude "releases/" --exclude "current" --exclude "persistent/" "${DEPLOY_DIR}/" "${current}/"
  fi
  link_persistent_data "${current}"
  switch_current_release "${current}"
}

switch_current_release() {
  local release_dir next_link
  release_dir="$(readlink -f "$1")"
  if [[ ! -d "${release_dir}" ]] || [[ "${release_dir}" != "${RELEASES_DIR}/"* ]]; then
    err "Refusing to activate invalid release path: $1"
    return 1
  fi
  next_link="${DEPLOY_DIR}/.current-next-$$"
  sudo ln -s "${release_dir}" "${next_link}"
  sudo mv -Tf "${next_link}" "${CURRENT_RELEASE}"
}

write_release_version() {
  local release_dir="$1" commit_sha="$2" commit_time="$3"
  sudo env RELEASE_DIR="${release_dir}" COMMIT_SHA="${commit_sha}" COMMIT_TIME="${commit_time}" python3 - <<'PY'
import json
import os
from datetime import datetime, timezone
from pathlib import Path

path = Path(os.environ["RELEASE_DIR"]) / "site" / "dist" / "version.json"
path.write_text(json.dumps({
    "version": os.environ["COMMIT_SHA"][:7],
    "commit": os.environ["COMMIT_SHA"],
    "commit_time": os.environ["COMMIT_TIME"],
    "published_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
}, indent=2) + "\n", encoding="utf-8")
PY
}

build_candidate_release() {
  local commit_sha="$1" repo_owner="$2" commit_time release_dir
  release_dir="${RELEASES_DIR}/${commit_sha}"
  if [[ -x "${release_dir}/.venv/bin/uvicorn" ]]; then
    printf '%s\n' "${release_dir}"
    return 0
  fi
  # A venv's console scripts have an absolute interpreter path. Build directly
  # in the final, as-yet-unreferenced release directory rather than moving a
  # staging directory after venv creation.
  sudo rm -rf "${release_dir}"
  commit_time="$(sudo -u "${repo_owner}" git -C "${REPO_DIR}" log -1 --format=%cI "${commit_sha}")"
  sudo mkdir -p "${release_dir}"
  if ! sudo -u "${repo_owner}" git -C "${REPO_DIR}" archive --format=tar "${commit_sha}" | sudo tar -x -C "${release_dir}"; then
    sudo rm -rf "${release_dir}"
    return 1
  fi
  link_persistent_data "${release_dir}"
  if ! sudo python3 -m venv "${release_dir}/.venv" \
    || ! sudo "${release_dir}/.venv/bin/pip" install --upgrade pip \
    || ! sudo "${release_dir}/.venv/bin/pip" install -r "${release_dir}/site/backend/requirements.txt"; then
    sudo rm -rf "${release_dir}"
    return 1
  fi
  sudo mkdir -p "${release_dir}/site/dist"
  sudo chown -R "${repo_owner}:${repo_owner}" "${release_dir}/site/frontend" "${release_dir}/site/dist"
  if ! sudo -H -u "${repo_owner}" npm -C "${release_dir}/site/frontend" ci --no-audit --no-fund \
    || ! sudo -H -u "${repo_owner}" npm -C "${release_dir}/site/frontend" run build \
    || [[ ! -f "${release_dir}/site/dist/index.html" ]]; then
    sudo rm -rf "${release_dir}"
    return 1
  fi
  write_release_version "${release_dir}" "${commit_sha}" "${commit_time}"
  sudo chown -R www-data:www-data "${release_dir}"
  printf '%s\n' "${release_dir}"
}

release_health_is_valid() {
  local expected_sha="$1" payload
  wait_for_backend_health || return 1
  payload="$(curl -fsS --max-time 5 "http://127.0.0.1:${BACKEND_PORT}/version.json")" || return 1
  python3 -c 'import json, sys; raise SystemExit(0 if json.load(sys.stdin).get("commit") == sys.argv[1] else 1)' "${expected_sha}" <<< "${payload}"
}

prune_old_releases() {
  local active previous=() release
  active="$(current_release_path)"
  mapfile -t previous < <(find "${RELEASES_DIR}" -mindepth 1 -maxdepth 1 -type d ! -name '.*' -printf '%T@ %p\n' | sort -rn | cut -d' ' -f2-)
  local kept=0
  for release in "${previous[@]}"; do
    [[ "${release}" == "${active}" ]] && continue
    if (( kept < RELEASE_RETENTION )); then
      ((kept += 1))
      continue
    fi
    sudo rm -rf "${release}"
  done
}

last_failed_commit() {
  python3 -c 'import json, sys; p=json.load(open(sys.argv[1])); print(p.get("attempted_commit", "") if p.get("status") in {"failed", "rolled_back"} else "")' "${DEPLOY_STATUS_FILE}" 2>/dev/null || true
}

deploy_commit() {
  local commit_sha="$1" repo_owner="$2" defer_for_workflows="${3:-false}"
  local previous active release_dir workflow_count
  previous="$(current_release_path)"
  active="$(current_release_commit)"
  if [[ "${active}" == "${commit_sha}" ]]; then
    write_deployment_status "success" "up_to_date" "${commit_sha}" "${active}" "" "" "false" "0" "Already running requested commit."
    return 0
  fi
  workflow_count="$(active_workflow_count)"
  if [[ "${defer_for_workflows}" == "true" ]] && (( workflow_count > 0 )); then
    write_deployment_status "deferred" "active_workflows" "${commit_sha}" "${active}" "${active}" "" "true" "${workflow_count}" "Waiting for active workflows before restarting."
    return 0
  fi
  write_deployment_status "building" "candidate" "${commit_sha}" "${active}" "${active}" "" "false" "0" "Building isolated candidate release."
  if ! release_dir="$(build_candidate_release "${commit_sha}" "${repo_owner}")"; then
    write_deployment_status "failed" "build" "${commit_sha}" "${active}" "${active}" "" "false" "0" "Candidate build failed; active release was not changed."
    return 1
  fi
  write_deployment_status "activating" "switch" "${commit_sha}" "${active}" "${active}" "" "false" "0" "Switching the current release."
  switch_current_release "${release_dir}"
  if install_backend_service && release_health_is_valid "${commit_sha}"; then
    prune_old_releases
    write_deployment_status "success" "healthy" "${commit_sha}" "${commit_sha}" "${active}" "" "false" "0" "Candidate is serving and passed health checks."
    return 0
  fi
  write_deployment_status "failed" "health" "${commit_sha}" "${commit_sha}" "${active}" "" "false" "0" "Candidate failed after activation; rolling back."
  if [[ -n "${previous}" ]] && [[ -d "${previous}" ]]; then
    switch_current_release "${previous}"
    if install_backend_service && release_health_is_valid "${active}"; then
      write_deployment_status "rolled_back" "rollback" "${commit_sha}" "${active}" "${active}" "true" "false" "0" "Rollback completed and the previous release is healthy."
    else
      write_deployment_status "failed" "rollback" "${commit_sha}" "${active}" "${active}" "false" "false" "0" "Rollback could not restore a healthy service."
    fi
  fi
  return 1
}

run_auto_update() {
  # Fetch only: deployment uses an archived commit, never merges or rewrites
  # the source checkout. A failed fetch/build therefore cannot alter current.
  require_repo
  validate_deploy_settings
  exec 9>"/run/lock/${SERVICE_NAME}-update.lock"
  if ! flock -n 9; then
    write_deployment_status "deferred" "lock" "" "$(current_release_commit)" "" "" "true" "0" "Another deployment is already running."
    return 0
  fi

  local repo_owner remote_sha failed_sha
  repo_owner="$(stat -c '%U' "${REPO_DIR}/.git" 2>/dev/null || echo root)"
  git_as_owner() { sudo -u "${repo_owner}" git -C "${REPO_DIR}" "$@"; }
  ensure_release_layout
  if ! git_as_owner remote get-url origin >/dev/null 2>&1; then
    write_deployment_status "failed" "origin" "" "$(current_release_commit)" "" "" "false" "0" "Repository has no origin remote."
    return 1
  fi
  if ! git_as_owner fetch --quiet --force origin main; then
    write_deployment_status "failed" "fetch" "" "$(current_release_commit)" "" "" "false" "0" "git fetch failed; the active release remains unchanged."
    return 1
  fi
  remote_sha="$(git_as_owner rev-parse origin/main 2>/dev/null || true)"
  if ! [[ "${remote_sha}" =~ ^[0-9a-f]{40}$ ]]; then
    write_deployment_status "failed" "resolve" "" "$(current_release_commit)" "" "" "false" "0" "Could not resolve origin/main."
    return 1
  fi
  failed_sha="$(last_failed_commit)"
  if [[ "${failed_sha}" == "${remote_sha}" ]]; then
    write_deployment_status "deferred" "quarantined" "${remote_sha}" "$(current_release_commit)" "" "" "true" "0" "Commit previously failed; waiting for origin/main to change."
    return 0
  fi
  deploy_commit "${remote_sha}" "${repo_owner}" true
}

install_auto_update_units() {
  banner "Automatic Updates"

  local service_file="/etc/systemd/system/${SERVICE_NAME}-update.service"
  local timer_file="/etc/systemd/system/${SERVICE_NAME}-update.timer"
  local repo_owner repo_owner_home

  repo_owner="$(stat -c '%U' "${REPO_DIR}/.git")"
  repo_owner_home="$(getent passwd "${repo_owner}" | cut -d: -f6)"
  if [[ -z "${repo_owner_home}" ]]; then
    err "Could not determine the home directory for repository owner ${repo_owner}."
    return 1
  fi

  sudo tee "${service_file}" >/dev/null <<EOF
[Unit]
Description=Auto-update ${SERVICE_NAME} from origin/main
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
Environment="HOME=${repo_owner_home}"
TimeoutStartSec=30min
ExecStart=/bin/bash ${REPO_DIR}/deploy/deploy.sh --update
EOF

  sudo tee "${timer_file}" >/dev/null <<EOF
[Unit]
Description=Check origin/main for ${SERVICE_NAME} every 60s

[Timer]
OnBootSec=60
OnUnitInactiveSec=60
AccuracySec=1s
Persistent=true

[Install]
WantedBy=timers.target
EOF

  sudo chmod 644 "${service_file}" "${timer_file}"
  sudo systemctl daemon-reload
  sudo systemctl enable --now "${SERVICE_NAME}-update.timer"
  info "Auto-update enabled: checks origin/main 60 seconds after each prior check finishes."
  info "Disable later with: sudo systemctl disable --now ${SERVICE_NAME}-update.timer"
}

main() {
  if [[ "${1:-}" == "--update" ]]; then
    run_auto_update
    exit $?
  fi

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
  ensure_release_layout
  repo_owner="$(stat -c '%U' "${REPO_DIR}/.git" 2>/dev/null || echo root)"
  initial_sha="$(sudo -u "${repo_owner}" git -C "${REPO_DIR}" rev-parse HEAD)"
  deploy_commit "${initial_sha}" "${repo_owner}" false
  # deploy_commit installs the service when it activates a new candidate. The
  # explicit call also upgrades a legacy unit after a one-time migration.
  install_backend_service
  install_nginx_config
  warn_if_openai_key_missing
  verify_local_site

  echo ""
  if ask_to_continue "Attempt to host the site via Cloudflare?"; then
    setup_cloudflare
  else
    info "Skipped Cloudflare hosting. Local site remains available at http://${LOCAL_DOMAIN}"
  fi

  echo ""
  if ask_to_continue "Enable automatic updates (check GitHub every 60s and force-redeploy new commits)?"; then
    install_auto_update_units
    AUTO_UPDATE=1
  else
    sudo systemctl disable --now "${SERVICE_NAME}-update.timer" 2>/dev/null || true
    AUTO_UPDATE=0
    info "Automatic updates disabled."
  fi
  save_deploy_config

  banner "Complete"
  info "Local site: http://${LOCAL_DOMAIN}"
  info "Backend service: sudo systemctl status ${SERVICE_NAME}"
  info "Logs: sudo journalctl -u ${SERVICE_NAME} -f"
  if [[ "${AUTO_UPDATE:-0}" == "1" ]]; then
    info "Auto-update: enabled (origin/main every 60s) — sudo systemctl status ${SERVICE_NAME}-update.timer"
  fi
  warn_if_openai_key_missing
}

main "$@"
