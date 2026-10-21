"""Process-local admission and provider cooldown shared by all LLM transports.

Background calls fail fast into durable job deferral instead of sleeping with a
worker occupied. Foreground calls retain normal transport retries. A bounded
background reservation prevents continuous foreground traffic from starving history.
"""

import math
import os
import random
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import wraps

from observability.logger import get_runtime_logger

logger = get_runtime_logger(__name__)
_BACKGROUND = ContextVar("llm_background", default=False)
_SESSION = ContextVar("llm_background_session", default=None)
_LOCK = threading.RLock()
_STATES = {}
RESERVATION_SECONDS = 300


class BackgroundDeferred(RuntimeError):
    def __init__(self, reason, delay=5):
        super().__init__(reason)
        self.reason = reason
        self.delay = max(1, math.ceil(delay))


@dataclass
class _State:
    foreground: int = 0
    background: bool = False
    failures: int = 0
    cooldown_until: float = 0
    last_background: float = field(default_factory=lambda: time.monotonic())
    deferred: int = 0
    waiting: dict = field(default_factory=dict)


def is_background():
    return _BACKGROUND.get()


@contextmanager
def background_work(lane="background"):
    token = _BACKGROUND.set(True)
    session = {
        "lane": lane,
        "held": None,
        "ready": False,
        "endpoint": os.getenv("LLM_BASE_URL", ""),
    }
    session_token = _SESSION.set(session)
    try:
        yield
    finally:
        with _LOCK:
            if session["held"] is not None:
                session["held"].background = False
                session["held"].waiting.pop(lane, None)
            elif session["ready"]:
                _state(session["endpoint"]).waiting.pop(lane, None)
        _SESSION.reset(session_token)
        _BACKGROUND.reset(token)


def _state(endpoint):
    return _STATES.setdefault(endpoint.rstrip("/"), _State())


def _register(state, lane, now):
    for stale in [name for name, stamp in state.waiting.items() if now - stamp > 60]:
        state.waiting.pop(stale, None)
    state.waiting[lane] = now


def _blocked(state, now, lane):
    if state.cooldown_until > now:
        return BackgroundDeferred("provider_cooldown", state.cooldown_until - now)
    if state.background:
        return BackgroundDeferred("background_busy")
    if state.waiting and next(iter(state.waiting)) != lane:
        return BackgroundDeferred("background_turn")
    if state.foreground and now - state.last_background < RESERVATION_SECONDS:
        return BackgroundDeferred("foreground_priority")
    return None


def background_ready():
    session = _SESSION.get()
    if session is None:
        return True
    with _LOCK:
        state = _state(session["endpoint"])
        now = time.monotonic()
        _register(state, session["lane"], now)
        session["ready"] = _blocked(state, now, session["lane"]) is None
        if not session["ready"]:
            state.deferred += 1
        return session["ready"]


@contextmanager
def request_slot(endpoint):
    background = is_background()
    session = _SESSION.get()
    with _LOCK:
        state = _state(endpoint)
        if background and (session is None or session["held"] is not state):
            lane = session["lane"] if session else "background"
            _register(state, lane, time.monotonic())
            blocked = _blocked(state, time.monotonic(), lane)
            if blocked:
                state.deferred += 1
                raise blocked
            state.background = True
            state.last_background = time.monotonic()
            state.waiting.pop(lane, None)
            if session is not None:
                session["held"] = state
                session["endpoint"] = endpoint
        elif background and state.cooldown_until > time.monotonic():
            raise BackgroundDeferred("provider_cooldown", state.cooldown_until - time.monotonic())
        elif not background:
            state.foreground += 1
    try:
        yield
    except Exception as exc:
        from llm_policy import failure_code

        if failure_code(exc) in {"model_unavailable", "model_timeout", "model_rate_limited"}:
            with _LOCK:
                state.failures += 1
                delay = min(300, 30 * 2 ** min(state.failures - 1, 4) * random.uniform(0.9, 1.1))
                state.cooldown_until = time.monotonic() + delay
            logger.warning(
                "[llm_admission] provider_cooldown failures=%d delay_s=%d",
                state.failures,
                math.ceil(delay),
            )
            if background:
                raise BackgroundDeferred("provider_unavailable", delay) from exc
        raise
    else:
        with _LOCK:
            state.failures = 0
            state.cooldown_until = 0
    finally:
        with _LOCK:
            if background and session is None:
                state.background = False
            elif not background:
                state.foreground -= 1


def admitted(endpoint):
    """Wrap a synchronous physical request; endpoint resolution remains transport-owned."""

    def decorate(function):
        @wraps(function)
        def call(*args, **kwargs):
            with request_slot(endpoint()):
                return function(*args, **kwargs)

        return call

    return decorate


def status():
    with _LOCK:
        now = time.monotonic()
        return [
            {
                "foreground": s.foreground,
                "background": s.background,
                "cooldown_seconds": max(0, math.ceil(s.cooldown_until - now)),
                "failure_streak": s.failures,
                "deferred": s.deferred,
                "waiting_lanes": len(s.waiting),
            }
            for s in _STATES.values()
        ]
