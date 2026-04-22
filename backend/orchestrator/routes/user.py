from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

import devices as devices_service
import user_facts
import user_locations
from auth import get_current_user
from notifications.preferences import (
    get_notification_settings,
    get_push_settings,
    update_notification_channels,
    update_push_settings,
)
from observability.logger import get_runtime_logger
from schemas import (
    DeviceRegisterIn,
    NotificationSettingsListOut,
    NotificationSettingsOut,
    NotificationTypeChannelsUpdateIn,
    NotificationTypeSettingsOut,
    PushNotificationsUpdateIn,
    UserFactOut,
    UserFactUpdateIn,
    UserLocationOut,
    UserLocationUpdateIn,
)

logger = get_runtime_logger(__name__)


def create_user_router() -> APIRouter:
    router = APIRouter()

    @router.get("/mobile/settings", response_model=NotificationSettingsOut)
    def read_user_settings(user: dict = Depends(get_current_user)):
        email = user.get("email") or user.get("user_email")
        if not email:
            raise HTTPException(status_code=400, detail="User email is missing")
        return get_push_settings(email)

    @router.put("/mobile/settings/push-notifications", response_model=NotificationSettingsOut)
    def update_push_notifications(
        payload: PushNotificationsUpdateIn,
        user: dict = Depends(get_current_user),
    ):
        email = user.get("email") or user.get("user_email")
        if not email:
            raise HTTPException(status_code=400, detail="User email is missing")
        return update_push_settings(email, payload.enabled)

    @router.get("/mobile/settings/notifications", response_model=NotificationSettingsListOut)
    def read_notification_settings(user: dict = Depends(get_current_user)):
        email = user.get("email") or user.get("user_email")
        if not email:
            raise HTTPException(status_code=400, detail="User email is missing")
        return get_notification_settings(email)

    @router.put(
        "/mobile/settings/notifications/{notification_type}",
        response_model=NotificationTypeSettingsOut,
    )
    def update_notification_type_settings(
        notification_type: str,
        payload: NotificationTypeChannelsUpdateIn,
        user: dict = Depends(get_current_user),
    ):
        email = user.get("email") or user.get("user_email")
        if not email:
            raise HTTPException(status_code=400, detail="User email is missing")
        try:
            return update_notification_channels(email, notification_type, payload.channels)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/mobile/devices/register")
    def register_device(payload: DeviceRegisterIn, user: dict = Depends(get_current_user)):
        email = user.get("email") or user.get("user_email")
        if not email:
            raise HTTPException(status_code=400, detail="User email is missing")
        return devices_service.register_device(
            user_email=email,
            expo_push_token=payload.expo_push_token,
            platform=payload.platform,
            device_name=payload.device_name,
            app_version=payload.app_version,
            os_version=payload.os_version,
        )

    @router.delete("/mobile/devices/unregister")
    def unregister_device(
        expo_push_token: str = Query(..., alias="expoPushToken"),
        user: dict = Depends(get_current_user),
    ):
        email = user.get("email") or user.get("user_email")
        if not email:
            raise HTTPException(status_code=400, detail="User email is missing")
        deleted = devices_service.unregister_device(email, expo_push_token)
        if not deleted:
            raise HTTPException(status_code=404, detail="Device not found")
        return {"ok": True}

    @router.post("/mobile/location", response_model=UserLocationOut)
    def update_mobile_location(
        payload: UserLocationUpdateIn,
        request: Request,
        user: dict = Depends(get_current_user),
    ):
        email = user.get("email") or user.get("user_email")
        if not email:
            raise HTTPException(status_code=400, detail="User email is missing")
        logger.info(
            "[mobile/location] Received location update user=%s lat=%.6f lon=%.6f source=%s captured_at=%s debug_request_id=%s batch_id=%s sample_index=%s/%s app_state=%s",
            email,
            payload.lat,
            payload.lon,
            payload.source or "unknown",
            payload.captured_at.isoformat() if payload.captured_at else "none",
            request.headers.get("x-location-debug-request-id") or "none",
            request.headers.get("x-location-debug-batch-id") or "none",
            request.headers.get("x-location-debug-sample-index") or "none",
            request.headers.get("x-location-debug-sample-count") or "none",
            request.headers.get("x-location-debug-app-state") or "unknown",
        )
        return user_locations.upsert_user_location(
            user_email=email,
            lat=payload.lat,
            lon=payload.lon,
            accuracy_m=payload.accuracy_m,
            captured_at=payload.captured_at,
            source=payload.source,
            timezone_name=payload.timezone,
            place_name=payload.place_name,
            city=payload.city,
            country=payload.country,
            debug_context={
                "debug_request_id": request.headers.get("x-location-debug-request-id"),
                "batch_id": request.headers.get("x-location-debug-batch-id"),
                "sample_index": request.headers.get("x-location-debug-sample-index"),
                "sample_count": request.headers.get("x-location-debug-sample-count"),
                "client_captured_at": request.headers.get("x-location-debug-captured-at"),
                "app_state": request.headers.get("x-location-debug-app-state"),
            },
        )

    @router.get("/mobile/location", response_model=UserLocationOut)
    def read_mobile_location(user: dict = Depends(get_current_user)):
        email = user.get("email") or user.get("user_email")
        if not email:
            raise HTTPException(status_code=400, detail="User email is missing")
        location = user_locations.get_last_known_location(email)
        if not location:
            raise HTTPException(status_code=404, detail="Location not found")
        return location

    @router.get("/user/facts", response_model=list[UserFactOut])
    @router.get("/mobile/user/facts", response_model=list[UserFactOut])
    def list_user_facts(user: dict = Depends(get_current_user)):
        user_email = user.get("email")
        if not user_email:
            raise HTTPException(status_code=400, detail="Authenticated user email missing")
        facts = user_facts.get_user_facts(user_email)
        return [UserFactOut(**f) for f in facts]

    @router.put("/user/facts/{fact_id}", response_model=UserFactOut)
    @router.put("/mobile/user/facts/{fact_id}", response_model=UserFactOut)
    def update_user_fact(
        fact_id: str,
        payload: UserFactUpdateIn,
        user: dict = Depends(get_current_user),
    ):
        user_email = user.get("email")
        if not user_email:
            raise HTTPException(status_code=400, detail="Authenticated user email missing")

        existing = user_facts.get_fact(fact_id)
        if not existing or existing.get("user_email") != user_email:
            raise HTTPException(status_code=404, detail="Fact not found")

        try:
            updated = user_facts.update_fact(
                fact_id,
                content=payload.content,
                category=payload.category,
                importance=payload.importance,
                fact_mode=payload.fact_mode,
                rule_type=payload.rule_type,
                rule_scope=payload.rule_scope,
                rule_payload=payload.rule_payload,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not updated:
            raise HTTPException(status_code=404, detail="Fact not found")
        return UserFactOut(**updated)

    @router.delete("/user/facts/{fact_id}", status_code=204)
    @router.delete("/mobile/user/facts/{fact_id}", status_code=204)
    def delete_user_fact(fact_id: str, user: dict = Depends(get_current_user)):
        user_email = user.get("email")
        if not user_email:
            raise HTTPException(status_code=400, detail="Authenticated user email missing")

        existing = user_facts.get_fact(fact_id)
        if not existing or existing.get("user_email") != user_email:
            raise HTTPException(status_code=404, detail="Fact not found")

        user_facts.delete_fact(fact_id)
        return Response(status_code=204)

    return router
