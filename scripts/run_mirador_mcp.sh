#!/bin/sh
set -eu

PROJECT_ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
MIRADOR_KEY="${MIRADOR_API_KEY:-}"
if [ -z "$MIRADOR_KEY" ]; then
  MIRADOR_SECRET_FILE="${HOME}/.config/digital-brain/mirador.key"
  if [ -r "$MIRADOR_SECRET_FILE" ]; then
    IFS= read -r MIRADOR_KEY < "$MIRADOR_SECRET_FILE" || true
  fi
fi
if [ -z "$MIRADOR_KEY" ]; then
  echo "Mirador MCP key is unavailable; set MIRADOR_API_KEY or install the local secret file." >&2
  exit 1
fi
export MIRADOR_API_KEY="$MIRADOR_KEY"
unset MIRADOR_KEY
exec /usr/bin/python3 "$PROJECT_ROOT/scripts/mirador_mcp.py"
