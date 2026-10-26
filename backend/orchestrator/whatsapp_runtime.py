"""Managed provider processes plus independent, checkpointed archive worker."""

from __future__ import annotations

import multiprocessing
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from db import get_conn
from messaging_archive import drain_once, runtime_state
from messaging_normalization import ConnectionState, Provider
from observability.logger import get_runtime_logger

logger = get_runtime_logger(__name__)
_STOP = threading.Event()
_THREADS: list[threading.Thread] = []
_QR_LOCK = threading.Lock()
_QR: dict[str, tuple[str, float]] = {}
_ROOT = Path(os.getenv("WHATSAPP_STORAGE_DIR", "storage/whatsapp"))
_PASSWORDS: dict[str, tuple[str, int, str | None, float]] = {}


@dataclass
class Session:
    process: Any
    pipe: Any
    generation: int
    started_at: float


def pairing_qr(account_id: str) -> str | None:
    with _QR_LOCK:
        value = _QR.get(account_id)
        if not value or value[1] <= time.monotonic():
            _QR.pop(account_id, None)
            return None
        return value[0]


def password_challenge(account_id: str) -> str | None:
    with _QR_LOCK:
        value = _PASSWORDS.get(account_id)
        return value[0] if value and value[3] > time.monotonic() else None


def submit_password(account_id: str, generation: int, challenge: str, password: str):
    with _QR_LOCK:
        value = _PASSWORDS.get(account_id)
        if (
            not value
            or value[0] != challenge
            or value[1] != generation
            or value[3] <= time.monotonic()
            or value[2] is not None
        ):
            raise LookupError("login_expired")
        _PASSWORDS[account_id] = (challenge, generation, password, value[3])


def clear_qr(account_id: str):
    with _QR_LOCK:
        _QR.pop(account_id, None)
        _PASSWORDS.pop(account_id, None)


def _stop_session(session: Session):
    if session.process.is_alive():
        session.process.terminate()
        session.process.join(timeout=3)
        if session.process.is_alive():
            session.process.kill()
            session.process.join(timeout=2)
    session.process.close()
    session.pipe.close()


def _supervise():
    from auth import ALLOWED_USERS
    from telegram_native import configured
    from telegram_native import run as telegram_run
    from whatsapp_native import run

    sessions: dict[str, Session] = {}
    retry_after: dict[str, float] = {}
    context = multiprocessing.get_context("spawn")
    try:
        # Hold a dedicated connection/lease for the entire process lifetime.
        # Only one server worker/replica may own WhatsApp sessions at a time.
        with get_conn() as lease, lease.cursor() as lock:
            lock.execute("SELECT pg_try_advisory_lock(732141973) AS acquired")
            if not lock.fetchone()["acquired"]:
                return
            lease.commit()
            while not _STOP.is_set():
                lock.execute("SELECT 1")  # Detect lease connection loss before continuing.
                lease.commit()
                with get_conn() as conn, conn.cursor() as cur:
                    cur.execute(
                        "SELECT account_id, generation, provider FROM messaging_accounts WHERE enabled AND user_email = ANY(%s)",
                        (list(ALLOWED_USERS),),
                    )
                    rows = cur.fetchall()
                    desired = {row["account_id"]: row["generation"] for row in rows}
                    providers = {row["account_id"]: Provider(row["provider"]) for row in rows}
                for key, session in list(sessions.items()):
                    if desired.get(key) != session.generation or not session.process.is_alive():
                        _stop_session(session)
                        clear_qr(key)
                        del sessions[key]
                        retry_after[key] = time.monotonic() + 10
                        if key in desired:
                            runtime_state(
                                key,
                                desired[key],
                                ConnectionState.CONNECTING,
                                "connection_restarting",
                            )
                        continue
                    with _QR_LOCK:
                        password = _PASSWORDS.get(key)
                        if password and password[2] is not None:
                            if password[1] == session.generation and password[3] > time.monotonic():
                                session.pipe.send(
                                    {"challenge": password[0], "password": password[2]}
                                )
                            _PASSWORDS[key] = (password[0], password[1], None, password[3])
                    while session.pipe.poll():
                        try:
                            event = session.pipe.recv()
                        except (EOFError, OSError):
                            break
                        if event.get("kind") == "qr":
                            with _QR_LOCK:
                                _QR[key] = (
                                    event["code"],
                                    time.monotonic() + min(int(event["timeout"]), 120),
                                )
                        elif event.get("kind") == "clear_qr":
                            clear_qr(key)
                        elif event.get("kind") == "password_required":
                            with _QR_LOCK:
                                _PASSWORDS[key] = (
                                    event["challenge"],
                                    session.generation,
                                    None,
                                    time.monotonic() + 300,
                                )
                        elif event.get("kind") == "clear_password":
                            with _QR_LOCK:
                                _PASSWORDS.pop(key, None)
                        elif event.get("kind") == "capture_failure":
                            logger.warning(
                                "[messaging] capture_failed provider=whatsapp stage=%s category=%s reason=%s",
                                event["stage"], event["category"], event["reason"],
                            )
                        elif event.get("kind") == "error":
                            runtime_state(
                                key,
                                session.generation,
                                ConnectionState.ATTENTION,
                                "connection_failed",
                            )
                for key, generation in desired.items():
                    if key in sessions or retry_after.get(key, 0) > time.monotonic():
                        continue
                    telegram = providers[key] == Provider.TELEGRAM
                    if telegram and not configured():
                        runtime_state(key, generation, ConnectionState.ATTENTION, "setup_required")
                        continue
                    root = (
                        Path(os.getenv("TELEGRAM_STORAGE_DIR", "storage/telegram"))
                        if telegram
                        else _ROOT
                    )
                    path = root / key
                    path.mkdir(mode=0o700, parents=True, exist_ok=True)
                    path.chmod(0o700)
                    parent, child = context.Pipe(duplex=True)
                    process = context.Process(
                        target=telegram_run if telegram else run,
                        args=(key, generation, str(path / "session.db"), child, os.getpid()),
                        daemon=True,
                    )
                    process.start()
                    child.close()
                    sessions[key] = Session(process, parent, generation, time.monotonic())
                    runtime_state(key, generation, ConnectionState.CONNECTING)
                _STOP.wait(2)
    finally:
        for key, session in sessions.items():
            _stop_session(session)
            clear_qr(key)


def _history_worker():
    while not _STOP.is_set():
        try:
            processed = drain_once()
        except Exception:
            processed = 0
            logger.warning("[messaging] archive_worker_unavailable")
        _STOP.wait(0.1 if processed else 2)


def _supervisor_worker():
    while not _STOP.is_set():
        try:
            _supervise()
        except Exception:
            logger.warning("[messaging] session_supervisor_unavailable")
        _STOP.wait(10)


def start_worker():
    if _THREADS:
        return
    _STOP.clear()
    for target in (_supervisor_worker, _history_worker):
        thread = threading.Thread(target=target, name=target.__name__, daemon=True)
        _THREADS.append(thread)
        thread.start()


def stop_worker():
    _STOP.set()
    for thread in _THREADS:
        thread.join(timeout=8)
    _THREADS.clear()
    with _QR_LOCK:
        _QR.clear()
        _PASSWORDS.clear()
