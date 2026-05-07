"""MQTT topic parsing and building helpers."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ParsedTopic:
    robot_id: str
    module_id: str | None
    message_type: str  # "telemetry", "status", "command/ack"


def parse_topic(topic: str) -> ParsedTopic | None:
    """Parse an MQTT topic string into structured components.

    Supported patterns:
        robot/{robot_id}/module/{module_id}/telemetry
        robot/{robot_id}/module/{module_id}/status
        robot/{robot_id}/module/{module_id}/command/ack
    """
    parts = topic.split("/")

    if len(parts) < 3 or parts[0] != "robot":
        return None

    robot_id = parts[1]

    # robot/{robot_id}/module/{module_id}/...
    if len(parts) >= 5 and parts[2] == "module":
        module_id = parts[3]
        remaining = "/".join(parts[4:])

        if remaining in ("telemetry", "status", "command/ack"):
            return ParsedTopic(
                robot_id=robot_id,
                module_id=module_id,
                message_type=remaining,
            )

    return None


def build_command_topic(robot_id: str, module_id: str) -> str:
    """Build the MQTT topic for sending a command to a robot module."""
    return f"robot/{robot_id}/module/{module_id}/command"
