#!/usr/bin/env python3
"""
Generate a service manifest that the orchestrator can expose via /system/versions.

Usage:
    python scripts/generate_service_manifest.py \
        --output backend/orchestrator/config/service_manifest.json
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_OUTPUT = Path("backend/orchestrator/config/service_manifest.json")

DIGITAL_BRAIN_SERVICES = {
    "orchestrator": {
        "name": "Orchestrator API",
        "image": "appcalipse/digital-brain-orchestrator",
    },
    "frontend": {
        "name": "Frontend Web",
        "image": "appcalipse/digital-brain-frontend",
    },
    "removed_service": {
        "name": "Removed Service Service",
        "image": "digital-brain-removed-service",
    },
}

THIRD_PARTY_SERVICES = {
    "db": {
        "name": "Postgres + pgvector",
        "image": "pgvector/pgvector:pg16",
        "version": "pg16",
    },
    "qdrant": {
        "name": "Qdrant Vector DB",
        "image": "qdrant/qdrant:latest",
        "version": "latest",
    },
}


def _git(*args: str) -> Optional[str]:
    try:
        return (
            subprocess.check_output(["git", *args], stderr=subprocess.DEVNULL)
            .decode("utf-8")
            .strip()
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None


def _build_digital_brain_entries(build_version: str, git_sha: str, built_at: str) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    for service_id, info in DIGITAL_BRAIN_SERVICES.items():
        entry = {
            "id": service_id,
            "name": info["name"],
            "version": build_version,
            "git_sha": git_sha,
            "build_time": built_at,
            "image": info["image"],
        }
        entries.append(entry)
    return entries


def _build_third_party_entries() -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    for service_id, info in THIRD_PARTY_SERVICES.items():
        entry = {
            "id": service_id,
            "name": info["name"],
            "version": info.get("version", "unknown"),
            "image": info["image"],
        }
        entries.append(entry)
    return entries


def generate_manifest(output: Path, deployment: Optional[str] = None) -> None:
    git_sha = _git("rev-parse", "HEAD") or "unknown"
    git_description = _git("describe", "--tags", "--dirty", "--always") or git_sha
    build_timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    services: List[Dict[str, Any]] = []
    services.extend(_build_digital_brain_entries(git_description, git_sha, build_timestamp))
    services.extend(_build_third_party_entries())

    manifest = {
        "generated_at": build_timestamp,
        "metadata": {
            "git_sha": git_sha,
            "git_description": git_description,
            "deployment": deployment,
        },
        "services": services,
    }

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Wrote service manifest with {len(services)} entries to {output}")


def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(description="Generate orchestrator service manifest JSON.")
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Destination path (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--deployment",
        type=str,
        default=None,
        help="Optional deployment identifier recorded in the manifest metadata.",
    )

    args = parser.parse_args(argv)
    generate_manifest(args.output, args.deployment)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

