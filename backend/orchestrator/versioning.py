from __future__ import annotations

import json
import os
from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any

from observability.logger import get_runtime_logger
from schemas import ServiceVersion, ServiceVersionCollection

logger = get_runtime_logger(__name__)

MANIFEST_PATH_ENV = "SERVICE_MANIFEST_PATH"
DEFAULT_MANIFEST_PATH = "/app/config/service_manifest.json"
ENV_PREFIX = "SERVICE_INFO__"


def _load_manifest() -> tuple[list[dict[str, Any]], str | None, dict[str, Any]]:
    """Load service definitions from a JSON manifest file if it exists."""
    manifest_path = os.getenv(MANIFEST_PATH_ENV, DEFAULT_MANIFEST_PATH)
    if not manifest_path:
        return [], None, {}

    try:
        with open(manifest_path, encoding="utf-8") as manifest_file:
            payload = json.load(manifest_file)
    except FileNotFoundError:
        logger.info("Service manifest not found at %s", manifest_path)
        return [], manifest_path, {}
    except json.JSONDecodeError as exc:
        logger.error("Unable to parse service manifest %s: %s", manifest_path, exc)
        return [], manifest_path, {"error": str(exc)}

    services = payload.get("services")
    if not isinstance(services, list):
        logger.warning("Service manifest %s is missing a 'services' array", manifest_path)
        services = []

    metadata = payload.get("metadata") or {}
    if payload.get("generated_at"):
        metadata.setdefault("generated_at", payload["generated_at"])

    return services, manifest_path, metadata


def _load_env_overrides() -> list[dict[str, Any]]:
    """Parse SERVICE_INFO__* environment variables that contain JSON payloads."""
    overrides: list[dict[str, Any]] = []

    for key, serialized in os.environ.items():
        if not key.startswith(ENV_PREFIX):
            continue

        service_id = key[len(ENV_PREFIX) :].strip()
        if not service_id:
            logger.warning("Skipping SERVICE_INFO__ entry with empty service id")
            continue

        try:
            payload = json.loads(serialized)
        except json.JSONDecodeError as exc:
            logger.error("Invalid JSON in %s: %s", key, exc)
            continue

        if not isinstance(payload, dict):
            logger.warning("SERVICE_INFO__ payload for %s must be a JSON object", service_id)
            continue

        payload.setdefault("id", service_id)
        overrides.append(payload)

    return overrides


def _fallback_services() -> list[dict[str, Any]]:
    """Provide coarse defaults so the API always returns something meaningful."""
    known_services: Iterable[tuple[str, str, str | None]] = (
        (
            "orchestrator",
            "Orchestrator API",
            os.getenv("ORCHESTRATOR_IMAGE", "appcalipse/digital-brain-orchestrator"),
        ),
        ("frontend", "Frontend Web", os.getenv("FRONTEND_IMAGE", "digital-brain-frontend")),
        ("db", "Postgres + pgvector", "pgvector/pgvector:pg16"),
    )

    fallback_entries: list[dict[str, Any]] = []
    for service_id, display_name, image in known_services:
        version_value = os.getenv(f"{service_id.upper()}_VERSION") or os.getenv(
            f"{service_id.upper()}_GIT_SHA"
        )
        build_time = os.getenv(f"{service_id.upper()}_BUILD_TIME")
        entry: dict[str, Any] = {
            "id": service_id,
            "name": display_name,
            "version": version_value or "unknown",
            "git_sha": os.getenv(f"{service_id.upper()}_GIT_SHA"),
            "build_time": build_time,
            "image": image,
            "notes": "Generated from fallback defaults; override via manifest or env.",
            "metadata": {
                "version_env": f"{service_id.upper()}_VERSION",
                "git_sha_env": f"{service_id.upper()}_GIT_SHA",
                "build_time_env": f"{service_id.upper()}_BUILD_TIME",
            },
        }
        fallback_entries.append(entry)

    return fallback_entries


def _merge_services(
    fallback: list[dict[str, Any]],
    manifest_entries: list[dict[str, Any]],
    env_overrides: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge service maps giving priority to manifest, then environment overrides."""
    registry: dict[str, dict[str, Any]] = {}

    def apply(entries: list[dict[str, Any]], source: str) -> None:
        for raw in entries:
            service_id = (
                raw.get("id") or raw.get("service_id") or raw.get("service") or raw.get("name")
            )
            if not service_id:
                logger.warning("Skipping service entry without id: %s", raw)
                continue

            normalized_id = str(service_id).strip()
            if not normalized_id:
                logger.warning("Skipping service entry with blank id: %s", raw)
                continue

            normalized_id = normalized_id.lower()
            entry = dict(raw)
            entry["id"] = normalized_id
            entry.setdefault("name", raw.get("name") or normalized_id.replace("_", " ").title())

            existing = registry.setdefault(normalized_id, {"id": normalized_id, "sources": []})

            metadata = entry.pop("metadata", None)
            if metadata and isinstance(metadata, dict):
                merged_metadata = existing.get("metadata", {}).copy()
                merged_metadata.update(metadata)
                existing["metadata"] = merged_metadata

            notes = entry.pop("notes", None)
            if notes:
                existing_notes = existing.get("notes")
                if existing_notes and existing_notes != notes:
                    combined_notes = f"{existing_notes} | {notes}"
                    existing["notes"] = combined_notes
                else:
                    existing["notes"] = notes

            sources = existing.setdefault("sources", [])
            if source not in sources:
                sources.append(source)

            for key, value in entry.items():
                if key in {"id", "sources"}:
                    continue
                # Prefer non-empty overrides
                if value is None or value == "":
                    continue
                existing[key] = value

    # Apply sources in priority order: fallback < manifest < environment overrides
    apply(fallback, "fallback")
    apply(manifest_entries, "manifest")
    apply(env_overrides, "environment")

    # Ensure orchestrator always appears with at least fallback data
    if "orchestrator" not in registry:
        registry["orchestrator"] = {
            "id": "orchestrator",
            "name": "Orchestrator API",
            "version": "unknown",
            "sources": ["implicit"],
            "notes": "Added by orchestrator process; no version information found.",
        }

    return list(registry.values())


def get_service_versions() -> ServiceVersionCollection:
    """Return the aggregated service version manifest."""
    manifest_services, manifest_path, manifest_metadata = _load_manifest()
    env_overrides = _load_env_overrides()
    fallback_services = _fallback_services()

    merged_services = _merge_services(fallback_services, manifest_services, env_overrides)

    models = [
        ServiceVersion(**service)
        for service in sorted(merged_services, key=lambda item: item.get("name", ""))
    ]

    return ServiceVersionCollection(
        generated_at=datetime.now(timezone.utc),
        services=models,
        manifest_path=manifest_path,
        manifest_metadata=manifest_metadata,
        env_entry_count=len(env_overrides),
        fallback_count=len(fallback_services),
    )
