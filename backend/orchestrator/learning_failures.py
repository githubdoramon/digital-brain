"""Bounded local rejection records, retained independently of rotating logs."""

import json

from observability.diagnostics import redacted_json


def rejection_details(error, batch=()):
    original = error.__cause__ or error
    data = {
        "error_type": type(error).__name__,
        "details": getattr(original, "details", {}),
        "source_refs": [f"{row['source_id']}:{row['part']}" for row in batch],
    }
    encoded = redacted_json(data)
    if len(encoded) > 12000:
        return {"truncated": True, "preview": encoded[:11000], "original_chars": len(encoded)}
    return json.loads(encoded)
