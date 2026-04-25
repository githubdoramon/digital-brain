from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from evals.registry import get_eval_flow, run_eval_flow
from evals.types import EvalLlmRequestOptions
from observability.logger import get_runtime_logger

logger = get_runtime_logger(__name__)

_JOBS: dict[str, dict[str, Any]] = {}
_JOBS_LOCK = asyncio.Lock()
_JOB_TTL = timedelta(hours=2)
_MAX_JOBS = 100
_BACKGROUND_TASKS: set[asyncio.Task[Any]] = set()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _job_snapshot(job: dict[str, Any]) -> dict[str, Any]:
    return {
        "job_id": job["job_id"],
        "status": job["status"],
        "flow_id": job["flow_id"],
        "flow_label": job.get("flow_label"),
        "llm_model": job.get("llm_model"),
        "request_options": job.get("request_options") or {},
        "repetitions": job.get("repetitions"),
        "discard_first_attempt": job.get("discard_first_attempt"),
        "created_at": job.get("created_at"),
        "started_at": job.get("started_at"),
        "finished_at": job.get("finished_at"),
        "error": job.get("error"),
        "progress": job.get("progress") or {},
        "result": job.get("result"),
    }


async def _prune_jobs() -> None:
    now = _utcnow()
    expired_ids = [
        job_id
        for job_id, job in _JOBS.items()
        if now - datetime.fromisoformat(str(job.get("created_at"))) > _JOB_TTL
    ]
    for job_id in expired_ids:
        _JOBS.pop(job_id, None)

    if len(_JOBS) <= _MAX_JOBS:
        return

    oldest = sorted(_JOBS.items(), key=lambda item: str(item[1].get("created_at")))
    overflow = len(_JOBS) - _MAX_JOBS
    for job_id, _job in oldest[:overflow]:
        _JOBS.pop(job_id, None)


async def _set_job_state(job_id: str, **updates: Any) -> None:
    async with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if not job:
            return
        job.update(updates)


async def create_eval_job(
    *,
    flow_id: str,
    llm_model: str | None,
    repetitions: int,
    user_email: str,
    discard_first_attempt: bool,
    request_options: EvalLlmRequestOptions | None = None,
) -> dict[str, Any]:
    flow = get_eval_flow(flow_id)
    if flow is None:
        raise ValueError(f"Unknown eval flow: {flow_id}")

    job_id = uuid.uuid4().hex
    created_at = _utcnow().isoformat()
    job = {
        "job_id": job_id,
        "status": "queued",
        "flow_id": flow_id,
        "flow_label": flow.label,
        "llm_model": llm_model,
        "request_options": {
            "stream": bool(request_options.stream) if request_options else False,
            "temperature": request_options.temperature if request_options else None,
            "max_tokens": request_options.max_tokens if request_options else None,
            "reasoning_effort": request_options.reasoning_effort if request_options else None,
        },
        "repetitions": repetitions,
        "discard_first_attempt": discard_first_attempt,
        "created_at": created_at,
        "started_at": None,
        "finished_at": None,
        "error": None,
        "progress": {
            "current_case": 0,
            "total_cases": len(flow.cases),
            "current_attempt": 0,
            "total_attempts": len(flow.cases) * max(1, repetitions),
            "current_case_id": None,
            "current_case_title": None,
        },
        "result": None,
    }

    async with _JOBS_LOCK:
        await _prune_jobs()
        _JOBS[job_id] = job

    logger.info(
        "[evals.job] queued job_id=%s flow_id=%s llm_model=%r repetitions=%s discard_first_attempt=%s",
        job_id,
        flow_id,
        llm_model,
        repetitions,
        discard_first_attempt,
    )

    async def _runner() -> None:
        await _set_job_state(job_id, status="running", started_at=_utcnow().isoformat())
        try:
            result = await run_eval_flow(
                flow_id=flow_id,
                llm_model=llm_model,
                repetitions=repetitions,
                user_email=user_email,
                discard_first_attempt=discard_first_attempt,
                request_options=request_options,
                progress_callback=lambda progress: _set_job_state(job_id, progress=progress),
            )
            await _set_job_state(
                job_id,
                status="completed",
                finished_at=_utcnow().isoformat(),
                result=result,
            )
            logger.info("[evals.job] completed job_id=%s", job_id)
        except Exception as exc:
            logger.error("[evals.job] failed job_id=%s error=%s", job_id, exc, exc_info=exc)
            await _set_job_state(
                job_id,
                status="failed",
                finished_at=_utcnow().isoformat(),
                error=str(exc),
            )

    task = asyncio.create_task(_runner())
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)
    return _job_snapshot(job)


async def get_eval_job(job_id: str) -> dict[str, Any] | None:
    async with _JOBS_LOCK:
        await _prune_jobs()
        job = _JOBS.get(job_id)
        if not job:
            return None
        return _job_snapshot(job)
