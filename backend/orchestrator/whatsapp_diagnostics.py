"""Allow-listed child-process diagnostics; never serialize exception text."""

from contextlib import suppress


class IdentityProjectionError(ValueError):
    def __init__(self, field, address_kind):
        self.code = "unsupported_identity"
        self.details = {"identity_field": field, "address_kind": address_kind}
        super().__init__(self.code)


class ExcludedWhatsAppSource(IdentityProjectionError):
    """Known out-of-scope provider sources are skips, not failed captures."""


def project_identity(value, field):
    from messaging_normalization import jid

    if isinstance(value, str):
        user, _, server = value.partition("@")
    else:
        user, server = getattr(value, "user", ""), getattr(value, "server", "")
    kind = {
        "s.whatsapp.net": "phone", "lid": "lid", "g.us": "group",
        "newsletter": "newsletter", "broadcast": "broadcast",
    }.get(server, "unknown")
    if server == "broadcast" and user == "status":
        kind = "status"
    if field == "chat" and kind in {"status", "broadcast", "newsletter"}:
        raise ExcludedWhatsAppSource(field, kind)
    try:
        return jid(value)
    except (ValueError, AttributeError, TypeError):
        raise IdentityProjectionError(field, kind) from None

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
    if isinstance(error, ValueError) and len(error.args) == 1:
        value = error.args[0]
        if isinstance(value, str) and value in REASONS:
            reason = value
    event = {
        "kind": "capture_failure",
        "stage": stage if stage in STAGES else "unknown",
        "category": category,
        "reason": reason,
    }
    if isinstance(error, IdentityProjectionError):
        event.update(error.details)
    if isinstance(error, psycopg.Error) and error.sqlstate:
        event["sqlstate"] = error.sqlstate
    if isinstance(error, ExcludedWhatsAppSource):
        event["kind"] = "capture_ignored"
    return event


def report_failure(pipe, stage: str, error: Exception):
    # Reporting must not prevent durable account health from being updated.
    with suppress(BrokenPipeError, EOFError, OSError):
        pipe.send(failure_event(stage, error))


def health_event(counters, connected, last_staged, now):
    return {"kind": "capture_health", "connected": bool(connected),
            "counts": dict(counters),
            "last_live_staged_age_s": None if last_staged is None else max(0, int(now - last_staged))}
