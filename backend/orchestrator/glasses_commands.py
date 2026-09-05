"""Backend implementation of the authenticated smart-glasses command API."""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from psycopg.types.json import Json

from db import get_conn
from glasses_audio import put_audio
from glasses_tts import TTSUnavailableError, synthesize_kokoro
from observability.logger import get_runtime_logger
from voice_response import (
    ResponseModality,
    prepare_voice_answer_sync,
)

logger = get_runtime_logger(__name__)

_command_timing: ContextVar[dict[str, float] | None] = ContextVar(
    "glasses_command_timing", default=None
)


def _record_timing(name: str, elapsed_ms: float) -> None:
    timings = _command_timing.get()
    if timings is not None:
        timings[name] = round(max(0.0, elapsed_ms), 1)


@contextmanager
def _timed_phase(name: str) -> Iterator[None]:
    started_at = time.perf_counter()
    try:
        yield
    finally:
        _record_timing(name, (time.perf_counter() - started_at) * 1_000)


def _client_timing_payload(payload: Any) -> dict[str, Any] | None:
    value = getattr(payload, "client_timings", None)
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        value = value.model_dump(exclude_none=True)
    if not isinstance(value, dict):
        return None
    return value


class GlassesCommandError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


def normalize_transcript(transcript: str) -> str:
    """Normalize only whitespace/case and terminal punctuation for shortcuts."""
    import re

    value = re.sub(r"\s+", " ", str(transcript or "").strip().lower())
    return re.sub(r"[.!?,;:]+$", "", value).strip()


def shortcut_for_transcript(transcript: str) -> str | None:
    return {
        "slash new": "new",
        "front gate": "front_gate",
        "car gate": "car_gate",
    }.get(normalize_transcript(transcript))


def _gate_config(kind: str) -> tuple[str, dict[str, Any]]:
    if kind == "front_gate":
        tool_name = os.getenv("GLASSES_FRONT_GATE_TOOL", "intent__HassTurnOn").strip()
        target_name = os.getenv("GLASSES_FRONT_GATE_NAME", "House gate automation").strip()
    else:
        tool_name = os.getenv("GLASSES_CAR_GATE_TOOL", "intent__HassTurnOn").strip()
        target_name = os.getenv("GLASSES_CAR_GATE_NAME", "Garage gate automation").strip()

    # HassTurnOn is a generic Assist intent and requires a target selector.
    # Keep the target fixed in controller configuration; an operator can still
    # override the tool with a dedicated script tool, which takes empty args.
    arguments = {"name": target_name} if tool_name == "intent__HassTurnOn" else {}
    return tool_name, arguments


def _safe_log_payload(value: Any, *, limit: int = 2_000) -> str:
    """Serialize bounded diagnostics while redacting credential-shaped fields."""

    sensitive_fragments = ("authorization", "password", "secret", "token")

    def sanitize(item: Any) -> Any:
        if isinstance(item, dict):
            return {
                str(key): (
                    "[REDACTED]"
                    if any(fragment in str(key).lower() for fragment in sensitive_fragments)
                    else sanitize(child)
                )
                for key, child in item.items()
            }
        if isinstance(item, (list, tuple)):
            return [sanitize(child) for child in item]
        return item

    try:
        rendered = json.dumps(sanitize(value), default=str, ensure_ascii=False, sort_keys=True)
    except Exception:
        rendered = repr(value)
    return rendered if len(rendered) <= limit else f"{rendered[:limit]}...[truncated]"


async def execute_gate(kind: str, *, command_id: str | None = None) -> dict[str, Any]:
    """Invoke the configured fixed HA operation without model/tool discovery."""
    from mcp.servers.home_assistant import call_ha_tool_async, is_ha_configured

    tool_name, arguments = _gate_config(kind)
    started_at = time.monotonic()
    logger.info(
        "[glasses] gate shortcut dispatch command_id=%s control=%s tool=%s arguments=%s",
        command_id or "unavailable",
        kind,
        tool_name,
        arguments,
    )
    if not tool_name:
        logger.error(
            "[glasses] gate shortcut configuration invalid command_id=%s control=%s "
            "tool=%r arguments=%s",
            command_id or "unavailable",
            kind,
            tool_name,
            arguments,
        )
        raise GlassesCommandError("shortcut_unavailable", "That gate shortcut is not configured.")
    if not is_ha_configured():
        logger.error(
            "[glasses] gate shortcut unavailable command_id=%s control=%s reason=ha_not_configured",
            command_id or "unavailable",
            kind,
        )
        raise GlassesCommandError("ha_unavailable", "Home Assistant is not configured.", retryable=True)
    try:
        result = await call_ha_tool_async(tool_name, arguments)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning(
            "[glasses] gate shortcut transport failure command_id=%s control=%s "
            "tool=%s arguments=%s elapsed_ms=%d exception_type=%s error=%s",
            command_id or "unavailable",
            kind,
            tool_name,
            arguments,
            round((time.monotonic() - started_at) * 1_000),
            type(exc).__name__,
            exc,
            exc_info=exc,
        )
        raise GlassesCommandError(
            "ha_execution_failed", "The gate could not be operated.", retryable=True
        ) from exc
    if not result.get("success"):
        logger.warning(
            "[glasses] gate shortcut rejected command_id=%s control=%s tool=%s arguments=%s "
            "elapsed_ms=%d error=%r ha_response=%s",
            command_id or "unavailable",
            kind,
            tool_name,
            arguments,
            round((time.monotonic() - started_at) * 1_000),
            result.get("error"),
            _safe_log_payload(result),
        )
        raise GlassesCommandError(
            "ha_execution_failed",
            "The gate could not be operated.",
            retryable=False,
        )
    logger.info(
        "[glasses] gate shortcut completed command_id=%s control=%s tool=%s arguments=%s "
        "elapsed_ms=%d",
        command_id or "unavailable",
        kind,
        tool_name,
        arguments,
        round((time.monotonic() - started_at) * 1_000),
    )
    return {"tool_name": tool_name, "arguments": arguments}


def claim_command(user_email: str, command_id: str, transcript: str) -> dict[str, Any]:
    """Atomically claim an idempotency key before any side effect."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO glasses_command_executions (user_email, command_id, transcript, status)
            VALUES (%s, %s, %s, 'processing')
            ON CONFLICT (user_email, command_id) DO NOTHING
            RETURNING user_email, command_id, status, outcome, response, error
            """,
            (user_email, command_id, transcript),
        )
        row = cur.fetchone()
        if row:
            conn.commit()
            return {"claimed": True, **dict(row)}
        cur.execute(
            """
            SELECT user_email, command_id, transcript, status, outcome, response, error
            FROM glasses_command_executions
            WHERE user_email = %s AND command_id = %s
            """,
            (user_email, command_id),
        )
        row = cur.fetchone()
        conn.commit()
        if not row:
            raise GlassesCommandError(
                "idempotency_unavailable", "Command state could not be read.", retryable=True
            )
        result = dict(row)
        if result.get("transcript") != transcript:
            raise GlassesCommandError(
                "command_id_reused", "command_id was already used for another transcript."
            )
        result["claimed"] = False
        return result


def finish_command(
    user_email: str,
    command_id: str,
    *,
    status: str,
    outcome: str | None,
    response: dict[str, Any] | None = None,
    error: dict[str, Any] | None = None,
) -> None:
    with _timed_phase("idempotency_finish_ms"):
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE glasses_command_executions
                SET status = %s, outcome = %s, response = %s, error = %s, updated_at = NOW()
                WHERE user_email = %s AND command_id = %s AND status = 'processing'
                """,
                (
                    status,
                    outcome,
                    Json(response) if response is not None else None,
                    Json(error) if error else None,
                    user_email,
                    command_id,
                ),
            )
            conn.commit()


def _error_response(command_id: str, exc: GlassesCommandError) -> dict[str, Any]:
    return {
        "outcome": "error",
        "command_id": command_id,
        "error": {"code": exc.code, "message": exc.message, "retryable": exc.retryable},
    }


async def process_command(payload: Any, user: dict[str, Any]) -> dict[str, Any]:
    """Process one command and emit a complete correlated latency record."""
    started_at = time.perf_counter()
    timings: dict[str, float] = {}
    token = _command_timing.set(timings)
    response: dict[str, Any] | None = None
    try:
        response = await _process_command(payload, user)
        return response
    finally:
        timings["total_ms"] = round((time.perf_counter() - started_at) * 1_000, 1)
        command_id = str(getattr(payload, "command_id", "unknown"))
        logger.info(
            "[glasses] command latency command_id=%s outcome=%s client_timings=%s "
            "backend_timings=%s",
            command_id,
            response.get("outcome") if response else "exception",
            _safe_log_payload(_client_timing_payload(payload) or {}),
            _safe_log_payload(timings),
        )
        _command_timing.reset(token)


async def _process_command(payload: Any, user: dict[str, Any]) -> dict[str, Any]:
    """Claim, execute, and durably complete one command."""
    user_email = str(user.get("email") or user.get("user_email") or "").strip()
    if not user_email:
        raise GlassesCommandError("auth_user_missing", "Authenticated user email missing.")
    command_id = str(payload.command_id)
    transcript = str(payload.transcript).strip()
    try:
        with _timed_phase("idempotency_claim_ms"):
            claimed = claim_command(user_email, command_id, transcript)
    except GlassesCommandError:
        raise
    except Exception as exc:
        logger.exception("[glasses] idempotency claim failed command_id=%s", command_id)
        raise GlassesCommandError(
            "idempotency_unavailable",
            "Command state could not be claimed.",
            retryable=True,
        ) from exc
    if not claimed.get("claimed"):
        if claimed.get("status") == "processing":
            # A concurrent duplicate waits for the owner to publish its
            # terminal result. If the owner was cancelled, the bounded wait
            # expires without ever starting a second side effect.
            try:
                wait_seconds = max(0.1, float(os.getenv("GLASSES_IDEMPOTENCY_WAIT_SECONDS", "60")))
            except ValueError:
                wait_seconds = 60.0
            deadline = asyncio.get_running_loop().time() + wait_seconds
            while asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(0.05)
                try:
                    latest = claim_command(user_email, command_id, transcript)
                except GlassesCommandError as exc:
                    return _error_response(command_id, exc)
                except Exception:
                    logger.exception(
                        "[glasses] idempotency poll failed command_id=%s", command_id
                    )
                    return _error_response(
                        command_id,
                        GlassesCommandError(
                            "idempotency_unavailable",
                            "Command state could not be read.",
                            retryable=True,
                        ),
                    )
                if latest.get("claimed"):
                    # This should only occur if an operator removed the row;
                    # fail closed rather than risk a second gate operation.
                    return _error_response(
                        command_id,
                        GlassesCommandError("idempotency_unavailable", "Command state changed unexpectedly."),
                    )
                if latest.get("status") != "processing":
                    return latest.get("response") or _error_response(
                        command_id,
                        GlassesCommandError(
                            "command_state_missing",
                            "Command result is unavailable.",
                            retryable=True,
                        ),
                    )
            return {
                "outcome": "error",
                "command_id": command_id,
                "error": {
                    "code": "command_in_progress",
                    "message": "That command is still processing.",
                    "retryable": False,
                },
            }
        return claimed.get("response") or _error_response(
            command_id,
            GlassesCommandError("command_state_missing", "Command result is unavailable.", retryable=True),
        )

    try:
        with _timed_phase("shortcut_resolution_ms"):
            shortcut = shortcut_for_transcript(transcript)
        non_shortcut_deadline = None
        if shortcut not in {"new", "front_gate", "car_gate"}:
            try:
                agent_timeout = max(1.0, float(os.getenv("GLASSES_AGENT_TIMEOUT_SECONDS", "60")))
            except ValueError:
                agent_timeout = 60.0
            non_shortcut_deadline = asyncio.get_running_loop().time() + agent_timeout
        if shortcut == "new":
            from routes.chat import _make_reset_bundle, _resolve_session_context
            from schemas import AskIn

            ask_payload = AskIn(
                question="/new",
                thread_id=payload.thread_id,
                client_context=payload.client_context,
            )
            with _timed_phase("session_context_ms"):
                ctx = _resolve_session_context(ask_payload, user_email, force_new_session=True)
            response = {
                "outcome": "shortcut_completed",
                "command_id": command_id,
                "thread_id": ctx.session_id,
                "session_id": ctx.session_id,
                "answer": _make_reset_bundle(ctx)["answer"],
                "silent": True,
            }
        elif shortcut in {"front_gate", "car_gate"}:
            timeout = max(0.1, float(os.getenv("GLASSES_SHORTCUT_TIMEOUT_SECONDS", "10")))
            async with asyncio.timeout(timeout):
                with _timed_phase("ha_execution_ms"):
                    details = await execute_gate(shortcut, command_id=command_id)
            response = {
                "outcome": "control_completed",
                "command_id": command_id,
                "answer": "Done.",
                "silent": True,
                "control": shortcut,
                **details,
            }
        else:
            # Reuse the existing slash-command/pending-preview machinery. This
            # keeps /event follow-ups on their normal command thread instead of
            # inventing a glasses-only conversation type.
            from routes.chat import (
                _command_response_text,
                _handle_command,
                handle_pending_event,
            )

            # Glasses commands are a trusted voice-only API. The client cannot
            # downgrade the response to text or bypass voice guardrails.
            modality = ResponseModality.VOICE
            transformed_answers: dict[str, str] = {}

            def response_text_transform(text: str) -> str:
                if non_shortcut_deadline is not None and time.monotonic() >= non_shortcut_deadline:
                    raise TimeoutError
                cached = transformed_answers.get(text)
                if cached is not None:
                    return cached
                with _timed_phase("voice_answer_prepare_ms"):
                    transformed = prepare_voice_answer_sync(text)
                if non_shortcut_deadline is not None and time.monotonic() >= non_shortcut_deadline:
                    raise TimeoutError
                transformed_answers[text] = transformed
                return transformed

            def run_existing_command() -> Any:
                command_payload = handle_pending_event(
                    transcript,
                    user_email,
                    user,
                    payload.thread_id,
                    None,
                    client_context=payload.client_context.model_dump(exclude_none=True)
                    if payload.client_context
                    else None,
                    media_attachments=[],
                    user_metadata=None,
                    command_response_text=_command_response_text,
                    command_assistant_metadata=lambda result: ({"command_result": result}, None),
                    progress_callback=None,
                    response_text_transform=response_text_transform,
                )
                if command_payload:
                    return command_payload
                return _handle_command(
                    transcript,
                    user_email,
                    user,
                    payload.thread_id,
                    payload.client_context.model_dump(exclude_none=True)
                    if payload.client_context
                    else None,
                    media_attachments=[],
                    user_metadata=None,
                    progress_callback=None,
                    response_text_transform=response_text_transform,
                )

            remaining = (
                non_shortcut_deadline - asyncio.get_running_loop().time()
                if non_shortcut_deadline is not None
                else 0
            )
            if remaining <= 0:
                raise asyncio.TimeoutError
            try:
                with _timed_phase("agent_execution_ms"):
                    command_payload = await asyncio.wait_for(
                        asyncio.to_thread(run_existing_command), timeout=remaining
                    )
            except asyncio.TimeoutError:
                # `run_existing_command` is legacy synchronous code. Cancelling
                # its worker cannot stop a handler already executing; the outer
                # path records a terminal deadline error and never exposes any
                # late result/audio from that worker.
                logger.warning("[glasses] synchronous command exceeded deadline command_id=%s", command_id)
                raise
            if command_payload:
                command_result, command_thread_id, command_ui_directives = command_payload
                answer = response_text_transform(_command_response_text(command_result))
                response_audio = None
                if modality is ResponseModality.VOICE:
                    try:
                        remaining = (
                            non_shortcut_deadline - asyncio.get_running_loop().time()
                            if non_shortcut_deadline is not None
                            else 0
                        )
                        if remaining <= 0:
                            raise asyncio.TimeoutError
                        with _timed_phase("tts_synthesis_ms"):
                            wav_bytes = await asyncio.wait_for(
                                asyncio.to_thread(synthesize_kokoro, answer), timeout=remaining
                            )
                        if (
                            non_shortcut_deadline is not None
                            and asyncio.get_running_loop().time() >= non_shortcut_deadline
                        ):
                            raise asyncio.TimeoutError
                        with _timed_phase("audio_store_ms"):
                            audio_meta = put_audio(wav_bytes, user_email=user_email)
                    except asyncio.TimeoutError:
                        raise
                    except TTSUnavailableError as exc:
                        raise GlassesCommandError("tts_unavailable", str(exc), retryable=True) from exc
                    except Exception as exc:
                        raise GlassesCommandError(
                            "tts_failed", "Voice playback could not be prepared.", retryable=True
                        ) from exc
                    response_audio = {
                        **audio_meta,
                        "download_url": f"/mobile/glasses/audio/{audio_meta['audio_id']}",
                    }
                response = {
                    "outcome": "agent_response",
                    "command_id": command_id,
                    "thread_id": command_thread_id,
                    "session_id": command_thread_id,
                    "answer": answer,
                    "command_result": command_result,
                    "ui_directives": command_ui_directives,
                }
                if response_audio:
                    response["audio"] = response_audio
                finish_command(
                    user_email,
                    command_id,
                    status="completed",
                    outcome=response["outcome"],
                    response=response,
                )
                return response

            from routes.chat import _resolve_session_context
            from schemas import AskIn

            ask_payload = AskIn(
                question=transcript,
                thread_id=payload.thread_id,
                client_context=payload.client_context,
            )
            remaining = (
                non_shortcut_deadline - asyncio.get_running_loop().time()
                if non_shortcut_deadline is not None
                else 0
            )
            if remaining <= 0:
                raise asyncio.TimeoutError
            with _timed_phase("session_context_ms"):
                ctx = await asyncio.wait_for(
                    asyncio.to_thread(_resolve_session_context, ask_payload, user_email),
                    timeout=remaining,
                )
            if ctx.is_reset_only:
                response = {
                    "outcome": "shortcut_completed",
                    "command_id": command_id,
                    "thread_id": ctx.session_id,
                    "session_id": ctx.session_id,
                    "answer": "New session started. How can I help you?",
                    "silent": True,
                }
            else:
                import llm

                modality = ResponseModality.VOICE
                context = (
                    payload.client_context.model_dump(exclude_none=True)
                    if payload.client_context
                    else None
                )
                remaining = (
                    non_shortcut_deadline - asyncio.get_running_loop().time()
                    if non_shortcut_deadline is not None
                    else 0
                )
                if remaining <= 0:
                    raise asyncio.TimeoutError
                async with asyncio.timeout(remaining):
                    with _timed_phase("agent_execution_ms"):
                        bundle = await llm.answer_question(
                            ctx.question,
                            search_limit=30,
                            user_id=user_email,
                            session_id=ctx.session_id,
                            user_email=user_email,
                            client_context=context,
                            response_modality=modality.value,
                        )
                    answer = str(bundle.get("answer") or "").strip()
                    if modality is ResponseModality.VOICE:
                        # llm.answer_question applies the voice contract before
                        # it records the exchange. Rewriting here would happen
                        # after persistence and could make TTS diverge from the
                        # saved canonical answer.
                        audio_meta = None
                        try:
                            with _timed_phase("tts_synthesis_ms"):
                                wav_bytes = await asyncio.to_thread(synthesize_kokoro, answer)
                            if (
                                non_shortcut_deadline is not None
                                and asyncio.get_running_loop().time() >= non_shortcut_deadline
                            ):
                                raise asyncio.TimeoutError
                            with _timed_phase("audio_store_ms"):
                                audio_meta = put_audio(wav_bytes, user_email=user_email)
                        except TTSUnavailableError as exc:
                            raise GlassesCommandError("tts_unavailable", str(exc), retryable=True) from exc
                        except Exception as exc:
                            logger.warning("[glasses] TTS synthesis failed: %s", exc, exc_info=exc)
                            raise GlassesCommandError(
                                "tts_failed", "Voice playback could not be prepared.", retryable=True
                            ) from exc
                        response_audio = {
                            **audio_meta,
                            "download_url": f"/mobile/glasses/audio/{audio_meta['audio_id']}",
                        }
                    else:
                        response_audio = None
                response = {
                    "outcome": "agent_response",
                    "command_id": command_id,
                    "thread_id": ctx.session_id,
                    "session_id": ctx.session_id,
                    "answer": answer,
                }
                if response_audio:
                    response["audio"] = response_audio
        finish_command(
            user_email,
            command_id,
            status="completed",
            outcome=response["outcome"],
            response=response,
        )
        return response
    except asyncio.TimeoutError:
        error = GlassesCommandError(
            "deadline_exceeded", "The command exceeded its deadline.", retryable=False
        )
        response = _error_response(command_id, error)
        finish_command(
            user_email,
            command_id,
            status="failed",
            outcome="error",
            response=response,
            error=response["error"],
        )
        return response
    except GlassesCommandError as exc:
        response = _error_response(command_id, exc)
        finish_command(
            user_email,
            command_id,
            status="failed",
            outcome="error",
            response=response,
            error=response["error"],
        )
        return response
    except asyncio.CancelledError:
        # The database row intentionally remains processing: the operation may
        # have reached HA, so a caller must not retry under a new execution.
        logger.warning("[glasses] command task cancelled command_id=%s", command_id)
        raise
    except Exception:
        logger.exception("[glasses] command failed command_id=%s", command_id)
        error = GlassesCommandError("command_failed", "The command could not be completed.", retryable=True)
        response = _error_response(command_id, error)
        finish_command(
            user_email,
            command_id,
            status="failed",
            outcome="error",
            response=response,
            error=response["error"],
        )
        return response
