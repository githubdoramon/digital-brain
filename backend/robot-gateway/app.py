"""Robot Gateway — FastAPI entry point."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from capture_relay import CaptureRelayManager
from commands import handle_command_ack
from db import get_conn
from db_migrations import run_pending_migrations
from mqtt_client import MqttManager
from observability.logger import configure_logging, get_runtime_logger
from routes.capture import create_capture_router
from routes.commands import create_commands_router
from routes.health import create_health_router
from routes.robots import create_robots_router
from routes.telemetry import create_telemetry_router
from telemetry import handle_status, handle_telemetry

logger = get_runtime_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    logger.info("[app] Starting robot-gateway...")

    # 1. Run migrations
    try:
        run_pending_migrations()
    except Exception:
        from config import ROBOT_GATEWAY_AUTO_MIGRATE_FAIL_FAST

        if ROBOT_GATEWAY_AUTO_MIGRATE_FAIL_FAST:
            raise
        logger.exception("Database migration failed; continuing startup")

    # 2. Verify DB connectivity
    with get_conn():
        pass
    logger.info("[app] Database connection verified")

    # 3. Start MQTT client
    mqtt = MqttManager()
    mqtt.register_handler("telemetry", handle_telemetry)
    mqtt.register_handler("status", handle_status)
    mqtt.register_handler("command/ack", handle_command_ack)
    app.state.mqtt = mqtt

    capture_relay = CaptureRelayManager()
    app.state.capture_relay = capture_relay

    mqtt_task = asyncio.create_task(mqtt.run())
    await capture_relay.start(mqtt)
    logger.info("[app] MQTT client started")

    yield

    # 4. Shutdown
    await capture_relay.stop(mqtt)
    mqtt_task.cancel()
    with suppress(asyncio.CancelledError):
        await mqtt_task
    logger.info("[app] Shutdown complete")


api = FastAPI(
    title="Robot Gateway",
    version="0.1",
    lifespan=lifespan,
)

api.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routes
api.include_router(create_health_router())
api.include_router(create_robots_router())
api.include_router(create_telemetry_router())
api.include_router(create_commands_router())
api.include_router(create_capture_router())
