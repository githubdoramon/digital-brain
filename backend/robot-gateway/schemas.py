"""Pydantic models for robot-gateway HTTP API and MQTT payloads."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Robot management
# ---------------------------------------------------------------------------

class RobotIn(BaseModel):
    robot_id: str
    name: str
    description: str | None = None
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RobotUpdateIn(BaseModel):
    name: str | None = None
    description: str | None = None
    tags: list[str] | None = None
    metadata: dict[str, Any] | None = None


class RobotOut(BaseModel):
    robot_id: str
    name: str
    description: str | None = None
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    last_seen_at: datetime | None = None
    registered_at: datetime
    updated_at: datetime
    modules: list[ModuleOut] = Field(default_factory=list)


class ModuleIn(BaseModel):
    module_id: str
    name: str
    module_type: str = "generic"
    capabilities: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ModuleUpdateIn(BaseModel):
    name: str | None = None
    module_type: str | None = None
    capabilities: list[str] | None = None
    metadata: dict[str, Any] | None = None


class ModuleOut(BaseModel):
    module_id: str
    robot_id: str
    name: str
    module_type: str
    status: str
    capabilities: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    last_seen_at: datetime | None = None
    registered_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Telemetry
# ---------------------------------------------------------------------------

class TelemetryPayload(BaseModel):
    """Schema for MQTT telemetry messages (JSON body the robot publishes)."""

    measured_at: datetime
    payload_type: str = "generic"
    data: dict[str, Any] = Field(default_factory=dict)


class TelemetryOut(BaseModel):
    id: int
    robot_id: str
    module_id: str
    measured_at: datetime
    received_at: datetime
    payload_type: str
    payload: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

class CommandIn(BaseModel):
    command_type: str
    payload: dict[str, Any] = Field(default_factory=dict)


class CommandOut(BaseModel):
    command_id: str
    robot_id: str
    module_id: str
    command_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    status: str
    created_at: datetime
    sent_at: datetime | None = None
    acked_at: datetime | None = None
    error: str | None = None
    created_by: str | None = None


# ---------------------------------------------------------------------------
# Status (MQTT payloads)
# ---------------------------------------------------------------------------

class ModuleStatusPayload(BaseModel):
    """Schema for MQTT module status messages."""

    status: Literal["online", "offline", "error"]
    detail: str | None = None


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

class HealthOut(BaseModel):
    mqtt_connected: bool
    db_reachable: bool
    subscribed_topics: list[str] = Field(default_factory=list)


class CaptureStreamRequest(BaseModel):
    robot_id: str
    module_id: str
    video: bool = True
    audio: bool = True
    requested_by_user_id: str | None = None


class CaptureStreamOut(BaseModel):
    session_id: str
    robot_id: str
    module_id: str
    created_at: datetime
    expires_at: datetime
    upstream_connected: bool
    viewer_count: int
    video_enabled: bool
    audio_enabled: bool
    last_video_frame_at: datetime | None = None
    last_audio_chunk_at: datetime | None = None
    last_upstream_activity_at: datetime | None = None
    requested_by_user_id: str | None = None
    last_error: str | None = None
    status: str
    video_meta: dict[str, Any] | None = None
    audio_meta: dict[str, Any] | None = None
    viewer_paths: dict[str, str] = Field(default_factory=dict)


# Forward reference resolution
RobotOut.model_rebuild()
