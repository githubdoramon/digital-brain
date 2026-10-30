"""Slow synchronous providers must not prevent request cancellation."""

import asyncio
import threading
from contextlib import suppress
from types import SimpleNamespace

import pytest

from agent.controller import AgentController
from agent.limits import AgentConfig
from agent.router import IntentRouter
from agents.main.agent import build_main_conversational_agent
from voice_context import is_voice_request, spoken_reply


@pytest.mark.asyncio
async def test_agent_model_wait_can_be_cancelled_without_finishing_turn(monkeypatch):
    config = AgentConfig(enable_intent_routing=False)
    agent = build_main_conversational_agent(
        max_steps=config.max_steps, max_tool_calls=config.max_tool_calls, timeout_seconds=120
    )
    controller = AgentController(config=config, conversational_agent=agent)
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    finalized = []

    async def prepare(**kwargs):
        return None, None, [{"role": "user", "content": "A short question."}], []

    monkeypatch.setattr(controller, "_prepare_execution_context", prepare)
    monkeypatch.setattr(controller, "_inject_inferred_location", lambda **kwargs: None)
    monkeypatch.setattr(controller, "_finalize", lambda *a, **kw: finalized.append(True))

    def model(*args):
        try:
            assert is_voice_request()
            assert controller._active_llm_policy is not None
            started.set()
            assert release.wait(2)
            return {"message": {"content": "A short answer."}}
        finally:
            finished.set()

    monkeypatch.setattr(controller, "_call_llm", model)
    with spoken_reply():
        task = asyncio.create_task(controller.run("A short question."))
    try:
        for _ in range(100):
            if started.is_set():
                break
            await asyncio.sleep(0.005)
        assert started.is_set()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 0.2)
        assert finalized == []
    finally:
        release.set()
        with suppress(asyncio.CancelledError):
            await task
        await asyncio.to_thread(finished.wait, 2)


@pytest.mark.asyncio
async def test_router_wait_does_not_block_event_loop(monkeypatch):
    router = IntentRouter(llm_base_url="http://localhost:11434/v1", llm_model="test-model")
    started, release = threading.Event(), threading.Event()
    monkeypatch.setattr(router, "_rule_based_classify", lambda *a: None)

    def classify(*args):
        started.set()
        assert release.wait(2)
        raise RuntimeError("synthetic provider unavailable")

    monkeypatch.setattr(router, "_llm_classify", classify)
    task = asyncio.create_task(router.classify("A short question."))
    try:
        for _ in range(100):
            if started.is_set():
                break
            await asyncio.sleep(0.005)
        assert started.is_set()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 0.2)
    finally:
        release.set()
        with suppress(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_model_policy_is_independent_between_concurrent_requests():
    config = AgentConfig()
    agent = build_main_conversational_agent(
        max_steps=config.max_steps, max_tool_calls=config.max_tool_calls, timeout_seconds=120
    )
    controller = AgentController(config=config, conversational_agent=agent)

    async def request(name):
        policy = SimpleNamespace(model=name)
        controller._active_llm_policy = policy
        controller._last_llm_policy = policy
        await asyncio.sleep(0)
        assert await asyncio.to_thread(lambda: controller._active_llm_policy.model) == name
        assert controller._last_llm_policy.model == name

    await asyncio.gather(request("model-a"), request("model-b"))
