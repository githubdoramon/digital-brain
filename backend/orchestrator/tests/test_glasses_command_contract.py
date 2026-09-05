from __future__ import annotations

import asyncio
import sys
from types import ModuleType, SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from glasses_audio import clear_audio, get_audio, put_audio
from glasses_commands import normalize_transcript, shortcut_for_transcript
from schemas import GlassesCommandClientTimings, GlassesCommandIn
from voice_response import (
    ResponseModality,
    normalize_modality,
    sanitize_voice_answer,
    validate_voice_answer,
)


def test_shortcuts_are_exact_after_only_allowed_normalization():
    assert normalize_transcript("  FRONT   GATE!!! ") == "front gate"
    assert shortcut_for_transcript("  FRONT   GATE!!! ") == "front_gate"
    assert shortcut_for_transcript(" slash   new? ") == "new"
    assert shortcut_for_transcript("CAR GATE.") == "car_gate"
    assert shortcut_for_transcript("open the front gate") is None
    assert shortcut_for_transcript("front gates") is None
    assert shortcut_for_transcript("front door") is None


def test_normalize_modality_preserves_enum_values():
    assert normalize_modality(ResponseModality.VOICE) is ResponseModality.VOICE
    assert normalize_modality("voice") is ResponseModality.VOICE


def test_main_and_memory_profiles_receive_voice_instruction_only_in_voice_mode():
    from agent.state import AgentState
    from agents.main.message_builder import build_main_messages
    from agents.memory_expert.message_builder import build_memory_expert_messages

    for builder in (build_main_messages, build_memory_expert_messages):
        voice_state = AgentState(goal="hello")
        voice_state.request_context["response_modality"] = ResponseModality.VOICE
        kwargs = {
            "question": "hello",
            "state": voice_state,
            "conversation_history": None,
            "user_email": None,
            "search_limit": 5,
            "client_context": None,
        }
        if builder is build_main_messages:
            kwargs["skill_injector"] = lambda *_args, **_kwargs: None
        voice_contents = [item["content"] for item in builder(**kwargs) if item["role"] == "system"]
        assert any("VOICE RESPONSE MODE" in content for content in voice_contents)

        text_state = AgentState(goal="hello")
        kwargs["state"] = text_state
        text_contents = [item["content"] for item in builder(**kwargs) if item["role"] == "system"]
        assert not any("VOICE RESPONSE MODE" in content for content in text_contents)


def test_command_schema_accepts_existing_client_context_and_aliases():
    command = GlassesCommandIn(
        commandId=uuid4(),
        transcript="What is next?",
        sessionId="thread_abc",
        client_context={"timezone": "Europe/Lisbon", "locale": "en-US"},
    )
    assert command.thread_id == "thread_abc"
    assert not hasattr(command, "response_modality")


def test_command_schema_accepts_optional_client_timings():
    command = GlassesCommandIn(
        command_id=uuid4(),
        transcript="What is next?",
        client_timings={
            "wake_to_listening_start_ms": 120,
            "transcription_ms": 85,
            "transcription_attempt_count": 1,
        },
    )
    assert isinstance(command.client_timings, GlassesCommandClientTimings)
    assert command.client_timings.transcription_ms == 85


def test_command_schema_rejects_negative_client_timing():
    with pytest.raises(ValueError):
        GlassesCommandIn(
            command_id=uuid4(),
            transcript="What is next?",
            client_timings={"transcription_ms": -1},
        )


def test_command_schema_rejects_client_modality_override():
    with pytest.raises(ValueError):
        GlassesCommandIn(command_id=uuid4(), transcript="hi", response_modality="text")


def test_voice_guardrail_is_plain_and_bounded():
    answer = sanitize_voice_answer("**Done.** See https://example.invalid/a.\n- Ask for more.")
    assert answer == "Done. See Ask for more."
    assert "http" not in answer
    assert "*" not in answer
    assert len(answer.split(". ")) <= 2


def test_voice_guardrail_validates_lists_ids_and_realistic_length():
    candidate = "\n".join(f"- item {index}" for index in range(50))
    candidate += " See thread: abcdef0123456789."
    assert {"too_many_words", "list", "raw_id"}.issubset(
        validate_voice_answer(candidate)
    )
    safe = sanitize_voice_answer(candidate)
    assert not validate_voice_answer(safe)
    assert len(safe.split()) <= 48


def test_audio_is_ephemeral_and_expiring():
    import glasses_audio

    clear_audio()
    ref = put_audio(b"RIFF", user_email="a@example.invalid", ttl_seconds=1)
    assert get_audio(ref["audio_id"], user_email="a@example.invalid") == b"RIFF"
    assert get_audio(ref["audio_id"], user_email="b@example.invalid") is None
    glasses_audio._audio_objects[ref["audio_id"]].expires_at -= __import__("datetime").timedelta(seconds=2)
    assert get_audio(ref["audio_id"], user_email="a@example.invalid") is None
    clear_audio()
    assert get_audio(ref["audio_id"]) is None


def test_audio_route_requires_owner_and_deletes_after_successful_response():
    from routes.glasses import create_glasses_router

    clear_audio()
    ref = put_audio(b"RIFF", user_email="a@example.invalid")
    route = next(item for item in create_glasses_router().routes if item.path.endswith("{audio_id}"))
    response = route.endpoint(ref["audio_id"], {"email": "a@example.invalid"})
    assert response.body == b"RIFF"
    assert get_audio(ref["audio_id"], user_email="a@example.invalid") == b"RIFF"
    asyncio.run(response.background())
    assert get_audio(ref["audio_id"], user_email="a@example.invalid") is None


def test_audio_route_denies_wrong_owner():
    from routes.glasses import create_glasses_router

    clear_audio()
    ref = put_audio(b"RIFF", user_email="a@example.invalid")
    route = next(item for item in create_glasses_router().routes if item.path.endswith("{audio_id}"))
    with pytest.raises(HTTPException) as caught:
        route.endpoint(ref["audio_id"], {"email": "b@example.invalid"})
    assert caught.value.status_code == 404


@pytest.mark.asyncio
async def test_fixed_gate_operation_uses_turn_on_intent_with_fixed_script_name(monkeypatch):
    import glasses_commands

    calls = []

    async def fake_call(tool_name, arguments):
        calls.append((tool_name, arguments))
        return {"success": True}

    monkeypatch.setattr("mcp.servers.home_assistant.is_ha_configured", lambda: True)
    monkeypatch.setattr("mcp.servers.home_assistant.call_ha_tool_async", fake_call)
    result = await glasses_commands.execute_gate("car_gate")
    assert result == {
        "tool_name": "intent__HassTurnOn",
        "arguments": {"name": "Garage gate automation"},
    }
    assert calls == [("intent__HassTurnOn", {"name": "Garage gate automation"})]


@pytest.mark.asyncio
async def test_turn_on_gate_target_name_can_be_overridden(monkeypatch):
    import glasses_commands

    calls = []

    async def fake_call(tool_name, arguments):
        calls.append((tool_name, arguments))
        return {"success": True}

    monkeypatch.setenv("GLASSES_FRONT_GATE_NAME", "Custom house gate")
    monkeypatch.setattr("mcp.servers.home_assistant.is_ha_configured", lambda: True)
    monkeypatch.setattr("mcp.servers.home_assistant.call_ha_tool_async", fake_call)
    result = await glasses_commands.execute_gate("front_gate")
    assert result == {
        "tool_name": "intent__HassTurnOn",
        "arguments": {"name": "Custom house gate"},
    }
    assert calls == [("intent__HassTurnOn", {"name": "Custom house gate"})]


@pytest.mark.asyncio
async def test_command_latency_log_combines_client_and_backend_timings(monkeypatch, caplog):
    import glasses_commands

    monkeypatch.setattr(
        glasses_commands,
        "claim_command",
        lambda *_args: {"claimed": True, "status": "processing"},
    )
    monkeypatch.setattr(glasses_commands, "finish_command", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        glasses_commands,
        "execute_gate",
        lambda *_args, **_kwargs: asyncio.sleep(0, result={"tool_name": "script__toggle_house_gate", "arguments": {}}),
    )
    caplog.set_level("INFO")
    payload = SimpleNamespace(
        command_id=uuid4(),
        transcript="front gate",
        thread_id=None,
        client_context=None,
        client_timings=GlassesCommandClientTimings(
            wake_to_listening_start_ms=120,
            transcription_ms=85,
            transcription_attempt_count=1,
        ),
    )
    response = await glasses_commands.process_command(payload, {"email": "user@example.invalid"})
    assert response["outcome"] == "control_completed"
    message = caplog.text
    assert "[glasses] command latency" in message
    assert 'transcription_ms": 85' in message
    assert "ha_execution_ms" in message
    assert "total_ms" in message


@pytest.mark.asyncio
async def test_gate_tool_name_can_be_overridden_without_changing_empty_args(monkeypatch):
    import glasses_commands

    calls = []

    async def fake_call(tool_name, arguments):
        calls.append((tool_name, arguments))
        return {"success": True}

    monkeypatch.setenv("GLASSES_FRONT_GATE_TOOL", "script__custom_house_gate")
    monkeypatch.setattr("mcp.servers.home_assistant.is_ha_configured", lambda: True)
    monkeypatch.setattr("mcp.servers.home_assistant.call_ha_tool_async", fake_call)
    result = await glasses_commands.execute_gate("front_gate")
    assert result == {"tool_name": "script__custom_house_gate", "arguments": {}}
    assert calls == [("script__custom_house_gate", {})]


@pytest.mark.asyncio
async def test_fixed_gate_failure_is_structured_and_never_discovered(monkeypatch):
    import glasses_commands

    async def failed_call(tool_name, arguments):
        assert tool_name == "intent__HassTurnOn"
        assert arguments == {"name": "House gate automation"}
        return {"success": False, "error": "script unavailable"}

    monkeypatch.setattr("mcp.servers.home_assistant.is_ha_configured", lambda: True)
    monkeypatch.setattr("mcp.servers.home_assistant.call_ha_tool_async", failed_call)
    with pytest.raises(glasses_commands.GlassesCommandError) as caught:
        await glasses_commands.execute_gate("front_gate")
    assert caught.value.code == "ha_execution_failed"


@pytest.mark.asyncio
async def test_fixed_gate_failure_logs_correlation_and_ha_payload(monkeypatch, caplog):
    import glasses_commands

    async def failed_call(_tool_name, _arguments):
        return {
            "success": False,
            "error": "service call failed",
            "result": {"message": "entity not found", "access_token": "do-not-log"},
        }

    monkeypatch.setattr("mcp.servers.home_assistant.is_ha_configured", lambda: True)
    monkeypatch.setattr("mcp.servers.home_assistant.call_ha_tool_async", failed_call)
    with pytest.raises(glasses_commands.GlassesCommandError):
        await glasses_commands.execute_gate("front_gate", command_id="command-123")

    message = caplog.text
    assert "command_id=command-123" in message
    assert "control=front_gate" in message
    assert "intent__HassTurnOn" in message
    assert "entity not found" in message
    assert "do-not-log" not in message
    assert "[REDACTED]" in message


def test_mcp_error_content_becomes_actionable_error():
    from mcp.client import MCPClient

    client = MCPClient(base_url="http://example.invalid", token="fake-token")
    result = client._parse_tool_result(
        {
            "isError": True,
            "content": [{"type": "text", "text": "Service call failed: entity not found"}],
        }
    )

    assert result.success is False
    assert result.error == "Service call failed: entity not found"
    assert result.to_dict()["error"] == "Service call failed: entity not found"


@pytest.mark.asyncio
async def test_gate_timeout_is_terminal_and_non_retryable(monkeypatch):
    import glasses_commands

    stored = {}
    monkeypatch.setenv("GLASSES_SHORTCUT_TIMEOUT_SECONDS", "0.1")
    monkeypatch.setattr(glasses_commands, "claim_command", lambda *_args: {"claimed": True})
    monkeypatch.setattr(
        glasses_commands,
        "finish_command",
        lambda _user, _id, **kwargs: stored.update(kwargs),
    )

    async def slow_call(_tool_name, _arguments):
        await asyncio.sleep(1)
        return {"success": True}

    monkeypatch.setattr("mcp.servers.home_assistant.is_ha_configured", lambda: True)
    monkeypatch.setattr("mcp.servers.home_assistant.call_ha_tool_async", slow_call)
    payload = SimpleNamespace(
        command_id=uuid4(), transcript="front gate", thread_id=None, client_context=None
    )
    result = await glasses_commands.process_command(payload, {"email": "user@example.invalid"})
    assert result["error"] == {
        "code": "deadline_exceeded",
        "message": "The command exceeded its deadline.",
        "retryable": False,
    }
    assert stored["status"] == "failed"


def test_kokoro_engine_is_cached_and_cpu_provider_requested(monkeypatch, tmp_path):
    import glasses_tts

    instances = []
    create_calls = []

    class FakeKokoro:
        def __init__(self, *args, **kwargs):
            instances.append((args, kwargs))

        def create(self, *_args, **kwargs):
            create_calls.append(kwargs)
            return [0.0, 0.1], 16000

    module = ModuleType("kokoro_onnx")
    module.Kokoro = FakeKokoro
    monkeypatch.setitem(sys.modules, "kokoro_onnx", module)
    model = tmp_path / "model.onnx"
    voices = tmp_path / "voices.bin"
    monkeypatch.setenv("KOKORO_MODEL_PATH", str(model))
    monkeypatch.setenv("KOKORO_VOICES_PATH", str(voices))
    glasses_tts._engine = None
    glasses_tts._engine_key = None
    glasses_tts.synthesize_kokoro("one")
    glasses_tts.synthesize_kokoro("two")
    assert len(instances) == 1
    assert instances[0][1]["providers"] == ["CPUExecutionProvider"]
    assert create_calls[0]["voice"] == "af_heart"
    assert create_calls[0]["lang"] == "en-us"


@pytest.mark.asyncio
async def test_concurrent_gate_requests_are_idempotent(monkeypatch):
    import glasses_commands

    command_id = str(uuid4())
    state = {"status": None, "response": None}
    calls = []

    def fake_claim(_user, _command_id, _transcript):
        if state["status"] is None:
            state["status"] = "processing"
            return {"claimed": True, "status": "processing"}
        return {
            "claimed": False,
            "status": state["status"],
            "response": state["response"],
            "transcript": _transcript,
        }

    def fake_finish(_user, _command_id, *, status, outcome, response=None, error=None):
        state["status"] = status
        state["response"] = response

    async def fake_gate(_kind, *, command_id=None):
        assert command_id
        calls.append(True)
        return {"tool_name": "script__toggle_car_gate", "arguments": {}}

    monkeypatch.setattr(glasses_commands, "claim_command", fake_claim)
    monkeypatch.setattr(glasses_commands, "finish_command", fake_finish)
    monkeypatch.setattr(glasses_commands, "execute_gate", fake_gate)
    monkeypatch.setenv("GLASSES_IDEMPOTENCY_WAIT_SECONDS", "1")

    payload = SimpleNamespace(
        command_id=command_id, transcript="car gate", thread_id=None, client_context=None
    )
    first, second = await asyncio.gather(
        glasses_commands.process_command(payload, {"email": "user@example.invalid"}),
        glasses_commands.process_command(payload, {"email": "user@example.invalid"}),
    )
    assert calls == [True]
    assert first["outcome"] == second["outcome"] == "control_completed"


@pytest.mark.asyncio
async def test_terminal_duplicate_replays_without_execution(monkeypatch):
    import glasses_commands

    replay = {"outcome": "control_completed", "command_id": str(uuid4()), "answer": "Done."}
    monkeypatch.setattr(
        glasses_commands,
        "claim_command",
        lambda *_args: {"claimed": False, "status": "completed", "response": replay},
    )
    monkeypatch.setattr(
        glasses_commands,
        "execute_gate",
        lambda *_args: (_ for _ in ()).throw(AssertionError("must not execute duplicate")),
    )
    payload = SimpleNamespace(
        command_id=replay["command_id"],
        transcript="car gate",
        thread_id=None,
        client_context=None,
    )
    assert await glasses_commands.process_command(payload, {"email": "user@example.invalid"}) == replay


@pytest.mark.asyncio
async def test_command_id_reuse_with_different_transcript_is_rejected(monkeypatch):
    import glasses_commands

    monkeypatch.setattr(
        glasses_commands,
        "claim_command",
        lambda *_args: (_ for _ in ()).throw(
            glasses_commands.GlassesCommandError("command_id_reused", "command_id was already used")
        ),
    )
    payload = SimpleNamespace(
        command_id=uuid4(), transcript="front gate", thread_id=None, client_context=None
    )
    with pytest.raises(glasses_commands.GlassesCommandError) as caught:
        await glasses_commands.process_command(payload, {"email": "user@example.invalid"})
    assert caught.value.code == "command_id_reused"


@pytest.mark.asyncio
async def test_agent_is_server_forced_voice_and_tts_uses_canonical_answer(monkeypatch):
    import glasses_commands
    import routes.chat as chat_routes

    stored = {}
    tts_inputs = []
    llm_kwargs = {}

    monkeypatch.setattr(
        glasses_commands,
        "claim_command",
        lambda *_args: {"claimed": True, "status": "processing"},
    )
    monkeypatch.setattr(
        glasses_commands,
        "finish_command",
        lambda _user, _id, **kwargs: stored.update(kwargs),
    )
    monkeypatch.setattr(chat_routes, "handle_pending_event", lambda *_a, **_k: None)
    monkeypatch.setattr(
        chat_routes,
        "_resolve_session_context",
        lambda *_a, **_k: SimpleNamespace(
            question="What is next?", session_id="thread_x", is_reset_only=False
        ),
    )

    async def fake_answer(*_args, **kwargs):
        llm_kwargs.update(kwargs)
        return {"answer": "A repaired answer.", "search_results": [], "events_results": []}

    monkeypatch.setattr(glasses_commands, "synthesize_kokoro", lambda text: tts_inputs.append(text) or b"wav")
    monkeypatch.setattr("llm.answer_question", fake_answer)

    payload = SimpleNamespace(
        command_id=uuid4(),
        transcript="What is next?",
        thread_id=None,
        client_context=None,
    )
    result = await glasses_commands.process_command(payload, {"email": "user@example.invalid"})
    assert llm_kwargs["response_modality"] == "voice"
    assert tts_inputs == ["A repaired answer."]
    assert stored["response"]["answer"] == tts_inputs[0]
    assert result["answer"] == tts_inputs[0]


@pytest.mark.asyncio
async def test_non_shortcut_deadline_covers_legacy_command_handling(monkeypatch):
    import glasses_commands
    import routes.chat as chat_routes

    stored = {}
    monkeypatch.setenv("GLASSES_AGENT_TIMEOUT_SECONDS", "1")
    monkeypatch.setattr(glasses_commands, "claim_command", lambda *_a: {"claimed": True})
    monkeypatch.setattr(
        glasses_commands,
        "finish_command",
        lambda _user, _id, **kwargs: stored.update(kwargs),
    )

    def slow_pending(*_args, **_kwargs):
        import time

        time.sleep(2)
        return None

    monkeypatch.setattr(chat_routes, "handle_pending_event", slow_pending)
    monkeypatch.setattr(chat_routes, "_handle_command", lambda *_a, **_k: None)
    payload = SimpleNamespace(
        command_id=uuid4(),
        transcript="ordinary question",
        thread_id=None,
        client_context=None,
    )
    result = await glasses_commands.process_command(payload, {"email": "user@example.invalid"})
    assert result["error"]["code"] == "deadline_exceeded"
    assert result["error"]["retryable"] is False
    assert stored["status"] == "failed"
