"""Robot Gateway — FastAPI entry point."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from commands import handle_command_ack
from db import get_conn
from db_migrations import run_pending_migrations
from mqtt_client import MqttManager
from observability.logger import configure_logging, get_runtime_logger
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

    mqtt_task = asyncio.create_task(mqtt.run())
    logger.info("[app] MQTT client started")

    yield

    # 4. Shutdown
    mqtt_task.cancel()
    try:
        await mqtt_task
    except asyncio.CancelledError:
        pass
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
