"""Tests for MqttManager helper methods."""

from mqtt_client import MqttManager


class TestDecodePayload:
    """MqttManager._decode_payload is a static method — test directly."""

    def test_valid_json_bytes(self):
        result = MqttManager._decode_payload(b'{"temp": 22.5}')
        assert result == {"temp": 22.5}

    def test_valid_json_string(self):
        result = MqttManager._decode_payload('{"status": "online"}')
        assert result == {"status": "online"}

    def test_valid_json_bytearray(self):
        result = MqttManager._decode_payload(bytearray(b'{"x": 1}'))
        assert result == {"x": 1}

    def test_invalid_json_returns_raw(self):
        result = MqttManager._decode_payload(b"not json")
        assert result == {"raw": "not json"}

    def test_empty_bytes_returns_raw(self):
        result = MqttManager._decode_payload(b"")
        assert result == {"raw": ""}

    def test_nested_json(self):
        payload = b'{"measured_at": "2026-04-17T14:30:00Z", "data": {"accel": [1, 2, 3]}}'
        result = MqttManager._decode_payload(payload)
        assert result["data"]["accel"] == [1, 2, 3]

    def test_utf8_content(self):
        result = MqttManager._decode_payload('{"name": "robot-\u00e9"}'.encode("utf-8"))
        assert result["name"] == "robot-\u00e9"

    def test_malformed_utf8_does_not_crash(self):
        result = MqttManager._decode_payload(b"\xff\xfe bad bytes")
        assert "raw" in result
