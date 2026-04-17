"""Telemetry query endpoints."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query

from auth import require_service_api_key
from schemas import TelemetryOut
from telemetry import get_latest_telemetry, query_telemetry


def create_telemetry_router() -> APIRouter:
    router = APIRouter()

    @router.get("/robots/{robot_id}/telemetry", response_model=list[TelemetryOut])
    def get_robot_telemetry(
        robot_id: str,
        since: datetime | None = None,
        until: datetime | None = None,
        payload_type: str | None = None,
        limit: int = Query(default=100, ge=1, le=1000),
        offset: int = Query(default=0, ge=0),
        _: None = Depends(require_service_api_key),
    ):
        return query_telemetry(
            robot_id,
            since=since,
            until=until,
            payload_type=payload_type,
            limit=limit,
            offset=offset,
        )

    @router.get(
        "/robots/{robot_id}/modules/{module_id}/telemetry",
        response_model=list[TelemetryOut],
    )
    def get_module_telemetry(
        robot_id: str,
        module_id: str,
        since: datetime | None = None,
        until: datetime | None = None,
        payload_type: str | None = None,
        limit: int = Query(default=100, ge=1, le=1000),
        offset: int = Query(default=0, ge=0),
        _: None = Depends(require_service_api_key),
    ):
        return query_telemetry(
            robot_id,
            module_id,
            since=since,
            until=until,
            payload_type=payload_type,
            limit=limit,
            offset=offset,
        )

    @router.get(
        "/robots/{robot_id}/modules/{module_id}/telemetry/latest",
        response_model=TelemetryOut,
    )
    def get_module_latest_telemetry(
        robot_id: str,
        module_id: str,
        _: None = Depends(require_service_api_key),
    ):
        result = get_latest_telemetry(robot_id, module_id)
        if not result:
            raise HTTPException(status_code=404, detail="No telemetry found")
        return result

    return router
