from __future__ import annotations

import asyncio
import base64
import io
import logging
import os
import time
import wave
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import BackgroundTasks, FastAPI, HTTPException, Response
from fastapi.testclient import TestClient

os.environ.setdefault("LLM_BASE_URL", "http://localhost:11434/v1")
os.environ.setdefault("LLM_CHAT_MODEL_FAST", "test-fast")
os.environ.setdefault("LLM_CHAT_MODEL_SMART", "test-smart")

import glasses_tts
import llm
from agent.tool_executor import ToolExecutionCoordinator
from auth import get_current_user
from observability.log_stream import ConsoleLogHandler, LogBufferHandler
from observability.logger import AgentLogger
from routes import glasses_chat
from schemas import AskOut
from time_context import current_context
from tools.registry import ToolRegistry
from voice_context import is_voice_request, spoken_reply, voice_messages


def body(text="Hey Brain, what is on my calendar?"):
    return {
        "transcript": text,
        "request_id": str(uuid4()),
        "captured_at_ms": int(time.time() * 1000),
        "timezone": "Europe/Lisbon",
    }


@pytest.fixture
def route(tmp_path, monkeypatch):
    monkeypatch.setenv("GLASSES_COMMAND_RECEIPT_DIR", str(tmp_path))
    calls = []

    async def ask(payload, tasks, user):
        assert is_voice_request()
        assert payload.thread_id is None and payload.session_id is None
        assert current_context()["timezone"] == "Europe/Lisbon"
        calls.append(payload.question)
        return AskOut(
            question=payload.question,
            answer="You have one meeting.",
            resolution={},
            search_results=[],
            events_results=[],
            thread_id="shared-thread",
        )

    async def synthesize(text):
        assert text == "You have one meeting."
        return {"encoding": "pcm16", "sample_rate": 24000, "data": "AAA="}

    monkeypatch.setattr(glasses_chat, "ask", ask)
    monkeypatch.setattr(glasses_chat, "synthesize", synthesize)
    app = FastAPI()
    app.include_router(glasses_chat.create_glasses_chat_router())
    app.dependency_overrides[get_current_user] = lambda: {"email": "viewer@example.com"}
    return TestClient(app), calls, app


def test_voice_chat_uses_shared_flow_and_does_not_repeat_claimed_requests(route):
    client, calls, _ = route
    request = body()
    result = client.post("/mobile/glasses/chat", json=request)
    assert result.status_code == 200
    assert result.headers["cache-control"] == "no-store"
    assert result.json()["thread_id"] == "shared-thread"
    assert result.json()["audio"]["encoding"] == "pcm16"
    assert client.post("/mobile/glasses/chat", json=request).status_code == 409
    assert calls == ["what is on my calendar?"]
    assert not is_voice_request()


def test_auth_expiry_missing_wake_empty_and_foreign_thread_are_rejected(route):
    client, calls, app = route
    for text in [
        "what is on my calendar",
        "I said Hey Brain what is on my calendar",
        "Hey Brain",
        "Hey Brain.",
        "Hey Brain! ...",
    ]:
        assert client.post("/mobile/glasses/chat", json=body(text)).status_code == 422
    expired = body()
    expired["captured_at_ms"] -= 91_000
    assert client.post("/mobile/glasses/chat", json=expired).status_code == 409
    assert (
        client.post(
            "/mobile/glasses/chat", json={**body(), "thread_id": "other-owner-thread"}
        ).status_code
        == 422
    )
    app.dependency_overrides.clear()
    assert client.post("/mobile/glasses/chat", json=body()).status_code == 401
    assert calls == []


def test_ambiguous_and_negated_requests_can_be_discussed_and_session_reset_is_preserved(route):
    client, calls, _ = route
    for text in [
        "Hey Brain don't open the gate",
        "Okay Brian can you take a picture and open the door",
        "Hey Brain slash new",
    ]:
        assert client.post("/mobile/glasses/chat", json=body(text)).status_code == 200
    assert calls == ["don't open the gate", "can you take a picture and open the door", "/new"]


def test_speech_failure_preserves_the_chat_answer(route, monkeypatch):
    async def unavailable(text):
        raise glasses_tts.SpeechUnavailable("tts_not_configured")

    monkeypatch.setattr(glasses_chat, "synthesize", unavailable)
    result = route[0].post("/mobile/glasses/chat", json=body()).json()
    assert result["answer"] == "You have one meeting."
    assert result["audio"] is None and result["audio_error"] == "tts_not_configured"


def test_speech_stage_reports_failure_without_private_content(route, monkeypatch, caplog):
    async def unavailable(text):
        raise glasses_tts.SpeechUnavailable("tts_not_configured")

    monkeypatch.setattr(glasses_chat, "synthesize", unavailable)
    with caplog.at_level(logging.INFO, logger="voice_context"):
        route[0].post("/mobile/glasses/chat", json=body())
    messages = [r.getMessage() for r in caplog.records if r.name == "voice_context"]
    assert messages == [
        "[glasses-chat] stage=started reason=none",
        "[glasses-chat] stage=answer_ready reason=none",
        "[glasses-chat] stage=speech_failed reason=tts_not_configured",
    ]
    assert not any("calendar" in message or "meeting" in message for message in messages)


def test_claims_are_owner_scoped_and_recovery_never_replays(route):
    client, calls, app = route
    request = body()
    assert client.post("/mobile/glasses/chat", json=request).status_code == 200
    app.dependency_overrides[get_current_user] = lambda: {"email": "another@example.com"}
    assert client.post("/mobile/glasses/chat", json=request).status_code == 200
    assert len(calls) == 2


def wav(rate=24000, channels=1, width=2, frames=100):
    stream = io.BytesIO()
    with wave.open(stream, "wb") as audio:
        audio.setnchannels(channels)
        audio.setsampwidth(width)
        audio.setframerate(rate)
        audio.writeframes(b"\0" * frames * channels * width)
    return stream.getvalue()


def test_audio_validation_rejects_corruption_unsupported_format_and_duration():
    result = glasses_tts.pcm_payload(wav())
    assert len(base64.b64decode(result["data"])) == 200
    for audio in [
        b"bad",
        wav(channels=2),
        wav(width=1),
        wav(frames=0),
        wav(rate=8000),
        wav(frames=24000 * 46),
        wav()[:-2],
    ]:
        with pytest.raises(glasses_tts.SpeechUnavailable):
            glasses_tts.pcm_payload(audio)


def test_voice_tool_restrictions_apply_to_visibility_and_execution_without_leaking():
    registry = ToolRegistry()
    registry._contracts = {
        name: SimpleNamespace(to_openai_tool=dict)
        for name in ["home_assistant", "bash", "run_skill_script", "search_memories"]
    }
    coordinator = ToolExecutionCoordinator(object())
    assert len(registry.get_tool_definitions()) == 4
    with spoken_reply():
        assert len(registry.get_tool_definitions()) == 1
        for name in ["home_assistant", "bash", "run_skill_script"]:
            result = asyncio.run(
                coordinator.execute_tool_call(
                    {"id": "x", "function": {"name": name, "arguments": "{}"}},
                    None,
                    "open the gate",
                    30,
                    "run",
                )
            )
            assert "error" in result
            assert "error" in coordinator.execute_handler(name, {}, None, "open the gate", 30)
        assert "spoken verbatim" in voice_messages()[0]["content"]
    assert len(registry.get_tool_definitions()) == 4
    assert voice_messages() == []


def test_same_canonical_answer_is_persisted_and_spoken(monkeypatch):
    persisted = []

    async def run(**kwargs):
        return {"answer": "**One meeting.** See [the calendar](https://calendar.example/)."}

    monkeypatch.setattr(llm, "_get_main_controller", lambda: SimpleNamespace(run=run))
    monkeypatch.setattr(llm.conversations, "get_conversation_history", lambda *a: [])
    monkeypatch.setattr(llm.conversations, "get_latest_assistant_metadata", lambda *a: None)
    monkeypatch.setattr(
        llm.conversations,
        "record_exchange",
        lambda **kw: persisted.append(kw) or {"message_count_before": 1},
    )
    with spoken_reply():
        result = asyncio.run(
            llm.answer_question(
                "My calendar?", session_id="thread", user_email="viewer@example.com"
            )
        )
    assert result["answer"] == persisted[0]["assistant_message"] == "One meeting. See the calendar."
    assert persisted[0]["user_metadata"]["input_source"] == "glasses"


def test_voice_logs_do_not_enter_console_or_general_log_buffer():
    output = io.StringIO()
    console = ConsoleLogHandler(output)
    buffer = LogBufferHandler()
    from observability.log_stream import get_log_buffer

    before = len(get_log_buffer().get_recent())
    record = logging.LogRecord("voice", logging.WARNING, __file__, 1, "private speech", (), None)
    with spoken_reply():
        console.emit(record)
        buffer.emit(record)
        agent_logger = AgentLogger()
        run = agent_logger.start_run("private speech", "viewer@example.com")
        assert agent_logger.export_json(run) is None
    assert output.getvalue() == ""
    assert len(get_log_buffer().get_recent()) == before


@pytest.mark.parametrize(
    "base", ["http://tts.example", "http://tts.example/v1", "http://tts.example/v1/audio/speech"]
)
def test_tts_endpoint_formats(base, monkeypatch):
    monkeypatch.setenv("TTS_BASE_URL", base)
    assert glasses_tts.speech_endpoint() == "http://tts.example/v1/audio/speech"


@pytest.mark.asyncio
@pytest.mark.parametrize("disconnect", [True, False])
async def test_disconnect_and_deadline_cancel_chat_and_release_owner(
    route, monkeypatch, disconnect
):
    started, cancelled = asyncio.Event(), asyncio.Event()

    async def waiting(*args):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    async def disconnected():
        await started.wait()
        return disconnect

    monkeypatch.setattr(glasses_chat, "ask", waiting)
    monkeypatch.setattr(glasses_chat, "CHAT_DEADLINE_SECONDS", 0.05)
    endpoint = next(r.endpoint for r in glasses_chat.create_glasses_chat_router().routes
                    if r.path == "/mobile/glasses/chat")
    with pytest.raises(HTTPException) as exc:
        await endpoint(
            glasses_chat.GlassesChatIn(**body()),
            SimpleNamespace(is_disconnected=disconnected),
            Response(),
            BackgroundTasks(),
            {"email": "viewer@example.com"},
        )
    assert exc.value.status_code == (499 if disconnect else 504)
    assert cancelled.is_set()
    assert "viewer@example.com" not in glasses_chat._active_owners
    assert not is_voice_request()


@pytest.mark.asyncio
async def test_remote_tts_sends_the_canonical_reply_and_returns_ephemeral_pcm(monkeypatch):
    import httpx

    requests = []

    async def handler(request):
        import json

        requests.append(json.loads(request.content))
        assert request.headers["Authorization"] == "Bearer synthetic-test-key"
        return httpx.Response(200, content=wav(), headers={"content-type": "audio/wav"})

    client = httpx.AsyncClient
    monkeypatch.setenv("TTS_PROVIDER", "openai_compatible")
    monkeypatch.setenv("TTS_BASE_URL", "http://tts.example/v1")
    monkeypatch.setenv("TTS_API_KEY", "synthetic-test-key")
    monkeypatch.setattr(
        glasses_tts.httpx,
        "AsyncClient",
        lambda **kw: client(transport=httpx.MockTransport(handler), **kw),
    )
    result = await glasses_tts.synthesize("You have one meeting.")
    assert result["sample_rate"] == 24000
    assert requests == [
        {
            "model": "kokoro",
            "voice": "af_heart",
            "input": "You have one meeting.",
            "response_format": "wav",
        }
    ]


def test_wake_comparison_is_accent_insensitive_but_preserves_the_phrase():
    assert glasses_chat.chat_phrase("Héy Bráin, remember café plans") == "remember café plans"


@pytest.mark.parametrize("profile", ["main", "memory_expert"])
def test_both_conversational_profiles_receive_only_request_scoped_voice_instructions(
    monkeypatch, profile
):
    from importlib import import_module

    from agent.state import AgentState

    module = import_module(f"agents.{profile}.message_builder")
    monkeypatch.setattr(module, "_cached_tag_context", lambda: "")
    if profile == "main":
        build, extra = module.build_main_messages, {"skill_injector": lambda *_: None}
    else:
        build, extra = module.build_memory_expert_messages, {}
        monkeypatch.setattr(module, "inject_memory_expert_skills", lambda **kw: None)

    def messages():
        return build(
            question="What is next?",
            state=AgentState(goal="What is next?"),
            conversation_history=None,
            user_email=None,
            search_limit=30,
            client_context=None,
            **extra,
        )

    with spoken_reply():
        assert any("spoken verbatim" in m["content"] for m in messages())
    assert not any("spoken verbatim" in m["content"] for m in messages())


@pytest.mark.asyncio
async def test_no_tts_url_uses_local_cpu_engine(monkeypatch):
    import sys

    import glasses_kokoro as local

    calls = []

    class Engine:
        def create(self, text, **kwargs):
            calls.append((text, kwargs))
            return [0.1] * 100, 24000

    def session(model, **kwargs):
        assert kwargs["providers"] == ["CPUExecutionProvider"]
        return object()

    monkeypatch.setitem(
        sys.modules,
        "onnxruntime",
        SimpleNamespace(SessionOptions=SimpleNamespace, InferenceSession=session),
    )
    monkeypatch.setitem(
        sys.modules,
        "kokoro_onnx",
        SimpleNamespace(Kokoro=SimpleNamespace(from_session=lambda *a: Engine())),
    )
    monkeypatch.setattr(local.os.path, "isfile", lambda p: True)
    monkeypatch.setattr(local, "_engine", None)
    monkeypatch.setattr(local, "_engine_key", None)
    monkeypatch.delenv("TTS_BASE_URL", raising=False)
    result = await glasses_tts.synthesize("A short spoken reply.")
    assert result["sample_rate"] == 24000
    assert len(base64.b64decode(result["data"])) == 200
    assert calls[0][1]["voice"] == "af_heart"


@pytest.mark.asyncio
async def test_busy_local_inference_never_queues_another_turn(monkeypatch):
    import threading

    import glasses_kokoro as local

    slot = threading.BoundedSemaphore(1)
    monkeypatch.setattr(local, "_slot", slot)
    slot.acquire()
    try:
        with pytest.raises(glasses_tts.SpeechUnavailable):
            await local.synthesize_local("Another reply.")
    finally:
        slot.release()


def test_spoken_answer_has_a_hard_bound_before_persistence():
    from voice_context import speech_text

    text = "Your next meeting starts tomorrow. " + "Additional background information. " * 70
    concise = speech_text(text)
    assert concise.startswith("Your next meeting starts tomorrow.")
    assert len(concise.split()) <= 60
    assert len(concise) <= 600
    assert concise.endswith(".")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "frames,reason", [(0, "tts_audio_empty"), (24000 * 46, "tts_audio_too_long")]
)
async def test_local_audio_rejections_have_specific_safe_reasons(monkeypatch, frames, reason):
    import sys

    import glasses_kokoro as local

    class Samples:
        def __len__(self):
            return frames

    engine = SimpleNamespace(create=lambda *a, **kw: (Samples(), 24000))
    monkeypatch.setitem(sys.modules, "onnxruntime", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "kokoro_onnx", SimpleNamespace(Kokoro=object))
    monkeypatch.setattr(local.os.path, "isfile", lambda p: True)
    monkeypatch.setattr(local, "_engine", engine)
    monkeypatch.setattr(
        local, "_engine_key", ("/app/models/kokoro/model.onnx", "/app/models/kokoro/voices.bin")
    )
    with pytest.raises(glasses_tts.SpeechUnavailable, match=reason):
        await local.synthesize_local("A short reply.")


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", [None, "", "kokoro"])
async def test_legacy_remote_settings_cannot_override_local_default(monkeypatch, provider, caplog):
    import glasses_kokoro

    if provider is None:
        monkeypatch.delenv("TTS_PROVIDER", raising=False)
    else:
        monkeypatch.setenv("TTS_PROVIDER", provider)
    monkeypatch.setenv("TTS_BASE_URL", "https://legacy-tts.example/v1")
    monkeypatch.setenv("TTS_API_KEY", "synthetic-legacy-key")
    monkeypatch.setenv("TTS_MODEL", "legacy-model")
    monkeypatch.setenv("TTS_VOICE", "legacy-voice")

    async def local(text):
        assert text == "A short reply."
        return {"encoding": "pcm16", "sample_rate": 24000, "data": "AAA="}

    monkeypatch.setattr(glasses_kokoro, "synthesize_local", local)
    monkeypatch.setattr(
        glasses_tts.httpx,
        "AsyncClient",
        lambda **kw: pytest.fail("Remote provider must not be contacted"),
    )
    with caplog.at_level(logging.INFO, logger="voice_context"):
        result = await glasses_tts.synthesize("A short reply.")
    assert result["sample_rate"] == 24000
    assert "[glasses-chat] stage=synthesis_local reason=none" in caplog.text
    assert "synthetic-legacy-key" not in caplog.text
    assert "legacy-tts.example" not in caplog.text


@pytest.mark.asyncio
async def test_unknown_speech_provider_never_falls_back_to_remote(monkeypatch):
    monkeypatch.setenv("TTS_PROVIDER", "misspelled-provider")
    monkeypatch.setenv("TTS_BASE_URL", "https://tts.example/v1")
    with pytest.raises(glasses_tts.SpeechUnavailable, match="tts_not_configured"):
        await glasses_tts.synthesize("A short reply.")


@pytest.mark.asyncio
async def test_cpu_timeout_retains_slot_until_worker_finishes(monkeypatch):
    import threading
    from concurrent.futures import Future

    import glasses_kokoro as local

    slot = threading.BoundedSemaphore(1)
    future = Future()
    monkeypatch.setattr(local, "_slot", slot)
    monkeypatch.setattr(local, "_worker", SimpleNamespace(submit=lambda *a: future))
    monkeypatch.setattr(local, "LOCAL_TTS_TIMEOUT_SECONDS", 0.01)
    with pytest.raises(glasses_tts.SpeechUnavailable, match="tts_timeout"):
        await local.synthesize_local("A short reply.")
    assert not future.cancelled()
    with pytest.raises(glasses_tts.SpeechUnavailable):
        await local.synthesize_local("Another reply.")
    # Production _generate owns releasing the slot when CPU inference finishes.
    slot.release()
    future.set_exception(glasses_tts.SpeechUnavailable("tts_failed"))
    await asyncio.sleep(0)
    assert slot.acquire(blocking=False)
    slot.release()


@pytest.mark.asyncio
async def test_cpu_synthesis_uses_a_budget_that_covers_cold_start(monkeypatch):
    from concurrent.futures import Future

    import glasses_kokoro as local

    future = Future()
    future.set_result({"encoding": "pcm16", "sample_rate": 24000, "data": "AAA="})
    monkeypatch.setattr(local, "_worker", SimpleNamespace(submit=lambda *a: future))
    # A stubbed worker must release its acquired slot as the real worker does.
    import threading

    slot = threading.BoundedSemaphore(1)
    monkeypatch.setattr(local, "_slot", slot)
    observed = []
    real_wait = asyncio.wait_for

    async def wait(pending, timeout):
        observed.append(timeout)
        return await real_wait(pending, timeout)

    monkeypatch.setattr(local.asyncio, "wait_for", wait)
    try:
        result = await local.synthesize_local("A short reply.")
        assert result["sample_rate"] == 24000
        assert observed == [60]
    finally:
        slot.release()


@pytest.mark.parametrize("sentinel", [0xFFFFFFFF, 0x7FFFFFFF])
def test_remote_streaming_wav_uses_received_pcm_length(sentinel):
    import struct

    data = bytearray(wav(frames=200))
    struct.pack_into("<I", data, 4, sentinel)
    struct.pack_into("<I", data, 40, sentinel)
    result = glasses_tts.pcm_payload(bytes(data))
    assert len(base64.b64decode(result["data"])) == 400
    assert result["sample_rate"] == 24000


def test_streaming_wav_still_rejects_partial_frames_and_overlong_audio():
    import struct

    for original in [wav(frames=100)[:-1], wav(frames=24000 * 46), wav(frames=0)]:
        data = bytearray(original)
        struct.pack_into("<I", data, 4, 0xFFFFFFFF)
        struct.pack_into("<I", data, 40, 0xFFFFFFFF)
        with pytest.raises(glasses_tts.SpeechUnavailable):
            glasses_tts.pcm_payload(bytes(data))
    # A normal file claiming more bytes than received is still a truncated file.
    with pytest.raises(glasses_tts.SpeechUnavailable):
        glasses_tts.pcm_payload(wav()[:-2])


def test_explicit_speech_test_uses_canonical_tts_without_chat_or_receipts(route, monkeypatch):
    client, calls, _ = route
    received = []

    async def synthesize(text):
        assert is_voice_request()
        received.append(text)
        return {"encoding": "pcm16", "sample_rate": 24000, "data": "AAA="}

    monkeypatch.setattr(glasses_chat, "synthesize", synthesize)
    monkeypatch.setattr(glasses_chat, "claim", lambda *a: pytest.fail("No chat receipt needed"))
    response = client.post("/mobile/glasses/chat/speech-test", json={"text": "  Open the gate.  "})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert received == ["Open the gate."]
    assert response.json()["audio"]["encoding"] == "pcm16"
    assert "thread_id" not in response.json()
    assert calls == []


@pytest.mark.parametrize("text", ["", "   ", "a" * 401])
def test_speech_test_rejects_empty_or_oversized_input(route, text):
    client, calls, _ = route
    assert client.post("/mobile/glasses/chat/speech-test", json={"text": text}).status_code == 422
    assert calls == []


def test_speech_test_requires_auth_and_shares_owner_limit(route):
    client, calls, app = route
    glasses_chat._active_owners.add("viewer@example.com")
    try:
        assert client.post("/mobile/glasses/chat/speech-test", json={"text": "Hello."}).status_code == 429
    finally:
        glasses_chat._active_owners.discard("viewer@example.com")
    app.dependency_overrides.clear()
    assert client.post("/mobile/glasses/chat/speech-test", json={"text": "Hello."}).status_code == 401
    assert calls == []


def test_speech_test_returns_safe_tts_failure_and_releases_slot(route, monkeypatch):
    client, calls, _ = route

    async def unavailable(text):
        raise glasses_tts.SpeechUnavailable("tts_timeout")

    monkeypatch.setattr(glasses_chat, "synthesize", unavailable)
    response = client.post("/mobile/glasses/chat/speech-test", json={"text": "Hello."})
    assert response.json() == {"audio": None, "audio_error": "tts_timeout"}
    assert "viewer@example.com" not in glasses_chat._active_owners
    assert calls == []


def test_speech_test_deadline_cancels_synthesis_and_releases_slot(route, monkeypatch):
    client, _, _ = route
    cancelled = []

    async def slow(text):
        try:
            await asyncio.sleep(10)
        finally:
            cancelled.append(True)

    monkeypatch.setattr(glasses_chat, "synthesize", slow)
    monkeypatch.setattr(glasses_chat, "SPEECH_TEST_DEADLINE_SECONDS", 0.01)
    response = client.post("/mobile/glasses/chat/speech-test", json={"text": "Hello."})
    assert response.status_code == 504 and cancelled == [True]
    assert "viewer@example.com" not in glasses_chat._active_owners


@pytest.mark.asyncio
async def test_speech_test_disconnect_cancels_synthesis(route, monkeypatch):
    started, cancelled = asyncio.Event(), asyncio.Event()

    async def waiting(text):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    async def disconnected():
        await started.wait()
        return True

    monkeypatch.setattr(glasses_chat, "synthesize", waiting)
    endpoint = next(r.endpoint for r in glasses_chat.create_glasses_chat_router().routes
                    if r.path.endswith("/speech-test"))
    with pytest.raises(HTTPException) as exc:
        await endpoint(glasses_chat.GlassesSpeechTestIn(text="Hello."),
                       SimpleNamespace(is_disconnected=disconnected), Response(),
                       {"email": "viewer@example.com"})
    assert exc.value.status_code == 499 and cancelled.is_set()
    assert "viewer@example.com" not in glasses_chat._active_owners
