from __future__ import annotations

import json

from agent import llm_transport


async def test_stream_llm_with_tools_ignores_null_tool_calls(monkeypatch):
    async def fake_stream_llm_chat(*args, **kwargs):
        del args, kwargs
        yield json.dumps(
            {
                "choices": [
                    {
                        "delta": {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": None,
                        },
                        "finish_reason": None,
                    }
                ]
            }
        )
        yield json.dumps(
            {
                "choices": [
                    {
                        "delta": {"content": "hello"},
                        "finish_reason": "stop",
                    }
                ]
            }
        )

    monkeypatch.setattr(llm_transport, "stream_llm_chat", fake_stream_llm_chat)

    chunks = [
        chunk
        async for chunk in llm_transport.stream_llm_with_tools(
            [{"role": "user", "content": "hi"}],
            [],
            model="test-model",
            timeout=1,
        )
    ]

    assert chunks[-1]["done"] is True
    assert chunks[-1]["message"]["content"] == "hello"
