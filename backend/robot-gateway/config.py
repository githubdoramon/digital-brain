"""Centralized environment variable loading for robot-gateway."""

from __future__ import annotations

import os


def _env_flag(name: str, *, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


# PostgreSQL
POSTGRES_USER = os.getenv("POSTGRES_USER")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD")
POSTGRES_DB = os.getenv("POSTGRES_DB")
POSTGRES_HOST = os.getenv("POSTGRES_HOST")
POSTGRES_PORT = os.getenv("POSTGRES_PORT")
POSTGRES_SCHEMA = (os.getenv("POSTGRES_SCHEMA") or "public").strip()

# MQTT broker
MQTT_BROKER_HOST = os.getenv("MQTT_BROKER_HOST", "mosquitto")
MQTT_BROKER_PORT = int(os.getenv("MQTT_BROKER_PORT", "1883"))
MQTT_USERNAME = os.getenv("MQTT_USERNAME", "")
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD", "")

# Service-to-service auth
ORCHESTRATOR_API_KEY = os.getenv("ORCHESTRATOR_API_KEY")

# Gateway HTTP port (used in docker CMD, referenced here for completeness)
ROBOT_GATEWAY_PORT = int(os.getenv("ROBOT_GATEWAY_PORT", "8001"))

# Migrations
ROBOT_GATEWAY_AUTO_MIGRATE = _env_flag("ROBOT_GATEWAY_AUTO_MIGRATE", default=True)
ROBOT_GATEWAY_AUTO_MIGRATE_FAIL_FAST = _env_flag(
    "ROBOT_GATEWAY_AUTO_MIGRATE_FAIL_FAST", default=True
)
