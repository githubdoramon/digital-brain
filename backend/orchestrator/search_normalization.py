from __future__ import annotations

import unicodedata
from typing import Iterable, List


def _strip_accents(text: str) -> str:
    normalized = unicodedata.normalize("NFD", text)
    return "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")


def normalize_search_text(text: str) -> str:
    """Normalize input for case- and accent-insensitive search."""
    if text is None:
        return ""
    cleaned = _strip_accents(str(text))
    compact = " ".join(cleaned.split())
    return compact.casefold()


def normalize_search_list(values: Iterable[str] | None) -> List[str]:
    normalized: List[str] = []
    seen = set()
    for value in values or []:
        candidate = normalize_search_text(value)
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        normalized.append(candidate)
    return normalized

