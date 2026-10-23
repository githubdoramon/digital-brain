"""Provider backpressure without sleeps, lost retries, or foreground starvation."""

import asyncio
import threading
from unittest.mock import MagicMock

import pytest
import requests

import llm_admission as gate
import llm_helpers

ENDPOINT = "https://models.example.test/v1"


@pytest.fixture(autouse=True)
def provider(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", ENDPOINT)
    monkeypatch.setenv("LLM_CHAT_MODEL_FAST", "fast-test")
    monkeypatch.setenv("LLM_CHAT_MODEL_SMART", "smart-test")


def test_one_background_workflow_keeps_slot_across_phases_and_rotates_waiters():
    with gate.background_work("knowledge"):
        assert gate.background_ready()
        with gate.request_slot(ENDPOINT):
            assert gate.status()[0]["background"]
        # Request completion does not give away partially extracted candidates.
        with gate.background_work("summaries"):
            assert not gate.background_ready()
        with gate.request_slot(ENDPOINT):
            pass
    with gate.background_work("knowledge"):
        assert not gate.background_ready()  # The waiting summary gets the next turn.
    with gate.background_work("summaries"):
        assert gate.background_ready()
        with gate.request_slot(ENDPOINT):
            pass
    with gate.background_work("knowledge"):
        assert gate.background_ready()
    assert not gate.status()[0]["background"]


def test_foreground_has_priority_and_history_gets_bounded_reservation(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(gate.time, "monotonic", lambda: clock[0])
    with gate.request_slot(ENDPOINT):
        with gate.background_work("knowledge"):
            assert not gate.background_ready()
        clock[0] += gate.RESERVATION_SECONDS + 1
        with gate.background_work("knowledge"):
            assert gate.background_ready()
            with gate.request_slot(ENDPOINT):
                assert gate.status()[0]["foreground"] == 1
                assert gate.status()[0]["background"]
    assert gate.status()[0]["foreground"] == 0


def test_background_503_has_one_transport_attempt_then_one_recovery_probe(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(gate.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(gate.random, "uniform", lambda *a: 1)
    response = requests.Response()
    response.status_code = 503
    response._content = b'{"error":"temporarily unavailable"}'
    post = MagicMock(return_value=response)
    monkeypatch.setattr(llm_helpers.requests, "post", post)
    sleep = MagicMock()
    monkeypatch.setattr(llm_helpers.time, "sleep", sleep)
    payload = {"model": "smart-test", "messages": []}
    with gate.background_work("knowledge"):
        with pytest.raises(gate.BackgroundDeferred) as error:
            llm_helpers._post_chat_completion(payload, sensitive_payload=True)
    assert error.value.delay == 30 and post.call_count == 1
    sleep.assert_not_called()
    with gate.background_work("summaries"):
        assert not gate.background_ready()
    assert gate.status()[0]["cooldown_seconds"] == 30
    clock[0] += 31
    with gate.background_work("summaries"):
        assert gate.background_ready()
        with gate.request_slot(ENDPOINT):
            with gate.background_work("knowledge"):
                assert not gate.background_ready()
    assert gate.status()[0]["failure_streak"] == 0


def test_foreground_retains_normal_retry_behavior(monkeypatch):
    bad = requests.Response()
    bad.status_code = 503
    good = MagicMock(status_code=200, json=lambda: {"choices": []})
    post = MagicMock(side_effect=[bad, good])
    monkeypatch.setattr(llm_helpers.requests, "post", post)
    monkeypatch.setattr(llm_helpers.time, "sleep", lambda _: None)
    assert llm_helpers._post_chat_completion({"model": "smart-test", "messages": []}) == {
        "choices": []
    }
    assert post.call_count == 2


def test_foreground_failure_cools_background_and_foreground_success_reopens(monkeypatch):
    monkeypatch.setattr(gate.random, "uniform", lambda *a: 1)
    with pytest.raises(requests.Timeout), gate.request_slot(ENDPOINT):
        raise requests.Timeout()
    with gate.background_work("knowledge"):
        assert not gate.background_ready()
    with gate.request_slot(ENDPOINT):
        pass  # A successful interactive request is also a health signal.
    with gate.background_work("knowledge"):
        assert gate.background_ready()


@pytest.mark.asyncio
@pytest.mark.parametrize("cross_context", [False, True])
async def test_async_stream_releases_foreground_admission_on_close(monkeypatch, cross_context):
    response = MagicMock(status_code=200)

    async def lines():
        yield 'data: {"choices": []}'
        await asyncio.Event().wait()

    response.aiter_lines = lines
    from unittest.mock import AsyncMock

    client = MagicMock()
    client.stream.return_value.__aenter__ = AsyncMock(return_value=response)
    client.stream.return_value.__aexit__ = AsyncMock(return_value=False)
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=client)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(llm_helpers.httpx, "AsyncClient", factory)
    monkeypatch.setattr(llm_helpers, "_prepare_chat_request", lambda payload, *args: payload)
    stream = llm_helpers.stream_llm_chat([{"role": "user", "content": "Synthetic request"}])
    if cross_context:
        await asyncio.create_task(stream.__anext__())
    else:
        await stream.__anext__()
    assert gate.status()[0]["foreground"] == 1
    await stream.aclose()
    assert gate.status()[0]["foreground"] == 0


def test_stale_waiting_lane_cannot_block_live_workers(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(gate.time, "monotonic", lambda: clock[0])
    with gate.request_slot(ENDPOINT):
        with gate.background_work("stopped-worker"):
            assert not gate.background_ready()
    clock[0] += 61
    with gate.background_work("knowledge"):
        assert gate.background_ready()


def test_stopped_slow_worker_expires_without_losing_live_pollers(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(gate.time, "monotonic", lambda: clock[0])
    with gate.background_work("active"):
        with gate.request_slot(ENDPOINT):
            with gate.background_work("stopped-briefing", poll_seconds=60):
                assert not gate.background_ready()
            for offset in (30, 60.2, 90, 120.4, 150, 181):
                clock[0] = 1000 + offset
                with gate.background_work("knowledge", poll_seconds=2):
                    assert not gate.background_ready()
    # The stopped lane expires, but the regularly polled lane keeps its turn.
    with gate.background_work("knowledge", poll_seconds=2):
        assert gate.background_ready()
        with gate.request_slot(ENDPOINT):
            pass


def test_simultaneous_threads_admit_exactly_one_background_workflow():
    start = threading.Barrier(9)
    admitted = threading.Event()
    release = threading.Event()
    rejected_all = threading.Event()
    outcomes = []
    lock = threading.Lock()

    def worker(index):
        start.wait(timeout=3)
        with gate.background_work(f"lane-{index}"):
            try:
                with gate.request_slot(ENDPOINT):
                    admitted.set()
                    assert release.wait(3)
                    with lock:
                        outcomes.append("admitted")
            except gate.BackgroundDeferred:
                with lock:
                    outcomes.append("deferred")
                    if outcomes.count("deferred") == 7:
                        rejected_all.set()

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(8)]
    for thread in threads:
        thread.start()
    try:
        start.wait(timeout=3)
        assert admitted.wait(3) and rejected_all.wait(3)
        assert gate.status()[0]["background"]
    finally:
        release.set()
        for thread in threads:
            thread.join(timeout=3)
    assert outcomes.count("admitted") == 1 and outcomes.count("deferred") == 7
    assert not gate.status()[0]["background"]


@pytest.mark.parametrize("interrupted", [False, True])
def test_sync_stream_closes_provider_connection_before_releasing_admission(
    monkeypatch, interrupted
):
    response = MagicMock(status_code=200)
    if interrupted:
        response.iter_lines.side_effect = requests.Timeout("synthetic stream timeout")
    else:
        response.iter_lines.return_value = iter(['data: {"choices":[{"delta":{"content":"OK"}}]}'])
    monkeypatch.setattr(llm_helpers.requests, "post", lambda *a, **k: response)
    payload = {"model": "smart-test", "messages": []}
    if interrupted:
        with pytest.raises(requests.Timeout):
            llm_helpers._post_chat_completion_stream(payload)
    else:
        assert (
            llm_helpers._post_chat_completion_stream(payload)["choices"][0]["message"]["content"]
            == "OK"
        )
    response.close.assert_called_once()
    assert gate.status()[0]["foreground"] == 0
