from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import time
from typing import Any, Literal

ScheduleKind = Literal["daily_utc", "debounced_async"]


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

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["time_utc"] = self.time_utc.isoformat(timespec="minutes") if self.time_utc else None
        return payload


PROPOSED_EVENTS_DAILY = ScheduledJobSpec(
    job_type="proposed_events_daily",
    label="Proposed events",
    worker_module="proposed_event_jobs",
    schedule_kind="daily_utc",
    time_utc=time(hour=23, minute=59),
    poll_seconds=60,
    retry_seconds=300,
    description=(
        "Scans same-day location history in the user's latest captured timezone and proposes "
        "missing events for 15+ minute stays without overlapping events."
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

SCHEDULED_JOBS: tuple[ScheduledJobSpec, ...] = (
    PROPOSED_EVENTS_DAILY,
    DAILY_BRIEFING,
    MEETING_TRANSCRIPT,
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
        import meeting_transcript_jobs

        transcript = meeting_transcript_jobs.get_worker_status()
        status[str(transcript.get("job_type"))] = transcript
    except Exception as exc:
        status[MEETING_TRANSCRIPT.job_type] = {
            "job_type": MEETING_TRANSCRIPT.job_type,
            "worker_alive": False,
            "error": str(exc),
        }
    return status
