from __future__ import annotations

import os

import pytest
from fastapi import HTTPException

os.environ.setdefault("LLM_BASE_URL", "http://localhost:11434/v1")
os.environ.setdefault("LLM_CHAT_MODEL_FAST", "test-fast")
os.environ.setdefault("LLM_CHAT_MODEL_SMART", "test-smart")
os.environ.setdefault("GOOGLE_CLIENT_IDS", "test-client-id")
os.environ.setdefault("ALLOWED_USERS", "user@example.com")

from routes import chat


def test_llm_unavailable_error_payload_uses_service_message():
    payload = chat._llm_unavailable_error_payload()

    assert payload == {
        "code": "llm_unavailable",
        "message": "The LLM service is currently unavailable. Please try again shortly.",
    }


def test_raise_http_for_llm_unavailable_uses_503():
    with pytest.raises(HTTPException) as exc_info:
        chat._raise_http_for_llm_unavailable(RuntimeError("boom"))

    assert exc_info.value.status_code == 503
    assert (
        exc_info.value.detail
        == "The LLM service is currently unavailable. Please try again shortly."
    )


@pytest.fixture
def stream_route(monkeypatch):
    from types import SimpleNamespace

    from fastapi import BackgroundTasks

    from commands import storage

    chat._ask_runs.clear()
    monkeypatch.setattr(chat, "_prepare_command_media", lambda *a, **k: ([], None))
    monkeypatch.setattr(chat, "_should_reset_command_thread", lambda *a, **k: False)
    monkeypatch.setattr(storage, "get_pending_event", lambda *a: None)
    monkeypatch.setattr(
        chat,
        "_resolve_session_context",
        lambda *a, **k: SimpleNamespace(
            session_id="thread:test",
            question="Find the Beacon meeting participants.",
            is_new_session=False,
            is_reset_only=False,
        ),
    )
    endpoint = next(
        route.endpoint
        for route in chat.create_chat_router().routes
        if route.path == "/mobile/ask/stream"
    )

    async def response():
        return await endpoint(
            chat.AskIn(question="Find the Beacon meeting participants.", thread_id="thread:test"),
            BackgroundTasks(),
            user={"email": "reader@example.com"},
        )

    return response


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "ending", ["emitted_error", "raised_error", "missing_terminal", "completed"]
)
async def test_stream_and_polling_share_terminal_status(monkeypatch, stream_route, ending):
    import json

    async def events(*a, **k):
        yield {"type": "status", "message": "Reading your meeting."}
        if ending == "emitted_error":
            yield {"type": "error", "message": "context_length_exceeded"}
        elif ending == "raised_error":
            raise RuntimeError("private provider details")
        elif ending == "completed":
            yield {"type": "done", "bundle": {"answer": "Here are the meeting participants."}}

    monkeypatch.setattr(chat.llm, "answer_question_stream", events)
    response = await stream_route()
    run_id = response.headers["x-ask-run-id"]
    output = [
        json.loads(chunk.removeprefix("data:").strip()) async for chunk in response.body_iterator
    ]
    run = chat._ask_runs[run_id]
    assert run["status"] == ("completed" if ending == "completed" else "failed")
    assert run["status_message"] is None
    if ending == "completed":
        assert run["result"]["answer"] == "Here are the meeting participants."
    else:
        assert any(event["type"] == "error" for event in output)
        assert run["error"]
        assert "private provider details" not in str(output) + str(run["error"])
        if ending == "emitted_error":
            assert run["error"]["code"] == "context_length_exceeded"
            assert run["error"]["message"] != "context_length_exceeded"


@pytest.mark.asyncio
async def test_detached_error_stops_the_polling_run(monkeypatch, stream_route):
    import asyncio

    release = asyncio.Event()

    async def events(*a, **k):
        yield {"type": "status", "message": "Reading your meeting."}
        await release.wait()
        yield {"type": "error", "message": "context_length_exceeded"}

    monkeypatch.setattr(chat.llm, "answer_question_stream", events)
    response = await stream_route()
    iterator = response.body_iterator
    run_id = response.headers["x-ask-run-id"]
    await iterator.__anext__()  # session info
    await iterator.__anext__()  # starts producer, delivers status
    waiting = asyncio.create_task(iterator.__anext__())
    await asyncio.sleep(0)
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    producers = list(chat._detached_ask_tasks)
    assert producers and chat._ask_runs[run_id]["status"] == "running"
    release.set()
    await asyncio.wait_for(asyncio.gather(*producers), timeout=2)
    assert chat._ask_runs[run_id]["status"] == "failed"
    assert chat._ask_runs[run_id]["status_message"] is None
    assert chat._ask_runs[run_id]["error"]["code"] == "context_length_exceeded"


def test_unfinished_run_guard_does_not_overwrite_completed_results():
    run_id = chat._create_ask_run("reader@example.com", "thread:test")
    chat._touch_ask_run(run_id, status="completed", result={"answer": "Done"})
    assert chat._fail_unfinished_ask_run(run_id) is None
    assert chat._ask_runs[run_id]["result"] == {"answer": "Done"}
