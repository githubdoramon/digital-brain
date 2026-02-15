from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from datetime import date, datetime
from time import perf_counter
from typing import Any

from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse

import action_logs
import contacts as contacts_service
import conversations
import daily_briefings
import devices as devices_service
import documents
import events as events_service
import immich_client
import llm
import news_feeds
import places as places_service
import skills
import telegram_bot
import todos as todos_service
from agent.state import AgentState
from auth import get_current_user
from commands.event import (
    confirm_event_command as confirm_event_command_impl,
)
from commands.event import (
    event_pending_key,
    handle_pending_event,
)
from db import get_conn
from notifications.preferences import get_push_settings, update_push_settings
from observability.log_stream import (
    LOG_LEVELS,
    configure_logging,
    get_log_buffer,
    install_stdout_logger,
)
from observability.logger import get_runtime_logger
from schemas import (
    AskIn,
    AskOut,
    ContactIn,
    ContactMergeIn,
    ContactRelationshipIn,
    DailyBriefingIn,
    DailyBriefingOut,
    DeviceRegisterIn,
    DocumentCollection,
    DocumentDetailOut,
    DocumentSearchIn,
    DocumentUpdateIn,
    EventCommandConfirmation,
    EventCommandResult,
    EventIn,
    ExternalContactWebhook,
    ExternalEventPayload,
    MeetingIn,
    NewsTopicIn,
    NotificationSettingsOut,
    PlaceIn,
    PushNotificationsUpdateIn,
    ServiceVersionCollection,
    ThreadCreate,
    ThreadDetailOut,
    ThreadOut,
    ThreadUpdate,
    TodoIn,
    TodoStatusUpdateIn,
    ToolRunIn,
    ToolRunOut,
)
from tools.handlers import get_handler
from tools.registry import get_registry
from tools.validators.pre_execution import PreExecutionValidator
from ui_dsl import command_result_to_ui_directives
from ui_dsl.enums import CommandResultType
from versioning import get_service_versions

logger = get_runtime_logger(__name__)

ORCHESTRATOR_API_KEY = os.getenv("ORCHESTRATOR_API_KEY")


def require_service_api_key(
    x_service_api_key: str = Header(default="", alias="x-service-api-key"),
) -> None:
    if not ORCHESTRATOR_API_KEY:
        raise HTTPException(status_code=500, detail="Service API key is not configured")
    if x_service_api_key != ORCHESTRATOR_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid service API key")


def _parse_tags_payload(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        # Fallback: treat as comma-separated string
        parsed = [part.strip() for part in raw.split(",")]
    if not isinstance(parsed, list):
        raise ValueError("tags must be an array")
    tags: list[str] = []
    for item in parsed:
        if item is None:
            continue
        candidate = str(item).strip()
        if candidate:
            tags.append(candidate)
    return tags


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid document_date: {value}") from exc


def _parse_iso_date(value: str | None) -> date:
    if not value:
        raise HTTPException(status_code=400, detail="date is required")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid date: {value}") from exc


def _format_briefing_response(briefing: dict[str, Any]) -> dict[str, Any]:
    return {
        "briefing_id": briefing.get("briefing_id"),
        "date": briefing.get("briefing_date"),
        "timezone": briefing.get("timezone"),
        "event_count": briefing.get("event_count") or 0,
        "todo_count": briefing.get("todo_count") or 0,
        "summary": briefing.get("summary") or "",
        "markdown": briefing.get("markdown") or "",
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    install_stdout_logger()
    # Startup: Ensure we can connect at startup; this raises early if DB connection is misconfigured.
    with get_conn():
        pass
    yield
    # Shutdown: Add any cleanup here if needed


api = FastAPI(title="Personal Memory Orchestrator", version="0.3", lifespan=lifespan)

# Configure CORS to allow requests from the frontend
api.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@api.get("/system/versions", response_model=ServiceVersionCollection)
@api.get("/mobile/system/versions", response_model=ServiceVersionCollection)
def read_service_versions(user: dict = Depends(get_current_user)):
    return get_service_versions()


@api.get("/system/logs/stream")
async def stream_system_logs(
    level: str | None = Query(default=None),
    _: dict = Depends(get_current_user),
):
    if level:
        normalized = level.lower()
        if normalized not in LOG_LEVELS:
            raise HTTPException(status_code=400, detail=f"Invalid log level: {level}")
        level = normalized

    buffer = get_log_buffer()

    async def event_generator():
        last_id = 0
        while True:
            entries = buffer.get_since(last_id, level=level)
            if entries:
                for entry in entries:
                    last_id = entry.entry_id
                    yield f"data: {json.dumps(entry.to_dict())}\n\n"
            await asyncio.sleep(0.5)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@api.get("/system/logs")
def list_system_logs(
    level: str | None = Query(default=None),
    since_minutes: int | None = Query(default=15, ge=1, le=1440),
    limit: int = Query(default=200, ge=1, le=1000),
    _: dict = Depends(get_current_user),
):
    if level:
        normalized = level.lower()
        if normalized not in LOG_LEVELS:
            raise HTTPException(status_code=400, detail=f"Invalid log level: {level}")
        level = normalized

    buffer = get_log_buffer()
    entries = buffer.get_recent(
        since_minutes=since_minutes,
        level=level,
        limit=limit,
    )
    return {"entries": [entry.to_dict() for entry in entries]}


# --------------------------- Mobile endpoints ---------------------------


@api.get("/mobile/settings", response_model=NotificationSettingsOut)
def read_user_settings(user: dict = Depends(get_current_user)):
    email = user.get("email") or user.get("user_email")
    if not email:
        raise HTTPException(status_code=400, detail="User email is missing")
    return get_push_settings(email)


@api.put("/mobile/settings/push-notifications", response_model=NotificationSettingsOut)
def update_push_notifications(
    payload: PushNotificationsUpdateIn,
    user: dict = Depends(get_current_user),
):
    email = user.get("email") or user.get("user_email")
    if not email:
        raise HTTPException(status_code=400, detail="User email is missing")
    return update_push_settings(email, payload.enabled)


@api.post("/mobile/devices/register")
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


@api.delete("/mobile/devices/unregister")
def unregister_device(
    expo_push_token: str = Query(..., alias="expoPushToken"), user: dict = Depends(get_current_user)
):
    email = user.get("email") or user.get("user_email")
    if not email:
        raise HTTPException(status_code=400, detail="User email is missing")
    deleted = devices_service.unregister_device(email, expo_push_token)
    if not deleted:
        raise HTTPException(status_code=404, detail="Device not found")
    return {"ok": True}


# --------------------------- Ingest endpoints ---------------------------
@api.post("/ingest/contact")
@api.post("/mobile/ingest/contact")
def ingest_contact(c: ContactIn, user: dict = Depends(get_current_user)):
    contacts_service.ingest_contact(c)
    return {"ok": True}


@api.get("/contacts")
@api.get("/mobile/contacts")
def list_contacts(user: dict = Depends(get_current_user)):
    return {"contacts": contacts_service.list_contacts()}


@api.get("/contacts/merge-candidates")
def list_merge_candidates(user: dict = Depends(get_current_user)):
    return contacts_service.list_contact_merge_candidates()


@api.post("/contacts/merge")
def merge_contacts_endpoint(payload: ContactMergeIn, user: dict = Depends(get_current_user)):
    try:
        contact = contacts_service.merge_contacts(
            payload.primary_contact_id, payload.duplicate_contact_id
        )
    except LookupError:
        raise HTTPException(status_code=404, detail="One or both contacts not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "contact": contact}


@api.get("/contacts/{contact_id}")
@api.get("/mobile/contacts/{contact_id}")
def get_contact(contact_id: str, user: dict = Depends(get_current_user)):
    contact = contacts_service.get_contact(contact_id)
    if contact is None or contacts_service.is_external_placeholder(contact.get("display_name")):
        raise HTTPException(status_code=404, detail="Contact not found")
    return contact


@api.get("/places/{place_id}")
@api.get("/mobile/places/{place_id}")
def get_place(place_id: str, user: dict = Depends(get_current_user)):
    place = places_service.get_place(place_id)
    if place is None:
        raise HTTPException(status_code=404, detail="Place not found")
    return place


@api.get("/mobile/contacts/{contact_id}/avatar")
def get_contact_avatar(contact_id: str, _: dict = Depends(get_current_user)):
    logger.debug("[get_contact_avatar] contact_id=%s", contact_id)
    contact = contacts_service.get_contact(contact_id)
    logger.debug("[get_contact_avatar] contact=%s", contact)
    if contact is None or contacts_service.is_external_placeholder(contact.get("display_name")):
        raise HTTPException(status_code=404, detail="Contact not found")

    external_id = contact.get("external_id")
    logger.debug("[get_contact_avatar] external_id=%s", external_id)
    if not external_id:
        raise HTTPException(status_code=404, detail="Avatar not available")

    try:
        result = immich_client.fetch_person_thumbnail(external_id)
    except immich_client.ImmichClientError as exc:
        logger.exception("[get_contact_avatar] error=%s", exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    if not result:
        raise HTTPException(status_code=404, detail="Avatar not available")

    content, content_type = result
    return Response(content=content, media_type=content_type)


@api.post("/mobile/contacts/{contact_id}/relationships")
def upsert_contact_relationship_mobile(
    contact_id: str,
    rel: ContactRelationshipIn,
    _: dict = Depends(get_current_user),
):
    if rel.from_contact_id != contact_id:
        raise HTTPException(status_code=400, detail="from_contact_id must match contact_id")
    contacts_service.upsert_contact_relationship(rel)
    return {"ok": True}


@api.delete("/mobile/contacts/{contact_id}/relationships/{relationship_id}")
def delete_contact_relationship_mobile(
    contact_id: str,
    relationship_id: str,
    _: dict = Depends(get_current_user),
):
    deleted = contacts_service.delete_contact_relationship(relationship_id, contact_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Relationship not found")
    return {"ok": True}


@api.delete("/contacts/{contact_id}")
@api.delete("/mobile/contacts/{contact_id}")
def delete_contact(contact_id: str, user: dict = Depends(get_current_user)):
    deleted = contacts_service.delete_contact(contact_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Contact not found")
    return {"ok": True}


@api.post("/webhooks/contacts")
def receive_contact_webhook(
    payload: ExternalContactWebhook,
    _: None = Depends(require_service_api_key),
):
    event_name = (payload.event_name or "").lower()
    payload_body = payload.payload
    person = payload_body.person if payload_body else None
    if not person or not person.id:
        raise HTTPException(status_code=400, detail="Webhook payload is missing person information")

    external_id = str(person.id)

    if person.is_hidden:
        existing = contacts_service.get_contact_by_external_id(external_id)
        if existing:
            display_name = (existing.get("display_name") or "").strip().lower()
            if display_name.startswith("external contact"):
                try:
                    deleted = contacts_service.delete_contact(existing["contact_id"])
                except Exception as exc:
                    raise HTTPException(
                        status_code=500, detail=f"Failed to delete hidden contact: {exc}"
                    ) from exc
                if deleted:
                    # Stop early so we do not re-create the hidden contact later in this handler.
                    return {"ok": True, "action": "deleted"}
        return {"ok": True, "action": "ignored"}

    if event_name == "persondelete":
        try:
            updated = contacts_service.unlink_external_contact(external_id)
        except Exception as exc:
            raise HTTPException(
                status_code=500, detail=f"Failed to unlink external contact: {exc}"
            ) from exc
        return {"ok": True, "action": "unlinked" if updated else "ignored"}

    if event_name in {"personcreate", "personupdate"}:
        try:
            contact = contacts_service.sync_external_contact(
                person, payload_body.previous if payload_body else None
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=500, detail=f"Failed to process contact webhook: {exc}"
            ) from exc
        return {"ok": True, "contact": contact}

    raise HTTPException(status_code=400, detail=f"Unsupported eventName: {payload.event_name}")


@api.post("/webhooks/telegram/messages")
async def handle_telegram_messages(
    payload: dict[str, Any],
    request: Request,
):
    try:
        return telegram_bot.process_update(
            payload,
            secret_token=request.headers.get("X-Telegram-Bot-Api-Secret-Token"),
        )
    except telegram_bot.TelegramAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except telegram_bot.TelegramConfigError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except telegram_bot.TelegramProcessingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except telegram_bot.TelegramUploadError as exc:
        logger.exception("[telegram_bot] upload error=%s", exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@api.post("/ingest/place")
def ingest_place(p: PlaceIn, user: dict = Depends(get_current_user)):
    places_service.ingest_place(p)
    return {"ok": True}


@api.post("/ingest/todo")
@api.post("/mobile/ingest/todo")
def ingest_todo(todo: TodoIn, user: dict = Depends(get_current_user)):
    todos_service.ingest_todo(todo)
    return {"ok": True, "id": todo.todo_id}


@api.get("/todos")
def list_todos(
    user: dict = Depends(get_current_user),
    open_only: bool = Query(default=False),
    order: str | None = Query(default=None),
):
    return {"todos": todos_service.list_todos(open_only=open_only, order=order)}


@api.get("/mobile/todos")
def list_mobile_todos(
    user: dict = Depends(get_current_user),
    order: str | None = Query(default=None),
):
    return {"todos": todos_service.list_todos(open_only=True, order=order)}


@api.get("/todos/{todo_id}")
@api.get("/mobile/todos/{todo_id}")
def get_todo(todo_id: str, user: dict = Depends(get_current_user)):
    todo = todos_service.get_todo(todo_id)
    if not todo:
        raise HTTPException(status_code=404, detail="Todo not found")
    return todo


@api.patch("/todos/{todo_id}/status")
@api.patch("/mobile/todos/{todo_id}/status")
def update_todo_status(
    todo_id: str,
    payload: TodoStatusUpdateIn,
    user: dict = Depends(get_current_user),
):
    updated = todos_service.update_todo_status(todo_id, payload.status)
    if not updated:
        raise HTTPException(status_code=404, detail="Todo not found")
    return {"ok": True}


@api.get("/events/search")
@api.get("/mobile/events/search")
def search_events(
    user: dict = Depends(get_current_user),
    query: str | None = Query(default=None),
    limit: int = Query(default=10, ge=1, le=50),
):
    trimmed = (query or "").strip()
    with get_conn() as conn, conn.cursor() as cur:
        if trimmed:
            like = f"%{trimmed}%"
            cur.execute(
                """
                SELECT e.id,
                       e.title,
                       e.start_date,
                       e.end_date
                FROM events e
                WHERE e.title ILIKE %s OR e.summary ILIKE %s
                ORDER BY e.start_date DESC
                LIMIT %s
                """,
                (like, like, limit),
            )
        else:
            cur.execute(
                """
                SELECT e.id,
                       e.title,
                       e.start_date,
                       e.end_date
                FROM events e
                ORDER BY e.start_date DESC
                LIMIT %s
                """,
                (limit,),
            )
        rows: list[dict[str, Any]] = [dict(row) for row in cur.fetchall()]
    return {"events": rows}


@api.get("/events/{event_id}")
@api.get("/mobile/events/{event_id}")
def get_event_detail(event_id: str, user: dict = Depends(get_current_user)):
    events = events_service.get_events([event_id])
    if not events:
        raise HTTPException(status_code=404, detail="Event not found")
    return events[0]


@api.delete("/todos/{todo_id}")
def delete_todo(todo_id: str, user: dict = Depends(get_current_user)):
    deleted = todos_service.delete_todo(todo_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Todo not found")
    return {"ok": True}


@api.post("/ingest/event")
def ingest_event(e: EventIn, user: dict = Depends(get_current_user)):
    events_service.ingest_event(e)
    return {"ok": True, "id": e.id}


# --------------------------- Document endpoints ---------------------------
@api.post("/ingest/document", response_model=DocumentDetailOut)
async def upload_document(
    title: str | None = Form(None),
    tags: str | None = Form(None),
    description: str | None = Form(None),
    document_date: str | None = Form(None),
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
):
    try:
        parsed_tags = _parse_tags_payload(tags)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    try:
        parsed_date = _parse_iso_datetime(document_date)
    except HTTPException:
        raise
    except Exception:
        parsed_date = None

    try:
        document = documents.ingest_document(
            title=title,
            tags=parsed_tags,
            description=description,
            upload=file,
            document_date=parsed_date,
        )
    except documents.DocumentProcessingError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.exception("[documents] Failed to ingest document: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to ingest document")

    return DocumentDetailOut(**document)


@api.post("/ingest/document/external")
def ingest_external_document(
    file: UploadFile = File(...),
    _: None = Depends(require_service_api_key),
):
    try:
        document = documents.ingest_document(
            title=None,
            tags=None,
            description=None,
            upload=file,
            document_date=None,
        )
    except documents.DocumentProcessingError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.exception("[documents] Failed to ingest document: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to ingest document")

    return DocumentDetailOut(**document)


@api.get("/documents", response_model=DocumentCollection)
def list_documents(
    limit: int = Query(200, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: dict = Depends(get_current_user),
):
    docs = documents.list_documents(limit=limit, offset=offset)
    return DocumentCollection(documents=docs)


@api.get("/documents/{document_id}", response_model=DocumentDetailOut)
def get_document_detail(document_id: str, user: dict = Depends(get_current_user)):
    document = documents.get_document(document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    return DocumentDetailOut(**document)


@api.patch("/documents/{document_id}", response_model=DocumentDetailOut)
def update_document(
    document_id: str,
    payload: DocumentUpdateIn,
    user: dict = Depends(get_current_user),
):
    try:
        document = documents.update_document_metadata(
            document_id,
            title=payload.title,
            tags=payload.tags,
            description=payload.description,
            document_date=payload.document_date,
        )
    except documents.DocumentProcessingError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.exception("[documents] Failed to update document metadata: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to update document")

    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    return DocumentDetailOut(**document)


@api.post("/documents/search", response_model=DocumentCollection)
def search_documents_endpoint(payload: DocumentSearchIn, user: dict = Depends(get_current_user)):
    limit = payload.limit or 20
    docs = documents.search_documents(payload.query, tags=payload.tags, limit=limit)
    return DocumentCollection(documents=docs)


@api.delete("/documents/{document_id}")
def delete_document(document_id: str, user: dict = Depends(get_current_user)):
    deleted = documents.delete_document(document_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Document not found")
    return {"ok": True}


@api.get("/documents/{document_id}/download")
def download_document(document_id: str, user: dict = Depends(get_current_user)):
    info = documents.get_document_file(document_id)
    if not info:
        raise HTTPException(status_code=404, detail="Document not found")
    file_path = info.get("file_path")
    if not file_path or not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File unavailable")
    media_type = info.get("file_mime") or "application/octet-stream"
    filename = info.get("file_name") or document_id
    return FileResponse(file_path, media_type=media_type, filename=filename)


@api.post("/ingest/events/notes")
def ingest_meeting_notes(
    meetings: list[MeetingIn],
    _: None = Depends(require_service_api_key),
):
    ids = events_service.ingest_meeting_notes(meetings, todo_writer=todos_service.ingest_todo)
    return {"ok": True, "ids": ids}


@api.post("/ingest/event/external")
def ingest_external_event(
    payload: ExternalEventPayload,
    _: None = Depends(require_service_api_key),
):
    try:
        event_id = events_service.ingest_external_event(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "id": event_id}


@api.post("/commands/event/confirm", response_model=EventCommandResult)
@api.post("/mobile/commands/event/confirm", response_model=EventCommandResult)
def confirm_event_command(
    payload: EventCommandConfirmation,
    user: dict = Depends(get_current_user),
):
    user_email = user.get("email")
    if not user_email:
        raise HTTPException(status_code=400, detail="Authenticated user email missing")
    return confirm_event_command_impl(payload, user_email)


@api.post("/access/gate")
def validate_gate_access(
    image: UploadFile = File(...),
    _: None = Depends(require_service_api_key),
):
    try:
        image_bytes = image.file.read()
    except Exception as exc:  # pragma: no cover - defensive path
        raise HTTPException(status_code=400, detail=f"Failed to read image: {exc}") from exc

    try:
        contacts, _ = immich_client.identify_contacts_from_image(
            image_bytes=image_bytes,
            filename=image.filename,
            mime_type=image.content_type,
        )
    except immich_client.ImmichClientError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except immich_client.ImmichIdentifyError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    if not contacts:
        raise HTTPException(status_code=403, detail="Not authorized")

    for contact in contacts:
        contact_name = contact.get("display_name") or contact.get("contact_id") or "unknown"
        action_logs.insert_action_log(
            action_logs.PERSON_IDENTIFIED,
            {"name": contact_name, "location": "gate"},
        )
        tags = [tag.lower() for tag in (contact.get("tags") or []) if isinstance(tag, str)]
        open_gate = False
        if "gate-access" in tags:
            open_gate = True
            action_logs.insert_action_log(
                action_logs.LOG_TYPE_GATE_OPENED,
                {"name": contact_name, "location": "gate"},
            )

    contact_names = ", ".join(
        [
            contact.get("display_name") or contact.get("contact_id") or "unknown"
            for contact in contacts
        ]
    )

    return {"contact_names": contact_names, "open_gate": open_gate}


@api.get("/meetings/{meeting_id}")
def get_meeting(meeting_id: str, user: dict = Depends(get_current_user)):
    meeting = events_service.get_meeting(meeting_id)
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")
    return meeting


@api.get("/mobile/briefings/daily", response_model=DailyBriefingOut)
def get_daily_briefing(
    date_value: str = Query(..., alias="date"),
    timezone: str | None = Query(default=None),
    user: dict = Depends(get_current_user),
):
    user_email = user.get("email")
    if not user_email:
        raise HTTPException(status_code=400, detail="Authenticated user email missing")
    briefing_date = _parse_iso_date(date_value)
    briefing = daily_briefings.get_daily_briefing(
        user_email=user_email,
        briefing_date=briefing_date,
        timezone=timezone,
    )
    if not briefing:
        briefing = daily_briefings.get_daily_briefing(
            user_email="default_user",
            briefing_date=briefing_date,
            timezone=timezone,
        )
    if not briefing:
        raise HTTPException(status_code=404, detail="Briefing not found")
    return _format_briefing_response(briefing)


@api.get("/mobile/briefings/latest", response_model=DailyBriefingOut)
def get_latest_briefing(user: dict = Depends(get_current_user)):
    user_email = user.get("email")
    if not user_email:
        raise HTTPException(status_code=400, detail="Authenticated user email missing")
    briefing = daily_briefings.get_latest_daily_briefing(user_email=user_email)
    if not briefing:
        briefing = daily_briefings.get_latest_daily_briefing(user_email="default_user")
    if not briefing:
        raise HTTPException(status_code=404, detail="Briefing not found")
    return _format_briefing_response(briefing)


# --------------------------- Ask endpoint (LLM-powered) ---------------------------


class _SessionContext:
    """Context for a resolved session, used by both /ask and /ask/stream."""

    __slots__ = (
        "session_id",
        "question",
        "is_new_session",
        "is_reset_only",
        "user_email",
        "original_question",
    )

    def __init__(
        self,
        session_id: str,
        question: str,
        is_new_session: bool,
        is_reset_only: bool,
        user_email: str,
        original_question: str,
    ):
        self.session_id = session_id
        self.question = question
        self.is_new_session = is_new_session
        self.is_reset_only = is_reset_only
        self.user_email = user_email
        self.original_question = original_question


def _strip_command_prefix(message: str) -> str:
    """Remove a leading slash-command prefix (e.g. /new, /event) from a message."""
    from commands.parser import parse_command

    text = (message or "").strip()
    parsed = parse_command(text)
    if not parsed:
        return text
    return parsed.args


def _resolve_session_context(
    payload: AskIn,
    user_email: str,
    *,
    force_new_session: bool = False,
) -> _SessionContext:
    """
    Resolve session context from payload. Handles both explicit thread_id
    and main session mode with timeout/command parsing.

    Raises HTTPException on thread not found or permission errors.
    """
    from commands.parser import parse_command

    requested_thread_id = payload.thread_id or payload.session_id
    question = payload.question
    is_new_session = False
    parsed_command = parse_command(question)
    reset_requested = bool(parsed_command and parsed_command.command == "new")
    if reset_requested:
        question = parsed_command.args
        force_new_session = True

    if requested_thread_id and not force_new_session:
        # Explicit thread - existing behavior
        try:
            thread = conversations.ensure_thread(requested_thread_id, user_email)
        except LookupError:
            raise HTTPException(status_code=404, detail="Conversation thread not found")
        except PermissionError:
            raise HTTPException(
                status_code=403, detail="Conversation thread does not belong to user"
            )
    elif requested_thread_id and force_new_session:
        # /new should always reset context, even when client sends an explicit thread_id.
        thread = conversations.ensure_thread(None, user_email)
        is_new_session = True
    else:
        # Main session mode - resolve with timeout and command parsing
        if force_new_session:
            question = f"/new {question}".strip()
        thread, is_new_session, question = conversations.resolve_main_session(user_email, question)

    # Defensive normalization: never pass slash-command markers into agent prompt.
    question = _strip_command_prefix(question)

    session_id = thread["id"]
    is_reset_only = is_new_session and not question.strip()

    return _SessionContext(
        session_id=session_id,
        question=question,
        is_new_session=is_new_session,
        is_reset_only=is_reset_only,
        user_email=user_email,
        original_question=payload.question,
    )


_RESET_MESSAGE = "New session started. How can I help you?"


def _make_reset_bundle(ctx: _SessionContext) -> dict[str, Any]:
    """Create response bundle for a session reset with no actual question."""
    return {
        "question": ctx.original_question,
        "answer": _RESET_MESSAGE,
        "resolution": {},
        "search_results": [],
        "events_results": [],
        "session_id": ctx.session_id,
        "thread_id": ctx.session_id,
        "is_new_session": True,
    }


def _command_response_text(command_result: dict[str, Any]) -> str:
    result_type = CommandResultType.from_value(command_result.get("type"))
    if result_type is CommandResultType.EVENT_CONFIRMATION:
        return command_result.get("message") or "Event proposal ready."
    if result_type is CommandResultType.NEED_USER_INPUT:
        need_user_input = command_result.get("need_user_input")
        if isinstance(need_user_input, dict):
            prompt = str(need_user_input.get("prompt") or "").strip()
            if prompt:
                return prompt
        return "I need a few more details to continue."
    return command_result.get("message") or "Command completed."


def _sanitize_command_metadata(command_result: dict[str, Any]) -> dict[str, Any]:
    try:
        return json.loads(json.dumps(command_result, default=str))
    except Exception:
        return {
            "type": CommandResultType.from_value(command_result.get("type")).value,
        }


def _command_assistant_metadata(
    command_result: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    metadata: dict[str, Any] = {
        "command_result": _sanitize_command_metadata(command_result),
    }
    ui_directives = command_result_to_ui_directives(command_result)
    if ui_directives:
        metadata["ui_directives"] = ui_directives
    return metadata, ui_directives


def _handle_command(
    question: str,
    user_email: str,
    user: dict,
    thread_id: str | None,
) -> tuple[dict[str, Any], str, dict[str, Any] | None] | None:
    """
    Check if the question is a command and handle it.

    Returns command result dict if it's a command, None otherwise.
    """
    from commands import get_command_registry, parse_command
    from commands.storage import store_command_thread

    parsed_cmd = parse_command(question)
    if not parsed_cmd or parsed_cmd.command == "new":
        # /new is handled in session resolution, not here
        return None

    # Handle non-/new commands (like /event)
    command_thread = conversations.ensure_thread(
        None, user_email, title=f"Command: /{parsed_cmd.command}"
    )
    command_thread_id = command_thread["id"]
    pending_key = event_pending_key(user_email, command_thread_id)
    store_command_thread(pending_key, command_thread_id)

    registry = get_command_registry()
    context = {
        "user_email": user_email,
        "user": user,
        "thread_id": command_thread_id,
        "event_pending_key": pending_key,
    }
    command_result = registry.execute(parsed_cmd, context)

    if thread_id is None:
        conversations.set_main_session_thread(user_email, command_thread_id)

    assistant_metadata, ui_directives = _command_assistant_metadata(command_result)
    try:
        conversations.record_exchange(
            command_thread_id,
            user_email,
            question,
            _command_response_text(command_result),
            assistant_metadata=assistant_metadata,
        )
    except Exception as exc:
        logger.warning("[command_thread] Failed to record exchange: %s", exc, exc_info=exc)

    return command_result, command_thread_id, ui_directives


def _should_reset_command_thread(
    user_email: str,
    thread_id: str | None,
    pending_event_id: str | None,
) -> bool:
    if not user_email or not thread_id or pending_event_id:
        return False

    from commands.storage import is_command_thread

    return is_command_thread(thread_id)


@api.post("/ask", response_model=AskOut)
@api.post("/mobile/ask", response_model=AskOut)
async def ask(payload: AskIn, user: dict = Depends(get_current_user)):
    start_time = perf_counter()
    user_email = user.get("email")
    if not user_email:
        raise HTTPException(status_code=400, detail="Authenticated user email missing")

    # Check for commands or pending event refinements before session resolution
    try:
        command_payload = handle_pending_event(
            payload.question,
            user_email,
            user,
            payload.thread_id or payload.session_id,
            payload.pending_event_id,
            command_response_text=_command_response_text,
            command_assistant_metadata=_command_assistant_metadata,
        )
        if not command_payload:
            command_payload = _handle_command(
                payload.question,
                user_email,
                user,
                payload.thread_id or payload.session_id,
            )
        if command_payload:
            command_result, command_thread_id, command_ui_directives = command_payload
            from commands.storage import get_pending_event

            pending_event_id = get_pending_event(event_pending_key(user_email, command_thread_id))
            # Return command result
            return AskOut(
                question=payload.question,
                answer=_command_response_text(command_result),
                resolution={},
                search_results=[],
                events_results=[],
                thread_id=command_thread_id,
                session_id=command_thread_id,
                is_new_session=True,
                command_result=command_result,
                ui_directives=command_ui_directives,
                pending_event_id=pending_event_id,
            )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    from commands.storage import clear_command_thread_by_id

    force_new_session = False
    requested_thread_id = payload.thread_id or payload.session_id
    if requested_thread_id and _should_reset_command_thread(
        user_email, requested_thread_id, payload.pending_event_id
    ):
        clear_command_thread_by_id(requested_thread_id)
        new_thread = conversations.ensure_thread(None, user_email)
        payload = payload.copy(update={"thread_id": new_thread["id"], "session_id": None})
    elif not requested_thread_id and payload.pending_event_id is None:
        main_session = conversations.get_main_session(user_email)
        main_thread_id = main_session.get("current_thread_id") if main_session else None
        if main_thread_id and _should_reset_command_thread(user_email, main_thread_id, None):
            clear_command_thread_by_id(main_thread_id)
            force_new_session = True

    ctx = _resolve_session_context(payload, user_email, force_new_session=force_new_session)
    if ctx.is_new_session:
        logger.info("[session] new session started session=%s user=%s", ctx.session_id, user_email)

    # Handle /new command with no actual message
    if ctx.is_reset_only:
        logger.info("[ask] session reset session=%s user=%s", ctx.session_id, user_email)
        return AskOut(**_make_reset_bundle(ctx))

    limit = payload.limit or 30
    preview = ctx.question.strip().replace("\n", " ")
    if len(preview) > 120:
        preview = preview[:117] + "..."

    mode = "main_session" if not (payload.thread_id or payload.session_id) else "thread"
    logger.info(
        "[ask] start session=%s user=%s limit=%s mode=%s is_new=%s question=%r",
        ctx.session_id,
        user_email,
        limit,
        mode,
        ctx.is_new_session,
        preview,
    )

    bundle = await llm.answer_question(
        ctx.question,
        search_limit=limit,
        user_id=user_email,
        session_id=ctx.session_id,
        user_email=user_email,
        client_context=payload.client_context.model_dump(exclude_none=True)
        if payload.client_context
        else None,
        ui_submission=payload.ui_submission.model_dump(exclude_none=True)
        if payload.ui_submission
        else None,
    )
    bundle["thread_id"] = ctx.session_id
    bundle["session_id"] = ctx.session_id
    bundle["is_new_session"] = ctx.is_new_session

    elapsed = perf_counter() - start_time
    search_results = bundle.get("search_results")
    search_count = len(search_results) if isinstance(search_results, list) else "n/a"
    logger.info(
        "[ask] complete session=%s user=%s elapsed=%.3fs search_results=%s",
        ctx.session_id,
        user_email,
        elapsed,
        search_count,
    )

    from commands.storage import get_pending_event

    bundle["pending_event_id"] = get_pending_event(
        event_pending_key(user_email, payload.thread_id or payload.session_id)
    )
    return AskOut(**bundle)


@api.post("/ask/stream")
@api.post("/mobile/ask/stream")
async def ask_stream(payload: AskIn, user: dict = Depends(get_current_user)):
    """
    Stream LLM responses as Server-Sent Events (SSE).

    Returns a stream of events:
    - {"type": "session_info", ...} - Session metadata (sent first)
    - {"type": "token", "content": "..."} - Text chunks as they arrive
    - {"type": "tool_call", "name": "...", "args": {...}} - Tool invocations
    - {"type": "tool_result", "name": "...", "result": {...}} - Tool outputs
    - {"type": "status", "message": "..."} - Status updates
    - {"type": "done", "bundle": {...}} - Final complete response
    """
    start_time = perf_counter()
    user_email = user.get("email")
    if not user_email:
        raise HTTPException(status_code=400, detail="Authenticated user email missing")

    # Check for commands or pending event refinements before session resolution
    try:
        command_payload = handle_pending_event(
            payload.question,
            user_email,
            user,
            payload.thread_id or payload.session_id,
            payload.pending_event_id,
            command_response_text=_command_response_text,
            command_assistant_metadata=_command_assistant_metadata,
        )
        if not command_payload:
            command_payload = _handle_command(
                payload.question,
                user_email,
                user,
                payload.thread_id or payload.session_id,
            )
        if command_payload:
            command_result, command_thread_id, command_ui_directives = command_payload
            from commands.storage import get_pending_event

            pending_event_id = get_pending_event(event_pending_key(user_email, command_thread_id))

            # Return command result as SSE stream
            async def command_generator():
                bundle = {
                    "question": payload.question,
                    "answer": _command_response_text(command_result),
                    "resolution": {},
                    "search_results": [],
                    "events_results": [],
                    "thread_id": command_thread_id,
                    "session_id": command_thread_id,
                    "is_new_session": True,
                    "command_result": command_result,
                    "ui_directives": command_ui_directives,
                    "pending_event_id": pending_event_id,
                }
                yield f"data: {json.dumps({'type': 'done', 'bundle': bundle})}\n\n"

            return StreamingResponse(
                command_generator(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                },
            )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    from commands.storage import clear_command_thread_by_id

    force_new_session = False
    requested_thread_id = payload.thread_id or payload.session_id
    if requested_thread_id and _should_reset_command_thread(
        user_email, requested_thread_id, payload.pending_event_id
    ):
        clear_command_thread_by_id(requested_thread_id)
        new_thread = conversations.ensure_thread(None, user_email)
        payload = payload.copy(update={"thread_id": new_thread["id"], "session_id": None})
    elif not requested_thread_id and payload.pending_event_id is None:
        main_session = conversations.get_main_session(user_email)
        main_thread_id = main_session.get("current_thread_id") if main_session else None
        if main_thread_id and _should_reset_command_thread(user_email, main_thread_id, None):
            clear_command_thread_by_id(main_thread_id)
            force_new_session = True

    ctx = _resolve_session_context(payload, user_email, force_new_session=force_new_session)
    if ctx.is_new_session:
        logger.info("[session] new session started session=%s user=%s", ctx.session_id, user_email)

    # Handle /new command with no actual message
    if ctx.is_reset_only:
        logger.info("[ask/stream] session reset session=%s user=%s", ctx.session_id, user_email)
        reset_bundle = _make_reset_bundle(ctx)

        async def reset_generator():
            yield f"data: {json.dumps({'type': 'session_info', 'thread_id': ctx.session_id, 'is_new_session': True})}\n\n"
            yield f"data: {json.dumps({'type': 'token', 'content': _RESET_MESSAGE})}\n\n"
            yield f"data: {json.dumps({'type': 'done', 'bundle': reset_bundle})}\n\n"

        return StreamingResponse(
            reset_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    limit = payload.limit or 30

    preview = ctx.question.strip().replace("\n", " ")
    if len(preview) > 120:
        preview = preview[:117] + "..."
    mode = "main_session" if not (payload.thread_id or payload.session_id) else "thread"
    logger.info(
        "[ask/stream] start session=%s user=%s limit=%s mode=%s is_new=%s question=%r",
        ctx.session_id,
        user_email,
        limit,
        mode,
        ctx.is_new_session,
        preview,
    )

    async def event_generator():
        try:
            yield f"data: {json.dumps({'type': 'session_info', 'thread_id': ctx.session_id, 'is_new_session': ctx.is_new_session})}\n\n"

            async for event in llm.answer_question_stream(
                ctx.question,
                search_limit=limit,
                user_id=user_email,
                session_id=ctx.session_id,
                user_email=user_email,
                client_context=payload.client_context.model_dump(exclude_none=True)
                if payload.client_context
                else None,
                ui_submission=payload.ui_submission.model_dump(exclude_none=True)
                if payload.ui_submission
                else None,
            ):
                if event.get("type") == "done":
                    from commands.storage import get_pending_event

                    bundle = event.get("bundle", {})
                    bundle["thread_id"] = ctx.session_id
                    bundle["session_id"] = ctx.session_id
                    bundle["is_new_session"] = ctx.is_new_session
                    bundle["pending_event_id"] = get_pending_event(
                        event_pending_key(user_email, payload.thread_id or payload.session_id)
                    )
                    event["bundle"] = bundle

                yield f"data: {json.dumps(event, default=str)}\n\n"

                if event.get("type") == "done":
                    elapsed = perf_counter() - start_time
                    logger.info(
                        "[ask/stream] complete session=%s user=%s elapsed=%.3fs",
                        ctx.session_id,
                        user_email,
                        elapsed,
                    )
        except Exception as exc:
            logger.exception("[ask/stream] error session=%s", ctx.session_id)
            yield f"data: {json.dumps({'type': 'error', 'message': str(exc)})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@api.get("/threads", response_model=list[ThreadOut])
@api.get("/mobile/threads", response_model=list[ThreadOut])
def list_conversation_threads(user: dict = Depends(get_current_user)):
    user_email = user.get("email")
    if not user_email:
        raise HTTPException(status_code=400, detail="Authenticated user email missing")
    threads = conversations.list_threads(user_email)
    return [ThreadOut(**thread) for thread in threads]


@api.post("/threads", response_model=ThreadOut)
@api.post("/mobile/threads", response_model=ThreadOut)
def create_conversation_thread(payload: ThreadCreate, user: dict = Depends(get_current_user)):
    user_email = user.get("email")
    if not user_email:
        raise HTTPException(status_code=400, detail="Authenticated user email missing")
    thread = conversations.ensure_thread(None, user_email, title=payload.title)
    return ThreadOut(**thread)


@api.get("/threads/{thread_id}", response_model=ThreadDetailOut)
@api.get("/mobile/threads/{thread_id}", response_model=ThreadDetailOut)
def get_conversation_thread(thread_id: str, user: dict = Depends(get_current_user)):
    user_email = user.get("email")
    if not user_email:
        raise HTTPException(status_code=400, detail="Authenticated user email missing")
    thread = conversations.get_thread_with_messages(thread_id, user_email)
    if not thread:
        raise HTTPException(status_code=404, detail="Conversation thread not found")
    return ThreadDetailOut(
        **{
            **{k: v for k, v in thread.items() if k != "messages"},
            "messages": thread.get("messages", []),
        }
    )


@api.put("/threads/{thread_id}", response_model=ThreadOut)
@api.put("/mobile/threads/{thread_id}", response_model=ThreadOut)
def update_conversation_thread(
    thread_id: str,
    payload: ThreadUpdate,
    user: dict = Depends(get_current_user),
):
    user_email = user.get("email")
    if not user_email:
        raise HTTPException(status_code=400, detail="Authenticated user email missing")
    conversations.ensure_thread(thread_id, user_email)
    normalized_title = payload.title.strip() if payload.title else None
    updates = ["updated_at = NOW()"]
    params: list = []

    if payload.title is not None:
        updates.append("title = %s")
        params.append(normalized_title)

    with get_conn() as conn, conn.cursor() as cur:
        query = f"""
            UPDATE conversation_threads
            SET {", ".join(updates)}
            WHERE id = %s AND user_email = %s
            RETURNING id, user_email, title, created_at, updated_at
        """
        params.extend([thread_id, user_email])
        cur.execute(query, params)
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Conversation thread not found")
        conn.commit()
    return ThreadOut(**row)


@api.delete("/threads/{thread_id}", status_code=204)
@api.delete("/mobile/threads/{thread_id}", status_code=204)
def delete_conversation_thread(thread_id: str, user: dict = Depends(get_current_user)):
    user_email = user.get("email")
    if not user_email:
        raise HTTPException(status_code=400, detail="Authenticated user email missing")
    deleted = conversations.delete_thread(thread_id, user_email)
    if not deleted:
        raise HTTPException(status_code=404, detail="Conversation thread not found")
    return Response(status_code=204)


# --------------------------- Skills Management Endpoints ---------------------------


@api.get("/skills")
def list_skills(user: dict = Depends(get_current_user)):
    """List all available skills with their metadata."""
    registry = skills.get_registry()
    skill_list = registry.list_skills()
    return {
        "skills": [s.to_dict() for s in skill_list],
        "total": len(skill_list),
    }


@api.get("/skills/{skill_name}")
def get_skill(skill_name: str, user: dict = Depends(get_current_user)):
    """Get details for a specific skill."""
    registry = skills.get_registry()
    skill = registry.get_skill(skill_name)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill '{skill_name}' not found")
    return skill.to_dict()


@api.post("/skills/match")
def match_skills(
    query: str = Query(..., description="User query to match against skills"),
    max_skills: int = Query(2, ge=1, le=5, description="Maximum skills to return"),
    min_confidence: float = Query(0.5, ge=0.0, le=1.0, description="Minimum confidence threshold"),
    user: dict = Depends(get_current_user),
):
    """
    Test skill matching for a query.

    Returns skills that would be activated for the given query,
    useful for debugging and understanding skill selection.
    """
    registry = skills.get_registry()
    matches = registry.find_matching_skills(
        query,
        max_skills=max_skills,
        min_confidence=min_confidence,
    )
    return {
        "query": query,
        "matches": [m.to_dict() for m in matches],
        "total_matches": len(matches),
    }


@api.get("/skills/stats")
def get_skills_stats(user: dict = Depends(get_current_user)):
    """Get skills registry statistics including activation counts."""
    registry = skills.get_registry()
    return registry.get_stats()


@api.post("/skills/reload")
def reload_skills(user: dict = Depends(get_current_user)):
    """Force reload all skills from disk."""
    from skills.registry import reload_registry

    count = reload_registry()
    return {"reloaded": count, "message": f"Reloaded {count} skills"}


@api.post("/tools/run", response_model=ToolRunOut)
def run_tool(payload: ToolRunIn, user: dict = Depends(get_current_user)):
    user_email = user.get("email")
    if not user_email:
        raise HTTPException(status_code=400, detail="Authenticated user email missing")

    registry = get_registry()
    contract = registry.get_contract(payload.tool_name)
    if not contract:
        raise HTTPException(status_code=404, detail=f"Unknown tool: {payload.tool_name}")

    validator = PreExecutionValidator(registry)
    validation = validator.validate(payload.tool_name, payload.args)
    if not validation.valid:
        raise HTTPException(status_code=400, detail=validation.to_message())

    normalized_args = contract.normalize(payload.args)
    handler = get_handler(payload.tool_name)
    if handler is None:
        raise HTTPException(status_code=500, detail=f"Tool handler not found: {payload.tool_name}")

    state = AgentState(goal=f"tool_run:{payload.tool_name}")
    search_limit = normalized_args.get("limit")
    if not isinstance(search_limit, int):
        search_limit = 30

    start = perf_counter()
    try:
        result = handler(
            normalized_args,
            state=state,
            question="",
            search_limit=search_limit,
            user_email=user_email,
            conversation_history=None,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Tool execution failed: {exc}") from exc
    duration_ms = (perf_counter() - start) * 1000

    return ToolRunOut(
        tool_name=payload.tool_name,
        args=payload.args,
        normalized_args=normalized_args,
        result=result,
        duration_ms=duration_ms,
    )


# ---------------------------------------------------------------------------
# Contact Resolution Endpoint
# ---------------------------------------------------------------------------


@api.post("/contacts/resolve")
def resolve_contacts_endpoint(
    request_data: dict[str, Any],
    user: dict = Depends(get_current_user),
):
    """
    Resolve person mentions in text to contacts.

    Extracts people from text and resolves them to database contacts using:
    - Direct name matching (fuzzy search)
    - Relationship resolution ("my daughter" → Emma)
    - Nested relationships ("my daughter's doctor" → Dr. Smith via Emma)
    - LLM disambiguation when ambiguous

    Request body:
    {
        "text": "visited my daughter's eye doctor"
    }

    Returns:
    {
        "status": "success" | "need_user_input" | "no_people" | "error",
        "text": str,
        "people_mentioned": ["my daughter's eye doctor"],
        "resolved_contacts": [
            {
                "original_text": "my daughter's eye doctor",
                "contact_id": "...",
                "display_name": "Dr. Smith",
                "matched_via": "nested_relationship",
                "confidence": "medium",
                "resolution_path": ["user", "Emma", "Dr. Smith"]
            }
        ],
        "new_contacts": [...],
        "ambiguous_contacts": [...],
        "need_user_input": {
            "kind": "disambiguation",
            "prompt": "...",
            "fields": [...]
        }
    }
    """
    from contact_resolution_service import resolve_contacts_request

    user_email = user.get("email")
    if not user_email:
        raise HTTPException(status_code=400, detail="Authenticated user email missing")

    # Add user_email to request data
    request_data["user_email"] = user_email

    return resolve_contacts_request(request_data)


# ---------------------------------------------------------------------------
# News Topics
# ---------------------------------------------------------------------------


@api.get("/news-topics")
@api.get("/mobile/news-topics")
def list_news_topics(user: dict = Depends(get_current_user)):
    topics = news_feeds.list_topics()
    return {"topics": topics}


@api.post("/news-topics")
@api.post("/mobile/news-topics")
def upsert_news_topic(
    payload: NewsTopicIn,
    user: dict = Depends(get_current_user),
):
    topic = news_feeds.upsert_topic(
        topic_id=payload.topic_id,
        label=payload.label,
        keywords=payload.keywords,
        enabled=payload.enabled,
    )
    return topic


@api.delete("/news-topics/{topic_id}")
@api.delete("/mobile/news-topics/{topic_id}")
def delete_news_topic(
    topic_id: str,
    user: dict = Depends(get_current_user),
):
    deleted = news_feeds.delete_topic(topic_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Topic not found")
    return {"ok": True}


# ---------------------------------------------------------------------------
# Emergency Stock Endpoint
# ---------------------------------------------------------------------------


@api.get("/agents/emergency-stock/run")
def run_emergency_stock_endpoint(
    _=Depends(require_service_api_key),
):
    """
    Run the emergency stock check against a Google Sheet.
    """
    from agents.emergency_stock.executor import handle_emergency_stock_request

    result = handle_emergency_stock_request()
    if result.get("status") == "error":
        raise HTTPException(status_code=500, detail=result.get("message", "Unknown error"))
    return result


@api.post("/agents/daily-briefing/run", response_model=DailyBriefingOut)
def run_daily_briefing_agent(
    payload: DailyBriefingIn,
    _=Depends(require_service_api_key),
):
    from agents.daily_briefing.executor import handle_daily_briefing_request

    result = handle_daily_briefing_request(payload.model_dump())
    if result.get("status") == "error":
        raise HTTPException(status_code=400, detail=result.get("message", "Invalid request"))
    return result
