from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from datetime import datetime
from time import perf_counter
from typing import Any, Callable
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response
from fastapi.responses import StreamingResponse

import conversations
import llm
from auth import get_current_user
from commands.event import (
    event_pending_key,
    handle_pending_event,
)
from db import get_conn
from observability.logger import get_runtime_logger
from schemas import (
    AskIn,
    AskOut,
    AskRunStatusOut,
    MainSessionOut,
    ThreadCreate,
    ThreadDetailOut,
    ThreadOut,
    ThreadUpdate,
)
from ui_dsl import command_result_to_ui_directives
from ui_dsl.enums import CommandResultType

logger = get_runtime_logger(__name__)

ASK_RUNS_TTL_SECONDS = 1800
_ask_runs: dict[str, dict[str, Any]] = {}


def _should_log_stream_event(event_type: str) -> bool:
    return event_type in {
        "status",
        "tool_call",
        "tool_result",
        "done",
        "error",
        "clear_content",
    }


def create_chat_router() -> APIRouter:
    router = APIRouter()

    @router.get("/ask/runs/{run_id}", response_model=AskRunStatusOut)
    @router.get("/mobile/ask/runs/{run_id}", response_model=AskRunStatusOut)
    def get_ask_run_status(run_id: str, user: dict = Depends(get_current_user)):
        user_email = user.get("email")
        if not user_email:
            raise HTTPException(status_code=400, detail="Authenticated user email missing")

        run = _ask_runs.get(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Run not found")
        if run.get("user_email") != user_email:
            raise HTTPException(status_code=403, detail="Run does not belong to user")

        return AskRunStatusOut(
            run_id=run_id,
            thread_id=run.get("thread_id"),
            status=str(run.get("status") or "unknown"),
            updated_at=run.get("updated_at") or datetime.utcnow(),
            status_message=run.get("status_message"),
            result=run.get("result"),
            error=run.get("error"),
        )

    @router.post("/ask", response_model=AskOut)
    @router.post("/mobile/ask", response_model=AskOut)
    async def ask(
        payload: AskIn,
        background_tasks: BackgroundTasks,
        user: dict = Depends(get_current_user),
    ):
        start_time = perf_counter()
        user_email = user.get("email")
        if not user_email:
            raise HTTPException(status_code=400, detail="Authenticated user email missing")

        try:
            command_payload = handle_pending_event(
                payload.question,
                user_email,
                user,
                payload.thread_id or payload.session_id,
                payload.pending_event_id,
                client_context=payload.client_context.model_dump(exclude_none=True)
                if payload.client_context
                else None,
                command_response_text=_command_response_text,
                command_assistant_metadata=_command_assistant_metadata,
                progress_callback=None,
            )
            if not command_payload:
                command_payload = _handle_command(
                    payload.question,
                    user_email,
                    user,
                    payload.thread_id or payload.session_id,
                    payload.client_context.model_dump(exclude_none=True)
                    if payload.client_context
                    else None,
                    progress_callback=None,
                )
            if command_payload:
                command_result, command_thread_id, command_ui_directives = command_payload
                from commands.storage import get_pending_event

                pending_event_id = get_pending_event(event_pending_key(user_email, command_thread_id))
                return AskOut(
                    question=payload.question,
                    answer=_command_response_text(command_result),
                    resolution={},
                    search_results=[],
                    events_results=[],
                    thread_id=command_thread_id,
                    session_id=command_thread_id,
                    is_new_session=True,
                    command_result=command_result,
                    ui_directives=command_ui_directives,
                    pending_event_id=pending_event_id,
                )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        from commands.storage import clear_command_thread_by_id

        force_new_session = False
        requested_thread_id = payload.thread_id or payload.session_id
        if requested_thread_id and _should_reset_command_thread(
            user_email,
            requested_thread_id,
            payload.pending_event_id,
        ):
            clear_command_thread_by_id(requested_thread_id)
            new_thread = conversations.ensure_thread(None, user_email)
            payload = payload.copy(update={"thread_id": new_thread["id"], "session_id": None})
        elif not requested_thread_id and payload.pending_event_id is None:
            main_session = conversations.get_main_session(user_email)
            main_thread_id = main_session.get("current_thread_id") if main_session else None
            if main_thread_id and _should_reset_command_thread(user_email, main_thread_id, None):
                clear_command_thread_by_id(main_thread_id)
                force_new_session = True

        ctx = _resolve_session_context(payload, user_email, force_new_session=force_new_session)
        if ctx.is_new_session:
            logger.info("[session] new session started session=%s user=%s", ctx.session_id, user_email)

        if ctx.is_reset_only:
            logger.info("[ask] session reset session=%s user=%s", ctx.session_id, user_email)
            return AskOut(**_make_reset_bundle(ctx))

        limit = payload.limit or 30
        preview = ctx.question.strip().replace("\n", " ")
        if len(preview) > 120:
            preview = preview[:117] + "..."

        mode = "main_session" if not (payload.thread_id or payload.session_id) else "thread"
        logger.info(
            "[ask] start session=%s user=%s limit=%s mode=%s is_new=%s question=%r",
            ctx.session_id,
            user_email,
            limit,
            mode,
            ctx.is_new_session,
            preview,
        )

        def _schedule_fact_extraction(**kwargs):
            from fact_extraction import maybe_extract_facts

            background_tasks.add_task(maybe_extract_facts, **kwargs)

        bundle = await llm.answer_question(
            ctx.question,
            search_limit=limit,
            user_id=user_email,
            session_id=ctx.session_id,
            user_email=user_email,
            client_context=payload.client_context.model_dump(exclude_none=True)
            if payload.client_context
            else None,
            ui_submission=payload.ui_submission.model_dump(exclude_none=True)
            if payload.ui_submission
            else None,
            on_exchange_persisted=_schedule_fact_extraction,
        )
        bundle["thread_id"] = ctx.session_id
        bundle["session_id"] = ctx.session_id
        bundle["is_new_session"] = ctx.is_new_session

        elapsed = perf_counter() - start_time
        search_results = bundle.get("search_results")
        search_count = len(search_results) if isinstance(search_results, list) else "n/a"
        logger.info(
            "[ask] complete session=%s user=%s elapsed=%.3fs search_results=%s",
            ctx.session_id,
            user_email,
            elapsed,
            search_count,
        )

        from commands.storage import get_pending_event

        bundle["pending_event_id"] = get_pending_event(event_pending_key(user_email, ctx.session_id))
        return AskOut(**bundle)

    @router.post("/ask/stream")
    @router.post("/mobile/ask/stream")
    async def ask_stream(
        payload: AskIn,
        background_tasks: BackgroundTasks,
        user: dict = Depends(get_current_user),
    ):
        start_time = perf_counter()
        user_email = user.get("email")
        if not user_email:
            raise HTTPException(status_code=400, detail="Authenticated user email missing")
        run_id = _create_ask_run(user_email, payload.thread_id or payload.session_id)

        from commands import parse_command
        from commands.storage import get_pending_event

        hint_thread_id = payload.thread_id or payload.session_id
        parsed_command_hint = parse_command(payload.question)
        has_command_hint = bool(parsed_command_hint and parsed_command_hint.command != "new")
        has_pending_event_hint = bool(
            payload.pending_event_id
            or get_pending_event(event_pending_key(user_email, hint_thread_id))
        )

        if has_command_hint or has_pending_event_hint:

            async def command_generator():
                heartbeat_seconds = 5.0
                queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
                loop = asyncio.get_running_loop()
                disconnect_event = asyncio.Event()

                def emit_status(message: str) -> None:
                    _touch_ask_run(run_id, status="running", status_message=message)
                    logger.info(
                        "[ask/stream] command status run=%s user=%s message=%r",
                        run_id,
                        user_email,
                        message,
                    )
                    if disconnect_event.is_set():
                        return
                    loop.call_soon_threadsafe(
                        queue.put_nowait,
                        {"type": "status", "message": message},
                    )

                def execute_command_flow() -> dict[str, Any]:
                    try:
                        emit_status("Understanding request...")
                        command_payload = handle_pending_event(
                            payload.question,
                            user_email,
                            user,
                            payload.thread_id or payload.session_id,
                            payload.pending_event_id,
                            client_context=payload.client_context.model_dump(exclude_none=True)
                            if payload.client_context
                            else None,
                            command_response_text=_command_response_text,
                            command_assistant_metadata=_command_assistant_metadata,
                            progress_callback=emit_status,
                        )
                        if not command_payload:
                            command_payload = _handle_command(
                                payload.question,
                                user_email,
                                user,
                                payload.thread_id or payload.session_id,
                                payload.client_context.model_dump(exclude_none=True)
                                if payload.client_context
                                else None,
                                progress_callback=emit_status,
                            )
                        if not command_payload:
                            return {
                                "type": "error",
                                "message": "Command processing could not be completed.",
                            }

                        command_result, command_thread_id, command_ui_directives = command_payload
                        _touch_ask_run(run_id, thread_id=command_thread_id)
                        pending_event_id = get_pending_event(
                            event_pending_key(user_email, command_thread_id)
                        )

                        emit_status("Finalizing response...")
                        bundle = {
                            "question": payload.question,
                            "answer": _command_response_text(command_result),
                            "resolution": {},
                            "search_results": [],
                            "events_results": [],
                            "thread_id": command_thread_id,
                            "session_id": command_thread_id,
                            "is_new_session": True,
                            "command_result": command_result,
                            "ui_directives": command_ui_directives,
                            "pending_event_id": pending_event_id,
                        }
                        _touch_ask_run(
                            run_id,
                            status="completed",
                            status_message=None,
                            result=bundle,
                            error=None,
                        )
                        return {"type": "done", "bundle": bundle}
                    except ValueError as exc:
                        _touch_ask_run(
                            run_id,
                            status="failed",
                            status_message=None,
                            error={"code": "validation_error", "message": str(exc)},
                        )
                        return {"type": "error", "message": str(exc)}
                    except Exception as exc:
                        logger.exception("[ask/stream] command error user=%s", user_email)
                        _touch_ask_run(
                            run_id,
                            status="failed",
                            status_message=None,
                            error={"code": "execution_error", "message": str(exc)},
                        )
                        return {"type": "error", "message": str(exc)}

                async def _produce() -> None:
                    try:
                        final_event = await asyncio.to_thread(execute_command_flow)
                        logger.info(
                            "[ask/stream] command final_event run=%s user=%s type=%s",
                            run_id,
                            user_email,
                            str(final_event.get("type") or "unknown"),
                        )
                        if not disconnect_event.is_set():
                            await queue.put(final_event)
                    finally:
                        if not disconnect_event.is_set():
                            await queue.put(None)

                producer_task = asyncio.create_task(_produce())
                try:
                    yield f"data: {json.dumps({'type': 'session_info', 'thread_id': payload.thread_id or payload.session_id, 'is_new_session': False, 'run_id': run_id}, default=str)}\\n\\n"
                    try:
                        while True:
                            try:
                                event = await asyncio.wait_for(queue.get(), timeout=heartbeat_seconds)
                            except asyncio.TimeoutError:
                                yield ": keep-alive\\n\\n"
                                continue

                            if event is None:
                                break

                            event_type = str(event.get("type") or "unknown")
                            if _should_log_stream_event(event_type):
                                logger.info(
                                    "[ask/stream] command emit run=%s user=%s type=%s",
                                    run_id,
                                    user_email,
                                    event_type,
                                )

                            yield f"data: {json.dumps(event, default=str)}\\n\\n"
                            if event.get("type") in {"done", "error"}:
                                break
                    except asyncio.CancelledError:
                        disconnect_event.set()
                        logger.info("[ask/stream] command client disconnected user=%s", user_email)
                        raise
                finally:
                    if disconnect_event.is_set():
                        logger.info(
                            "[ask/stream] command continues after disconnect user=%s",
                            user_email,
                        )
                        _touch_ask_run(run_id, status_message="Disconnected, still processing...")
                    else:
                        with suppress(asyncio.CancelledError):
                            await producer_task

            return StreamingResponse(
                command_generator(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                    "X-Ask-Run-Id": run_id,
                    "X-Ask-Thread-Id": str(payload.thread_id or payload.session_id or ""),
                },
            )

        from commands.storage import clear_command_thread_by_id

        force_new_session = False
        requested_thread_id = payload.thread_id or payload.session_id
        if requested_thread_id and _should_reset_command_thread(
            user_email,
            requested_thread_id,
            payload.pending_event_id,
        ):
            clear_command_thread_by_id(requested_thread_id)
            new_thread = conversations.ensure_thread(None, user_email)
            payload = payload.copy(update={"thread_id": new_thread["id"], "session_id": None})
        elif not requested_thread_id and payload.pending_event_id is None:
            main_session = conversations.get_main_session(user_email)
            main_thread_id = main_session.get("current_thread_id") if main_session else None
            if main_thread_id and _should_reset_command_thread(user_email, main_thread_id, None):
                clear_command_thread_by_id(main_thread_id)
                force_new_session = True

        ctx = _resolve_session_context(payload, user_email, force_new_session=force_new_session)
        if ctx.is_new_session:
            logger.info("[session] new session started session=%s user=%s", ctx.session_id, user_email)

        if ctx.is_reset_only:
            _touch_ask_run(run_id, thread_id=ctx.session_id)
            logger.info("[ask/stream] session reset session=%s user=%s", ctx.session_id, user_email)
            reset_bundle = _make_reset_bundle(ctx)

            async def reset_generator():
                yield f"data: {json.dumps({'type': 'session_info', 'thread_id': ctx.session_id, 'is_new_session': True, 'run_id': run_id}, default=str)}\\n\\n"
                yield f"data: {json.dumps({'type': 'token', 'content': _RESET_MESSAGE}, default=str)}\\n\\n"
                yield f"data: {json.dumps({'type': 'done', 'bundle': reset_bundle}, default=str)}\\n\\n"

                _touch_ask_run(
                    run_id,
                    status="completed",
                    status_message=None,
                    result=reset_bundle,
                    error=None,
                )

            return StreamingResponse(
                reset_generator(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                    "X-Ask-Run-Id": run_id,
                    "X-Ask-Thread-Id": ctx.session_id,
                },
            )

        limit = payload.limit or 30
        _touch_ask_run(run_id, thread_id=ctx.session_id)

        preview = ctx.question.strip().replace("\n", " ")
        if len(preview) > 120:
            preview = preview[:117] + "..."
        mode = "main_session" if not (payload.thread_id or payload.session_id) else "thread"
        logger.info(
            "[ask/stream] start session=%s user=%s limit=%s mode=%s is_new=%s question=%r",
            ctx.session_id,
            user_email,
            limit,
            mode,
            ctx.is_new_session,
            preview,
        )

        def _schedule_fact_extraction(**kwargs):
            from fact_extraction import maybe_extract_facts

            background_tasks.add_task(maybe_extract_facts, **kwargs)

        async def event_generator():
            heartbeat_seconds = 5.0
            disconnect_event = asyncio.Event()

            async def _stream_events(queue: asyncio.Queue[dict[str, Any] | None]) -> None:
                try:
                    async for event in llm.answer_question_stream(
                        ctx.question,
                        search_limit=limit,
                        user_id=user_email,
                        session_id=ctx.session_id,
                        user_email=user_email,
                        client_context=payload.client_context.model_dump(exclude_none=True)
                        if payload.client_context
                        else None,
                        ui_submission=payload.ui_submission.model_dump(exclude_none=True)
                        if payload.ui_submission
                        else None,
                        on_exchange_persisted=_schedule_fact_extraction,
                    ):
                        if event.get("type") == "done":
                            from commands.storage import get_pending_event

                            bundle = event.get("bundle", {})
                            bundle["thread_id"] = ctx.session_id
                            bundle["session_id"] = ctx.session_id
                            bundle["is_new_session"] = ctx.is_new_session
                            bundle["pending_event_id"] = get_pending_event(
                                event_pending_key(user_email, ctx.session_id)
                            )
                            event["bundle"] = bundle

                        event_type = event.get("type")
                        if _should_log_stream_event(str(event_type or "")):
                            logger.info(
                                "[ask/stream] upstream event run=%s session=%s user=%s type=%s",
                                run_id,
                                ctx.session_id,
                                user_email,
                                str(event_type or "unknown"),
                            )
                        if event_type == "status":
                            _touch_ask_run(
                                run_id,
                                status="running",
                                status_message=str(event.get("message") or "Working..."),
                            )
                        elif event_type == "tool_call":
                            name = str(event.get("name") or "tool")
                            _touch_ask_run(
                                run_id,
                                status="running",
                                status_message=f"Using {name}...",
                            )
                        elif event_type == "done":
                            _touch_ask_run(
                                run_id,
                                status="completed",
                                status_message=None,
                                result=event.get("bundle"),
                                error=None,
                            )

                        if not disconnect_event.is_set():
                            await queue.put(event)
                except Exception as exc:
                    logger.exception("[ask/stream] error session=%s", ctx.session_id)
                    _touch_ask_run(
                        run_id,
                        status="failed",
                        status_message=None,
                        error={"code": "execution_error", "message": str(exc)},
                    )
                    if not disconnect_event.is_set():
                        await queue.put({"type": "error", "message": str(exc)})
                finally:
                    if not disconnect_event.is_set():
                        await queue.put(None)

            try:
                yield f"data: {json.dumps({'type': 'session_info', 'thread_id': ctx.session_id, 'is_new_session': ctx.is_new_session, 'run_id': run_id}, default=str)}\\n\\n"

                queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
                producer_task = asyncio.create_task(_stream_events(queue))
                try:
                    try:
                        while True:
                            try:
                                event = await asyncio.wait_for(queue.get(), timeout=heartbeat_seconds)
                            except asyncio.TimeoutError:
                                yield ": keep-alive\\n\\n"
                                continue

                            if event is None:
                                break

                            event_type = str(event.get("type") or "unknown")
                            if _should_log_stream_event(event_type):
                                logger.info(
                                    "[ask/stream] emit run=%s session=%s user=%s type=%s",
                                    run_id,
                                    ctx.session_id,
                                    user_email,
                                    event_type,
                                )

                            yield f"data: {json.dumps(event, default=str)}\\n\\n"

                            if event.get("type") == "done":
                                elapsed = perf_counter() - start_time
                                logger.info(
                                    "[ask/stream] complete session=%s user=%s elapsed=%.3fs",
                                    ctx.session_id,
                                    user_email,
                                    elapsed,
                                )
                    except asyncio.CancelledError:
                        disconnect_event.set()
                        logger.info(
                            "[ask/stream] client disconnected session=%s user=%s",
                            ctx.session_id,
                            user_email,
                        )
                        _touch_ask_run(run_id, status_message="Disconnected, still processing...")
                        raise
                finally:
                    if disconnect_event.is_set():
                        logger.info(
                            "[ask/stream] execution continues after disconnect session=%s user=%s",
                            ctx.session_id,
                            user_email,
                        )
                    else:
                        with suppress(asyncio.CancelledError):
                            await producer_task
            except Exception as exc:
                logger.exception("[ask/stream] error session=%s", ctx.session_id)
                yield f"data: {json.dumps({'type': 'error', 'message': str(exc)}, default=str)}\\n\\n"

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
                "X-Ask-Run-Id": run_id,
                "X-Ask-Thread-Id": ctx.session_id,
            },
        )

    @router.get("/threads", response_model=list[ThreadOut])
    @router.get("/mobile/threads", response_model=list[ThreadOut])
    def list_conversation_threads(user: dict = Depends(get_current_user)):
        user_email = user.get("email")
        if not user_email:
            raise HTTPException(status_code=400, detail="Authenticated user email missing")
        threads = conversations.list_threads(user_email)
        return [ThreadOut(**thread) for thread in threads]

    @router.get("/main-session", response_model=MainSessionOut)
    @router.get("/mobile/main-session", response_model=MainSessionOut)
    def get_main_session_thread(user: dict = Depends(get_current_user)):
        user_email = user.get("email")
        if not user_email:
            raise HTTPException(status_code=400, detail="Authenticated user email missing")

        thread, is_new_session, _ = conversations.resolve_main_session(user_email, "")
        thread_id = str(thread["id"])
        thread_detail = conversations.get_thread_with_messages(thread_id, user_email) or {}

        from commands.storage import get_pending_event

        pending_event_id = get_pending_event(event_pending_key(user_email, thread_id))

        return MainSessionOut(
            thread_id=thread_id,
            thread_title=thread_detail.get("title") or thread.get("title"),
            is_new_session=is_new_session,
            pending_event_id=pending_event_id,
            messages=thread_detail.get("messages") or [],
        )

    @router.post("/threads", response_model=ThreadOut)
    @router.post("/mobile/threads", response_model=ThreadOut)
    def create_conversation_thread(payload: ThreadCreate, user: dict = Depends(get_current_user)):
        user_email = user.get("email")
        if not user_email:
            raise HTTPException(status_code=400, detail="Authenticated user email missing")
        thread = conversations.ensure_thread(None, user_email, title=payload.title)
        return ThreadOut(**thread)

    @router.get("/threads/{thread_id}", response_model=ThreadDetailOut)
    @router.get("/mobile/threads/{thread_id}", response_model=ThreadDetailOut)
    def get_conversation_thread(thread_id: str, user: dict = Depends(get_current_user)):
        user_email = user.get("email")
        if not user_email:
            raise HTTPException(status_code=400, detail="Authenticated user email missing")
        thread = conversations.get_thread_with_messages(thread_id, user_email)
        if not thread:
            raise HTTPException(status_code=404, detail="Conversation thread not found")
        return ThreadDetailOut(
            **{
                **{k: v for k, v in thread.items() if k != "messages"},
                "messages": thread.get("messages", []),
            }
        )

    @router.put("/threads/{thread_id}", response_model=ThreadOut)
    @router.put("/mobile/threads/{thread_id}", response_model=ThreadOut)
    def update_conversation_thread(
        thread_id: str,
        payload: ThreadUpdate,
        user: dict = Depends(get_current_user),
    ):
        user_email = user.get("email")
        if not user_email:
            raise HTTPException(status_code=400, detail="Authenticated user email missing")
        conversations.ensure_thread(thread_id, user_email)
        normalized_title = payload.title.strip() if payload.title else None
        updates = ["updated_at = NOW()"]
        params: list[Any] = []

        if payload.title is not None:
            updates.append("title = %s")
            params.append(normalized_title)

        with get_conn() as conn, conn.cursor() as cur:
            query = f"""
                UPDATE conversation_threads
                SET {", ".join(updates)}
                WHERE id = %s AND user_email = %s
                RETURNING id, user_email, title, created_at, updated_at
            """
            params.extend([thread_id, user_email])
            cur.execute(query, params)
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Conversation thread not found")
            conn.commit()
        return ThreadOut(**dict(row))

    @router.delete("/threads/{thread_id}", status_code=204)
    @router.delete("/mobile/threads/{thread_id}", status_code=204)
    def delete_conversation_thread(thread_id: str, user: dict = Depends(get_current_user)):
        user_email = user.get("email")
        if not user_email:
            raise HTTPException(status_code=400, detail="Authenticated user email missing")
        deleted = conversations.delete_thread(thread_id, user_email)
        if not deleted:
            raise HTTPException(status_code=404, detail="Conversation thread not found")
        return Response(status_code=204)

    return router


def _touch_ask_run(run_id: str, **updates: Any) -> None:
    existing = _ask_runs.get(run_id)
    if not existing:
        return
    existing.update(updates)
    existing["updated_at"] = datetime.utcnow()


def _create_ask_run(user_email: str, thread_id: str | None) -> str:
    run_id = f"run_{uuid4().hex[:16]}"
    _ask_runs[run_id] = {
        "run_id": run_id,
        "user_email": user_email,
        "thread_id": thread_id,
        "status": "running",
        "updated_at": datetime.utcnow(),
        "status_message": "Starting request...",
        "result": None,
        "error": None,
    }
    cutoff = datetime.utcnow().timestamp() - ASK_RUNS_TTL_SECONDS
    stale_ids = [
        key
        for key, value in _ask_runs.items()
        if (value.get("updated_at") or datetime.utcnow()).timestamp() < cutoff
    ]
    for stale_id in stale_ids:
        _ask_runs.pop(stale_id, None)
    return run_id


class _SessionContext:
    __slots__ = (
        "session_id",
        "question",
        "is_new_session",
        "is_reset_only",
        "user_email",
        "original_question",
    )

    def __init__(
        self,
        session_id: str,
        question: str,
        is_new_session: bool,
        is_reset_only: bool,
        user_email: str,
        original_question: str,
    ):
        self.session_id = session_id
        self.question = question
        self.is_new_session = is_new_session
        self.is_reset_only = is_reset_only
        self.user_email = user_email
        self.original_question = original_question


def _strip_command_prefix(message: str) -> str:
    from commands.parser import parse_command

    text = (message or "").strip()
    parsed = parse_command(text)
    if not parsed:
        return text
    return parsed.args


def _resolve_session_context(
    payload: AskIn,
    user_email: str,
    *,
    force_new_session: bool = False,
) -> _SessionContext:
    from commands.parser import parse_command

    requested_thread_id = payload.thread_id or payload.session_id
    question = payload.question
    is_new_session = False
    parsed_command = parse_command(question)
    reset_requested = bool(parsed_command and parsed_command.command == "new")
    if reset_requested:
        question = parsed_command.args
        force_new_session = True

    if requested_thread_id and not force_new_session:
        try:
            thread = conversations.ensure_thread(requested_thread_id, user_email)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail="Conversation thread not found") from exc
        except PermissionError as exc:
            raise HTTPException(
                status_code=403,
                detail="Conversation thread does not belong to user",
            ) from exc
    elif requested_thread_id and force_new_session:
        thread = conversations.ensure_thread(None, user_email)
        is_new_session = True
    else:
        if force_new_session:
            question = f"/new {question}".strip()
        thread, is_new_session, question = conversations.resolve_main_session(user_email, question)

    question = _strip_command_prefix(question)
    session_id = thread["id"]
    is_reset_only = is_new_session and not question.strip()

    return _SessionContext(
        session_id=session_id,
        question=question,
        is_new_session=is_new_session,
        is_reset_only=is_reset_only,
        user_email=user_email,
        original_question=payload.question,
    )


_RESET_MESSAGE = "New session started. How can I help you?"


def _make_reset_bundle(ctx: _SessionContext) -> dict[str, Any]:
    return {
        "question": ctx.original_question,
        "answer": _RESET_MESSAGE,
        "resolution": {},
        "search_results": [],
        "events_results": [],
        "session_id": ctx.session_id,
        "thread_id": ctx.session_id,
        "is_new_session": True,
    }


def _command_response_text(command_result: dict[str, Any]) -> str:
    result_type = CommandResultType.from_value(command_result.get("type"))
    if result_type is CommandResultType.EVENT_CONFIRMATION:
        return command_result.get("message") or "Event proposal ready."
    if result_type is CommandResultType.NEED_USER_INPUT:
        need_user_input = command_result.get("need_user_input")
        if isinstance(need_user_input, dict):
            prompt = str(need_user_input.get("prompt") or "").strip()
            if prompt:
                return prompt
        return "I need a few more details to continue."
    return command_result.get("message") or "Command completed."


def _sanitize_command_metadata(command_result: dict[str, Any]) -> dict[str, Any]:
    try:
        return json.loads(json.dumps(command_result, default=str))
    except Exception:
        return {
            "type": CommandResultType.from_value(command_result.get("type")).value,
        }


def _command_assistant_metadata(
    command_result: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    metadata: dict[str, Any] = {
        "command_result": _sanitize_command_metadata(command_result),
    }
    ui_directives = command_result_to_ui_directives(command_result)
    if ui_directives:
        metadata["ui_directives"] = ui_directives
    return metadata, ui_directives


def _handle_command(
    question: str,
    user_email: str,
    user: dict,
    thread_id: str | None,
    client_context: dict[str, Any] | None = None,
    progress_callback: Callable[[str], None] | None = None,
) -> tuple[dict[str, Any], str, dict[str, Any] | None] | None:
    from commands import get_command_registry, parse_command
    from commands.storage import store_command_thread

    parsed_cmd = parse_command(question)
    if not parsed_cmd or parsed_cmd.command == "new":
        return None

    command_thread = conversations.ensure_thread(
        None,
        user_email,
        title=f"Command: /{parsed_cmd.command}",
    )
    command_thread_id = command_thread["id"]
    pending_key = event_pending_key(user_email, command_thread_id)
    store_command_thread(pending_key, command_thread_id)

    registry = get_command_registry()
    context = {
        "user_email": user_email,
        "user": user,
        "thread_id": command_thread_id,
        "event_pending_key": pending_key,
        "client_context": client_context,
        "progress_callback": progress_callback,
    }
    command_result = registry.execute(parsed_cmd, context)

    if thread_id is None:
        conversations.set_main_session_thread(user_email, command_thread_id)

    assistant_metadata, ui_directives = _command_assistant_metadata(command_result)
    try:
        conversations.record_exchange(
            command_thread_id,
            user_email,
            question,
            _command_response_text(command_result),
            assistant_metadata=assistant_metadata,
        )
    except Exception as exc:
        logger.warning("[command_thread] Failed to record exchange: %s", exc, exc_info=exc)

    return command_result, command_thread_id, ui_directives


def _should_reset_command_thread(
    user_email: str,
    thread_id: str | None,
    pending_event_id: str | None,
) -> bool:
    if not user_email or not thread_id or pending_event_id:
        return False

    from commands.storage import is_command_thread

    return is_command_thread(thread_id)
