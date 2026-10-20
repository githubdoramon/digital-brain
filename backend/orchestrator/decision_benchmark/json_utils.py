from __future__ import annotations

import json
from typing import Any


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def _reject_constant(_value):
    raise ValueError("nonfinite_json_number")


def decode_json(text: str) -> Any:
    """Reject ambiguous duplicate keys and non-JSON numeric constants."""
    return json.loads(text, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
