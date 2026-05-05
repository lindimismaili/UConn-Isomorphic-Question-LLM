#!/usr/bin/env bash
set -euo pipefail

# Launch the local backend and Vite development UI from anywhere in the repository.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
FRONTEND_DIR="${REPO_DIR}/frontend"

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-5173}"
BACKEND_HOST="${BACKEND_HOST:-127.0.0.1}"
BACKEND_PORT="${BACKEND_PORT:-8000}"
BACKEND_URL="${VITE_BACKEND_URL:-http://${BACKEND_HOST}:${BACKEND_PORT}}"
BACKEND_PID=""

cleanup() {
  if [[ -n "${BACKEND_PID}" ]] && kill -0 "${BACKEND_PID}" 2>/dev/null; then
    kill "${BACKEND_PID}" 2>/dev/null || true
    wait "${BACKEND_PID}" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

if [[ ! -f "${FRONTEND_DIR}/package.json" ]]; then
  echo "ERROR: frontend/package.json not found at ${FRONTEND_DIR}" >&2
  exit 1
fi

if [[ ! -d "${FRONTEND_DIR}/node_modules" ]]; then
  if [[ -f "${FRONTEND_DIR}/package-lock.json" ]]; then
    npm -C "${FRONTEND_DIR}" ci --no-audit --no-fund
  else
    npm -C "${FRONTEND_DIR}" install --no-audit --no-fund
  fi
fi

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
    uv venv "${REPO_DIR}/.venv"
    printf '%s\n' "${REPO_DIR}/.venv/bin/python"
    return
  fi

  echo "ERROR: No Python virtual environment found and uv is not installed." >&2
  echo "Create a venv first, then rerun: python3 -m venv .venv && .venv/bin/pip install -r backend/requirements.txt" >&2
  exit 1
}

backend_is_healthy() {
  curl -fsS "${BACKEND_URL}/api/health" >/dev/null 2>&1
}

wait_for_backend() {
  local attempts=60
  local attempt

  for ((attempt = 1; attempt <= attempts; attempt++)); do
    if backend_is_healthy; then
      return 0
    fi
    sleep 0.5
  done

  return 1
}

PYTHON_BIN="$(find_python)"

if ! "${PYTHON_BIN}" -c "import fastapi, uvicorn" >/dev/null 2>&1; then
  echo "Installing backend Python dependencies into $(${PYTHON_BIN} -c 'import sys; print(sys.prefix)')"
  "${PYTHON_BIN}" -m pip install -r "${REPO_DIR}/backend/requirements.txt"
fi

if backend_is_healthy; then
  echo "Using existing backend at ${BACKEND_URL}"
else
  echo "Starting backend at ${BACKEND_URL}"
  (
    cd "${REPO_DIR}"
    exec "${PYTHON_BIN}" -m uvicorn backend.src.server:app --host "${BACKEND_HOST}" --port "${BACKEND_PORT}" --reload
  ) &
  BACKEND_PID="$!"

  if ! wait_for_backend; then
    echo "ERROR: backend did not become healthy at ${BACKEND_URL}" >&2
    exit 1
  fi
fi

echo "Starting Vite UI at http://localhost:${PORT}"
VITE_BACKEND_URL="${BACKEND_URL}" npm -C "${FRONTEND_DIR}" run dev:ui -- --host "${HOST}" --port "${PORT}" "$@"
