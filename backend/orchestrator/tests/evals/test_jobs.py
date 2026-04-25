from __future__ import annotations

import asyncio

import pytest

from evals import jobs


@pytest.mark.asyncio
async def test_create_eval_job_runs_in_background(monkeypatch):
    async def fake_run_eval_flow(**kwargs):
        progress_callback = kwargs.get("progress_callback")
        if progress_callback is not None:
            await progress_callback(
                {
                    "current_case": 1,
                    "total_cases": 1,
                    "current_attempt": 1,
                    "total_attempts": 1,
                    "current_case_id": "case-1",
                    "current_case_title": "Case 1",
                    "status": "running",
                }
            )
        await asyncio.sleep(0)
        return {"summary": {"pass_rate": 1.0}, "cases": []}

    monkeypatch.setattr(jobs, "get_eval_flow", lambda flow_id: type("Flow", (), {"label": "Fake", "cases": [object()]})() if flow_id == "fake" else None)
    monkeypatch.setattr(jobs, "run_eval_flow", fake_run_eval_flow)

    job = await jobs.create_eval_job(
        flow_id="fake",
        llm_model="test-model",
        repetitions=2,
        user_email="user@example.com",
        discard_first_attempt=True,
        request_options=None,
        case_json_schemas=None,
    )

    assert job["status"] == "queued"

    snapshot = None
    for _ in range(20):
        snapshot = await jobs.get_eval_job(job["job_id"])
        assert snapshot is not None
        if snapshot["status"] == "completed":
            break
        await asyncio.sleep(0.01)

    assert snapshot is not None
    assert snapshot["status"] == "completed"
    assert snapshot["result"] == {"summary": {"pass_rate": 1.0}, "cases": []}
    assert snapshot["progress"]["current_case_id"] == "case-1"
    assert snapshot["request_options"]["stream"] is False
