#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BACKEND_DIR="${PROJECT_ROOT}/backend/orchestrator"

set -a
if [[ -f "${PROJECT_ROOT}/.env" ]]; then
  # shellcheck disable=SC1091
  source "${PROJECT_ROOT}/.env"
fi
if [[ -f "${PROJECT_ROOT}/.env.local" ]]; then
  # shellcheck disable=SC1091
  source "${PROJECT_ROOT}/.env.local"
fi
if [[ -f "${BACKEND_DIR}/.env" ]]; then
  # shellcheck disable=SC1091
  source "${BACKEND_DIR}/.env"
fi
if [[ -f "${BACKEND_DIR}/.env.local" ]]; then
  # shellcheck disable=SC1091
  source "${BACKEND_DIR}/.env.local"
fi
set +a

cd "${BACKEND_DIR}"
PYTHON_BIN="python"
if [[ -n "${VIRTUAL_ENV:-}" ]]; then
  PYTHON_BIN="${VIRTUAL_ENV}/bin/python"
elif [[ -x "${BACKEND_DIR}/.venv/bin/python" ]]; then
  PYTHON_BIN="${BACKEND_DIR}/.venv/bin/python"
fi

if [[ $# -gt 0 ]]; then
  SCRIPT_PATH="$1"
  shift
  "${PYTHON_BIN}" "${SCRIPT_PATH}" "$@"
else
  "${PYTHON_BIN}" -m uvicorn app:api --host 0.0.0.0 --port 8000 --reload
fi
