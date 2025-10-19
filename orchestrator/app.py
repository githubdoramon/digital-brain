from __future__ import annotations

from fastapi import FastAPI

import llm
import retrieval
from db import get_conn
from schemas import AskIn, AskOut, ContactIn, EventIn, GetIn, PlaceIn, ResolveIn, SearchIn

api = FastAPI(title="Personal Memory Orchestrator", version="0.3")


@api.on_event("startup")
def check_database_connection() -> None:
    # Ensure we can connect at startup; this raises early if DB_DSN is misconfigured.
    with get_conn():
        pass


# --------------------------- Ingest endpoints ---------------------------
@api.post("/ingest/contact")
def ingest_contact(c: ContactIn):
    retrieval.ingest_contact(c)
    return {"ok": True}


@api.post("/ingest/place")
def ingest_place(p: PlaceIn):
    retrieval.ingest_place(p)
    return {"ok": True}


@api.post("/ingest/event")
def ingest_event(e: EventIn):
    retrieval.ingest_event(e)
    return {"ok": True, "id": e.id}


# --------------------------- Tool-friendly endpoints ---------------------------
@api.post("/resolve")
def resolve(payload: ResolveIn):
    return retrieval.resolve_query(
        payload.text,
        need_contacts=payload.need_contacts,
        need_places=payload.need_places,
    )


@api.post("/search")
def search(payload: SearchIn):
    return retrieval.search_memories(
        query=payload.query,
        people=payload.people,
        place_ids=payload.place_ids,
        time_start=payload.time_start,
        time_end=payload.time_end,
        limit=payload.limit or 5,
    )


@api.post("/get")
def get_events(payload: GetIn):
    return {"events": retrieval.get_events(payload.ids)}


# --------------------------- Ask endpoint (LLM-powered) ---------------------------
@api.post("/ask", response_model=AskOut)
def ask(payload: AskIn):
    bundle = llm.answer_question(payload.question, search_limit=payload.limit or 3)
    return AskOut(**bundle)
