from __future__ import annotations

from contextlib import asynccontextmanager
from time import perf_counter
from typing import List, Optional

import json
import os

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

import conversations
import documents
import llm
import retrieval
from auth import get_current_user
from db import get_conn
from schemas import (
    AskIn,
    AskOut,
    ContactIn,
    ContactRelationshipIn,
    EventIn,
    GetIn,
    MeetingIn,
    PlaceIn,
    DocumentCollection,
    DocumentDetailOut,
    DocumentSearchIn,
    ResolveIn,
    SearchIn,
    TodoIn,
    ThreadCreate,
    ThreadDetailOut,
    ThreadOut,
    ThreadUpdate,
    ServiceVersionCollection,
)
from versioning import get_service_versions


ORCHESTRATOR_API_KEY = os.getenv("ORCHESTRATOR_API_KEY")


def require_service_api_key(x_service_api_key: str = Header(default="", alias="x-service-api-key")) -> None:
    if not ORCHESTRATOR_API_KEY:
        raise HTTPException(status_code=500, detail="Service API key is not configured")
    if x_service_api_key != ORCHESTRATOR_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid service API key")


def _parse_tags_payload(raw: Optional[str]) -> List[str]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        # Fallback: treat as comma-separated string
        parsed = [part.strip() for part in raw.split(",")]
    if not isinstance(parsed, list):
        raise ValueError("tags must be an array")
    tags: List[str] = []
    for item in parsed:
        if item is None:
            continue
        candidate = str(item).strip()
        if candidate:
            tags.append(candidate)
    return tags


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


# --------------------------- System endpoints ---------------------------
@api.get("/system/versions", response_model=ServiceVersionCollection)
def read_service_versions(user: dict = Depends(get_current_user)):
    return get_service_versions()


# --------------------------- Ingest endpoints ---------------------------
@api.post("/ingest/contact")
def ingest_contact(c: ContactIn, user: dict = Depends(get_current_user)):
    retrieval.ingest_contact(c)
    return {"ok": True}


@api.post("/ingest/contact-relationship")
def ingest_contact_relationship(r: ContactRelationshipIn, user: dict = Depends(get_current_user)):
    retrieval.upsert_contact_relationship(r)
    return {"ok": True}


@api.delete("/contact-relationships/{relationship_id}")
def delete_contact_relationship(relationship_id: str, user: dict = Depends(get_current_user)):
    deleted = retrieval.delete_contact_relationship(relationship_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Relationship not found")
    return {"ok": True}


@api.get("/contacts")
def list_contacts(user: dict = Depends(get_current_user)):
    return {"contacts": retrieval.list_contacts()}


@api.get("/contacts/{contact_id}")
def get_contact(contact_id: str, user: dict = Depends(get_current_user)):
    contact = retrieval.get_contact(contact_id)
    if contact is None:
        raise HTTPException(status_code=404, detail="Contact not found")
    return contact


@api.delete("/contacts/{contact_id}")
def delete_contact(contact_id: str, user: dict = Depends(get_current_user)):
    deleted = retrieval.delete_contact(contact_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Contact not found")
    return {"ok": True}


@api.get("/contacts/{contact_id}/relationships")
def get_contact_relationships(contact_id: str, user: dict = Depends(get_current_user)):
    return {"relationships": retrieval.list_contact_relationships(contact_id)}


@api.post("/ingest/place")
def ingest_place(p: PlaceIn, user: dict = Depends(get_current_user)):
    retrieval.ingest_place(p)
    return {"ok": True}


@api.post("/ingest/todo")
def ingest_todo(todo: TodoIn, user: dict = Depends(get_current_user)):
    retrieval.ingest_todo(todo)
    return {"ok": True, "id": todo.todo_id}


@api.get("/todos")
def list_todos(user: dict = Depends(get_current_user)):
    return {"todos": retrieval.list_todos()}


@api.get("/todos/{todo_id}")
def get_todo(todo_id: str, user: dict = Depends(get_current_user)):
    todo = retrieval.get_todo(todo_id)
    if not todo:
        raise HTTPException(status_code=404, detail="Todo not found")
    return todo


@api.delete("/todos/{todo_id}")
def delete_todo(todo_id: str, user: dict = Depends(get_current_user)):
    deleted = retrieval.delete_todo(todo_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Todo not found")
    return {"ok": True}


@api.post("/ingest/event")
def ingest_event(e: EventIn, user: dict = Depends(get_current_user)):
    retrieval.ingest_event(e)
    return {"ok": True, "id": e.id}


@api.post("/documents", response_model=DocumentDetailOut)
async def upload_document(
    title: str = Form(...),
    tags: Optional[str] = Form(None),
    summary: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
):
    try:
        parsed_tags = _parse_tags_payload(tags)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    try:
        document = documents.ingest_document(
            title=title,
            tags=parsed_tags,
            summary=summary,
            description=description,
            upload=file,
        )
    except documents.DocumentProcessingError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        print(f"[documents] Failed to ingest document: {exc}")
        raise HTTPException(status_code=500, detail="Failed to ingest document")

    return DocumentDetailOut(**document)


@api.get("/documents", response_model=DocumentCollection)
def list_documents(user: dict = Depends(get_current_user)):
    docs = documents.list_documents()
    return DocumentCollection(documents=docs)


@api.get("/documents/{document_id}", response_model=DocumentDetailOut)
def get_document_detail(document_id: str, user: dict = Depends(get_current_user)):
    document = documents.get_document(document_id)
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


@api.post("/ingest/meetings")
def ingest_meetings(
    meetings: List[MeetingIn],
    _: None = Depends(require_service_api_key),
):
    ids = retrieval.ingest_meetings(meetings)
    return {"ok": True, "ids": ids}


@api.get("/meetings/{meeting_id}")
def get_meeting(meeting_id: str, user: dict = Depends(get_current_user)):
    meeting = retrieval.get_meeting(meeting_id)
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")
    return meeting


# --------------------------- Tool-friendly endpoints ---------------------------
@api.post("/resolve")
def resolve(payload: ResolveIn, user: dict = Depends(get_current_user)):
    return retrieval.resolve_query(
        payload.text,
        need_contacts=payload.need_contacts,
        need_places=payload.need_places,
    )


@api.post("/search")
def search(payload: SearchIn, user: dict = Depends(get_current_user)):
    return retrieval.search_memories(
        query=payload.query,
        people=payload.people,
        place_ids=payload.place_ids,
        time_start=payload.time_start,
        time_end=payload.time_end,
        limit=payload.limit or 5,
    )


@api.post("/get")
def get_events(payload: GetIn, user: dict = Depends(get_current_user)):
    return {"events": retrieval.get_events(payload.ids)}


# --------------------------- Ask endpoint (LLM-powered) ---------------------------
@api.post("/ask", response_model=AskOut)
async def ask(payload: AskIn, user: dict = Depends(get_current_user)):
    start_time = perf_counter()
    user_email = user.get("email")
    if not user_email:
        raise HTTPException(status_code=400, detail="Authenticated user email missing")

    requested_thread_id = payload.thread_id or payload.session_id
    try:
        thread = conversations.ensure_thread(requested_thread_id, user_email)
    except LookupError:
        raise HTTPException(status_code=404, detail="Conversation thread not found")
    except PermissionError:
        raise HTTPException(status_code=403, detail="Conversation thread does not belong to user")

    session_id = thread["id"]
    limit = payload.limit or 3
    preview = payload.question.strip().replace("\n", " ")
    if len(preview) > 120:
        preview = preview[:117] + "..."

    print(
        f"[ask] start session={session_id} user={user_email} limit={limit} question={preview!r}"
    )

    bundle = await llm.answer_question(
        payload.question,
        search_limit=limit,
        user_id=user_email,
        session_id=session_id,
        user_email=user_email,
    )
    bundle["thread_id"] = session_id
    bundle["session_id"] = session_id

    elapsed = perf_counter() - start_time
    search_results = bundle.get("search_results")
    search_count = len(search_results) if isinstance(search_results, list) else "n/a"
    print(
        f"[ask] complete session={session_id} user={user_email} elapsed={elapsed:.3f}s search_results={search_count}"
    )

    return AskOut(**bundle)


@api.get("/threads", response_model=List[ThreadOut])
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
    thread = conversations.ensure_thread(thread_id, user_email)
    normalized_title = payload.title.strip() if payload.title else None
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE conversation_threads
            SET title = %s, updated_at = NOW()
            WHERE id = %s AND user_email = %s
            RETURNING id, user_email, title, created_at, updated_at
            """,
            (normalized_title, thread_id, user_email),
        )
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
