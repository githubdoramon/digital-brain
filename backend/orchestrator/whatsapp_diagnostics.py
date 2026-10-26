"""Allow-listed child-process diagnostics; never serialize exception text."""

from contextlib import suppress

STAGES = frozenset({
    "bind_account", "live_normalize", "live_stage", "history_capture",
    "history_startup", "history_request", "identity_refresh", "session_run",
})
REASONS = frozenset({
    "missing_timezone", "missing_timestamp", "unsupported_identity",
    "missing_message_identity", "record_too_large", "batch_too_large",
    "provider_mismatch", "profile_identity_mismatch", "account_mismatch",
    "queue_full", "history_chunk_too_large", "history_incomplete",
    "missing_self_identity",
})


def failure_event(stage: str, error: Exception) -> dict:
    # Exception class names can also be arbitrary. Emit only these categories.
    import psycopg

    category = "other"
    for kind in (TimeoutError, ValueError, AttributeError, TypeError, OSError,
                 psycopg.IntegrityError, psycopg.ProgrammingError,
                 psycopg.OperationalError, psycopg.Error):
        if isinstance(error, kind):
            category = kind.__name__
            break
    reason = "unspecified"
    if type(error) is ValueError and len(error.args) == 1:
        value = error.args[0]
        if isinstance(value, str) and value in REASONS:
            reason = value
    return {
        "kind": "capture_failure",
        "stage": stage if stage in STAGES else "unknown",
        "category": category,
        "reason": reason,
    }


def report_failure(pipe, stage: str, error: Exception):
    # Reporting must not prevent durable account health from being updated.
    with suppress(BrokenPipeError, EOFError, OSError):
        pipe.send(failure_event(stage, error))
