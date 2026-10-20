from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import time
from typing import Any, Literal

ScheduleKind = Literal["daily_utc", "daily_local", "debounced_async", "polling"]


@dataclass(frozen=True)
class ScheduledJobSpec:
    job_type: str
    label: str
    worker_module: str
    schedule_kind: ScheduleKind
    time_utc: time | None
    poll_seconds: int
    retry_seconds: int | None
    description: str
    trigger_source: str
    time_local: time | None = None
    timezone_name: str | None = None
    run_interval_seconds: int | None = None
    times_local: tuple[str, ...] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["time_utc"] = self.time_utc.isoformat(timespec="minutes") if self.time_utc else None
        payload["time_local"] = (
            self.time_local.isoformat(timespec="minutes") if self.time_local else None
        )
        return payload


PROPOSED_EVENTS_DAILY = ScheduledJobSpec(
    job_type="proposed_events_daily",
    label="Proposed events",
    worker_module="proposed_event_jobs",
    schedule_kind="daily_utc",
    time_utc=time(hour=4, minute=0),
    poll_seconds=60,
    retry_seconds=300,
    description=(
        "Scans a rolling two-local-day location-history window in the user's latest captured "
        "timezone and proposes missing events for 15+ minute stays without overlapping events."
    ),
    trigger_source="backend scheduler",
)

DAILY_BRIEFING = ScheduledJobSpec(
    job_type="daily_briefing",
    label="Daily briefing",
    worker_module="daily_briefing_jobs",
    schedule_kind="daily_utc",
    time_utc=time(hour=5, minute=0),
    poll_seconds=60,
    retry_seconds=300,
    description=(
        "Generates one daily briefing per active user. Also remains externally triggerable "
        "through /agents/daily-briefing/run."
    ),
    trigger_source="backend scheduler and external trigger",
)

EMERGENCY_STOCK = ScheduledJobSpec(
    job_type="emergency_stock_daily",
    label="Emergency stock",
    worker_module="emergency_stock_jobs",
    schedule_kind="daily_local",
    time_utc=None,
    poll_seconds=60,
    retry_seconds=300,
    description=(
        "Checks emergency stock once per local calendar day, updates the configured Google "
        "Sheet and Bring list, and notifies about actions."
    ),
    trigger_source="backend scheduler and external trigger",
    time_local=time(hour=5, minute=0),
)

DATABASE_BACKUP = ScheduledJobSpec(
    job_type="database_backup_daily",
    label="Encrypted database backup",
    worker_module="database_backup_jobs",
    schedule_kind="daily_local",
    time_utc=None,
    poll_seconds=60,
    retry_seconds=300,
    description=(
        "Creates a custom-format PostgreSQL dump, compresses and encrypts it, then uploads "
        "the encrypted artifact to the configured S3 bucket."
    ),
    trigger_source="backend scheduler",
    time_local=time(hour=0, minute=0),
    timezone_name="Europe/Lisbon",
)

MEETING_TRANSCRIPT = ScheduledJobSpec(
    job_type="meeting_transcript",
    label="Meeting transcript ingest",
    worker_module="meeting_transcript_jobs",
    schedule_kind="debounced_async",
    time_utc=None,
    poll_seconds=2,
    retry_seconds=60,
    description=(
        "Processes queued meeting transcripts after a debounce window; new submissions for "
        "the same meeting replace the pending job."
    ),
    trigger_source="POST /ingest/meetings/transcript",
)

EVENT_TAG_ENRICHMENT = ScheduledJobSpec(
    job_type="event_tag_enrichment",
    label="Event tag enrichment",
    worker_module="event_tag_jobs",
    schedule_kind="debounced_async",
    time_utc=None,
    poll_seconds=2,
    retry_seconds=60,
    description=(
        "Generates and persists event tags after event creation or update so command and "
        "ingest flows do not block on tagging."
    ),
    trigger_source="event persistence",
)

DOCUMENT_TAG_ENRICHMENT = ScheduledJobSpec(
    job_type="document_tag_enrichment",
    label="Document tag enrichment",
    worker_module="document_tag_jobs",
    schedule_kind="debounced_async",
    time_utc=None,
    poll_seconds=2,
    retry_seconds=60,
    description=(
        "Translates, generates, sanitizes, and persists document tags after document "
        "creation or metadata update."
    ),
    trigger_source="document persistence",
)

DOCUMENT_ENHANCEMENT = ScheduledJobSpec(
    job_type="document_enhancement",
    label="Document enhancement",
    worker_module="document_enhancement_jobs",
    schedule_kind="debounced_async",
    time_utc=None,
    poll_seconds=2,
    retry_seconds=60,
    description="Completes extraction, metadata generation, embeddings, and tag enrichment after upload.",
    trigger_source="document upload and web retry",
)

CONTACT_TAG_ENRICHMENT = ScheduledJobSpec(
    job_type="contact_tag_enrichment",
    label="Contact tag enrichment",
    worker_module="contact_tag_jobs",
    schedule_kind="debounced_async",
    time_utc=None,
    poll_seconds=2,
    retry_seconds=60,
    description=(
        "Generates and persists contact tags after contact creation or update using the "
        "shared tag manager."
    ),
    trigger_source="contact persistence",
)

CONTACT_ENRICHMENT = ScheduledJobSpec(
    job_type="contact_enrichment",
    label="Contact enrichment",
    worker_module="contact_enrichment_jobs",
    schedule_kind="debounced_async",
    time_utc=None,
    poll_seconds=30,
    retry_seconds=300,
    description=(
        "Analyzes confirmed event/contact context for optional profession and relationship "
        "suggestions and stores them for user review."
    ),
    trigger_source="confirmed event or contact command",
)

GOOGLE_CALENDAR_SYNC = ScheduledJobSpec(
    job_type="google_calendar_sync",
    label="Google Calendar sync",
    worker_module="google_calendar_sync_jobs",
    schedule_kind="polling",
    time_utc=None,
    poll_seconds=30,
    retry_seconds=300,
    time_local=time(hour=3, minute=0),
    timezone_name="Europe/Lisbon",
    run_interval_seconds=300,
    description=(
        "Polls selected Google calendars every five minutes, syncs meeting occurrences across "
        "the recent two-week and upcoming two-month window, and reconciles the recent and "
        "upcoming two weeks each Sunday at 03:00 Europe/Lisbon."
    ),
    trigger_source="backend scheduler",
)

MICROSOFT_CALENDAR_SYNC = ScheduledJobSpec(
    job_type="microsoft_calendar_sync",
    label="Microsoft Calendar sync",
    worker_module="microsoft_calendar_sync_jobs",
    schedule_kind="polling",
    time_utc=None,
    poll_seconds=30,
    retry_seconds=300,
    time_local=time(hour=3, minute=0),
    timezone_name="Europe/Lisbon",
    run_interval_seconds=300,
    description=(
        "Polls selected Microsoft 365 calendars every five minutes, syncs meeting occurrences "
        "across the recent two-week and upcoming two-month window, and reconciles the recent "
        "and upcoming two weeks each Sunday at 03:00 Europe/Lisbon."
    ),
    trigger_source="backend scheduler",
)

CONVERSATION_INDEX = ScheduledJobSpec(
    job_type="conversation_index",
    label="Conversation search index",
    worker_module="conversation_index_jobs",
    schedule_kind="polling",
    time_utc=None,
    poll_seconds=2,
    retry_seconds=60,
    description="Indexes saved conversation passages for History and memory retrieval; backfills existing messages and retries failures.",
    trigger_source="conversation persistence and startup backfill",
)

SCHEDULED_INTELLIGENCE = ScheduledJobSpec(
    job_type="scheduled_intelligence",
    label="Memory analysis",
    worker_module="intelligence_jobs",
    schedule_kind="daily_local",
    time_utc=None,
    poll_seconds=30,
    retry_seconds=300,
    time_local=time(hour=5),
    times_local=("05:00", "19:00"),
    timezone_name="Europe/Lisbon",
    description="Reviews new evidence, maintains knowledge, proposes work and completes evidenced tasks.",
    trigger_source="backend scheduler",
)

KNOWLEDGE_LEARNING = ScheduledJobSpec(
    job_type="knowledge_learning",
    label="Knowledge learning",
    worker_module="knowledge_jobs",
    schedule_kind="polling",
    time_utc=None,
    poll_seconds=10,
    retry_seconds=300,
    description="Learns from Brain chats, revisits retained history, and refreshes entity summaries.",
    trigger_source="persisted evidence and background worker",
)

SCHEDULED_JOBS: tuple[ScheduledJobSpec, ...] = (
    KNOWLEDGE_LEARNING,
    SCHEDULED_INTELLIGENCE,
    CONVERSATION_INDEX,
    PROPOSED_EVENTS_DAILY,
    DAILY_BRIEFING,
    EMERGENCY_STOCK,
    DATABASE_BACKUP,
    MEETING_TRANSCRIPT,
    EVENT_TAG_ENRICHMENT,
    DOCUMENT_TAG_ENRICHMENT,
    DOCUMENT_ENHANCEMENT,
    CONTACT_TAG_ENRICHMENT,
    CONTACT_ENRICHMENT,
    GOOGLE_CALENDAR_SYNC,
    MICROSOFT_CALENDAR_SYNC,
)


def list_scheduled_jobs() -> list[dict[str, Any]]:
    return [job.to_dict() for job in SCHEDULED_JOBS]


def get_scheduled_job(job_type: str) -> ScheduledJobSpec | None:
    normalized = str(job_type or "").strip()
    return next((job for job in SCHEDULED_JOBS if job.job_type == normalized), None)


def list_scheduled_job_statuses() -> list[dict[str, Any]]:
    runtime_status = _load_runtime_status()
    statuses: list[dict[str, Any]] = []
    for job in SCHEDULED_JOBS:
        statuses.append(
            {
                **job.to_dict(),
                "runtime": runtime_status.get(job.job_type)
                or {
                    "job_type": job.job_type,
                    "worker_alive": False,
                    "error": "runtime status unavailable",
                },
            }
        )
    return statuses


def _load_runtime_status() -> dict[str, dict[str, Any]]:
    status: dict[str, dict[str, Any]] = {}
    try:
        import knowledge_jobs

        status[KNOWLEDGE_LEARNING.job_type] = knowledge_jobs.get_worker_status()
    except Exception:
        status[KNOWLEDGE_LEARNING.job_type] = {"worker_alive": False}

    try:
        import intelligence_jobs

        status[SCHEDULED_INTELLIGENCE.job_type] = intelligence_jobs.get_worker_status()
    except Exception:
        status[SCHEDULED_INTELLIGENCE.job_type] = {"worker_alive": False}
    try:
        import conversation_index_jobs

        status[CONVERSATION_INDEX.job_type] = conversation_index_jobs.get_worker_status()
    except Exception:
        status[CONVERSATION_INDEX.job_type] = {
            "job_type": CONVERSATION_INDEX.job_type,
            "worker_alive": False,
            "error": "Conversation index status unavailable",
        }
    try:
        import proposed_event_jobs

        proposed = proposed_event_jobs.get_worker_status()
        status[str(proposed.get("job_type"))] = proposed
    except Exception as exc:
        status[PROPOSED_EVENTS_DAILY.job_type] = {
            "job_type": PROPOSED_EVENTS_DAILY.job_type,
            "worker_alive": False,
            "error": str(exc),
        }

    try:
        import daily_briefing_jobs

        briefing = daily_briefing_jobs.get_worker_status()
        status[str(briefing.get("job_type"))] = briefing
    except Exception as exc:
        status[DAILY_BRIEFING.job_type] = {
            "job_type": DAILY_BRIEFING.job_type,
            "worker_alive": False,
            "error": str(exc),
        }

    try:
        import emergency_stock_jobs

        emergency_stock = emergency_stock_jobs.get_worker_status()
        status[str(emergency_stock.get("job_type"))] = emergency_stock
    except Exception as exc:
        status[EMERGENCY_STOCK.job_type] = {
            "job_type": EMERGENCY_STOCK.job_type,
            "worker_alive": False,
            "error": str(exc),
        }

    try:
        import database_backup_jobs

        backup = database_backup_jobs.get_worker_status()
        status[str(backup.get("job_type"))] = backup
    except Exception as exc:
        status[DATABASE_BACKUP.job_type] = {
            "job_type": DATABASE_BACKUP.job_type,
            "worker_alive": False,
            "error": str(exc),
        }

    try:
        import meeting_transcript_jobs

        transcript = meeting_transcript_jobs.get_worker_status()
        status[str(transcript.get("job_type"))] = transcript
    except Exception as exc:
        status[MEETING_TRANSCRIPT.job_type] = {
            "job_type": MEETING_TRANSCRIPT.job_type,
            "worker_alive": False,
            "error": str(exc),
        }
    try:
        import google_calendar_sync_jobs

        calendar_sync = google_calendar_sync_jobs.get_worker_status()
        status[str(calendar_sync.get("job_type"))] = calendar_sync
    except Exception as exc:
        status[GOOGLE_CALENDAR_SYNC.job_type] = {
            "job_type": GOOGLE_CALENDAR_SYNC.job_type,
            "worker_alive": False,
            "error": str(exc),
        }
    try:
        import microsoft_calendar_sync_jobs

        calendar_sync = microsoft_calendar_sync_jobs.get_worker_status()
        status[str(calendar_sync.get("job_type"))] = calendar_sync
    except Exception as exc:
        status[MICROSOFT_CALENDAR_SYNC.job_type] = {
            "job_type": MICROSOFT_CALENDAR_SYNC.job_type,
            "worker_alive": False,
            "error": str(exc),
        }
    try:
        import event_tag_jobs

        event_tags = event_tag_jobs.get_worker_status()
        status[str(event_tags.get("job_type"))] = event_tags
    except Exception as exc:
        status[EVENT_TAG_ENRICHMENT.job_type] = {
            "job_type": EVENT_TAG_ENRICHMENT.job_type,
            "worker_alive": False,
            "error": str(exc),
        }
    try:
        import document_tag_jobs

        document_tags = document_tag_jobs.get_worker_status()
        status[str(document_tags.get("job_type"))] = document_tags
    except Exception as exc:
        status[DOCUMENT_TAG_ENRICHMENT.job_type] = {
            "job_type": DOCUMENT_TAG_ENRICHMENT.job_type,
            "worker_alive": False,
            "error": str(exc),
        }
    try:
        import document_enhancement_jobs

        enhancement = document_enhancement_jobs.get_worker_status()
        status[str(enhancement.get("job_type"))] = enhancement
    except Exception as exc:
        status[DOCUMENT_ENHANCEMENT.job_type] = {
            "job_type": DOCUMENT_ENHANCEMENT.job_type,
            "worker_alive": False,
            "error": str(exc),
        }
    try:
        import contact_tag_jobs

        contact_tags = contact_tag_jobs.get_worker_status()
        status[str(contact_tags.get("job_type"))] = contact_tags
    except Exception as exc:
        status[CONTACT_TAG_ENRICHMENT.job_type] = {
            "job_type": CONTACT_TAG_ENRICHMENT.job_type,
            "worker_alive": False,
            "error": str(exc),
        }
    return status
