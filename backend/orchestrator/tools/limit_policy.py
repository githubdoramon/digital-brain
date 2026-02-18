"""Shared policy helpers for handling unbounded result requests."""

from __future__ import annotations

import re

_ALL_RESULTS_PATTERN = re.compile(r"\b(all|everyone|everybody|entire|whole)\b", re.IGNORECASE)


def wants_all_results(text: str) -> bool:
    """Return True when user wording explicitly requests all results."""
    return bool(_ALL_RESULTS_PATTERN.search(text or ""))
