#!/usr/bin/env bash
set -euo pipefail

COMPOSE_DIR=${COMPOSE_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}
COMPOSE_FILE=${COMPOSE_FILE:-docker-compose.yml}
DOCKER_CMD=${DOCKER_CMD:-docker}
COMPOSE_PROJECT_NAME=${COMPOSE_PROJECT_NAME:-}

if ! command -v "$DOCKER_CMD" >/dev/null 2>&1; then
  echo "Error: docker command not found at '$DOCKER_CMD'" >&2
  exit 127
fi

if [[ ! -f "$COMPOSE_DIR/$COMPOSE_FILE" ]]; then
  echo "Error: compose file '$COMPOSE_DIR/$COMPOSE_FILE' not found" >&2
  exit 1
fi

cd "$COMPOSE_DIR"

compose_args=("-f" "$COMPOSE_FILE")
if [[ -n "$COMPOSE_PROJECT_NAME" ]]; then
  compose_args+=("-p" "$COMPOSE_PROJECT_NAME")
fi

"aws ecr get-login-password --region eu-central-1 | docker login --username AWS --password-stdin 249728805044.dkr.ecr.eu-central-1.amazonaws.com"
"$DOCKER_CMD" compose "${compose_args[@]}" pull
"$DOCKER_CMD" compose "${compose_args[@]}" up -d

echo "Deployment complete"
