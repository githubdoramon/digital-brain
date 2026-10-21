"""Clocks are mandatory, source-relative and isolated across requests."""

import asyncio
from datetime import datetime, timezone

import pytest

import llm_helpers
import time_context


class FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2028, 5, 13, 0, 30, tzinfo=timezone.utc).astimezone(tz)


def test_all_payloads_have_current_clock_and_client_local_date(monkeypatch):
    monkeypatch.setattr(time_context, "datetime", FrozenDateTime)
    messages = [{"role": "user", "content": "Her birthday is today."}]
    with time_context.use_time_context(time_context.snapshot({"timezone": "America/Los_Angeles"})):
        for stream in (False, True):
            payload = llm_helpers.build_chat_payload(messages, model="test-model", stream=stream)
            clock = payload["messages"][0]
            assert clock["role"] == "system"
            assert "UTC now: 2028-05-13T00:30:00+00:00" in clock["content"]
            assert "2028-05-12T17:30:00-07:00" in clock["content"]
            assert "timestamp and timezone of the message containing" in clock["content"]
    assert messages == [{"role": "user", "content": "Her birthday is today."}]


@pytest.mark.asyncio
async def test_concurrent_and_streaming_contexts_are_isolated_and_reset():
    @time_context.with_request_time
    async def run(client_context):
        await asyncio.sleep(0)
        return time_context.current_context()["timezone"]

    @time_context.with_request_time
    async def stream(client_context):
        yield time_context.current_context()["timezone"]
        yield time_context.current_context()["timezone"]

    assert await asyncio.gather(run({"timezone": "UTC"}), run({"timezone": "Asia/Tokyo"})) == [
        "UTC",
        "Asia/Tokyo",
    ]
    iterator = stream({"timezone": "Asia/Tokyo"})
    assert await iterator.__anext__() == "Asia/Tokyo"
    assert time_context.current_context() == {}
    await iterator.aclose()
    assert time_context.current_context() == {}


def test_invalid_timezone_falls_back_to_utc():
    assert time_context.snapshot({"timezone": "not-a-timezone"})["timezone"] == "UTC"
