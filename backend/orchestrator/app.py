from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from db import get_conn
from db_migrations import run_pending_migrations
from observability.log_stream import configure_logging, install_stdout_logger
from observability.logger import get_runtime_logger
from routes.automation import create_automation_router
from routes.chat import create_chat_router
from routes.contacts import create_contacts_router
from routes.daily_briefing import create_daily_briefing_router
from routes.documents import create_documents_router
from routes.events import create_events_router
from routes.news import create_news_router
from routes.places import create_places_router
from routes.system import create_system_router
from routes.todos import create_todos_router
from routes.user import create_user_router

logger = get_runtime_logger(__name__)


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
