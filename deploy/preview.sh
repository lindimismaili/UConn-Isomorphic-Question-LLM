#!/usr/bin/env bash
set -euo pipefail

# Run the site locally with a self-contained loopback hostname.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
SITE_DIR="${REPO_DIR}/site"
FRONTEND_DIR="${REPO_DIR}/site/frontend"
SITE_VENV="${SITE_DIR}/.venv"
LOCAL_DOMAIN="${LOCAL_DOMAIN:-isomorphic.localhost}"
MODE="--dev"
MODE_WAS_SET=0
FRONTEND_HOST="127.0.0.1"
LAN_MODE=0
BACKEND_HOST="${BACKEND_HOST:-127.0.0.1}"
BACKEND_PORT="${BACKEND_PORT:-8000}"
DEV_PORT="${PORT:-5173}"
WEB_PORT="${WEB_PORT:-8000}"
BACKEND_PID=""
PREVIEW_ALLOWED_ORIGINS=""
LAN_PREVIEW_ORIGINS=""
PREVIEW_ACCESS_TOKEN=""
PREVIEW_ACCESS_TOKEN_GENERATED="false"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

info() { echo -e "${GREEN}[preview]${NC} $1"; }
warn() { echo -e "${YELLOW}[preview]${NC} $1"; }
err() { echo -e "${RED}[preview]${NC} $1" >&2; }

usage() {
  echo "Usage: bash deploy/preview.sh [--dev|--web] [--lan]"
  echo "  --dev  Run backend plus Vite hot reload at http://${LOCAL_DOMAIN}:${DEV_PORT}"
  echo "  --web  Build frontend and serve it through FastAPI at http://${LOCAL_DOMAIN}:${WEB_PORT}"
  echo "  --lan  Bind the Vite dev server to all interfaces (dev only; trusted networks only)"
  echo "         The launcher prints and authorizes the exact detected LAN URL and enforces an Access Gate token."
  echo "         Set PREVIEW_LAN_HOST to a reachable LAN hostname or IPv4 address if detection fails."
  echo "         Without --lan, preview servers listen on 127.0.0.1 only."
}

for arg in "$@"; do
  case "${arg}" in
    --dev|dev)
      if [[ "${MODE_WAS_SET}" -eq 1 ]] && [[ "${MODE}" != "--dev" ]]; then
        err "Choose only one preview mode."
        usage >&2
        exit 1
      fi
      MODE="--dev"
      MODE_WAS_SET=1
      ;;
    --web|web)
      if [[ "${MODE_WAS_SET}" -eq 1 ]] && [[ "${MODE}" != "--web" ]]; then
        err "Choose only one preview mode."
        usage >&2
        exit 1
      fi
      MODE="--web"
      MODE_WAS_SET=1
      ;;
    --lan)
      LAN_MODE=1
      FRONTEND_HOST="0.0.0.0"
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      err "Unknown preview option: ${arg}"
      usage >&2
      exit 1
      ;;
  esac
done

if [[ "${MODE}" == "--web" ]] && [[ "${LAN_MODE}" -eq 1 ]]; then
  err "--lan is only supported with --dev."
  usage >&2
  exit 1
fi

case "${BACKEND_HOST}" in
  127.0.0.1|localhost|::1)
    ;;
  *)
    err "BACKEND_HOST must remain loopback-only for preview."
    err "Use --lan to expose the Vite proxy with an exact origin and Access Gate token."
    exit 1
    ;;
esac

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
  if [[ -x "${SITE_VENV}/bin/python" ]]; then
    printf '%s\n' "${SITE_VENV}/bin/python"
    return
  fi

  if command -v uv >/dev/null 2>&1; then
    info "Creating project virtual environment at ${SITE_VENV}" >&2
    uv venv --python 3.12 "${SITE_VENV}"
  else
    local bootstrap_python=""
    for candidate in python3.12 python3 python; do
      if command -v "${candidate}" >/dev/null 2>&1 \
        && "${candidate}" -c 'import sys; raise SystemExit(0 if (3, 10) <= sys.version_info[:2] < (3, 14) else 1)' 2>/dev/null; then
        bootstrap_python="${candidate}"
        break
      fi
    done
    if [[ -z "${bootstrap_python}" ]]; then
      err "Python 3.10-3.13 is required to create ${SITE_VENV}."
      exit 1
    fi
    info "Creating project virtual environment at ${SITE_VENV}" >&2
    "${bootstrap_python}" -m venv "${SITE_VENV}"
  fi

  if [[ ! -x "${SITE_VENV}/bin/python" ]]; then
    err "Project virtual environment creation failed at ${SITE_VENV}."
    exit 1
  fi
  printf '%s\n' "${SITE_VENV}/bin/python"
}

ensure_dependencies() {
  PYTHON_BIN="$(find_python)"

  local frontend_lock="${FRONTEND_DIR}/package-lock.json"
  local frontend_marker="${FRONTEND_DIR}/node_modules/.package-lock.sha256"
  local frontend_digest
  frontend_digest="$("${PYTHON_BIN}" -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest())' "${frontend_lock}")"
  if [[ ! -f "${frontend_marker}" ]] || [[ "$(<"${frontend_marker}")" != "${frontend_digest}" ]]; then
    info "Installing locked frontend dependencies."
    npm -C "${FRONTEND_DIR}" ci --no-audit --no-fund
    printf '%s\n' "${frontend_digest}" >"${frontend_marker}"
    chmod 600 "${frontend_marker}"
  fi

  local backend_lock="${SITE_DIR}/backend/requirements.txt"
  local backend_marker="${SITE_VENV}/.requirements.sha256"
  local backend_digest
  backend_digest="$("${PYTHON_BIN}" -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest())' "${backend_lock}")"
  if [[ ! -f "${backend_marker}" ]] \
    || [[ "$(<"${backend_marker}")" != "${backend_digest}" ]] \
    || ! "${PYTHON_BIN}" -c "import fastapi, uvicorn; from agents.exceptions import ModelRefusalError" >/dev/null 2>&1 \
    || ! "${PYTHON_BIN}" -m pip check >/dev/null 2>&1; then
    info "Installing locked backend Python dependencies into ${SITE_VENV}."
    "${PYTHON_BIN}" -m pip install --require-hashes --requirement "${backend_lock}"
    "${PYTHON_BIN}" -m pip check
    printf '%s\n' "${backend_digest}" >"${backend_marker}"
    chmod 600 "${backend_marker}"
  fi

  if ! (
    cd "${REPO_DIR}/site"
    "${PYTHON_BIN}" -c \
      "import os; from backend.src.bootstrap import load_dotenv; from backend.src.paths import ENV_PATH; load_dotenv(ENV_PATH); raise SystemExit(0 if os.getenv('OPENAI_API_KEY') else 1)"
  ) >/dev/null 2>&1; then
    warn "OPENAI_API_KEY is not configured in the process environment, site/.env.local, or site/.env. The site can start, but workflows will fail until it is set."
  fi
}

backend_is_healthy() {
  curl -fsS "http://${BACKEND_HOST}:${BACKEND_PORT}/api/health" >/dev/null 2>&1
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
    if [[ "${candidate}" != "${BACKEND_PORT}" ]] && ! port_is_listening "${candidate}"; then
      DEV_PORT="${candidate}"
      if [[ "${DEV_PORT}" != "${start_port}" ]]; then
        warn "Using dev preview port ${DEV_PORT}; ${start_port} was unavailable."
      fi
      return
    fi

    if [[ "${candidate}" == "${BACKEND_PORT}" ]]; then
      warn "Dev preview port ${candidate} is reserved for the backend; trying $((candidate + 1))."
    else
      warn "Dev preview port ${candidate} is in use; trying $((candidate + 1))."
    fi
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

configure_preview_origins() {
  local origin_args=(
    --site-dir "${SITE_DIR}"
    --local-domain "${LOCAL_DOMAIN}"
    --dev-port "${DEV_PORT}"
    --web-port "${WEB_PORT}"
  )
  if [[ "${LAN_MODE}" -eq 1 ]]; then
    origin_args+=(--lan)
  fi

  local origin_config
  if ! origin_config="$(
    "${PYTHON_BIN}" "${SCRIPT_DIR}/preview_origins.py" "${origin_args[@]}"
  )"; then
    err "Could not configure exact preview origins."
    exit 1
  fi
  PREVIEW_ALLOWED_ORIGINS="$(
    "${PYTHON_BIN}" -c \
      'import json,sys; print(json.load(sys.stdin)["allowedOriginsValue"])' \
      <<<"${origin_config}"
  )"
  LAN_PREVIEW_ORIGINS="$(
    "${PYTHON_BIN}" -c \
      'import json,sys; print("\\n".join(json.load(sys.stdin)["lanOrigins"]))' \
      <<<"${origin_config}"
  )"
  PREVIEW_ACCESS_TOKEN="$(
    "${PYTHON_BIN}" -c \
      'import json,sys; print(json.load(sys.stdin)["accessToken"])' \
      <<<"${origin_config}"
  )"
  PREVIEW_ACCESS_TOKEN_GENERATED="$(
    "${PYTHON_BIN}" -c \
      'import json,sys; print(str(json.load(sys.stdin)["accessTokenGenerated"]).lower())' \
      <<<"${origin_config}"
  )"
}

print_port_listeners() {
  local port="$1"

  if command -v lsof >/dev/null 2>&1; then
    lsof -nP -iTCP:"${port}" -sTCP:LISTEN || true
  else
    warn "Install lsof to inspect which process is using port ${port}."
  fi
}

start_backend() {
  local -a backend_env=("ALLOWED_ORIGINS=${PREVIEW_ALLOWED_ORIGINS}")
  if [[ "${LAN_MODE}" -eq 1 ]]; then
    backend_env+=("APP_ACCESS_TOKEN=${PREVIEW_ACCESS_TOKEN}")
  fi

  info "Starting backend at http://${LOCAL_DOMAIN}:${BACKEND_PORT}"
  (
    cd "${REPO_DIR}/site"
    exec env "${backend_env[@]}" \
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
  select_backend_port
  select_dev_port
  configure_preview_origins
  start_backend

  if [[ "${LAN_MODE}" -eq 1 ]]; then
    warn "LAN mode enabled: Vite will listen on all interfaces. Use only on a trusted network."
    while IFS= read -r origin; do
      if [[ -n "${origin}" ]]; then
        info "LAN preview URL: ${origin}"
      fi
    done <<<"${LAN_PREVIEW_ORIGINS}"
    if [[ "${PREVIEW_ACCESS_TOKEN_GENERATED}" == "true" ]]; then
      warn "No APP_ACCESS_TOKEN was configured; generated a one-session LAN access token."
      info "One-time Access Gate token: ${PREVIEW_ACCESS_TOKEN}"
      info "Paste that token into the browser Access Gate. It expires when this preview stops."
    else
      info "Access Gate: use the configured APP_ACCESS_TOKEN (its value is not displayed)."
    fi
  fi
  info "Starting hot reload dev preview at http://${LOCAL_DOMAIN}:${DEV_PORT}"
  # `--force` re-optimizes deps from scratch (ignores node_modules/.vite) so a
  # fresh start never serves a stale pre-bundled cache. Source files are always
  # served fresh by Vite's dev server / HMR.
  env -u APP_ACCESS_TOKEN \
    VITE_BACKEND_URL="http://${BACKEND_HOST}:${BACKEND_PORT}" \
    npm -C "${FRONTEND_DIR}" run dev:ui -- --host "${FRONTEND_HOST}" --port "${DEV_PORT}" --strictPort --force
}

run_web_preview() {
  ensure_local_domain
  ensure_dependencies

  # Always produce a fresh build so the preview never serves stale assets.
  # `vite build` empties dist first (emptyOutDir), so this is a full rebuild.
  info "Building frontend into ${REPO_DIR}/site/dist"
  npm -C "${FRONTEND_DIR}" run build

  # Never identify or terminate a listener by its public HTTP response. Select
  # a free port and launch a newly owned process on freshly built assets.
  select_web_port
  configure_preview_origins

  info "Starting built web preview at http://${LOCAL_DOMAIN}:${WEB_PORT}"
  cd "${REPO_DIR}/site"
  exec env \
    HOST="${BACKEND_HOST}" \
    PORT="${WEB_PORT}" \
    ALLOWED_ORIGINS="${PREVIEW_ALLOWED_ORIGINS}" \
    "${PYTHON_BIN}" -m backend.src.server
}

case "${MODE}" in
  --dev)
    run_dev_preview
    ;;
  --web)
    run_web_preview
    ;;
esac
