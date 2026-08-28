#!/usr/bin/env bash

set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_root"

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

resolve_google_services_file() {
  if [[ -n "${GOOGLE_SERVICES_FILE:-}" ]]; then
    if [[ "$GOOGLE_SERVICES_FILE" = /* ]]; then
      printf '%s\n' "$GOOGLE_SERVICES_FILE"
    else
      printf '%s\n' "$project_root/$GOOGLE_SERVICES_FILE"
    fi
    return
  fi

  if [[ -f "$project_root/google-services.json" ]]; then
    printf '%s\n' "$project_root/google-services.json"
    return
  fi

  local git_common_dir source_checkout candidate
  git_common_dir="$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null || true)"
  if [[ -n "$git_common_dir" ]]; then
    source_checkout="$(dirname "$git_common_dir")"
    candidate="$source_checkout/mobile/google-services.json"
    if [[ -f "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return
    fi
  fi
}

GOOGLE_SERVICES_FILE="$(resolve_google_services_file)"
if [[ -z "$GOOGLE_SERVICES_FILE" || ! -f "$GOOGLE_SERVICES_FILE" ]]; then
  echo "Missing Android Firebase config for the local EAS build." >&2
  echo "Set GOOGLE_SERVICES_FILE in mobile/.env to an existing absolute or project-relative google-services.json path." >&2
  exit 1
fi

export NODE_ENV=production
export APP_VARIANT=production
export GOOGLE_SERVICES_FILE

exec eas build -p android --profile preview --local
