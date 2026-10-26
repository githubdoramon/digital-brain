"""Capture failures remain useful without exporting provider/user content."""

import json
from types import SimpleNamespace

import psycopg
import pytest

from whatsapp_diagnostics import failure_event, report_failure


@pytest.mark.parametrize(("stage", "error", "category", "reason"), [
    ("live_normalize", ValueError("missing_timezone"), "ValueError", "missing_timezone"),
    ("live_stage", ValueError("queue_full"), "ValueError", "queue_full"),
    ("live_stage", psycopg.OperationalError("private DSN"), "OperationalError", "unspecified"),
    ("history_startup", TimeoutError("private JID"), "TimeoutError", "unspecified"),
    ("history_request", AttributeError("private token"), "AttributeError", "unspecified"),
    ("history_capture", ValueError("private body"), "ValueError", "unspecified"),
    ("private stage", RuntimeError("private payload"), "other", "unspecified"),
])
def test_failure_diagnostics_allowlist(stage, error, category, reason):
    events = []
    report_failure(SimpleNamespace(send=events.append), stage, error)
    assert events == [{"kind": "capture_failure", "stage": stage if stage != "private stage" else "unknown",
                       "category": category, "reason": reason}]
    assert "private" not in json.dumps(events)


def test_failure_reporting_tolerates_closed_pipe():
    def send(_event):
        raise BrokenPipeError("closed")

    report_failure(SimpleNamespace(send=send), "live_stage", ValueError("queue_full"))


def test_arbitrary_exception_class_and_arguments_are_not_exported():
    private_error = type("private_identity", (Exception,), {})
    assert failure_event("live_stage", private_error("secret"))["category"] == "other"
    assert failure_event("live_stage", ValueError({"secret": "value"}))["reason"] == "unspecified"
