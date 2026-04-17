"""Command dispatch and query endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from auth import require_service_api_key
from commands import create_command, get_command, list_commands, mark_command_failed, mark_command_sent
from observability.logger import get_runtime_logger
from schemas import CommandIn, CommandOut

logger = get_runtime_logger(__name__)


def create_commands_router() -> APIRouter:
    router = APIRouter()

    @router.post(
        "/robots/{robot_id}/modules/{module_id}/commands",
        response_model=CommandOut,
        status_code=201,
    )
    async def send_command(
        robot_id: str,
        module_id: str,
        body: CommandIn,
        request: Request,
        _: None = Depends(require_service_api_key),
    ):
        try:
            cmd = create_command(
                robot_id=robot_id,
                module_id=module_id,
                command_type=body.command_type,
                payload=body.payload,
                created_by="middleware",
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc))

        # Publish to MQTT
        mqtt = request.app.state.mqtt
        try:
            await mqtt.publish_command(robot_id, module_id, {
                "command_id": cmd["command_id"],
                "command_type": cmd["command_type"],
                "payload": body.payload,
            })
            mark_command_sent(cmd["command_id"])
            cmd["status"] = "sent"
        except Exception as exc:
            logger.error("[commands] Failed to publish command %s: %s", cmd["command_id"], exc)
            mark_command_failed(cmd["command_id"], str(exc))
            cmd["status"] = "failed"
            cmd["error"] = str(exc)

        return cmd

    @router.get("/robots/{robot_id}/commands", response_model=list[CommandOut])
    def get_robot_commands(
        robot_id: str,
        limit: int = Query(default=100, ge=1, le=1000),
        offset: int = Query(default=0, ge=0),
        _: None = Depends(require_service_api_key),
    ):
        return list_commands(robot_id, limit=limit, offset=offset)

    @router.get("/commands/{command_id}", response_model=CommandOut)
    def get_command_status(
        command_id: str,
        _: None = Depends(require_service_api_key),
    ):
        cmd = get_command(command_id)
        if not cmd:
            raise HTTPException(status_code=404, detail="Command not found")
        return cmd

    return router
