"""Inactive instrumentation hooks reserved for a future observability provider.

These hooks do not collect or export data. Decorators return the original
function so sync, async, and streaming behavior remains unchanged.
"""
from collections.abc import Callable
from concurrent.futures import Executor, Future
from contextlib import contextmanager, nullcontext
from contextvars import copy_context
from typing import Any, TypeVar

_T = TypeVar("_T")


def timed_workflow_stage(flow: str, stage: str):  # noqa: ARG001
    return lambda function: function


def traced_agent_run(function: _T) -> _T:
    return function


def traced_tool_call(function: _T) -> _T:
    return function


def traced_llm_request(function: _T) -> _T:
    return function


@contextmanager
def traced_intelligence_run():
    yield None


def traced_daily_briefing_run():
    return nullcontext()


def record_agent_run_metrics(*args: Any, **kwargs: Any) -> None:
    pass


def submit_with_current_context(
    executor: Executor, function: Callable[..., _T], /, *args: Any, **kwargs: Any
) -> Future[_T]:
    context = copy_context()
    return executor.submit(context.run, function, *args, **kwargs)
