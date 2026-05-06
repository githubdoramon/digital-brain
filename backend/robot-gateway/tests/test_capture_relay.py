from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException

import capture_relay


def test_build_public_ws_url_upgrades_https(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(capture_relay, "CAPTURE_RELAY_PUBLIC_BASE_URL", "https://brain.example.com")
    assert capture_relay._build_public_ws_url() == "wss://brain.example.com/api/capture/relay/connect"


def test_mint_and_validate_relay_token(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(capture_relay, "CAPTURE_RELAY_TOKEN_SECRET", "test-secret")
    expires_at = datetime.now(UTC) + timedelta(minutes=5)

    token = capture_relay.mint_relay_token(
        session_id="sess_123",
        robot_id="robot-1",
        module_id="head-capture",
        tracks=["audio", "video"],
        expires_at=expires_at,
    )

    claims = capture_relay.validate_relay_token(token)

    assert claims["session_id"] == "sess_123"
    assert claims["robot_id"] == "robot-1"
    assert claims["module_id"] == "head-capture"
    assert claims["tracks"] == ["audio", "video"]


def test_validate_relay_token_rejects_expired(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(capture_relay, "CAPTURE_RELAY_TOKEN_SECRET", "test-secret")
    expires_at = datetime.now(UTC) - timedelta(seconds=1)

    token = capture_relay.mint_relay_token(
        session_id="sess_123",
        robot_id="robot-1",
        module_id="head-capture",
        tracks=["video"],
        expires_at=expires_at,
    )

    with pytest.raises(HTTPException) as exc_info:
        capture_relay.validate_relay_token(token)

    assert exc_info.value.status_code == 401
    assert "expired" in str(exc_info.value.detail).lower()


def test_mint_viewer_token_scopes_audio_track(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(capture_relay, "CAPTURE_RELAY_TOKEN_SECRET", "test-secret")
    expires_at = datetime.now(UTC) + timedelta(minutes=5)

    token = capture_relay.mint_viewer_token(
        session_id="sess_audio",
        robot_id="robot-1",
        module_id="head-capture",
        track="audio",
        expires_at=expires_at,
    )

    claims = capture_relay.validate_relay_token(token)

    assert claims["session_id"] == "sess_audio"
    assert claims["tracks"] == ["audio"]


def test_offer_queue_item_drops_stale_frame():
    queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=1)
    capture_relay.CaptureRelayManager._offer_queue_item(queue, b"first")
    capture_relay.CaptureRelayManager._offer_queue_item(queue, b"second")

    assert queue.qsize() == 1
    assert queue.get_nowait() == b"second"
