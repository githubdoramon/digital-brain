"""GPU-only speech endpoint contracts; synthetic audio and provider responses only."""

import io
import wave
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import speech_transcription as speech
from auth import get_current_user
from routes.glasses_speech_eval import create_glasses_speech_eval_router
from routes.speech import create_speech_router


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
    app.include_router(create_speech_router())
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


def test_live_capture_requires_auth_and_is_independent_of_replay_opt_in(monkeypatch):
    monkeypatch.setenv("GLASSES_SPEECH_EVAL_ENABLED", "false")
    provider = MagicMock(return_value={"text": "Hey Brain take a picture"})
    monkeypatch.setattr(speech, "request", provider)
    url = "/mobile/glasses/transcribe"
    assert client(False).post(url, content=wav()).status_code == 401
    provider.assert_not_called()
    data = wav()
    assert client().post(url, content=data).status_code == 200
    provider.assert_called_once_with(
        speech.MODEL, "transcribe", data, None, replay=False, vocabulary="commands-v1"
    )


def test_replay_disabled_without_live_speech_being_disabled(monkeypatch):
    monkeypatch.setenv("GLASSES_SPEECH_EVAL_ENABLED", "false")
    response = client().post("/mobile/glasses/speech-eval/prepare")
    assert response.status_code == 503
    assert response.json()["detail"] == "evaluation_disabled"
    catalog = client().get("/mobile/glasses/speech-eval/models").json()
    assert not catalog["enabled"]
    assert {model["id"] for model in catalog["models"]} == {
        "large-v3-turbo",
        "parakeet-tdt-0.6b-v3",
    }
    assert "cpuThreadOptions" not in catalog


@pytest.mark.parametrize("path", ["transcribe", "speech-eval/transcribe"])
def test_upload_bound_before_any_provider_call(monkeypatch, path):
    provider = MagicMock()
    monkeypatch.setattr(speech, "request", provider)
    response = client().post("/mobile/glasses/" + path, content=b"x" * (speech.MAX_AUDIO_BYTES + 1))
    assert response.status_code == 413
    provider.assert_not_called()


def test_retired_engines_rejected_not_silently_replaced(monkeypatch):
    monkeypatch.setenv("GLASSES_SPEECH_EVAL_ENABLED", "true")
    assert (
        client().post("/mobile/glasses/speech-eval/prepare?provider=cpu&model=small.en").status_code
        == 422
    )
    assert client().post("/mobile/glasses/speech-eval/prepare?model=small.en").status_code == 400
    assert client().post("/mobile/glasses/speech-eval/prepare?threads=4").status_code == 400
