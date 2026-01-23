from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from datetime import datetime
from time import perf_counter
from typing import Any
from uuid import uuid4

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
import devices as devices_service
import documents
import events as events_service
import immich_client
import llm
import places as places_service
import skills
import telegram_bot
import todos as todos_service
from auth import get_current_user
from db import get_conn
from notifications.preferences import get_push_settings, update_push_settings
from schemas import (
    AskIn,
    AskOut,
    ContactIn,
    ContactMergeIn,
    ContactRelationshipIn,
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
    NotificationSettingsOut,
    PlaceIn,
    PushNotificationsUpdateIn,
    ServiceVersionCollection,
    ThreadCreate,
    ThreadDetailOut,
    ThreadOut,
    ThreadUpdate,
    TodoIn,
)
from versioning import get_service_versions

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


@asynccontextmanager
async def lifespan(app: FastAPI):
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
def ingest_contact(c: ContactIn, user: dict = Depends(get_current_user)):
    contacts_service.ingest_contact(c)
    return {"ok": True}


@api.get("/contacts")
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
def get_contact(contact_id: str, user: dict = Depends(get_current_user)):
    contact = contacts_service.get_contact(contact_id)
    if contact is None:
        raise HTTPException(status_code=404, detail="Contact not found")
    return contact


@api.delete("/contacts/{contact_id}")
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
        print(f"[telegram_bot] upload error={exc}")
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@api.post("/ingest/place")
def ingest_place(p: PlaceIn, user: dict = Depends(get_current_user)):
    places_service.ingest_place(p)
    return {"ok": True}


@api.post("/ingest/todo")
def ingest_todo(todo: TodoIn, user: dict = Depends(get_current_user)):
    todos_service.ingest_todo(todo)
    return {"ok": True, "id": todo.todo_id}


@api.get("/todos")
def list_todos(user: dict = Depends(get_current_user)):
    return {"todos": todos_service.list_todos()}


@api.get("/todos/{todo_id}")
def get_todo(todo_id: str, user: dict = Depends(get_current_user)):
    todo = todos_service.get_todo(todo_id)
    if not todo:
        raise HTTPException(status_code=404, detail="Todo not found")
    return todo


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


# Removed: /threads/{thread_id}/events endpoint - part of old event_capture system
# Use /event command instead


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
        print(f"[documents] Failed to ingest document: {exc}")
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
        print(f"[documents] Failed to ingest document: {exc}")
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
        print(f"[documents] Failed to update document metadata: {exc}")
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
def confirm_event_command(
    payload: EventCommandConfirmation,
    user: dict = Depends(get_current_user),
):
    """
    Confirm and create an event from /event command.

    This endpoint receives the user's confirmation of the extracted event data,
    creates any new entities (contacts, places), and stores the event.
    """
    user_email = user.get("email")
    if not user_email:
        raise HTTPException(status_code=400, detail="Authenticated user email missing")

    if not payload.confirmed:
        from commands.storage import clear_pending_event_by_preview_id, delete_command_data

        delete_command_data(payload.preview_id)
        clear_pending_event_by_preview_id(payload.preview_id)
        return EventCommandResult(
            success=False,
            error="Event creation cancelled by user",
        )

    # Retrieve stored command data
    from datetime import datetime

    from commands.storage import (
        clear_pending_event_by_preview_id,
        delete_command_data,
        get_command_data,
    )

    command_data = get_command_data(payload.preview_id)
    if not command_data:
        raise HTTPException(
            status_code=404,
            detail="Event preview not found or expired. Please try the /event command again.",
        )

    extracted = command_data["extracted"]
    resolution = command_data["resolution"]

    # Apply modifications to extracted data
    if payload.modifications:
        mods = payload.modifications
        if "title" in mods:
            extracted["title"] = mods["title"]
        if "summary" in mods:
            extracted["summary"] = mods["summary"]
        if "when" in mods:
            extracted["when"] = (
                datetime.fromisoformat(mods["when"].replace("Z", "+00:00"))
                if mods["when"]
                else None
            )
        if "where" in mods:
            extracted["where"] = mods["where"]
        if "tags" in mods:
            extracted["tags"] = mods["tags"]

    try:
        # 1. Create new contacts
        created_contacts = []
        contact_id_map = {}

        for new_contact in resolution["new_entities"]["contacts"]:
            display_name = new_contact["display_name"]
            inferred_profession = new_contact.get("inferred_profession")
            comments = None
            if inferred_profession:
                comments = f"Inferred profession: {inferred_profession}"
            contact_id = f"contact:{display_name.lower().replace(' ', '_')}#{uuid4().hex[:6]}"

            contact_in = ContactIn(
                contact_id=contact_id,
                display_name=display_name,
                aliases=[],
                emails=[],
                phones=[],
                links=[],
                tags=[],
                comments=comments,
            )

            contacts_service.ingest_contact(contact_in)
            created_contacts.append({"contact_id": contact_id, "display_name": display_name})
            contact_id_map[display_name] = contact_id

        # 1b. Create confirmed relationships (after contacts exist)
        confirmed_relationships = []
        if payload.modifications:
            confirmed_relationships = payload.modifications.get("confirmed_relationships") or []

        if confirmed_relationships:
            existing_contact_map = {
                contact["display_name"]: contact["contact_id"]
                for contact in resolution.get("contacts", [])
                if contact.get("display_name") and contact.get("contact_id")
            }
            all_contact_map = {**existing_contact_map, **contact_id_map}

            def _resolve_relationship_contact_id(
                rel: dict[str, Any],
                key_prefix: str,
            ) -> str | None:
                contact_id = rel.get(f"{key_prefix}_contact_id")
                if contact_id:
                    return contact_id
                display_name = rel.get(f"{key_prefix}_display_name")
                if display_name:
                    return all_contact_map.get(display_name)
                return None

            for relationship in confirmed_relationships:
                if not isinstance(relationship, dict):
                    continue

                from_contact_id = _resolve_relationship_contact_id(relationship, "from")
                to_contact_id = _resolve_relationship_contact_id(relationship, "to")
                relationship_type = relationship.get("relationship_type") or relationship.get(
                    "type"
                )
                reciprocal_type = relationship.get("reciprocal_type") or relationship.get(
                    "other_type"
                )

                if not from_contact_id or not to_contact_id or not relationship_type:
                    continue

                rel_in = ContactRelationshipIn(
                    relationship_id=f"rel:{uuid4().hex}",
                    from_contact_id=from_contact_id,
                    to_contact_id=to_contact_id,
                    relationship_type=relationship_type,
                    reciprocal_type=reciprocal_type,
                )
                contacts_service.upsert_contact_relationship(rel_in)

        # 2. Create new places
        created_places = []
        place_id_map = {}

        for new_place in resolution["new_entities"]["places"]:
            place_name = new_place["name"]
            place_id = f"plc_{place_name.lower().replace(' ', '_')}_{uuid4().hex[:6]}"

            place_in = PlaceIn(
                place_id=place_id,
                name=place_name,
                city=None,
                country=None,
                lat=None,
                lon=None,
                geohash=None,
            )

            places_service.ingest_place(place_in)
            created_places.append({"place_id": place_id, "name": place_name})
            place_id_map[place_name] = place_id

        # 3. Build list of all contact IDs (existing + new)
        all_contact_ids = []
        for existing_contact in resolution["contacts"]:
            all_contact_ids.append(existing_contact["contact_id"])
        for created_contact in created_contacts:
            all_contact_ids.append(created_contact["contact_id"])

        # 4. Get place_id (existing or newly created)
        place_id = None
        where = extracted.get("where")
        if where:
            place_id = place_id_map.get(where)

        # 5. Create the event
        event_id = f"event:{uuid4().hex}"
        when = extracted.get("when")

        event_in = EventIn(
            id=event_id,
            startDate=when if when else datetime.now(),
            endDate=None,
            placeId=place_id,
            people=all_contact_ids,
            tags=extracted.get("tags", []),
            types=extracted.get("types", ["generic"]),
            title=extracted.get("title", ""),
            summary=extracted.get("summary", ""),
            raw={"source": "event_command"},
        )

        events_service.ingest_event(event_in)

        # Clean up stored data
        delete_command_data(payload.preview_id)
        clear_pending_event_by_preview_id(payload.preview_id)

        return EventCommandResult(
            success=True,
            event_id=event_id,
            created_contacts=created_contacts,
            created_places=created_places,
        )

    except Exception as e:
        print(f"[event_confirm] Failed to create event: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create event: {str(e)}")


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


def _resolve_session_context(payload: AskIn, user_email: str) -> _SessionContext:
    """
    Resolve session context from payload. Handles both explicit thread_id
    and main session mode with timeout/command parsing.

    Raises HTTPException on thread not found or permission errors.
    """
    requested_thread_id = payload.thread_id or payload.session_id
    question = payload.question
    is_new_session = False

    if requested_thread_id:
        # Explicit thread - existing behavior
        try:
            thread = conversations.ensure_thread(requested_thread_id, user_email)
        except LookupError:
            raise HTTPException(status_code=404, detail="Conversation thread not found")
        except PermissionError:
            raise HTTPException(
                status_code=403, detail="Conversation thread does not belong to user"
            )
    else:
        # Main session mode - resolve with timeout and command parsing
        thread, is_new_session, question = conversations.resolve_main_session(user_email, question)

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
        "detailed_events": [],
        "session_id": ctx.session_id,
        "thread_id": ctx.session_id,
        "is_new_session": True,
    }


def _event_pending_key(user_email: str, thread_id: str | None) -> str:
    resolved_thread = thread_id or "main"
    return f"{user_email}:{resolved_thread}"


def _handle_pending_event(
    question: str,
    user_email: str,
    user: dict,
    thread_id: str | None,
    pending_event_id: str | None,
) -> dict[str, Any] | None:
    from uuid import uuid4

    from commands import get_command_registry, parse_command
    from commands.storage import (
        clear_pending_event,
        delete_command_data,
        get_command_data,
        get_pending_event,
        store_command_data,
    )

    if parse_command(question):
        return None

    key = _event_pending_key(user_email, thread_id)
    preview_id = pending_event_id or get_pending_event(key)
    if not preview_id:
        return None

    command_data = get_command_data(preview_id)
    if not command_data:
        clear_pending_event(key)
        return None

    clarification_id = f"event:clarification:{uuid4().hex[:8]}"
    store_command_data(clarification_id, command_data)
    delete_command_data(preview_id)
    clear_pending_event(key)

    original_message = command_data.get("original_message") or question
    combined_message = (
        f"/event {original_message}\n\nAdditional details: {question}\n\n"
        f"[clarification_id:{clarification_id}]"
    )

    parsed_cmd = parse_command(combined_message)
    if not parsed_cmd:
        return None

    registry = get_command_registry()
    context = {
        "user_email": user_email,
        "user": user,
        "thread_id": thread_id,
        "event_pending_key": key,
    }
    return registry.execute(parsed_cmd, context)


def _handle_command(
    question: str,
    user_email: str,
    user: dict,
    thread_id: str | None,
) -> dict[str, Any] | None:
    """
    Check if the question is a command and handle it.

    Returns command result dict if it's a command, None otherwise.
    """
    from commands import get_command_registry, parse_command

    parsed_cmd = parse_command(question)
    if not parsed_cmd or parsed_cmd.command == "new":
        # /new is handled in session resolution, not here
        return None

    # Handle non-/new commands (like /event)
    registry = get_command_registry()
    context = {
        "user_email": user_email,
        "user": user,
        "thread_id": thread_id,
        "event_pending_key": _event_pending_key(user_email, thread_id),
    }
    return registry.execute(parsed_cmd, context)


@api.post("/ask", response_model=AskOut)
@api.post("/mobile/ask", response_model=AskOut)
async def ask(payload: AskIn, user: dict = Depends(get_current_user)):
    start_time = perf_counter()
    user_email = user.get("email")
    if not user_email:
        raise HTTPException(status_code=400, detail="Authenticated user email missing")

    # Check for commands or pending event refinements before session resolution
    try:
        command_result = _handle_pending_event(
            payload.question,
            user_email,
            user,
            payload.thread_id or payload.session_id,
            payload.pending_event_id,
        )
        if not command_result:
            command_result = _handle_command(
                payload.question,
                user_email,
                user,
                payload.thread_id or payload.session_id,
            )
        if command_result:
            from commands.storage import get_pending_event

            pending_event_id = get_pending_event(
                _event_pending_key(user_email, payload.thread_id or payload.session_id)
            )
            # Return command result
            return AskOut(
                question=payload.question,
                answer="",
                resolution={},
                search_results=[],
                detailed_events=[],
                thread_id=payload.thread_id or "",
                session_id=payload.session_id or "",
                is_new_session=False,
                command_result=command_result,
                pending_event_id=pending_event_id,
            )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    ctx = _resolve_session_context(payload, user_email)

    # Handle /new command with no actual message
    if ctx.is_reset_only:
        print(f"[ask] session reset session={ctx.session_id} user={user_email}")
        return AskOut(**_make_reset_bundle(ctx))

    limit = payload.limit or 3
    preview = ctx.question.strip().replace("\n", " ")
    if len(preview) > 120:
        preview = preview[:117] + "..."

    mode = "main_session" if not (payload.thread_id or payload.session_id) else "thread"
    print(
        f"[ask] start session={ctx.session_id} user={user_email} limit={limit} mode={mode} is_new={ctx.is_new_session} question={preview!r}"
    )

    bundle = await llm.answer_question(
        ctx.question,
        search_limit=limit,
        user_id=user_email,
        session_id=ctx.session_id,
        user_email=user_email,
    )
    bundle["thread_id"] = ctx.session_id
    bundle["session_id"] = ctx.session_id
    bundle["is_new_session"] = ctx.is_new_session

    elapsed = perf_counter() - start_time
    search_results = bundle.get("search_results")
    search_count = len(search_results) if isinstance(search_results, list) else "n/a"
    print(
        f"[ask] complete session={ctx.session_id} user={user_email} elapsed={elapsed:.3f}s search_results={search_count}"
    )

    from commands.storage import get_pending_event

    bundle["pending_event_id"] = get_pending_event(
        _event_pending_key(user_email, payload.thread_id or payload.session_id)
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
        command_result = _handle_pending_event(
            payload.question,
            user_email,
            user,
            payload.thread_id or payload.session_id,
            payload.pending_event_id,
        )
        if not command_result:
            command_result = _handle_command(
                payload.question,
                user_email,
                user,
                payload.thread_id or payload.session_id,
            )
        if command_result:
            from commands.storage import get_pending_event

            pending_event_id = get_pending_event(
                _event_pending_key(user_email, payload.thread_id or payload.session_id)
            )

            # Return command result as SSE stream
            async def command_generator():
                bundle = {
                    "question": payload.question,
                    "answer": "",
                    "resolution": {},
                    "search_results": [],
                    "detailed_events": [],
                    "thread_id": payload.thread_id or "",
                    "session_id": payload.session_id or "",
                    "is_new_session": False,
                    "command_result": command_result,
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

    ctx = _resolve_session_context(payload, user_email)

    # Handle /new command with no actual message
    if ctx.is_reset_only:
        print(f"[ask/stream] session reset session={ctx.session_id} user={user_email}")
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

    limit = payload.limit or 3

    preview = ctx.question.strip().replace("\n", " ")
    if len(preview) > 120:
        preview = preview[:117] + "..."
    mode = "main_session" if not (payload.thread_id or payload.session_id) else "thread"
    print(
        f"[ask/stream] start session={ctx.session_id} user={user_email} limit={limit} mode={mode} is_new={ctx.is_new_session} question={preview!r}"
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
            ):
                if event.get("type") == "done":
                    from commands.storage import get_pending_event

                    bundle = event.get("bundle", {})
                    bundle["thread_id"] = ctx.session_id
                    bundle["session_id"] = ctx.session_id
                    bundle["is_new_session"] = ctx.is_new_session
                    bundle["pending_event_id"] = get_pending_event(
                        _event_pending_key(user_email, payload.thread_id or payload.session_id)
                    )
                    event["bundle"] = bundle

                yield f"data: {json.dumps(event, default=str)}\n\n"

                if event.get("type") == "done":
                    elapsed = perf_counter() - start_time
                    print(
                        f"[ask/stream] complete session={ctx.session_id} user={user_email} elapsed={elapsed:.3f}s"
                    )
        except Exception as exc:
            print(f"[ask/stream] error session={ctx.session_id}: {exc}")
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
def list_conversation_threads(user: dict = Depends(get_current_user)):
    user_email = user.get("email")
    if not user_email:
        raise HTTPException(status_code=400, detail="Authenticated user email missing")
    threads = conversations.list_threads(user_email)
    return [ThreadOut(**thread) for thread in threads]


@api.post("/threads", response_model=ThreadOut)
def create_conversation_thread(payload: ThreadCreate, user: dict = Depends(get_current_user)):
    user_email = user.get("email")
    if not user_email:
        raise HTTPException(status_code=400, detail="Authenticated user email missing")
    thread = conversations.ensure_thread(None, user_email, title=payload.title)
    return ThreadOut(**thread)


@api.get("/threads/{thread_id}", response_model=ThreadDetailOut)
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
        "status": "success" | "needs_clarification" | "no_people" | "error",
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
        "ambiguous_contacts": [...]
    }
    """
    from agents.contacts.executor import handle_resolve_contacts_request

    user_email = user.get("email")
    if not user_email:
        raise HTTPException(status_code=400, detail="Authenticated user email missing")

    # Add user_email to request data
    request_data["user_email"] = user_email

    return handle_resolve_contacts_request(request_data)


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
