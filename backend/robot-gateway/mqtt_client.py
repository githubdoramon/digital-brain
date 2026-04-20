"""MQTT client manager — connects, subscribes, dispatches, and publishes."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Callable, Coroutine

import aiomqtt

from config import MQTT_BROKER_HOST, MQTT_BROKER_PORT, MQTT_USERNAME, MQTT_PASSWORD
from mqtt_topics import build_command_topic, parse_topic
from observability.logger import get_runtime_logger

logger = get_runtime_logger(__name__)

# Type alias for message handlers
MessageHandler = Callable[[str, str | None, dict[str, Any]], Coroutine[Any, Any, None]]

# Wildcard subscriptions the gateway listens to.
# The "robot/#" catch-all is diagnostic — it ensures we log ANY message under
# the robot namespace, even if it doesn't match our structured patterns. This
# makes it obvious when firmware publishes to an unexpected topic.
_SUBSCRIPTIONS = [
    ("robot/+/module/+/telemetry", 0),   # QoS 0 for high-frequency telemetry
    ("robot/+/module/+/status", 1),       # QoS 1 for reliable status
    ("robot/+/module/+/command/ack", 1),  # QoS 1 for reliable ACKs
    ("robot/+/status", 1),                # QoS 1 for robot-level status
    ("robot/#", 0),                       # Diagnostic: catch all robot/* traffic
]


class MqttManager:
    """Manages the MQTT connection lifecycle, subscriptions, and message dispatch."""

    def __init__(self) -> None:
        self.connected: bool = False
        self.subscriptions: list[str] = []
        self._handlers: dict[str, MessageHandler] = {}
        self._client: aiomqtt.Client | None = None
        self._client_lock = asyncio.Lock()

    def register_handler(self, message_type: str, handler: MessageHandler) -> None:
        """Register a handler for a specific message type (e.g. 'telemetry', 'status')."""
        self._handlers[message_type] = handler

    async def run(self) -> None:
        """Main MQTT loop with automatic reconnection."""
        if not MQTT_USERNAME or not MQTT_PASSWORD:
            raise RuntimeError(
                "MQTT_USERNAME and MQTT_PASSWORD must be set — "
                "the broker requires authentication"
            )

        reconnect_delay = 5
        while True:
            try:
                async with aiomqtt.Client(
                    hostname=MQTT_BROKER_HOST,
                    port=MQTT_BROKER_PORT,
                    username=MQTT_USERNAME,
                    password=MQTT_PASSWORD,
                ) as client:
                    self._client = client
                    self.connected = True
                    reconnect_delay = 5
                    logger.info("[mqtt] Connected to %s:%d", MQTT_BROKER_HOST, MQTT_BROKER_PORT)

                    await self._subscribe_all(client)

                    async for message in client.messages:
                        try:
                            await self._dispatch(message)
                        except Exception:
                            logger.exception("[mqtt] Error dispatching message on topic %s", message.topic)

            except aiomqtt.MqttError as exc:
                self.connected = False
                self._client = None
                logger.warning("[mqtt] Disconnected (%s), reconnecting in %ds...", exc, reconnect_delay)
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, 60)
            except asyncio.CancelledError:
                self.connected = False
                self._client = None
                logger.info("[mqtt] Client loop cancelled, shutting down")
                raise

    async def _subscribe_all(self, client: aiomqtt.Client) -> None:
        topics = []
        for topic, qos in _SUBSCRIPTIONS:
            await client.subscribe(topic, qos=qos)
            topics.append(topic)
        self.subscriptions = topics
        logger.info("[mqtt] Subscribed to %d topic(s)", len(topics))

    async def _dispatch(self, message: aiomqtt.Message) -> None:
        topic = str(message.topic)
        raw_bytes = message.payload if isinstance(message.payload, (bytes, bytearray)) else str(message.payload).encode("utf-8")
        logger.info("[mqtt.recv] topic=%s size=%d bytes", topic, len(raw_bytes))

        parsed = parse_topic(topic)
        if not parsed:
            logger.warning(
                "[mqtt.recv] REJECTED topic=%s reason=unrecognized_pattern "
                "(expected robot/{id}/module/{mod}/{telemetry|status|command/ack} or robot/{id}/status)",
                topic,
            )
            return

        handler = self._handlers.get(parsed.message_type)
        if not handler:
            logger.warning(
                "[mqtt.recv] REJECTED topic=%s reason=no_handler message_type=%s",
                topic, parsed.message_type,
            )
            return

        payload = self._decode_payload(message.payload)
        if "raw" in payload and len(payload) == 1:
            logger.warning(
                "[mqtt.recv] payload is not valid JSON topic=%s preview=%r",
                topic, payload["raw"][:200] if isinstance(payload["raw"], str) else payload["raw"],
            )

        logger.info(
            "[mqtt.recv] DISPATCH topic=%s robot_id=%s module_id=%s type=%s",
            topic, parsed.robot_id, parsed.module_id, parsed.message_type,
        )
        await handler(parsed.robot_id, parsed.module_id, payload)

    @staticmethod
    def _decode_payload(raw: bytes | bytearray | str) -> dict[str, Any]:
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode("utf-8", errors="replace")
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {"raw": raw}

    async def publish_command(
        self, robot_id: str, module_id: str, payload: dict[str, Any]
    ) -> None:
        """Publish a command to a robot module via MQTT."""
        async with self._client_lock:
            if not self._client or not self.connected:
                raise RuntimeError("MQTT client is not connected")
            topic = build_command_topic(robot_id, module_id)
            await self._client.publish(
                topic,
                payload=json.dumps(payload).encode("utf-8"),
                qos=1,
            )
            logger.info("[mqtt] Published command to %s", topic)
