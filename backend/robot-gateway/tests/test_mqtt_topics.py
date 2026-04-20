"""Tests for MQTT topic parsing and building."""

from mqtt_topics import ParsedTopic, build_command_topic, parse_topic

# ---------------------------------------------------------------------------
# parse_topic — valid patterns
# ---------------------------------------------------------------------------


class TestParseTopicModuleTelemetry:
    def test_basic(self):
        result = parse_topic("robot/r1/module/imu/telemetry")
        assert result == ParsedTopic(robot_id="r1", module_id="imu", message_type="telemetry")

    def test_complex_ids(self):
        result = parse_topic("robot/robot-001/module/temp-sensor-3/telemetry")
        assert result == ParsedTopic(
            robot_id="robot-001", module_id="temp-sensor-3", message_type="telemetry"
        )


class TestParseTopicModuleStatus:
    def test_basic(self):
        result = parse_topic("robot/r1/module/motor/status")
        assert result == ParsedTopic(robot_id="r1", module_id="motor", message_type="status")


class TestParseTopicCommandAck:
    def test_basic(self):
        result = parse_topic("robot/r1/module/arm/command/ack")
        assert result == ParsedTopic(robot_id="r1", module_id="arm", message_type="command/ack")

    def test_ack_is_two_segments(self):
        """command/ack is joined from two path segments — verify it works."""
        result = parse_topic("robot/bot-x/module/gripper/command/ack")
        assert result is not None
        assert result.message_type == "command/ack"


class TestParseTopicRobotStatus:
    def test_basic(self):
        result = parse_topic("robot/r1/status")
        assert result == ParsedTopic(robot_id="r1", module_id=None, message_type="status")

    def test_module_id_is_none(self):
        result = parse_topic("robot/r1/status")
        assert result.module_id is None


# ---------------------------------------------------------------------------
# parse_topic — invalid / unrecognized
# ---------------------------------------------------------------------------


class TestParseTopicRejectsInvalid:
    def test_empty_string(self):
        assert parse_topic("") is None

    def test_wrong_prefix(self):
        assert parse_topic("device/r1/module/imu/telemetry") is None

    def test_too_short(self):
        assert parse_topic("robot/r1") is None

    def test_unknown_message_type(self):
        assert parse_topic("robot/r1/module/imu/logs") is None

    def test_command_without_ack(self):
        """robot/{id}/module/{mod}/command is the publish topic, not subscribed."""
        assert parse_topic("robot/r1/module/imu/command") is None

    def test_robot_level_telemetry_not_supported(self):
        """Telemetry must come from a module, not the robot itself."""
        assert parse_topic("robot/r1/telemetry") is None

    def test_extra_segments_after_telemetry(self):
        assert parse_topic("robot/r1/module/imu/telemetry/extra") is None

    def test_module_media_topic_not_yet_handled(self):
        # Planned future topic for binary media under the module path
        assert parse_topic("robot/r1/module/cam0/media") is None

    def test_missing_module_id(self):
        assert parse_topic("robot/r1/module//telemetry") is not None  # empty string is valid ID

    def test_totally_unrelated(self):
        assert parse_topic("home/sensors/temperature") is None


# ---------------------------------------------------------------------------
# build_command_topic
# ---------------------------------------------------------------------------


class TestBuildCommandTopic:
    def test_basic(self):
        assert build_command_topic("r1", "arm") == "robot/r1/module/arm/command"

    def test_complex_ids(self):
        assert (
            build_command_topic("robot-001", "motor-left")
            == "robot/robot-001/module/motor-left/command"
        )

    def test_roundtrip_not_parseable(self):
        """The command topic is for publishing, not subscribing — parse should reject it."""
        topic = build_command_topic("r1", "arm")
        assert parse_topic(topic) is None
