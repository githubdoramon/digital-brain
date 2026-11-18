"""The inactive hooks must not change application execution."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar

import pytest

from observability import instrumentation as hooks


def test_hooks_leave_sync_and_background_execution_unchanged():
    @hooks.traced_intelligence_run()
    def run():
        return 42

    assert run() == 42
    with hooks.traced_daily_briefing_run() as trace:
        assert trace is None

    value = ContextVar("test_value", default=None)
    value.set("present")
    with ThreadPoolExecutor(max_workers=1) as executor:
        assert hooks.submit_with_current_context(executor, value.get).result() == "present"


@pytest.mark.asyncio
async def test_hooks_preserve_async_streams_and_cancellation():
    @hooks.traced_llm_request
    async def request():
        await asyncio.Event().wait()

    @hooks.timed_workflow_stage("chat", "stream")
    async def stream():
        yield "first"
        yield "second"

    assert [item async for item in stream()] == ["first", "second"]
    task = asyncio.create_task(request())
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
