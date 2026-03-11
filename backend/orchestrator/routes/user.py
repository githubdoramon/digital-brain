from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response

import devices as devices_service
import user_facts
from auth import get_current_user
from notifications.preferences import get_push_settings, update_push_settings
from schemas import (
    DeviceRegisterIn,
    NotificationSettingsOut,
    PushNotificationsUpdateIn,
    UserFactOut,
    UserFactUpdateIn,
)


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

        updated = user_facts.update_fact(
            fact_id,
            content=payload.content,
            category=payload.category,
            importance=payload.importance,
        )
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
