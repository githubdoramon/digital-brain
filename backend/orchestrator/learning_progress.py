"""Read-only learning coverage shared by APIs and an independent log heartbeat."""

from __future__ import annotations

import json

import intelligence
import learning_fairness
import learning_ownership
import llm_admission
from db import get_conn
from observability.logger import get_runtime_logger

LOG_INTERVAL_SECONDS = 60
logger = get_runtime_logger(__name__)
_PHASES = (
    "processed",
    "ready",
    "running",
    "retrying",
    "paused",
    "deferred",
    "not_enqueued",
    "delayed",
    "available_on_demand",
)
# Reporting also recognizes old-dated sources in the prioritized queue. This does
# not change worker ordering or historical action permissions.
_HISTORY_SQL = """(COALESCE(j.historical,s.historical OR s.kind NOT IN ('chat','contact_note'))
    OR s.historical OR COALESCE(s.observed_at<st.started_at,FALSE)
    OR COALESCE((s.payload->>'sent_at')::timestamptz<st.started_at,FALSE))"""


def _empty(unit="source_parts"):
    return {
        "unit": unit,
        "total": 0,
        "remaining": 0,
        **dict.fromkeys(_PHASES, 0),
        "oldest_pending_observed_at": None,
        "oldest_pending_source_at": None,
        "next_retry_at": None,
        "last_success_at": None,
        "errors": {},
        "active": [],
    }


def _merge(rows, *, unit="source_parts"):
    result = _empty(unit)
    for row in rows:
        if row["phase"] != "available_on_demand":
            result["total"] += row["count"]
        result[row["phase"]] += row["count"]
        if row["phase"] != "processed":
            for field in (
                "oldest_pending_observed_at",
                "oldest_pending_source_at",
                "next_retry_at",
            ):
                value = row.get(field)
                if value is not None and (result[field] is None or value < result[field]):
                    result[field] = value
        success = row.get("last_success_at")
        if success is not None and (
            result["last_success_at"] is None or success > result["last_success_at"]
        ):
            result["last_success_at"] = success
        if row.get("error_code") and row["phase"] not in {"processed", "available_on_demand"}:
            code = row["error_code"]
            result["errors"][code] = result["errors"].get(code, 0) + row["count"]
    result["remaining"] = result["total"] - result["processed"]
    return result


def _state(counts, initialized):
    if not initialized:
        return "initializing"
    if counts["running"] or counts.get("active"):
        return "processing"
    if counts["ready"] or counts["not_enqueued"]:
        return "pending"
    if counts["remaining"] or counts.get("review_retrying") or counts.get("review_paused"):
        return "waiting"
    return "caught_up"


def _learning(cur, owner):
    # Include eligible parts not yet enqueued by the bounded 500-part seeder.
    # Eligibility matches initialize_owner; reporting also recognizes old dates.
    cur.execute(
        f"""WITH parts AS (
            SELECT {_HISTORY_SQL} AS historical,
                j.status,j.error_code,j.last_success_at,s.observed_at,
                (s.payload->>'sent_at')::timestamptz AS source_at,
                CASE WHEN j.status='done' THEN 'processed'
                    WHEN NOT (COALESCE(j.on_demand,FALSE) OR {learning_ownership.automatic_source_sql()})
                        AND NOT COALESCE(j.status='running' AND j.lease_until>NOW(),FALSE) THEN 'available_on_demand'
                    WHEN j.source_id IS NULL THEN 'not_enqueued'
                    WHEN j.status='deferred' THEN 'deferred'
                    WHEN j.status='running' AND j.lease_until>NOW() THEN 'running'
                    WHEN j.status='running' THEN 'ready'
                    WHEN j.attempts>=3 THEN 'paused'
                    WHEN j.next_attempt_at>NOW() THEN 'retrying'
                    ELSE 'ready' END AS phase,
                j.next_attempt_at
            FROM intelligence_sources s
            CROSS JOIN LATERAL generate_series(0,GREATEST(0,
                (length(COALESCE(s.payload->>'text',''))-1)/%s)) p(part)
            LEFT JOIN knowledge_learning j ON j.user_email=%s AND j.source_id=s.source_id AND j.part=p.part
            LEFT JOIN knowledge_backfills b ON b.user_email=%s
            LEFT JOIN intelligence_state st ON st.user_email=COALESCE(b.user_email,j.user_email)
            WHERE (s.owner_email IS NULL OR s.owner_email=%s) AND s.duplicate_of_source_id IS NULL
                AND knowledge_source_learnable(s.payload)
                AND (j.source_id IS NOT NULL OR (b.version>=1 AND
                    {learning_ownership.ELIGIBLE_SOURCE_SQL}))
        ) SELECT historical,status,phase,error_code,COUNT(*) AS count,
            MIN(observed_at) AS oldest_pending_observed_at,MIN(source_at) AS oldest_pending_source_at,
            MIN(next_attempt_at) FILTER (WHERE phase='retrying') AS next_retry_at,
            MAX(last_success_at) AS last_success_at
        FROM parts GROUP BY historical,status,phase,error_code""",
        (intelligence.CHUNK_SIZE, owner, owner, owner),
    )
    rows = [dict(row) for row in cur.fetchall()]
    lanes = {
        name: _merge([r for r in rows if r["historical"] == historical])
        for name, historical in (("ongoing", False), ("history", True))
    }
    counts = {}
    for row in rows:
        if row["status"]:
            counts[row["status"]] = counts.get(row["status"], 0) + row["count"]
    cur.execute(
        f"""SELECT {_HISTORY_SQL} AS historical,j.historical AS queue_historical,
            s.kind,j.source_id::text||':'||j.part::text AS source_ref,
            j.started_at,j.lease_until,j.attempts,
            EXTRACT(EPOCH FROM (NOW()-j.started_at))::bigint AS elapsed_s
        FROM knowledge_learning j JOIN intelligence_sources s USING(source_id)
        LEFT JOIN intelligence_state st ON st.user_email=j.user_email
        WHERE j.user_email=%s AND j.status='running' AND j.lease_until>NOW() AND s.duplicate_of_source_id IS NULL
        ORDER BY j.started_at LIMIT 10""",
        (owner,),
    )
    for row in cur.fetchall():
        item = dict(row)
        lanes["history" if item.pop("historical") else "ongoing"]["active"].append(item)
    return lanes, counts


def _summaries(cur, owner):
    cur.execute(
        """WITH subjects AS (
            SELECT *, CASE WHEN version=rendered_version THEN 'processed'
                WHEN lease_until>NOW() THEN 'running'
                WHEN history_dirty AND dirty_since>NOW()-INTERVAL '15 minutes' THEN 'delayed'
                WHEN next_attempt_at>NOW() AND error_code IS NOT NULL THEN 'retrying'
                WHEN next_attempt_at>NOW() THEN 'delayed' ELSE 'ready' END AS phase
            FROM knowledge_summaries WHERE user_email=%s
        ) SELECT phase,error_code,COUNT(*) AS count,MAX(last_success_at) AS last_success_at,
            MIN(next_attempt_at) FILTER (WHERE phase='retrying') AS next_retry_at
        FROM subjects GROUP BY phase,error_code""",
        (owner,),
    )
    counts = _merge(cur.fetchall(), unit="subjects")
    cur.execute(
        """SELECT subject_ref,started_at,lease_until,
            EXTRACT(EPOCH FROM (NOW()-started_at))::bigint AS elapsed_s
        FROM knowledge_summaries WHERE user_email=%s AND version<>rendered_version AND lease_until>NOW()
        ORDER BY started_at LIMIT 10""",
        (owner,),
    )
    counts["active"] = [dict(row) for row in cur.fetchall()]
    return counts


def _intelligence(cur, owner, cutoff):
    lanes = {}
    for name, historical in (("scheduled", False),):
        coverage = intelligence.progress_with_cursor(cur, owner, cutoff, knowledge_only=historical)
        counts = _empty()
        counts.update({key.removesuffix("_parts"): value for key, value in coverage.items()})
        cur.execute(
            """SELECT f.error_code,COUNT(*) AS count FROM intelligence_failures f
            JOIN intelligence_sources s USING(source_id)
            WHERE f.user_email=%s AND f.knowledge_only=%s AND s.observed_at<=%s
                AND (s.owner_email IS NULL OR s.owner_email=%s)
                AND (f.review OR NOT EXISTS(SELECT 1 FROM intelligence_processed p
                    WHERE p.user_email=f.user_email AND p.source_id=f.source_id AND p.part=f.part))
            GROUP BY f.error_code""",
            (owner, historical, cutoff, owner),
        )
        counts["errors"] = {row["error_code"]: row["count"] for row in cur.fetchall()}
        lanes[name] = counts
    # Retained as an explicit retired lane for older clients; no work is scheduled here.
    lanes["history"] = {**_empty(), "retired": True, "review_retrying": 0, "review_paused": 0}
    cur.execute(
        """SELECT DISTINCT ON (knowledge_only) knowledge_only,slot,status,cutoff,started_at,
            next_attempt_at,lease_until,batches,error_code,reviewed
        FROM intelligence_runs WHERE user_email=%s ORDER BY knowledge_only,cutoff DESC""",
        (owner,),
    )
    for row in cur.fetchall():
        item = dict(row)
        if not item.pop("knowledge_only"):
            lanes["scheduled"]["latest_run"] = item
    cur.execute(
        """SELECT knowledge_only,slot,cutoff,started_at,lease_until,batches,
            EXTRACT(EPOCH FROM (NOW()-started_at))::bigint AS elapsed_s
        FROM intelligence_runs WHERE user_email=%s AND status='running' AND lease_until>NOW()
        ORDER BY started_at LIMIT 10""",
        (owner,),
    )
    for row in cur.fetchall():
        item = dict(row)
        if not item.pop("knowledge_only"):
            lanes["scheduled"]["active"].append(item)
    # Runs claim batches, not individual parts. Do not count active runs as parts.
    for counts in lanes.values():
        counts["running_runs"] = len(counts["active"])
    return lanes


def snapshot(owner):
    import intelligence_jobs
    import knowledge_jobs

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        cur.execute(
            """SELECT NOW() AS as_of,st.started_at,st.seeded,b.version,b.seeded_at
            FROM (SELECT %s::text AS owner) o
            LEFT JOIN intelligence_state st ON st.user_email=o.owner
            LEFT JOIN knowledge_backfills b ON b.user_email=o.owner""",
            (owner,),
        )
        state = dict(cur.fetchone())
        initialized = bool(state["seeded"] and (state["version"] or 0) >= 1)
        learning, counts = _learning(cur, owner)
        analysis = _intelligence(cur, owner, state["as_of"])
        summaries = _summaries(cur, owner)
        cur.execute(
            """SELECT COUNT(*) AS total,COUNT(*) FILTER (WHERE embedding IS NOT NULL) AS processed,
                COUNT(*) FILTER (WHERE embedding IS NULL) AS remaining
            FROM intelligence_knowledge WHERE user_email=%s AND deleted_at IS NULL""",
            (owner,),
        )
        indexing = {"unit": "facts", **dict(cur.fetchone())}
        fairness = learning_fairness.status(cur, owner)
        cur.execute(
            """SELECT COUNT(*) AS ignored_note_versions FROM intelligence_sources
            WHERE duplicate_of_source_id IS NOT NULL AND (owner_email IS NULL OR owner_email=%s)""",
            (owner,),
        )
        deduplication = dict(cur.fetchone())
    for lane in (*learning.values(), *analysis.values(), summaries):
        lane["state"] = _state(lane, initialized)
    knowledge_workers = knowledge_jobs.get_worker_status()
    return {
        "as_of": state["as_of"],
        "initialized": initialized,
        "history_caught_up": initialized
        and all(
            lane["state"] == "caught_up" for lane in (learning["history"], analysis["history"])
        ),
        "learning_cutoff": state["seeded_at"],
        "history_policy": {
            "automatic_months": learning_ownership.history_months(),
            "older_on_demand": True,
        },
        "activation_at": state["started_at"],
        "workers": {
            "learning": knowledge_workers["worker_alive"],
            "summaries": knowledge_workers["summary_worker_alive"],
            "progress": knowledge_workers["progress_worker_alive"],
            "intelligence": intelligence_jobs.get_worker_status()["worker_alive"],
        },
        "schedule": {
            "times": [f"{hour:02d}:00" for hour in intelligence_jobs.HOURS],
            "timezone": intelligence_jobs.TIMEZONE,
        },
        "counts": counts,
        "learning": learning,
        "intelligence": analysis,
        "summaries": summaries,
        "indexing": indexing,
        "llm_admission": llm_admission.status(),
        "fairness": fairness,
        "deduplication": deduplication,
    }


def log_snapshot(owner):
    report = snapshot(owner)
    header = {
        key: report[key]
        for key in (
            "as_of",
            "initialized",
            "history_caught_up",
            "workers",
            "learning_cutoff",
            "history_policy",
            "activation_at",
            "schedule",
            "fairness",
            "llm_admission",
            "deduplication",
        )
    }
    logger.info("[learning] progress owner=%s %s", owner, json.dumps(header, default=str))
    lanes = {
        **{f"knowledge_{name}": value for name, value in report["learning"].items()},
        **{f"intelligence_{name}": value for name, value in report["intelligence"].items()},
        "summaries": report["summaries"],
        "indexing": report["indexing"],
    }
    for name, counts in lanes.items():
        logger.info(
            "[learning] progress owner=%s lane=%s %s", owner, name, json.dumps(counts, default=str)
        )


def log_loop(stop):
    from intelligence_jobs import _owners

    while not stop.is_set():
        for owner in _owners():
            try:
                log_snapshot(owner)
            except Exception:
                logger.exception("[learning] progress_snapshot_failed owner=%s", owner)
        stop.wait(LOG_INTERVAL_SECONDS)
