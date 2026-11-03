"""Origin checks for independent learning; generated artifacts remain searchable."""

import re

LEGACY_PREPARATION_SIGNATURE = "Prepared automatically by your personal memory assistant."


def is_app_generated(row):
    if row.get("kind") != "document":
        return False
    payload = row.get("payload") or {}
    if payload.get("evidence_origin") == "app_generated":
        return True
    # Older preparation documents predate structured origin metadata. Require the
    # app's explicit footer, not titles such as 'Meeting Prep' on uploaded notes.
    tail = (payload.get("text") or "")[-1500:]
    return bool(re.search(r"(?m)^\*?" + re.escape(LEGACY_PREPARATION_SIGNATURE), tail))
