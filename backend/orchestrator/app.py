from __future__ import annotations

import os
from contextlib import asynccontextmanager
from dataclasses import dataclass

import fastapi
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import conversations
from db import get_conn
from db_migrations import run_pending_migrations
from llm_helpers import warm_fast_model
from observability.log_stream import configure_logging, install_stdout_logger
from observability.logger import get_runtime_logger
from routes.automation import create_automation_router
from routes.chat import create_chat_router
from routes.contacts import create_contacts_router
from routes.daily_briefing import create_daily_briefing_router
from routes.documents import create_documents_router
from routes.evals import create_evals_router
from routes.events import create_events_router
from routes.news import create_news_router
from routes.places import create_places_router
from routes.system import create_system_router
from routes.todos import create_todos_router
from routes.user import create_user_router
from schemas import AskIn

logger = get_runtime_logger(__name__)


@dataclass
class _SessionContext:
    session_id: str
    question: str
    is_new_session: bool
    is_reset_only: bool
    user_email: str
    original_question: str


def _strip_command_prefix(message: str) -> str:
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
    """Compatibility helper kept for command stripping regressions."""
    from commands.parser import parse_command

    requested_thread_id = payload.thread_id or payload.session_id
    question = payload.question
    is_new_session = False
    parsed_command = parse_command(question)
    reset_requested = parsed_command is not None and parsed_command.command == "new"
    if parsed_command is not None and reset_requested:
        question = parsed_command.args or ""
        force_new_session = True

    if requested_thread_id and not force_new_session:
        try:
            thread = conversations.ensure_thread(requested_thread_id, user_email)
        except LookupError as exc:
            raise fastapi.HTTPException(status_code=404, detail="Conversation thread not found") from exc
        except PermissionError as exc:
            raise fastapi.HTTPException(
                status_code=403,
                detail="Conversation thread does not belong to user",
            ) from exc
    elif requested_thread_id and force_new_session:
        thread = conversations.ensure_thread(None, user_email)
        is_new_session = True
    else:
        if force_new_session:
            question = f"/new {question}".strip()
        thread, is_new_session, question = conversations.resolve_main_session(user_email, question)

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


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    install_stdout_logger()
    try:
        run_pending_migrations()
    except Exception:
        if os.getenv("DB_AUTO_MIGRATE_FAIL_FAST", "true").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }:
            raise
        logger.exception("Database migration failed; continuing startup")

    with get_conn():
        pass

    try:
        warm_fast_model()
    except Exception:
        logger.exception("Fast-model warmup failed; continuing startup")

    yield


api = FastAPI(title="Personal Memory Orchestrator", version="0.3", lifespan=lifespan)

api.include_router(create_daily_briefing_router())
api.include_router(create_news_router())
api.include_router(create_chat_router())
api.include_router(create_contacts_router())
api.include_router(create_places_router())
api.include_router(create_todos_router())
api.include_router(create_events_router())
api.include_router(create_documents_router())
api.include_router(create_evals_router())
api.include_router(create_system_router())
api.include_router(create_user_router())
api.include_router(create_automation_router())

api.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
