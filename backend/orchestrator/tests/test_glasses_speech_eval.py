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
    request.assert_called_once_with("small.en", "transcribe", True, data)


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
    with pytest.raises(speech.EvalFailure, match="server_timeout"):
        engine.request("small.en", "transcribe", True, wav())
    process.terminate.assert_called_once()
    pipe.close.assert_called_once()
    assert engine.process is None
    assert engine.lock.acquire(blocking=False)
    engine.lock.release()
