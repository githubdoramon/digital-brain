"""Tests for Pydantic schema validation."""

import pytest
from pydantic import ValidationError

from schemas import (
    CommandIn,
    ModuleIn,
    ModuleStatusPayload,
    RobotIn,
    RobotUpdateIn,
    TelemetryPayload,
)

# ---------------------------------------------------------------------------
# TelemetryPayload — the schema robots publish to MQTT
# ---------------------------------------------------------------------------


class TestTelemetryPayload:
    def test_valid_full(self):
        msg = TelemetryPayload(
            measured_at="2026-04-17T14:30:00Z",
            payload_type="temperature",
            data={"temperature_c": 22.5, "humidity_pct": 45},
        )
        assert msg.payload_type == "temperature"
        assert msg.data["temperature_c"] == 22.5

    def test_defaults(self):
        msg = TelemetryPayload(measured_at="2026-04-17T14:30:00Z")
        assert msg.payload_type == "generic"
        assert msg.data == {}

    def test_missing_measured_at_rejects(self):
        with pytest.raises(ValidationError):
            TelemetryPayload(payload_type="imu", data={})

    def test_nested_data(self):
        msg = TelemetryPayload(
            measured_at="2026-04-17T14:30:00Z",
            payload_type="imu",
            data={
                "accel": [0.01, -9.8, 0.02],
                "gyro": [0.0, 0.0, 0.1],
                "mag": {"x": 12.3, "y": -4.5, "z": 33.0},
            },
        )
        assert len(msg.data["accel"]) == 3
        assert msg.data["mag"]["z"] == 33.0

    def test_iso_datetime_variants(self):
        """Pydantic should accept various ISO datetime formats."""
        for ts in [
            "2026-04-17T14:30:00Z",
            "2026-04-17T14:30:00+00:00",
            "2026-04-17T14:30:00.123Z",
            "2026-04-17 14:30:00",
        ]:
            msg = TelemetryPayload(measured_at=ts)
            assert msg.measured_at is not None


# ---------------------------------------------------------------------------
# RobotIn
# ---------------------------------------------------------------------------


class TestRobotIn:
    def test_minimal(self):
        r = RobotIn(robot_id="r1", name="Test Bot")
        assert r.tags == []
        assert r.metadata == {}
        assert r.description is None

    def test_full(self):
        r = RobotIn(
            robot_id="r1",
            name="Test Bot",
            description="A test robot",
            tags=["mobile", "outdoor"],
            metadata={"firmware": "1.2.3"},
        )
        assert r.tags == ["mobile", "outdoor"]
        assert r.metadata["firmware"] == "1.2.3"

    def test_missing_required_rejects(self):
        with pytest.raises(ValidationError):
            RobotIn(robot_id="r1")  # missing name

        with pytest.raises(ValidationError):
            RobotIn(name="Bot")  # missing robot_id


# ---------------------------------------------------------------------------
# RobotUpdateIn — all fields optional
# ---------------------------------------------------------------------------


class TestRobotUpdateIn:
    def test_empty_is_valid(self):
        u = RobotUpdateIn()
        assert u.model_dump(exclude_none=True) == {}

    def test_partial_update(self):
        u = RobotUpdateIn(name="New Name")
        dumped = u.model_dump(exclude_none=True)
        assert dumped == {"name": "New Name"}
        assert "description" not in dumped


# ---------------------------------------------------------------------------
# ModuleIn
# ---------------------------------------------------------------------------


class TestModuleIn:
    def test_defaults(self):
        m = ModuleIn(module_id="imu", name="IMU Sensor")
        assert m.module_type == "generic"
        assert m.capabilities == []

    def test_with_capabilities(self):
        m = ModuleIn(
            module_id="cam0",
            name="Front Camera",
            module_type="camera",
            capabilities=["rgb", "depth", "ir"],
        )
        assert m.module_type == "camera"
        assert "depth" in m.capabilities


# ---------------------------------------------------------------------------
# CommandIn
# ---------------------------------------------------------------------------


class TestCommandIn:
    def test_minimal(self):
        c = CommandIn(command_type="stop")
        assert c.payload == {}

    def test_with_payload(self):
        c = CommandIn(
            command_type="move",
            payload={"x": 1.0, "y": 2.0, "speed": 0.5},
        )
        assert c.payload["speed"] == 0.5

    def test_missing_type_rejects(self):
        with pytest.raises(ValidationError):
            CommandIn(payload={"x": 1})


# ---------------------------------------------------------------------------
# ModuleStatusPayload
# ---------------------------------------------------------------------------


class TestModuleStatusPayload:
    def test_minimal(self):
        s = ModuleStatusPayload(status="online")
        assert s.detail is None

    def test_with_detail(self):
        s = ModuleStatusPayload(status="error", detail="Motor overheated")
        assert s.detail == "Motor overheated"
