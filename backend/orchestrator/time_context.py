"""Trusted request clocks shared by every model entry point."""

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from functools import wraps
from inspect import isasyncgenfunction, iscoroutinefunction, signature
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_CONTEXT = ContextVar("model_time_context", default=None)


def snapshot(client_context=None):
    context = client_context if isinstance(client_context, dict) else {}
    name = str(context.get("timezone") or "UTC")
    try:
        ZoneInfo(name)
    except (ValueError, ZoneInfoNotFoundError):
        name = "UTC"
    return {"sent_at": datetime.now(timezone.utc).isoformat(), "timezone": name}


@contextmanager
def use_time_context(context):
    token = _CONTEXT.set({**(_CONTEXT.get() or {}), **(context or {})})
    try:
        yield
    finally:
        _CONTEXT.reset(token)


def current_context():
    return dict(_CONTEXT.get() or {})


def model_time_message():
    context = _CONTEXT.get() or {}
    name = context.get("timezone", "UTC")
    try:
        zone = ZoneInfo(name)
    except (ValueError, ZoneInfoNotFoundError):
        name, zone = "UTC", timezone.utc
    now = datetime.now(timezone.utc)
    lines = [
        "Trusted time context:",
        f"UTC now: {now.isoformat()}",
        f"Local now ({name}): {now.astimezone(zone).isoformat()}",
    ]
    if context.get("sent_at"):
        lines.append(f"Current request sent at: {context['sent_at']}")
    lines.append(
        "Resolve relative dates against the timestamp and timezone of the message containing "
        "them, including historical evidence and original requests in clarification flows. "
        "Use the current request clock only for messages without an explicit source timestamp. "
        "Archive dates are evidence dates, never the current clock."
    )
    return {"role": "system", "content": "\n".join(lines)}


def with_request_time(function):
    """Scope both controller entry points without leaking clocks between requests."""
    sig = signature(function)

    def clock(args, kwargs):
        bound = sig.bind(*args, **kwargs)
        return current_context() or snapshot(bound.arguments.get("client_context"))

    if isasyncgenfunction(function):

        @wraps(function)
        async def stream(*args, **kwargs):
            context = clock(args, kwargs)
            iterator = function(*args, **kwargs)
            try:
                while True:
                    # Detach before yielding; consumers may resume in another context.
                    with use_time_context(context):
                        try:
                            item = await iterator.__anext__()
                        except StopAsyncIteration:
                            return
                    yield item
            finally:
                with use_time_context(context):
                    await iterator.aclose()

        return stream
    if iscoroutinefunction(function):

        @wraps(function)
        async def run(*args, **kwargs):
            with use_time_context(clock(args, kwargs)):
                return await function(*args, **kwargs)

        return run
    raise TypeError("with_request_time requires an async entry point")
