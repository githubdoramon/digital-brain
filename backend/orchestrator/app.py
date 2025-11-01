from __future__ import annotations

from contextlib import asynccontextmanager
from time import perf_counter
from typing import List, Optional

import os

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware

import llm
import retrieval
from auth import get_current_user, maybe_get_current_user
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
    ResolveIn,
    SearchIn,
    TodoIn,
)


ORCHESTRATOR_API_KEY = os.getenv("ORCHESTRATOR_API_KEY")


def require_service_api_key(x_service_api_key: str = Header(default="", alias="x-service-api-key")) -> None:
    if not ORCHESTRATOR_API_KEY:
        raise HTTPException(status_code=500, detail="Service API key is not configured")
    if x_service_api_key != ORCHESTRATOR_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid service API key")


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


@api.post("/ingest/meetings")
def ingest_meetings(
    meetings: List[MeetingIn],
    _: None = Depends(require_service_api_key),
):
    ids = retrieval.ingest_meetings(meetings)
    return {"ok": True, "ids": ids}


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
    session_id = payload.session_id or "<none>"
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
        user_id=user_email or "default_user",
        session_id=payload.session_id,
    )

    elapsed = perf_counter() - start_time
    search_results = bundle.get("search_results")
    search_count = len(search_results) if isinstance(search_results, list) else "n/a"
    print(
        f"[ask] complete session={session_id} user={user_email} elapsed={elapsed:.3f}s search_results={search_count}"
    )

    return AskOut(**bundle)
