"""Health check endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Request

from db import get_conn
from schemas import HealthOut


def create_health_router() -> APIRouter:
    router = APIRouter()

    @router.get("/health", response_model=HealthOut)
    def health_check(request: Request):
        mqtt = request.app.state.mqtt

        db_ok = False
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
                db_ok = True
        except Exception:
            pass

        return HealthOut(
            mqtt_connected=mqtt.connected,
            db_reachable=db_ok,
            subscribed_topics=mqtt.subscriptions,
        )

    return router
