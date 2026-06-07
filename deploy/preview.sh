#!/usr/bin/env bash
set -euo pipefail

# Run the site locally with a self-contained loopback hostname.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
FRONTEND_DIR="${REPO_DIR}/frontend"
LOCAL_DOMAIN="${LOCAL_DOMAIN:-isomorphic.localhost}"
MODE="${1:---dev}"
BACKEND_HOST="${BACKEND_HOST:-127.0.0.1}"
BACKEND_PORT="${BACKEND_PORT:-8000}"
DEV_PORT="${PORT:-5173}"
WEB_PORT="${WEB_PORT:-8000}"
BACKEND_PID=""

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

info() { echo -e "${GREEN}[preview]${NC} $1"; }
warn() { echo -e "${YELLOW}[preview]${NC} $1"; }
err() { echo -e "${RED}[preview]${NC} $1" >&2; }

cleanup() {
  if [[ -n "${BACKEND_PID}" ]] && kill -0 "${BACKEND_PID}" 2>/dev/null; then
    kill "${BACKEND_PID}" 2>/dev/null || true
    wait "${BACKEND_PID}" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

local_domain_resolves() {
  if command -v getent >/dev/null 2>&1; then
    getent hosts "${LOCAL_DOMAIN}" >/dev/null 2>&1 && return 0
  fi

  if command -v dscacheutil >/dev/null 2>&1; then
    dscacheutil -q host -a name "${LOCAL_DOMAIN}" >/dev/null 2>&1 && return 0
  fi

  ping -c 1 -W 1 "${LOCAL_DOMAIN}" >/dev/null 2>&1
}

ensure_local_domain() {
  if local_domain_resolves; then
    return
  fi

  err "${LOCAL_DOMAIN} does not resolve on this machine."
  err "Use a .localhost name for self-contained preview, or set LOCAL_DOMAIN to a hostname your OS already resolves."
  exit 1
}

find_python() {
  if [[ -n "${VIRTUAL_ENV:-}" ]] && [[ -x "${VIRTUAL_ENV}/bin/python" ]]; then
    printf '%s\n' "${VIRTUAL_ENV}/bin/python"
    return
  fi

  local newest_main_venv=""
  newest_main_venv="$(find "${HOME}/venvs" -maxdepth 1 -type d -name 'main_py*' 2>/dev/null | sort -V | tail -1 || true)"
  if [[ -n "${newest_main_venv}" ]] && [[ -x "${newest_main_venv}/bin/python" ]]; then
    printf '%s\n' "${newest_main_venv}/bin/python"
    return
  fi

  if [[ -x "${REPO_DIR}/.venv/bin/python" ]]; then
    printf '%s\n' "${REPO_DIR}/.venv/bin/python"
    return
  fi

  if command -v uv >/dev/null 2>&1; then
    info "Creating project virtual environment at ${REPO_DIR}/.venv" >&2
    uv venv "${REPO_DIR}/.venv"
    printf '%s\n' "${REPO_DIR}/.venv/bin/python"
    return
  fi

  err "No Python virtual environment found and uv is not installed."
  err "Create one with: python3 -m venv .venv && .venv/bin/pip install -r backend/requirements.txt"
  exit 1
}

ensure_dependencies() {
  if [[ ! -d "${FRONTEND_DIR}/node_modules" ]]; then
    if [[ -f "${FRONTEND_DIR}/package-lock.json" ]]; then
      npm -C "${FRONTEND_DIR}" ci --no-audit --no-fund
    else
      npm -C "${FRONTEND_DIR}" install --no-audit --no-fund
    fi
  fi

  PYTHON_BIN="$(find_python)"

  if ! "${PYTHON_BIN}" -c "import fastapi, uvicorn" >/dev/null 2>&1; then
    info "Installing backend Python dependencies into $(${PYTHON_BIN} -c 'import sys; print(sys.prefix)')"
    "${PYTHON_BIN}" -m pip install -r "${REPO_DIR}/backend/requirements.txt"
  fi

  if [[ ! -f "${REPO_DIR}/.env" ]] || ! grep -Eq '^OPENAI_API_KEY=.+$' "${REPO_DIR}/.env"; then
    warn ".env is missing OPENAI_API_KEY. The site can start, but workflows will fail until it is set."
  fi
}

backend_is_healthy() {
  curl -fsS "http://${BACKEND_HOST}:${BACKEND_PORT}/api/health" >/dev/null 2>&1
}

public_backend_is_healthy() {
  curl -fsS "http://${LOCAL_DOMAIN}:${WEB_PORT}/api/health" 2>/dev/null | grep -q '"status":"healthy"'
}

port_is_listening() {
  local port="$1"

  if command -v lsof >/dev/null 2>&1; then
    lsof -nP -iTCP:"${port}" -sTCP:LISTEN >/dev/null 2>&1
    return
  fi

  if command -v nc >/dev/null 2>&1; then
    nc -z "${BACKEND_HOST}" "${port}" >/dev/null 2>&1
    return
  fi

  (echo >"/dev/tcp/${BACKEND_HOST}/${port}") >/dev/null 2>&1
}

select_backend_port() {
  local start_port="${BACKEND_PORT}"
  local candidate="${start_port}"

  for _ in $(seq 1 100); do
    BACKEND_PORT="${candidate}"

    if backend_is_healthy; then
      info "Using existing backend at http://${BACKEND_HOST}:${BACKEND_PORT}"
      return
    fi

    if ! port_is_listening "${BACKEND_PORT}"; then
      if [[ "${BACKEND_PORT}" != "${start_port}" ]]; then
        warn "Using backend port ${BACKEND_PORT}; ${start_port} was unavailable."
      fi
      return
    fi

    warn "Backend port ${BACKEND_PORT} is in use by another process; trying $((BACKEND_PORT + 1))."
    candidate="$((candidate + 1))"
  done

  err "No available backend port found after ${start_port}."
  exit 1
}

select_dev_port() {
  local start_port="${DEV_PORT}"
  local candidate="${start_port}"

  for _ in $(seq 1 100); do
    if ! port_is_listening "${candidate}"; then
      DEV_PORT="${candidate}"
      if [[ "${DEV_PORT}" != "${start_port}" ]]; then
        warn "Using dev preview port ${DEV_PORT}; ${start_port} was unavailable."
      fi
      return
    fi

    warn "Dev preview port ${candidate} is in use; trying $((candidate + 1))."
    candidate="$((candidate + 1))"
  done

  err "No available dev preview port found after ${start_port}."
  exit 1
}

select_web_port() {
  local start_port="${WEB_PORT}"
  local candidate="${start_port}"

  for _ in $(seq 1 100); do
    WEB_PORT="${candidate}"

    if public_backend_is_healthy; then
      info "Using existing built web preview at http://${LOCAL_DOMAIN}:${WEB_PORT}"
      return
    fi

    if ! port_is_listening "${WEB_PORT}"; then
      if [[ "${WEB_PORT}" != "${start_port}" ]]; then
        warn "Using built web preview port ${WEB_PORT}; ${start_port} was unavailable."
      fi
      return
    fi

    warn "Built web preview port ${WEB_PORT} is in use by another process; trying $((WEB_PORT + 1))."
    candidate="$((candidate + 1))"
  done

  err "No available built web preview port found after ${start_port}."
  exit 1
}

preview_allowed_origins() {
  local dynamic_origins="http://${LOCAL_DOMAIN}:${DEV_PORT},http://${LOCAL_DOMAIN}:${WEB_PORT}"

  if [[ -n "${ALLOWED_ORIGINS:-}" ]]; then
    printf '%s,%s\n' "${ALLOWED_ORIGINS}" "${dynamic_origins}"
    return
  fi

  printf '%s\n' "${dynamic_origins}"
}

print_port_listeners() {
  local port="$1"

  if command -v lsof >/dev/null 2>&1; then
    lsof -nP -iTCP:"${port}" -sTCP:LISTEN || true
  else
    warn "Install lsof to inspect which process is using port ${port}."
  fi
}

start_backend_if_needed() {
  select_backend_port

  if backend_is_healthy; then
    return
  fi

  info "Starting backend at http://${LOCAL_DOMAIN}:${BACKEND_PORT}"
  (
    cd "${REPO_DIR}"
    exec env \
      ALLOWED_ORIGINS="$(preview_allowed_origins)" \
      "${PYTHON_BIN}" -m uvicorn backend.src.server:app --host "${BACKEND_HOST}" --port "${BACKEND_PORT}" --reload
  ) &
  BACKEND_PID="$!"

  for _ in $(seq 1 60); do
    backend_is_healthy && return
    sleep 0.5
  done

  err "Backend did not become healthy at http://${LOCAL_DOMAIN}:${BACKEND_PORT}"
  exit 1
}

run_dev_preview() {
  ensure_local_domain
  ensure_dependencies
  select_dev_port
  start_backend_if_needed

  info "Starting hot reload dev preview at http://${LOCAL_DOMAIN}:${DEV_PORT}"
  VITE_BACKEND_URL="http://${BACKEND_HOST}:${BACKEND_PORT}" \
    npm -C "${FRONTEND_DIR}" run dev:ui -- --host "0.0.0.0" --port "${DEV_PORT}" --strictPort
}

run_web_preview() {
  ensure_local_domain
  ensure_dependencies
  select_web_port

  if public_backend_is_healthy; then
    return
  fi

  if command -v lsof >/dev/null 2>&1 && lsof -nP -iTCP:"${WEB_PORT}" -sTCP:LISTEN >/dev/null 2>&1; then
    err "Port ${WEB_PORT} is already in use, but http://${LOCAL_DOMAIN}:${WEB_PORT}/api/health is not this backend."
    err "Stop the conflicting process or run with a different WEB_PORT."
    print_port_listeners "${WEB_PORT}"
    exit 1
  fi

  info "Building frontend into ${REPO_DIR}/dist"
  npm -C "${FRONTEND_DIR}" run build

  info "Starting built web preview at http://${LOCAL_DOMAIN}:${WEB_PORT}"
  cd "${REPO_DIR}"
  exec env \
    HOST="${BACKEND_HOST}" \
    PORT="${WEB_PORT}" \
    ALLOWED_ORIGINS="$(preview_allowed_origins)" \
    "${PYTHON_BIN}" -m backend.src.server
}

case "${MODE}" in
  --dev|dev)
    run_dev_preview
    ;;
  --web|web)
    run_web_preview
    ;;
  --help|-h)
    echo "Usage: bash deploy/preview.sh [--dev|--web]"
    echo "  --dev  Run backend plus Vite hot reload at http://${LOCAL_DOMAIN}:${DEV_PORT}"
    echo "  --web  Build frontend and serve it through FastAPI at http://${LOCAL_DOMAIN}:${WEB_PORT}"
    ;;
  *)
    err "Unknown preview mode: ${MODE}"
    err "Usage: bash deploy/preview.sh [--dev|--web]"
    exit 1
    ;;
esac
