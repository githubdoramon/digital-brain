"""Twice-daily analysis with durable runs, recoverable leases and quiet results."""

from __future__ import annotations

import threading
from contextlib import contextmanager
from datetime import datetime, time, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import intelligence
import intelligence_model
from db import get_conn
from observability.logger import get_runtime_logger
from observability.mirador import traced_intelligence_run

JOB_TYPE = "scheduled_intelligence"
TIMEZONE = "Europe/Lisbon"
HOURS = (5, 19)
POLL_SECONDS = 30
_STOP = threading.Event()
_THREAD = None
logger = get_runtime_logger(__name__)


def latest_slot(now):
    local = now.astimezone(ZoneInfo(TIMEZONE))
    day = local.date()
    hour = next((h for h in reversed(HOURS) if local.hour >= h), None)
    if hour is None:
        day -= timedelta(days=1)
        hour = HOURS[-1]
    return datetime.combine(day, time(hour), ZoneInfo(TIMEZONE))


def enqueue_due(owner, *, now=None):
    now = now or intelligence.utcnow()
    intelligence.initialize(owner)
    refresh_backlog(owner)
    requeue_deferred(owner)
    slot = latest_slot(now)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT started_at FROM intelligence_state WHERE user_email = %s", (owner,))
        started = cur.fetchone()["started_at"]
        if slot < started:
            return False
        cur.execute(
            """INSERT INTO intelligence_runs(run_id, user_email, slot, cutoff)
            VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
            (intelligence.key(owner, slot.isoformat()), owner, slot.isoformat(), now),
        )
        created = cur.rowcount > 0
        conn.commit()
        return created


def refresh_backlog(owner):
    """Late historical imports extend the knowledge-only pass, never the live queue."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """UPDATE intelligence_runs r SET cutoff=NOW(),
            next_attempt_at=CASE WHEN status='succeeded' THEN NOW() ELSE next_attempt_at END,
            status=CASE WHEN status='succeeded' THEN 'pending' ELSE status END
            WHERE r.user_email=%s AND slot='knowledge-backlog' AND status <> 'running'
            AND EXISTS (SELECT 1 FROM intelligence_sources s
                JOIN intelligence_state st ON st.user_email=r.user_email
                CROSS JOIN LATERAL generate_series(0, GREATEST(0,
                    (length(COALESCE(s.payload->>'text','')) - 1) / %s)) part(part)
                WHERE (s.owner_email IS NULL OR s.owner_email=r.user_email)
                AND (s.historical OR s.observed_at < st.started_at
                    OR COALESCE((s.payload->>'sent_at')::timestamptz < st.started_at, FALSE))
                AND NOT EXISTS (SELECT 1 FROM intelligence_processed p WHERE p.user_email=r.user_email
                    AND p.source_id=s.source_id AND p.part=part.part)
                AND NOT EXISTS (SELECT 1 FROM intelligence_failures f
                    WHERE f.user_email=r.user_email AND f.source_id=s.source_id AND NOT f.review
                    AND f.part=part.part
                    AND (f.next_attempt_at IS NULL OR f.next_attempt_at > NOW())))""",
            (owner, intelligence.CHUNK_SIZE),
        )
        conn.commit()


def claim_run(owners):
    if not owners:
        return None
    with get_conn() as conn, conn.cursor() as cur:
        # Lock owner state before choosing a run: backlog and scheduled work for
        # the same owner cannot infer/apply concurrently across server instances.
        cur.execute(
            """SELECT s.user_email FROM intelligence_state s WHERE s.user_email = ANY(%s)
            AND EXISTS(SELECT 1 FROM intelligence_runs r WHERE r.user_email = s.user_email
                AND r.status <> 'succeeded' AND r.next_attempt_at <= NOW()
                AND (r.lease_until IS NULL OR r.lease_until <= NOW()))
            AND NOT EXISTS(SELECT 1 FROM intelligence_runs active WHERE active.user_email = s.user_email
                AND active.lease_until > NOW())
            ORDER BY s.user_email FOR UPDATE SKIP LOCKED LIMIT 1""",
            (list(owners),),
        )
        owner = cur.fetchone()
        if not owner:
            return None
        cur.execute(
            """SELECT * FROM intelligence_runs WHERE user_email = %s AND status <> 'succeeded'
            AND next_attempt_at <= NOW() AND (lease_until IS NULL OR lease_until <= NOW())
            ORDER BY knowledge_only, cutoff, run_id FOR UPDATE LIMIT 1""",
            (owner["user_email"],),
        )
        run = dict(cur.fetchone())
        token = uuid4().hex
        cur.execute(
            """UPDATE intelligence_runs SET status='running', lease_token=%s,
            lease_until=NOW() + INTERVAL '15 minutes', error_code=NULL WHERE run_id=%s""",
            (token, run["run_id"]),
        )
        conn.commit()
        return {**run, "lease_token": token}


def requeue_deferred(owner):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """UPDATE intelligence_runs r SET status='pending', next_attempt_at=NOW(), reviewed=FALSE
            WHERE r.user_email=%s AND r.status='succeeded'
            AND EXISTS (SELECT 1 FROM intelligence_failures f
                JOIN intelligence_sources s USING(source_id)
                WHERE f.user_email=r.user_email AND f.knowledge_only=r.knowledge_only
                AND s.observed_at <= r.cutoff AND f.next_attempt_at <= NOW())""",
            (owner,),
        )
        conn.commit()


def finish_run(run, *, more=False, failed=False):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """UPDATE intelligence_runs SET status=%s, lease_until=NULL, lease_token=NULL,
            error_code=%s, next_attempt_at=NOW() + (%s * INTERVAL '1 second')
            WHERE run_id=%s AND lease_token=%s""",
            (
                "failed" if failed else "pending" if more else "succeeded",
                "analysis_failed" if failed else None,
                300 if failed else 1,
                run["run_id"],
                run["lease_token"],
            ),
        )
        if cur.rowcount and not more and not failed and not run["knowledge_only"]:
            cur.execute(
                "UPDATE intelligence_state SET last_success_at=NOW() WHERE user_email=%s",
                (run["user_email"],),
            )
        conn.commit()


def process_once(owners):
    run = claim_run(owners)
    if not run:
        return False
    try:
        with _heartbeat(run):
            return _process_run(run)
    except Exception:
        finish_run(run, failed=True)
        logger.warning("[intelligence] analysis batch failed; retry scheduled")
        return True


@contextmanager
def _heartbeat(run):
    stop = threading.Event()

    def renew():
        while not stop.wait(60):
            try:
                with get_conn() as conn, conn.cursor() as cur:
                    cur.execute(
                        """UPDATE intelligence_runs SET lease_until=NOW() + INTERVAL '15 minutes'
                        WHERE run_id=%s AND lease_token=%s AND status='running'""",
                        (run["run_id"], run["lease_token"]),
                    )
                    conn.commit()
                    if not cur.rowcount:
                        return
            except Exception:
                logger.warning("[intelligence] lease renewal unavailable")

    thread = threading.Thread(target=renew, name="intelligence-lease", daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=2)


@traced_intelligence_run()
def _process_run(run):
    for _ in range(4):
        if _STOP.is_set():
            finish_run(run, more=True)
            return True
        batch = intelligence.next_batch(
            run["user_email"], run["cutoff"], knowledge_only=run["knowledge_only"]
        )
        if not batch:
            if not run["knowledge_only"] and not run["reviewed"]:
                review_batch = intelligence.next_batch(
                    run["user_email"], run["cutoff"], review=True
                )
                try:
                    context, findings = (
                        intelligence_model.analyze(run["user_email"], review_batch)
                        if review_batch
                        else ({}, [])
                    )
                except intelligence_model.InvalidModelOutput as exc:
                    intelligence.defer_batch(
                        run["user_email"], run, review_batch, exc.code, review=True
                    )
                    logger.warning("[intelligence] review deferred reason=%s", exc.code)
                    continue
                intelligence.apply_batch(
                    run["user_email"], run, review_batch, context, findings, review=True
                )
            finish_run(run)
            return True
        try:
            context, findings = intelligence_model.analyze(
                run["user_email"], batch, knowledge_only=run["knowledge_only"]
            )
        except intelligence_model.InvalidModelOutput as exc:
            intelligence.defer_batch(run["user_email"], run, batch, exc.code)
            logger.warning("[intelligence] batch deferred reason=%s", exc.code)
            continue
        if _STOP.is_set():
            finish_run(run, more=True)
            return True
        intelligence.apply_batch(run["user_email"], run, batch, context, findings)
    finish_run(run, more=True)
    return True


def _owners():
    from auth import ALLOWED_USERS, DEV_BYPASS_AUTH, DEV_USER_EMAIL

    return {DEV_USER_EMAIL} if DEV_BYPASS_AUTH else set(ALLOWED_USERS)


def _loop():
    while not _STOP.is_set():
        try:
            owners = _owners()
            for owner in owners:
                enqueue_due(owner)
            processed = process_once(owners)
        except Exception:
            logger.warning("[intelligence] worker tick failed")
            processed = False
        _STOP.wait(1 if processed else POLL_SECONDS)


def start_worker():
    global _THREAD
    if _THREAD and _THREAD.is_alive():
        return
    _STOP.clear()
    _THREAD = threading.Thread(target=_loop, name="scheduled-intelligence", daemon=True)
    _THREAD.start()


def stop_worker(timeout=5):
    _STOP.set()
    if _THREAD and _THREAD.is_alive():
        _THREAD.join(timeout=timeout)


def get_worker_status():
    return {
        "job_type": JOB_TYPE,
        "worker_alive": bool(_THREAD and _THREAD.is_alive()),
        "time_local": ["05:00", "19:00"],
        "timezone_name": TIMEZONE,
        "poll_seconds": POLL_SECONDS,
    }
