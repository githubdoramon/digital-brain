"""Serial, restart-safe conversation embedding worker."""

from __future__ import annotations

import threading

import conversation_search
from db import get_conn
from observability.logger import get_runtime_logger

logger = get_runtime_logger(__name__)
_WORKER_THREAD: threading.Thread | None = None
_STOP_EVENT = threading.Event()


def start_worker() -> None:
    global _WORKER_THREAD
    if _WORKER_THREAD and _WORKER_THREAD.is_alive():
        return
    conversation_search.prepare_backfill()
    _STOP_EVENT.clear()
    _WORKER_THREAD = threading.Thread(target=_worker_loop, name="conversation-index", daemon=True)
    _WORKER_THREAD.start()


def stop_worker(timeout: float = 5.0) -> None:
    _STOP_EVENT.set()
    if _WORKER_THREAD:
        _WORKER_THREAD.join(timeout=timeout)


def get_worker_status() -> dict:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("""SELECT COUNT(*) AS total,
            COUNT(*) FILTER (WHERE indexed_revision <> revision) AS pending,
            COUNT(*) FILTER (WHERE attempts > 0 AND indexed_revision <> revision) AS retrying
            FROM conversation_message_index""")
        counts = cur.fetchone()
    return {
        "job_type": "conversation_index",
        "worker_alive": bool(_WORKER_THREAD and _WORKER_THREAD.is_alive()),
        **dict(counts or {}),
    }


def _worker_loop() -> None:
    while not _STOP_EVENT.is_set():
        try:
            if conversation_search.process_due_once():
                continue
        except Exception:
            # Keep source text, identifiers, embedding responses and credentials out of logs.
            logger.warning("[conversation_index] indexing failed; retry scheduled")
        _STOP_EVENT.wait(2)
