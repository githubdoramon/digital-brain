from __future__ import annotations

import io

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field, SecretStr

import messaging_archive as archive
import whatsapp_history
import whatsapp_runtime
from auth import get_current_user
from messaging_normalization import Provider
from telegram_native import configured


class ConnectionUpdate(BaseModel):
    enabled: bool


class ClassificationUpdate(BaseModel):
    context: archive.Context
    conversation_id: str | None = None


class TelegramPassword(BaseModel):
    challenge: str = Field(min_length=1, max_length=128)
    password: SecretStr = Field(min_length=1, max_length=256)


class IdentityLink(BaseModel):
    contact_id: str | None = Field(default=None, max_length=256)


class AnnouncementGroup(BaseModel):
    conversation_id: str
    announcement_only: bool


def _owner(user: dict) -> str:
    owner = user.get("email") or user.get("user_email")
    if not owner:
        raise HTTPException(401, "Sign in to continue.")
    return owner


def create_messaging_router() -> APIRouter:
    router = APIRouter(prefix="/messaging", tags=["messaging"])

    @router.put("/analysis")
    def analysis(payload: AnnouncementGroup, provider: Provider = Provider.WHATSAPP,
                 user: dict = Depends(get_current_user)):
        from db import get_conn

        with get_conn() as conn, conn.cursor() as cur:
            cur.execute("""UPDATE messaging_conversations c SET analysis_excluded=%s
                FROM messaging_accounts a WHERE c.account_id=a.account_id
                AND a.user_email=%s AND a.provider=%s AND c.conversation_id=%s AND c.kind='group'""",
                        (payload.announcement_only, _owner(user), provider.value, payload.conversation_id))
            if not cur.rowcount:
                raise HTTPException(404, "Group not found.")
            conn.commit()
        return {"ok": True}

    @router.get("/status")
    def status(
        response: Response,
        provider: Provider = Provider.WHATSAPP,
        user: dict = Depends(get_current_user),
    ):
        response.headers["Cache-Control"] = "no-store"
        owner = _owner(user)
        row = archive.account(owner, provider)
        return {
            "enabled": row["enabled"],
            "state": row["state"],
            "context": row["context"],
            "error_code": row.get("error_code"),
            "last_capture_at": row.get("last_capture_at"),
            **archive.counts(owner, provider),
            **(
                whatsapp_history.counts(owner)
                if provider == Provider.WHATSAPP
                else {"history_pending": 0, "history_stalled": 0}
            ),
            "provider": provider.value,
            "configured": provider == Provider.WHATSAPP or configured(),
            "password_challenge": whatsapp_runtime.password_challenge(row.get("account_id", "")),
            "capture_failures": row.get("capture_failures", 0),
            "history_coverage": "observed_only",
        }

    @router.put("/connection")
    def connection(
        payload: ConnectionUpdate,
        provider: Provider = Provider.WHATSAPP,
        user: dict = Depends(get_current_user),
    ):
        owner = _owner(user)
        old = archive.account(owner, provider)
        if payload.enabled and provider == Provider.TELEGRAM and not configured():
            raise HTTPException(409, "Telegram connection setup is required.")
        if old["enabled"] != payload.enabled:
            archive.set_connection(owner, payload.enabled, provider)
            if old.get("account_id"):
                whatsapp_runtime.clear_qr(old["account_id"])
        return {"ok": True}

    @router.post("/telegram/password")
    def telegram_password(payload: TelegramPassword, user: dict = Depends(get_current_user)):
        row = archive.account(_owner(user), Provider.TELEGRAM)
        if not row["enabled"] or not row.get("account_id"):
            raise HTTPException(409, "Connect Telegram first.")
        try:
            whatsapp_runtime.submit_password(
                row["account_id"],
                row["generation"],
                payload.challenge,
                payload.password.get_secret_value(),
            )
        except LookupError:
            raise HTTPException(409, "Login expired. Link Telegram again.")
        return {"ok": True}

    @router.get("/pairing")
    def pairing(
        response: Response,
        provider: Provider = Provider.WHATSAPP,
        user: dict = Depends(get_current_user),
    ):
        response.headers["Cache-Control"] = "no-store"
        row = archive.account(_owner(user), provider)
        code = whatsapp_runtime.pairing_qr(row.get("account_id", "")) if row["enabled"] else None
        if not code:
            return {"svg": None}
        import qrcode
        import qrcode.image.svg

        image = qrcode.make(code, image_factory=qrcode.image.svg.SvgPathImage)
        output = io.BytesIO()
        image.save(output)
        return {"svg": output.getvalue().decode()}

    @router.put("/context")
    def context(
        payload: ClassificationUpdate,
        provider: Provider = Provider.WHATSAPP,
        user: dict = Depends(get_current_user),
    ):
        try:
            archive.set_context(_owner(user), payload.context, payload.conversation_id, provider)
        except LookupError:
            raise HTTPException(404, "Conversation not found.")
        return {"ok": True}

    @router.get("/identities")
    def identities(
        contact_id: str | None = None,
        provider: Provider = Provider.WHATSAPP,
        user: dict = Depends(get_current_user),
    ):
        return {"identities": archive.identities(_owner(user), contact_id, provider)}

    @router.put("/identities/{identity_id}/contact")
    def link(
        identity_id: str,
        payload: IdentityLink,
        provider: Provider = Provider.WHATSAPP,
        user: dict = Depends(get_current_user),
    ):
        try:
            archive.link_identity(_owner(user), identity_id, payload.contact_id, provider)
        except LookupError:
            raise HTTPException(404, "Identity or contact not found.")
        return {"ok": True}

    @router.get("/conversations")
    def conversations(
        provider: Provider = Provider.WHATSAPP, user: dict = Depends(get_current_user)
    ):
        return {"conversations": archive.conversations(_owner(user), provider)}

    @router.post("/retry")
    def retry(provider: Provider = Provider.WHATSAPP, user: dict = Depends(get_current_user)):
        archive.retry_pending(_owner(user), provider)
        if provider == Provider.WHATSAPP:
            whatsapp_history.retry(_owner(user))
        return {"ok": True}

    @router.get("/messages")
    def messages(
        q: str = Query("", max_length=512),
        conversation_id: str | None = None,
        before_id: str | None = Query(None, max_length=64),
        limit: int = Query(30, ge=1, le=100),
        provider: Provider = Provider.WHATSAPP,
        user: dict = Depends(get_current_user),
    ):
        if before_id and (q or not conversation_id):
            raise HTTPException(422, "Choose a conversation to browse older messages.")
        return {
            "messages": archive.search(
                _owner(user),
                q,
                conversation_id=conversation_id,
                limit=limit,
                before_id=before_id,
                provider=provider,
            )
        }

    @router.get("/messages/{message_id}")
    def message(
        message_id: str,
        provider: Provider = Provider.WHATSAPP,
        user: dict = Depends(get_current_user),
    ):
        try:
            return archive.message_context(_owner(user), message_id, provider)
        except LookupError:
            raise HTTPException(404, "Message not found.")

    return router
