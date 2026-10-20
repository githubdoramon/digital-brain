import io
import wave
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import glasses_speech_eval as speech
from auth import get_current_user
from routes.glasses_speech_eval import create_glasses_speech_eval_router


def wav(rate=16000, channels=1, seconds=1):
    data = io.BytesIO()
    with wave.open(data, "wb") as output:
        output.setnchannels(channels)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(b"\0\0" * rate * channels * seconds)
    return data.getvalue()


def client(authenticated=True):
    app = FastAPI()
    app.include_router(create_glasses_speech_eval_router())
    if authenticated:
        app.dependency_overrides[get_current_user] = lambda: {"email": "viewer@example.com"}
    return TestClient(app)


def test_audio_validation():
    speech.validate_audio(wav())
    for data in [
        b"",
        b"arbitrary bytes",
        wav(rate=8000),
        wav(channels=2),
        wav()[:-2],
        wav(seconds=0),
    ]:
        with pytest.raises(speech.EvalFailure, match="invalid_audio"):
            speech.validate_audio(data)
    with pytest.raises(speech.EvalFailure, match="audio_too_large"):
        speech.validate_audio(wav(seconds=31))


def test_route_requires_auth_and_default_opt_in(monkeypatch):
    monkeypatch.delenv("GLASSES_SPEECH_EVAL_ENABLED", raising=False)
    assert (
        client(False)
        .post("/mobile/glasses/speech-eval/transcribe?model=small.en", content=wav())
        .status_code
        == 401
    )
    response = client().post("/mobile/glasses/speech-eval/transcribe?model=small.en", content=wav())
    assert response.status_code == 503
    assert response.json() == {"detail": "evaluation_disabled"}
    assert not client().get("/mobile/glasses/speech-eval/models").json()["enabled"]


def test_replay_route_only_passes_audio_and_selected_model(monkeypatch):
    monkeypatch.setenv("GLASSES_SPEECH_EVAL_ENABLED", "true")
    request = MagicMock(
        return_value={"text": "Hey Brain take a picture", "modelLoadMs": 80, "inferenceMs": 20}
    )
    monkeypatch.setattr(speech.engine, "request", request)
    data = wav()
    response = client().post(
        "/mobile/glasses/speech-eval/transcribe?model=small.en&cold=true", content=data
    )
    assert response.status_code == 200
    request.assert_called_once_with("small.en", "transcribe", True, data, None)


def test_upload_bound_before_inference(monkeypatch):
    monkeypatch.setenv("GLASSES_SPEECH_EVAL_ENABLED", "true")
    request = MagicMock()
    monkeypatch.setattr(speech.engine, "request", request)
    response = client().post(
        "/mobile/glasses/speech-eval/transcribe?model=small.en",
        content=b"x" * (speech.MAX_AUDIO_BYTES + 1),
    )
    assert response.status_code == 413
    request.assert_not_called()


@pytest.mark.parametrize(
    "reason,status",
    [
        ("server_busy", 429),
        ("server_timeout", 504),
        ("invalid_audio", 400),
        ("engine_unavailable", 503),
    ],
)
def test_safe_failures(monkeypatch, reason, status):
    monkeypatch.setenv("GLASSES_SPEECH_EVAL_ENABLED", "true")
    monkeypatch.setattr(speech.engine, "request", MagicMock(side_effect=speech.EvalFailure(reason)))
    response = client().post("/mobile/glasses/speech-eval/transcribe?model=small.en", content=wav())
    assert response.status_code == status
    assert response.json() == {"detail": reason}


def test_engine_rejects_arbitrary_paths(monkeypatch):
    monkeypatch.setenv("GLASSES_SPEECH_EVAL_ENABLED", "true")
    with pytest.raises(speech.EvalFailure, match="invalid_model"):
        speech.SpeechEvalEngine().request("/tmp/model", "prepare")


def test_timeout_terminates_child_and_releases_slot(monkeypatch):
    monkeypatch.setenv("GLASSES_SPEECH_EVAL_ENABLED", "true")
    engine = speech.SpeechEvalEngine()
    process, pipe = MagicMock(), MagicMock()
    process.is_alive.side_effect = [True, False]
    pipe.poll.return_value = False
    engine.process, engine.pipe = process, pipe
    engine.threads = 2
    with pytest.raises(speech.EvalFailure, match="server_timeout"):
        engine.request("small.en", "transcribe", True, wav())
    process.terminate.assert_called_once()
    pipe.close.assert_called_once()
    assert engine.process is None
    assert engine.lock.acquire(blocking=False)
    engine.lock.release()


@pytest.mark.parametrize(
    "error,expected",
    [
        (OSError(28, "private path /example/private"), "cache_storage_full"),
        (PermissionError(13, "private access token"), "cache_access_denied"),
        (MemoryError("private utterance"), "insufficient_memory"),
        (TimeoutError("private URL"), "network_timeout"),
        (RuntimeError("private utterance"), "model_download_failed"),
    ],
)
def test_failure_classification_never_copies_exception_messages(error, expected):
    result = speech.classify_failure(error, speech.EvalStage.DOWNLOAD)
    assert result == {"error": expected, "stage": "model_download"}
    assert "private" not in str(result)


def test_provider_http_failure_has_safe_status_and_stage(monkeypatch):
    error = RuntimeError("https://private.example/token?credential=secret")
    error.response = MagicMock(status_code=403)
    result = speech.classify_failure(error, speech.EvalStage.DOWNLOAD)
    assert result == {
        "error": "model_download_http_error",
        "stage": "model_download",
        "upstreamHttpStatus": 403,
    }
    monkeypatch.setenv("GLASSES_SPEECH_EVAL_ENABLED", "true")
    monkeypatch.setattr(
        speech.engine,
        "request",
        MagicMock(side_effect=speech.EvalFailure(result["error"], result["stage"], 403)),
    )
    response = client().post("/mobile/glasses/speech-eval/prepare?model=large-v3")
    assert response.json() == {
        "detail": "model_download_http_error",
        "failureStage": "model_download",
        "upstreamHttpStatus": 403,
    }


def test_progress_is_logged_in_parent_without_audio_text(monkeypatch, caplog):
    monkeypatch.setenv("GLASSES_SPEECH_EVAL_ENABLED", "true")
    engine = speech.SpeechEvalEngine()
    process, pipe = MagicMock(), MagicMock()
    process.is_alive.return_value = True
    pipe.poll.return_value = True
    pipe.recv.side_effect = [
        {"event": "stage", "stage": "model_download"},
        {"event": "stage", "stage": "inference"},
        {"text": "a private transcript", "modelLoadMs": 1, "inferenceMs": 2, "cpuThreads": 2},
    ]
    engine.process, engine.pipe = process, pipe
    engine.threads = 2
    with caplog.at_level("INFO", logger="glasses_speech_eval"):
        result = engine.request("small.en", "transcribe", False, wav())
    assert result["text"] == "a private transcript"
    assert "stage=inference" in caplog.text
    assert "completed model=small.en" in caplog.text
    assert "private transcript" not in caplog.text


def test_failed_stage_logged_without_provider_message(monkeypatch, caplog):
    monkeypatch.setenv("GLASSES_SPEECH_EVAL_ENABLED", "true")
    engine = speech.SpeechEvalEngine()
    process, pipe = MagicMock(), MagicMock()
    process.is_alive.return_value = True
    pipe.poll.return_value = True
    pipe.recv.return_value = {"error": "model_load_failed", "stage": "model_load"}
    engine.process, engine.pipe = process, pipe
    engine.threads = 2
    with caplog.at_level("INFO", logger="glasses_speech_eval"), pytest.raises(speech.EvalFailure):
        engine.request("large-v3", "transcribe", False, wav())
    assert "stage=model_load reason=model_load_failed" in caplog.text


def test_thread_selection_reaches_engine_and_is_bounded(monkeypatch):
    monkeypatch.setenv("GLASSES_SPEECH_EVAL_ENABLED", "true")
    request = MagicMock(return_value={"cpuThreads": 4})
    monkeypatch.setattr(speech.engine, "request", request)
    response = client().post(
        "/mobile/glasses/speech-eval/transcribe?model=small.en&threads=4", content=wav()
    )
    assert response.status_code == 200
    assert request.call_args.args[-1] == 4
    with pytest.raises(speech.EvalFailure, match="invalid_cpu_threads"):
        speech.SpeechEvalEngine().request("small.en", "prepare", threads=3)
    assert (
        client().post("/mobile/glasses/speech-eval/prepare?model=small.en&threads=99").status_code
        == 422
    )


def test_thread_change_restarts_model_process(monkeypatch):
    monkeypatch.setenv("GLASSES_SPEECH_EVAL_ENABLED", "true")
    engine = speech.SpeechEvalEngine()
    previous, previous_pipe = MagicMock(), MagicMock()
    previous.is_alive.side_effect = [True, False]
    engine.process, engine.pipe, engine.threads = previous, previous_pipe, 2
    context, process, pipe, child = MagicMock(), MagicMock(), MagicMock(), MagicMock()
    context.Pipe.return_value = (pipe, child)
    context.Process.return_value = process
    pipe.poll.return_value = True
    pipe.recv.return_value = {"prepared": True, "cpuThreads": 4}
    monkeypatch.setattr(speech.mp, "get_context", lambda _: context)
    result = engine.request("small.en", "prepare", threads=4)
    previous.terminate.assert_called_once()
    process.start.assert_called_once()
    assert context.Process.call_args.kwargs["args"][2] == 4
    assert result["cpuThreads"] == engine.threads == 4


def test_same_thread_count_keeps_warm_process(monkeypatch):
    monkeypatch.setenv("GLASSES_SPEECH_EVAL_ENABLED", "true")
    engine = speech.SpeechEvalEngine()
    process, pipe = MagicMock(), MagicMock()
    process.is_alive.return_value = True
    engine.process, engine.pipe, engine.threads = process, pipe, 4
    pipe.poll.return_value = True
    pipe.recv.return_value = {"modelAlreadyLoaded": True, "cpuThreads": 4}
    result = engine.request("small.en", "transcribe", audio=wav(), threads=4)
    process.terminate.assert_not_called()
    assert result["modelAlreadyLoaded"] is True
    assert result["cpuThreads"] == 4


def test_misreported_thread_count_is_not_scored_as_requested_configuration(monkeypatch):
    monkeypatch.setenv("GLASSES_SPEECH_EVAL_ENABLED", "true")
    engine = speech.SpeechEvalEngine()
    process, pipe = MagicMock(), MagicMock()
    process.is_alive.return_value = True
    engine.process, engine.pipe, engine.threads = process, pipe, 4
    pipe.poll.return_value = True
    pipe.recv.return_value = {"prepared": True, "cpuThreads": 2}
    with pytest.raises(speech.EvalFailure, match="cpu_configuration_mismatch"):
        engine.request("small.en", "prepare", threads=4)
