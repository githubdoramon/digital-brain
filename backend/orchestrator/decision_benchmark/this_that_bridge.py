"""Optional HTTP bridge for the upstream thisthat Python runtime."""

from __future__ import annotations

import hmac
import json
import os
import threading
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field


class ChoiceQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: str
    instructions: str
    criteria: dict[str, str] = Field(min_length=2, max_length=255)


class DecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str
    state: str | dict[str, Any] | list[Any]
    questions: dict[str, ChoiceQuestion] = Field(min_length=1, max_length=32)


def create_app(
    model_name: str,
    *,
    device: str = "auto",
    api_key_env: str | None = None,
    max_state_tokens: int = 8192,
    decider=None,
    question_factory=None,
) -> FastAPI:
    """Load once on startup; injected runtime/factory keep bridge tests GPU-free."""
    if max_state_tokens < 1:
        raise ValueError("max_state_tokens must be positive")
    api_key = os.environ.get(api_key_env) if api_key_env else None
    if api_key_env and not api_key:
        raise ValueError("The configured bridge API key environment variable is missing")
    lock = threading.Lock()

    @asynccontextmanager
    async def lifespan(app):
        if decider is None:
            from thisthat import Question, TypedDecider

            app.state.decider = TypedDecider.from_pretrained(model_name, device=device)
            app.state.question_factory = Question
        else:
            app.state.decider = decider
            app.state.question_factory = question_factory
        yield

    app = FastAPI(lifespan=lifespan)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request, _exc):
        # FastAPI's default validation response can echo supplied state.
        return JSONResponse(status_code=422, content={"detail": "invalid_request"})

    @app.post("/decide")
    def decide(body: DecisionRequest, request: Request):
        if api_key and not hmac.compare_digest(
            request.headers.get("authorization", ""), f"Bearer {api_key}"
        ):
            raise HTTPException(status_code=401, detail="unauthorized")
        if body.model != model_name:
            raise HTTPException(status_code=400, detail="model_mismatch")
        if any(q.type != "choice" for q in body.questions.values()):
            raise HTTPException(status_code=422, detail="choice_questions_only")
        state = (
            body.state
            if isinstance(body.state, str)
            else json.dumps(body.state, ensure_ascii=False)
        )
        runtime = request.app.state.decider
        factory = request.app.state.question_factory
        keys = list(body.questions)
        labels = [list(body.questions[key].criteria) for key in keys]
        questions = [
            factory(
                q.instructions,
                [f"{label}: {description}" for label, description in q.criteria.items()],
            )
            for q in body.questions.values()
        ]
        try:
            # Upstream defaults to truncating state at 1536 tokens. Reject over-budget
            # input and explicitly pass sufficient room so every benchmark sees all state.
            with lock:
                state_length = len(
                    runtime.tokenizer.encode("Context:\n" + state, add_special_tokens=False)
                )
                if state_length > max_state_tokens:
                    raise HTTPException(status_code=413, detail="state_token_limit")
                runtime.backend.synchronize()
                decisions = runtime.decide(state, questions, max_state_tokens=state_length + 16)
                runtime.backend.synchronize()
            if len(decisions) != len(keys):
                raise ValueError("decision_count")
            answers = {
                key: {
                    "type": "choice",
                    "choice": options[decision.index],
                    "confidence": float(decision.confidence),
                    "probabilities": {
                        label: float(p) for label, p in zip(options, decision.probabilities)
                    },
                }
                for key, options, decision in zip(keys, labels, decisions)
            }
            return {"model": model_name, "answers": answers}
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(status_code=500, detail="inference_failed") from None

    return app
