from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import secrets
from collections import deque
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException, status

from commands import create_command, mark_command_failed, mark_command_sent
from config import (
    CAPTURE_RELAY_AUDIO_BUFFER_CHUNKS,
    CAPTURE_RELAY_HEARTBEAT_TIMEOUT_SECONDS,
    CAPTURE_RELAY_IDLE_TIMEOUT_SECONDS,
    CAPTURE_RELAY_PUBLIC_BASE_URL,
    CAPTURE_RELAY_SESSION_TTL_SECONDS,
    CAPTURE_RELAY_TOKEN_SECRET,
    CAPTURE_RELAY_TOKEN_TTL_SECONDS,
    CAPTURE_RELAY_VIEWER_GRACE_SECONDS,
)
from observability.logger import get_runtime_logger

logger = get_runtime_logger(__name__)

VIDEO_TRACK = 0x01
AUDIO_TRACK = 0x02


def utc_now() -> datetime:
    return datetime.now(UTC)


def _encode_token_part(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode_token_part(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(f"{value}{padding}")


def _build_public_ws_url() -> str:
    base_url = CAPTURE_RELAY_PUBLIC_BASE_URL.rstrip("/")
    if base_url.startswith("https://"):
        base_url = f"wss://{base_url[len('https://') :]}"
    elif base_url.startswith("http://"):
        base_url = f"ws://{base_url[len('http://') :]}"
    elif not base_url.startswith(("ws://", "wss://")):
        base_url = f"wss://{base_url}"
    return f"{base_url}/api/capture/relay/connect"


def mint_relay_token(
    *,
    session_id: str,
    robot_id: str,
    module_id: str,
    tracks: list[str],
    expires_at: datetime,
) -> str:
    if not CAPTURE_RELAY_TOKEN_SECRET:
        raise RuntimeError("CAPTURE_RELAY_TOKEN_SECRET is not configured")

    payload = {
        "session_id": session_id,
        "robot_id": robot_id,
        "module_id": module_id,
        "tracks": sorted(set(tracks)),
        "exp": int(expires_at.timestamp()),
    }
    payload_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    signature = hmac.new(
        CAPTURE_RELAY_TOKEN_SECRET.encode("utf-8"),
        payload_bytes,
        hashlib.sha256,
    ).digest()
    return f"{_encode_token_part(payload_bytes)}.{_encode_token_part(signature)}"


def mint_viewer_token(
    *,
    session_id: str,
    robot_id: str,
    module_id: str,
    track: str,
    expires_at: datetime,
) -> str:
    return mint_relay_token(
        session_id=session_id,
        robot_id=robot_id,
        module_id=module_id,
        tracks=[track],
        expires_at=expires_at,
    )


def validate_relay_token(token: str) -> dict[str, Any]:
    if not CAPTURE_RELAY_TOKEN_SECRET:
        raise HTTPException(status_code=500, detail="Relay token secret is not configured")

    try:
        payload_part, signature_part = token.split(".", 1)
        payload_bytes = _decode_token_part(payload_part)
        signature = _decode_token_part(signature_part)
    except Exception as exc:  # pragma: no cover - malformed input guard
        raise HTTPException(status_code=401, detail="Invalid relay token") from exc

    expected_signature = hmac.new(
        CAPTURE_RELAY_TOKEN_SECRET.encode("utf-8"),
        payload_bytes,
        hashlib.sha256,
    ).digest()
    if not hmac.compare_digest(signature, expected_signature):
        raise HTTPException(status_code=401, detail="Invalid relay token")

    try:
        payload = json.loads(payload_bytes.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=401, detail="Invalid relay token") from exc

    exp = int(payload.get("exp") or 0)
    if exp <= int(utc_now().timestamp()):
        raise HTTPException(status_code=401, detail="Relay token expired")

    return payload


@dataclass(slots=True)
class RelayViewer:
    viewer_id: str
    kind: str
    queue: asyncio.Queue[bytes]


@dataclass(slots=True)
class RelaySession:
    session_id: str
    robot_id: str
    module_id: str
    created_at: datetime
    expires_at: datetime
    requested_by_user_id: str | None
    video_enabled: bool
    audio_enabled: bool
    last_error: str | None = None
    upstream_connected: bool = False
    upstream_hello: dict[str, Any] | None = None
    upstream_task: asyncio.Task[None] | None = None
    video_meta: dict[str, Any] | None = None
    audio_meta: dict[str, Any] | None = None
    last_video_frame_at: datetime | None = None
    last_audio_chunk_at: datetime | None = None
    last_upstream_activity_at: datetime | None = None
    latest_video_frame: bytes | None = None
    audio_buffer: deque[bytes] = field(
        default_factory=lambda: deque(maxlen=CAPTURE_RELAY_AUDIO_BUFFER_CHUNKS)
    )
    video_viewers: dict[str, RelayViewer] = field(default_factory=dict)
    audio_viewers: dict[str, RelayViewer] = field(default_factory=dict)

    @property
    def viewer_count(self) -> int:
        return len(self.video_viewers) + len(self.audio_viewers)

    @property
    def status(self) -> str:
        if self.upstream_connected:
            return "live"
        if self.last_error:
            return "error"
        return "waiting_for_upstream"


class CaptureRelayManager:
    def __init__(self) -> None:
        self._sessions: dict[str, RelaySession] = {}
        self._module_sessions: dict[tuple[str, str], str] = {}
        self._lock = asyncio.Lock()
        self._cleanup_task: asyncio.Task[None] | None = None
        self._mqtt = None

    async def start(self, mqtt) -> None:
        self._mqtt = mqtt
        if self._cleanup_task is None:
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())

    async def stop(self, mqtt) -> None:
        cleanup_task = self._cleanup_task
        self._cleanup_task = None
        if cleanup_task is not None:
            cleanup_task.cancel()
            with suppress(asyncio.CancelledError):
                await cleanup_task

        async with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
            self._module_sessions.clear()

        for session in sessions:
            await self._shutdown_session(session, mqtt, reason="gateway_shutdown")

    async def create_or_reuse_session(
        self,
        *,
        mqtt,
        robot_id: str,
        module_id: str,
        requested_by_user_id: str | None,
        video: bool,
        audio: bool,
    ) -> RelaySession:
        async with self._lock:
            module_key = (robot_id, module_id)
            existing_id = self._module_sessions.get(module_key)
            existing = self._sessions.get(existing_id) if existing_id else None
            if existing and self._is_session_usable(existing):
                existing.expires_at = utc_now() + timedelta(seconds=CAPTURE_RELAY_SESSION_TTL_SECONDS)
                logger.info(
                    "[capture_relay] Reusing session=%s robot=%s module=%s viewers=%d",
                    existing.session_id,
                    robot_id,
                    module_id,
                    existing.viewer_count,
                )
                return existing

            session = RelaySession(
                session_id=f"sess_{secrets.token_hex(12)}",
                robot_id=robot_id,
                module_id=module_id,
                created_at=utc_now(),
                expires_at=utc_now() + timedelta(seconds=CAPTURE_RELAY_SESSION_TTL_SECONDS),
                requested_by_user_id=requested_by_user_id,
                video_enabled=video,
                audio_enabled=audio,
            )
            self._sessions[session.session_id] = session
            self._module_sessions[module_key] = session.session_id

        try:
            await self._publish_start_stream(session, mqtt)
        except Exception:
            await self._pop_session(session.session_id)
            raise
        return session

    async def get_session(self, session_id: str) -> RelaySession | None:
        async with self._lock:
            return self._sessions.get(session_id)

    async def close_session(self, session_id: str, mqtt, *, reason: str) -> None:
        session = await self._pop_session(session_id)
        if session is None:
            return
        await self._shutdown_session(session, mqtt, reason=reason)

    async def attach_upstream(self, claims: dict[str, Any]) -> RelaySession:
        session_id = str(claims.get("session_id") or "")
        robot_id = str(claims.get("robot_id") or "")
        module_id = str(claims.get("module_id") or "")

        async with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                raise HTTPException(status_code=404, detail="Relay session not found")
            if session.robot_id != robot_id or session.module_id != module_id:
                raise HTTPException(status_code=403, detail="Relay token does not match session")
            if session.upstream_connected:
                raise HTTPException(status_code=409, detail="Relay upstream already connected")

            session.upstream_connected = True
            session.last_error = None
            session.last_upstream_activity_at = utc_now()

        logger.info(
            "[capture_relay] Upstream connected session=%s robot=%s module=%s",
            session.session_id,
            session.robot_id,
            session.module_id,
        )
        return session

    async def detach_upstream(self, session_id: str, *, reason: str) -> None:
        async with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return
            session.upstream_connected = False
            session.last_error = reason
            session.last_upstream_activity_at = utc_now()
        logger.info("[capture_relay] Upstream detached session=%s reason=%s", session_id, reason)

    async def handle_upstream_json(self, session_id: str, message: dict[str, Any]) -> None:
        async with self._lock:
            session = self._require_session_locked(session_id)
            session.last_upstream_activity_at = utc_now()
            message_type = str(message.get("type") or "")
            if message_type == "hello":
                session.upstream_hello = message
            elif message_type == "video_meta":
                session.video_meta = message
            elif message_type == "audio_meta":
                session.audio_meta = message
            elif message_type == "goodbye":
                session.last_error = str(message.get("reason") or "upstream_goodbye")

    async def handle_upstream_binary(self, session_id: str, payload: bytes) -> None:
        if not payload:
            return

        track_type = payload[0]
        chunk = payload[1:]
        if not chunk:
            return

        async with self._lock:
            session = self._require_session_locked(session_id)
            session.last_upstream_activity_at = utc_now()

            if track_type == VIDEO_TRACK:
                session.latest_video_frame = chunk
                session.last_video_frame_at = utc_now()
                viewers = list(session.video_viewers.values())
            elif track_type == AUDIO_TRACK:
                session.audio_buffer.append(chunk)
                session.last_audio_chunk_at = utc_now()
                viewers = list(session.audio_viewers.values())
            else:
                logger.warning("[capture_relay] Unknown track=%s session=%s", track_type, session_id)
                return

        for viewer in viewers:
            self._offer_queue_item(viewer.queue, chunk)

    async def register_video_viewer(self, session_id: str) -> tuple[RelaySession, RelayViewer]:
        async with self._lock:
            session = self._require_session_locked(session_id)
            viewer = RelayViewer(
                viewer_id=f"viewer_{secrets.token_hex(6)}",
                kind="video",
                queue=asyncio.Queue(maxsize=1),
            )
            session.video_viewers[viewer.viewer_id] = viewer
            if session.latest_video_frame is not None:
                self._offer_queue_item(viewer.queue, session.latest_video_frame)
            logger.info(
                "[capture_relay] Video viewer joined session=%s viewers=%d",
                session_id,
                session.viewer_count,
            )
            return session, viewer

    async def register_audio_viewer(self, session_id: str) -> tuple[RelaySession, RelayViewer, list[bytes]]:
        async with self._lock:
            session = self._require_session_locked(session_id)
            viewer = RelayViewer(
                viewer_id=f"viewer_{secrets.token_hex(6)}",
                kind="audio",
                queue=asyncio.Queue(maxsize=8),
            )
            session.audio_viewers[viewer.viewer_id] = viewer
            backlog = list(session.audio_buffer)
            logger.info(
                "[capture_relay] Audio viewer joined session=%s viewers=%d",
                session_id,
                session.viewer_count,
            )
            return session, viewer, backlog

    async def unregister_viewer(self, session_id: str, viewer: RelayViewer, mqtt) -> None:
        schedule_grace_close = False
        async with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return
            if viewer.kind == "video":
                session.video_viewers.pop(viewer.viewer_id, None)
            else:
                session.audio_viewers.pop(viewer.viewer_id, None)

            logger.info(
                "[capture_relay] Viewer left session=%s viewers=%d",
                session_id,
                session.viewer_count,
            )
            schedule_grace_close = session.viewer_count == 0 and session.upstream_connected

        if schedule_grace_close:
            asyncio.create_task(self._close_after_viewer_grace(session_id, mqtt))

    async def _close_after_viewer_grace(self, session_id: str, mqtt) -> None:
        await asyncio.sleep(CAPTURE_RELAY_VIEWER_GRACE_SECONDS)
        async with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return
            if session.viewer_count > 0:
                logger.info(
                    "[capture_relay] Viewer rejoined within grace, keeping session=%s viewers=%d",
                    session_id,
                    session.viewer_count,
                )
                return
            if not session.upstream_connected:
                return
        await self.close_session(session_id, mqtt, reason="last_viewer_left")

    async def mark_upstream_task(self, session_id: str, task: asyncio.Task[None] | None) -> None:
        async with self._lock:
            session = self._sessions.get(session_id)
            if session is not None:
                session.upstream_task = task

    def serialize_session(self, session: RelaySession) -> dict[str, Any]:
        viewer_token_expires_at = min(
            session.expires_at,
            utc_now() + timedelta(seconds=CAPTURE_RELAY_TOKEN_TTL_SECONDS),
        )
        audio_viewer_token = mint_viewer_token(
            session_id=session.session_id,
            robot_id=session.robot_id,
            module_id=session.module_id,
            track="audio",
            expires_at=viewer_token_expires_at,
        )

        return {
            "session_id": session.session_id,
            "robot_id": session.robot_id,
            "module_id": session.module_id,
            "created_at": session.created_at.isoformat(),
            "expires_at": session.expires_at.isoformat(),
            "upstream_connected": session.upstream_connected,
            "viewer_count": session.viewer_count,
            "video_enabled": session.video_enabled,
            "audio_enabled": session.audio_enabled,
            "last_video_frame_at": session.last_video_frame_at.isoformat()
            if session.last_video_frame_at
            else None,
            "last_audio_chunk_at": session.last_audio_chunk_at.isoformat()
            if session.last_audio_chunk_at
            else None,
            "last_upstream_activity_at": session.last_upstream_activity_at.isoformat()
            if session.last_upstream_activity_at
            else None,
            "requested_by_user_id": session.requested_by_user_id,
            "last_error": session.last_error,
            "status": session.status,
            "video_meta": session.video_meta,
            "audio_meta": session.audio_meta,
            "viewer_paths": {
                "status": f"/api/capture/streams/{session.session_id}/status",
                "camera_mjpg": f"/api/capture/streams/{session.session_id}/camera.mjpg",
                "audio_pcm_ws": f"/api/capture/streams/{session.session_id}/audio.pcm",
                "audio_pcm_ws_public": (
                    f"{_build_public_ws_url().removesuffix('/api/capture/relay/connect')}"
                    f"/api/capture/streams/{session.session_id}/audio.pcm"
                    f"?token={audio_viewer_token}"
                ),
            },
        }

    async def _publish_start_stream(self, session: RelaySession, mqtt) -> None:
        command_id = f"cmd_{secrets.token_hex(8)}"
        token_expires_at = utc_now() + timedelta(seconds=CAPTURE_RELAY_TOKEN_TTL_SECONDS)
        relay_token = mint_relay_token(
            session_id=session.session_id,
            robot_id=session.robot_id,
            module_id=session.module_id,
            tracks=self._session_tracks(session),
            expires_at=token_expires_at,
        )
        payload = {
            "command_id": command_id,
            "type": "start_stream",
            "stream_transport": "relay",
            "relay": {
                "session_id": session.session_id,
                "ws_url": _build_public_ws_url(),
                "token": relay_token,
                "expires_at": token_expires_at.isoformat().replace("+00:00", "Z"),
                "video": session.video_enabled,
                "audio": session.audio_enabled,
            },
        }

        try:
            create_command(
                robot_id=session.robot_id,
                module_id=session.module_id,
                command_type="start_stream",
                payload=payload,
                created_by=session.requested_by_user_id,
            )
            await mqtt.publish_command(session.robot_id, session.module_id, payload)
            mark_command_sent(command_id)
        except ValueError:
            raise
        except Exception as exc:
            session.last_error = str(exc)
            mark_command_failed(command_id, str(exc))
            raise HTTPException(status_code=502, detail=f"Failed to publish start_stream: {exc}") from exc

        logger.info(
            "[capture_relay] Started session=%s robot=%s module=%s",
            session.session_id,
            session.robot_id,
            session.module_id,
        )

    async def _publish_stop_stream(self, session: RelaySession, mqtt, *, reason: str) -> None:
        command_id = f"cmd_{secrets.token_hex(8)}"
        payload = {
            "command_id": command_id,
            "type": "stop_stream",
            "reason": reason,
        }

        try:
            create_command(
                robot_id=session.robot_id,
                module_id=session.module_id,
                command_type="stop_stream",
                payload=payload,
                created_by=session.requested_by_user_id,
            )
            await mqtt.publish_command(session.robot_id, session.module_id, payload)
            mark_command_sent(command_id)
        except Exception as exc:
            mark_command_failed(command_id, str(exc))
            logger.warning(
                "[capture_relay] Failed to publish stop_stream session=%s error=%s",
                session.session_id,
                exc,
            )

    async def _pop_session(self, session_id: str) -> RelaySession | None:
        async with self._lock:
            session = self._sessions.pop(session_id, None)
            if session is not None:
                self._module_sessions.pop((session.robot_id, session.module_id), None)
            return session

    async def _shutdown_session(self, session: RelaySession, mqtt, *, reason: str) -> None:
        current_task = asyncio.current_task()
        if session.upstream_task is not None and session.upstream_task is not current_task:
            session.upstream_task.cancel()
        await self._publish_stop_stream(session, mqtt, reason=reason)
        logger.info(
            "[capture_relay] Closed session=%s robot=%s module=%s reason=%s",
            session.session_id,
            session.robot_id,
            session.module_id,
            reason,
        )

    async def _cleanup_loop(self) -> None:
        while True:
            await asyncio.sleep(5)
            expired: list[str] = []
            now = utc_now()
            async with self._lock:
                for session in self._sessions.values():
                    idle_for = (
                        (now - session.last_upstream_activity_at).total_seconds()
                        if session.last_upstream_activity_at
                        else None
                    )
                    should_expire = session.expires_at <= now or (
                        session.viewer_count == 0
                        and (now - session.created_at).total_seconds()
                        >= CAPTURE_RELAY_IDLE_TIMEOUT_SECONDS
                    ) or (
                        session.upstream_connected
                        and idle_for is not None
                        and idle_for >= CAPTURE_RELAY_HEARTBEAT_TIMEOUT_SECONDS
                    )
                    if should_expire:
                        expired.append(session.session_id)

            for session_id in expired:
                if self._mqtt is None:
                    continue
                await self.close_session(session_id, self._mqtt, reason="timeout")

    def _session_tracks(self, session: RelaySession) -> list[str]:
        tracks: list[str] = []
        if session.video_enabled:
            tracks.append("video")
        if session.audio_enabled:
            tracks.append("audio")
        return tracks

    def _is_session_usable(self, session: RelaySession) -> bool:
        return session.expires_at > utc_now()

    def _require_session_locked(self, session_id: str) -> RelaySession:
        session = self._sessions.get(session_id)
        if session is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Relay session not found")
        return session

    @staticmethod
    def _offer_queue_item(queue: asyncio.Queue[bytes], payload: bytes) -> None:
        if queue.full():
            with suppress(asyncio.QueueEmpty):
                queue.get_nowait()
        with suppress(asyncio.QueueFull):
            queue.put_nowait(payload)
