"""Capture failures remain useful without exporting provider/user content."""

import json
from types import SimpleNamespace

import psycopg
import pytest

from whatsapp_diagnostics import (
    ExcludedWhatsAppSource,
    IdentityProjectionError,
    failure_event,
    project_identity,
    report_failure,
)


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


@pytest.mark.parametrize("address,kind", [
    ("status@broadcast", "status"), ("private@broadcast", "broadcast"),
    ("private@newsletter", "newsletter"),
])
def test_known_excluded_sources_are_distinct_from_capture_failures(address, kind, caplog):
    from whatsapp_runtime import log_capture_event

    with pytest.raises(ExcludedWhatsAppSource) as error:
        project_identity(address, "chat")
    event = failure_event("live_normalize", error.value)
    assert event["kind"] == "capture_ignored"
    assert event["identity_field"] == "chat" and event["address_kind"] == kind
    with caplog.at_level("INFO"):
        log_capture_event(event)
    assert all(record.levelname == "INFO" for record in caplog.records)
    assert address not in caplog.text


def test_unknown_sender_remains_warning_and_does_not_leak_address(caplog):
    from whatsapp_runtime import log_capture_event

    with pytest.raises(IdentityProjectionError) as error:
        project_identity("private@unknown-server", "sender")
    event = failure_event("live_normalize", error.value)
    assert event["kind"] == "capture_failure"
    assert event["identity_field"] == "sender" and event["address_kind"] == "unknown"
    log_capture_event(event)
    assert "WARNING" in caplog.text and "private" not in caplog.text


def test_exclusions_apply_only_to_chat_sources_before_message_projection():
    from messaging_normalization import live_record

    event = SimpleNamespace(data=SimpleNamespace(message_info=SimpleNamespace(
        source=SimpleNamespace(chat="status@broadcast"))))
    with pytest.raises(ExcludedWhatsAppSource):
        live_record(event, "15550000000@s.whatsapp.net")
    with pytest.raises(IdentityProjectionError) as error:
        project_identity("status@broadcast", "sender")
    assert not isinstance(error.value, ExcludedWhatsAppSource)


def test_database_error_has_diagnostic_sqlstate_without_query():
    event = failure_event("live_stage", psycopg.errors.UndefinedColumn("private query"))
    assert event["sqlstate"] == "42703"
    assert "private" not in json.dumps(event)


def test_health_logs_separate_transport_readiness_from_live_staging(caplog):
    from whatsapp_diagnostics import health_event
    from whatsapp_runtime import log_capture_event

    event = health_event({"live_received": 5, "live_excluded": 2, "live_stage_accepted": 3}, True, 100, 125)
    assert event["last_live_staged_age_s"] == 25
    with caplog.at_level("INFO"):
        log_capture_event(event)
    assert "capture_health" in caplog.text and '"live_stage_accepted": 3' in caplog.text
    assert health_event({}, True, None, 100)["last_live_staged_age_s"] is None
