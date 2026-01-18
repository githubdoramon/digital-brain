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
uvicorn app:api --host 0.0.0.0 --port 8000
