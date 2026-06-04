from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse

from auth import get_current_user
from capture_relay import CaptureRelayManager, validate_relay_token
from observability.logger import get_runtime_logger
from schemas import CaptureStreamOut, CaptureStreamRequest

logger = get_runtime_logger(__name__)


def create_capture_router() -> APIRouter:
    router = APIRouter()

    @router.post("/api/capture/streams", response_model=CaptureStreamOut, status_code=201)
    async def create_stream_session(
        body: CaptureStreamRequest,
        request: Request,
        user: dict = Depends(get_current_user),
    ):
        relay: CaptureRelayManager = request.app.state.capture_relay
        mqtt = request.app.state.mqtt

        try:
            session = await relay.create_or_reuse_session(
                mqtt=mqtt,
                robot_id=body.robot_id,
                module_id=body.module_id,
                requested_by_user_id=str(user.get("email") or body.requested_by_user_id or ""),
                video=body.video,
                audio=body.audio,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        return relay.serialize_session(session)

    @router.get("/api/capture/streams/{session_id}/status", response_model=CaptureStreamOut)
    async def get_stream_status(
        session_id: str,
        request: Request,
        _: dict = Depends(get_current_user),
    ):
        relay: CaptureRelayManager = request.app.state.capture_relay
        session = await relay.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Relay session not found")
        return relay.serialize_session(session)

    @router.delete("/api/capture/streams/{session_id}", status_code=204)
    async def stop_stream_session(
        session_id: str,
        request: Request,
        _: dict = Depends(get_current_user),
    ):
        relay: CaptureRelayManager = request.app.state.capture_relay
        await relay.close_session(session_id, request.app.state.mqtt, reason="manual_stop")

    @router.get("/api/capture/streams/{session_id}/camera.mjpg")
    async def get_camera_stream(
        session_id: str,
        request: Request,
        _: dict = Depends(get_current_user),
    ):
        relay: CaptureRelayManager = request.app.state.capture_relay
        mqtt = request.app.state.mqtt
        session, viewer = await relay.register_video_viewer(session_id)
        if not session.video_enabled:
            await relay.unregister_viewer(session_id, viewer, mqtt)
            raise HTTPException(status_code=400, detail="Video is not enabled for this session")

        async def frame_stream():
            try:
                while True:
                    try:
                        frame = await asyncio.wait_for(viewer.queue.get(), timeout=15)
                    except asyncio.TimeoutError:
                        if await relay.get_session(session_id) is None:
                            return
                        continue
                    headers = (
                        b"--frame\r\n"
                        b"Content-Type: image/jpeg\r\n"
                        + f"Content-Length: {len(frame)}\r\n\r\n".encode("ascii")
                    )
                    yield headers + frame + b"\r\n"
            except asyncio.CancelledError:
                return
            finally:
                await relay.unregister_viewer(session_id, viewer, mqtt)

        return StreamingResponse(
            frame_stream(),
            media_type="multipart/x-mixed-replace; boundary=frame",
            headers={"Cache-Control": "no-store"},
        )

    @router.websocket("/api/capture/streams/{session_id}/audio.pcm")
    async def stream_audio_pcm(websocket: WebSocket, session_id: str):
        viewer_token = websocket.query_params.get("token", "")
        logger.info(
            "[capture_relay] Audio viewer upgrade session=%s client=%s has_token=%s header_keys=%s",
            session_id,
            websocket.client,
            "yes" if viewer_token else "no",
            sorted(websocket.headers.keys()),
        )
        if viewer_token:
            try:
                claims = validate_relay_token(viewer_token)
            except HTTPException as exc:
                logger.warning(
                    "[capture_relay] Audio viewer token rejected session=%s status=%s detail=%s",
                    session_id,
                    exc.status_code,
                    exc.detail,
                )
                await websocket.close(code=4401)
                return
            if (
                claims.get("session_id") != session_id
                or claims.get("tracks") != ["audio"]
            ):
                logger.warning(
                    "[capture_relay] Audio viewer token scope rejected session=%s claims_session=%s tracks=%s",
                    session_id,
                    claims.get("session_id"),
                    claims.get("tracks"),
                )
                await websocket.close(code=4403)
                return
        else:
            logger.warning("[capture_relay] Audio viewer rejected session=%s reason=missing_token", session_id)
            await websocket.close(code=4401)
            return

        relay: CaptureRelayManager = websocket.app.state.capture_relay
        mqtt = websocket.app.state.mqtt
        session, viewer, backlog = await relay.register_audio_viewer(session_id)
        if not session.audio_enabled:
            await relay.unregister_viewer(session_id, viewer, mqtt)
            logger.warning("[capture_relay] Audio viewer rejected session=%s reason=audio_disabled", session_id)
            await websocket.close(code=4400, reason="Audio disabled")
            return

        await websocket.accept()
        logger.info(
            "[capture_relay] Audio viewer accepted session=%s viewer=%s backlog=%d",
            session_id,
            viewer.viewer_id,
            len(backlog),
        )
        chunks_sent = 0
        bytes_sent = 0
        try:
            for chunk in backlog:
                await websocket.send_bytes(chunk)
                chunks_sent += 1
                bytes_sent += len(chunk)
            while True:
                chunk = await viewer.queue.get()
                await websocket.send_bytes(chunk)
                chunks_sent += 1
                bytes_sent += len(chunk)
                if chunks_sent == 1 or chunks_sent % 100 == 0:
                    logger.info(
                        "[capture_relay] Audio viewer send session=%s viewer=%s chunks=%d bytes=%d last_bytes=%d",
                        session_id,
                        viewer.viewer_id,
                        chunks_sent,
                        bytes_sent,
                        len(chunk),
                    )
        except WebSocketDisconnect:
            logger.info(
                "[capture_relay] Audio viewer disconnected session=%s viewer=%s chunks=%d bytes=%d",
                session_id,
                viewer.viewer_id,
                chunks_sent,
                bytes_sent,
            )
        except Exception as exc:
            logger.warning(
                "[capture_relay] Audio viewer send failed session=%s viewer=%s chunks=%d bytes=%d error=%s",
                session_id,
                viewer.viewer_id,
                chunks_sent,
                bytes_sent,
                exc,
            )
        finally:
            await relay.unregister_viewer(session_id, viewer, mqtt)

    @router.websocket("/api/capture/relay/connect")
    async def connect_capture_relay(websocket: WebSocket):
        auth_header = websocket.headers.get("authorization", "")
        logger.info(
            "[capture_relay] upgrade client=%s auth=%s sec-proto=%s header_keys=%s",
            websocket.client,
            (auth_header[:24] + "...") if auth_header else "<missing>",
            websocket.headers.get("sec-websocket-protocol", "<missing>"),
            sorted(websocket.headers.keys()),
        )
        if not auth_header.startswith("Bearer "):
            logger.warning("[capture_relay] rejecting upgrade: missing/invalid Authorization header")
            await websocket.close(code=4401)
            return

        token = auth_header[len("Bearer ") :].strip()
        try:
            claims = validate_relay_token(token)
            relay: CaptureRelayManager = websocket.app.state.capture_relay
            session = await relay.attach_upstream(claims)
        except HTTPException as exc:
            logger.warning(
                "[capture_relay] rejecting upgrade: status=%s detail=%s",
                exc.status_code,
                exc.detail,
            )
            await websocket.close(code=4403, reason=exc.detail)
            return

        await websocket.accept()
        await relay.mark_upstream_task(session.session_id, asyncio.current_task())
        mqtt = websocket.app.state.mqtt

        try:
            while True:
                message = await websocket.receive()
                if "text" in message and message["text"] is not None:
                    await relay.handle_upstream_json(session.session_id, json.loads(message["text"]))
                elif "bytes" in message and message["bytes"] is not None:
                    await relay.handle_upstream_binary(session.session_id, message["bytes"])
                elif message.get("type") == "websocket.disconnect":
                    break
        except (WebSocketDisconnect, asyncio.CancelledError):
            pass
        except json.JSONDecodeError:
            logger.warning("[capture_relay] Invalid upstream JSON session=%s", session.session_id)
        finally:
            await relay.mark_upstream_task(session.session_id, None)
            await relay.detach_upstream(session.session_id, reason="upstream_disconnected")
            await relay.close_session(session.session_id, mqtt, reason="upstream_disconnected")

    return router
