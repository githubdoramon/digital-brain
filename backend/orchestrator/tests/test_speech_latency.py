"""Local HTTP evidence for public-key caching and GPU connection reuse; no external services."""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from google.auth import crypt, jwt

import speech_transcription as speech
from google_token_transport import GoogleCertificateRequest
from tests.test_glasses_speech_eval import client, wav


@pytest.fixture
def local_http():
    state = {
        "gets": 0,
        "ports": [],
        "cookies": [],
        "authorizations": [],
        "cache": "max-age=3600",
        "certs": {},
    }

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def reply(self, body):
            data = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", state["cache"])
            self.send_header("Set-Cookie", "provider-state=must-not-be-forwarded")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            state["gets"] += 1
            self.reply(state["certs"])

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            state["ports"].append(self.client_address[1])
            state["cookies"].append(self.headers.get("Cookie"))
            state["authorizations"].append(self.headers.get("Authorization"))
            self.reply({"text": "Test transcription"})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", state
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def test_public_keys_cached_but_every_token_still_verified(monkeypatch, local_http):
    import auth

    base, state = local_http
    transport = GoogleCertificateRequest()
    monkeypatch.setattr(
        auth, "certificate_request", lambda url, **kwargs: transport(base + "/certs", **kwargs)
    )
    monkeypatch.setattr(auth, "GOOGLE_CLIENT_IDS", ["test-audience"])
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    private = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    state["certs"] = {"test-key": public.decode()}
    now = int(time.time())
    claims = {
        "iss": "https://accounts.google.com",
        "sub": "synthetic",
        "aud": "test-audience",
        "email": "test@example.invalid",
        "iat": now,
        "exp": now + 3600,
    }
    signer = crypt.RSASigner.from_string(private, key_id="test-key")

    def token(**updates):
        return jwt.encode(signer, claims | updates).decode()

    assert auth.verify_google_token(token())["sub"] == "synthetic"
    assert auth.verify_google_token(token())["sub"] == "synthetic"
    assert state["gets"] == 1
    for invalid in [
        token(aud="wrong-audience"),
        token(iat=now - 7200, exp=now - 3600),
        token().rsplit(".", 1)[0] + ".invalid",
    ]:
        with pytest.raises(HTTPException) as error:
            auth.verify_google_token(invalid)
        assert error.value.status_code == 401
    assert state["gets"] == 1


def test_certificate_cache_honors_no_store(local_http):
    base, state = local_http
    state["cache"] = "no-store"
    transport = GoogleCertificateRequest()
    transport(base + "/certs")
    transport(base + "/certs")
    assert state["gets"] == 2


def test_speech_reuses_connection_without_cookies_or_stale_credentials(monkeypatch, local_http):
    base, state = local_http
    monkeypatch.setenv("SPEECH_BASE_URL", base)
    monkeypatch.setenv("SPEECH_API_KEY", "test-key-a")
    speech.transcribe(wav())
    monkeypatch.setenv("SPEECH_API_KEY", "test-key-b")
    speech.transcribe(wav())
    assert len(state["ports"]) == 2
    assert len(set(state["ports"])) == 1
    assert state["cookies"] == [None, None]
    assert state["authorizations"] == ["Bearer test-key-a", "Bearer test-key-b"]


def test_speech_route_exports_safe_timings_and_prevents_response_caching(monkeypatch):
    monkeypatch.setattr(speech, "request", lambda *args, **kwargs: {"text": "Test transcription"})
    response = client().post("/mobile/glasses/transcribe", content=wav())
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert "speech_backend;dur=" in response.headers["server-timing"]
    assert "speech_upload;dur=" in response.headers["server-timing"]
    assert "Test transcription" not in response.headers["server-timing"]


def test_certificate_cache_does_not_extend_expired_entries(local_http):
    base, state = local_http
    state["cache"] = "max-age=0"
    transport = GoogleCertificateRequest()
    transport(base + "/certs")
    transport(base + "/certs")
    assert state["gets"] == 2


@pytest.mark.asyncio
async def test_auth_keeps_allowlist_checks_and_moves_verification_off_event_loop(monkeypatch):
    from starlette.requests import Request

    import auth

    monkeypatch.setattr(auth, "DEV_BYPASS_AUTH", False)
    monkeypatch.setattr(auth, "ALLOWED_USERS", {"test@example.invalid"})
    calls = []
    main_thread = threading.get_ident()

    def verify(token):
        calls.append(threading.get_ident())
        return {"email": "test@example.invalid" if token == "allowed" else "denied@example.invalid"}

    monkeypatch.setattr(auth, "verify_google_token", verify)
    request = Request({"type": "http", "headers": []})
    assert (await auth.get_current_user("Bearer allowed", request))[
        "email"
    ] == "test@example.invalid"
    assert request.state.auth_duration_ms >= 0
    assert calls[0] != main_thread
    with pytest.raises(HTTPException) as error:
        await auth.get_current_user("Bearer denied", request)
    assert error.value.status_code == 403
    with pytest.raises(HTTPException):
        await auth.get_current_user(None, request)
    assert len(calls) == 2
