"""Bounded, conversation-scoped historical learning batches with per-part receipts."""

from datetime import datetime

import intelligence

MAX_PARTS = 12
MAX_TEXT_CHARS = 16000
MAX_GAP_SECONDS = 24 * 60 * 60
ELIGIBLE_SQL = """((j.status IN ('pending','failed') AND j.attempts<3 AND j.next_attempt_at<=NOW())
    OR (j.status='running' AND (j.lease_until IS NULL OR j.lease_until<NOW())))"""


def _date(payload):
    try:
        return datetime.fromisoformat(payload.get("sent_at", "").replace("Z", "+00:00"))
    except (ValueError, TypeError, AttributeError):
        return None


def select(cur, anchor):
    """Called under the owner's scheduling lock; never consume another conversation."""
    if not anchor["historical"]:
        return [anchor]
    cur.execute("SELECT * FROM intelligence_sources WHERE source_id=%s", (anchor["source_id"],))
    source = cur.fetchone()
    if source["kind"] not in {"chat", "message"}:
        return [anchor]
    field = "thread_id" if source["kind"] == "chat" else "conversation_id"
    conversation = source["payload"].get(field)
    if not conversation:
        return [anchor]
    cur.execute(
        f"""SELECT j.*,s.payload FROM knowledge_learning j JOIN intelligence_sources s USING(source_id)
        WHERE j.user_email=%s AND j.historical AND s.kind=%s AND s.owner_email=%s
        AND s.payload->>%s=%s AND s.duplicate_of_source_id IS NULL
        AND (j.source_id,j.part)>=(%s,%s)
        AND {ELIGIBLE_SQL}
        ORDER BY j.source_id,j.part FOR UPDATE OF j SKIP LOCKED LIMIT %s""",
        (
            anchor["user_email"],
            source["kind"],
            anchor["user_email"],
            field,
            conversation,
            anchor["source_id"],
            anchor["part"],
            MAX_PARTS,
        ),
    )
    selected, size = [], 0
    batch_limit = min(MAX_PARTS, anchor.get("batch_limit", MAX_PARTS))
    anchor_date = _date(source["payload"])
    for row in cur.fetchall():
        batch_limit = min(batch_limit, row.get("batch_limit", MAX_PARTS))
        if len(selected) >= batch_limit:
            break
        date = _date(row["payload"])
        if anchor_date and date:
            try:
                if abs((date - anchor_date).total_seconds()) > MAX_GAP_SECONDS:
                    break
            except TypeError:  # Mixed timezone certainty is not a safe episode boundary.
                break
        text = row["payload"].get("text") or ""
        part = row["part"]
        length = len(
            text[part * intelligence.CHUNK_SIZE : (part + 1) * intelligence.CHUNK_SIZE + 400]
        )
        if size + length > MAX_TEXT_CHARS:
            break
        selected.append({key: value for key, value in row.items() if key != "payload"})
        size += length
    return selected or [anchor]


def sources(jobs, load):
    """Every claimed part stays primary; share at most six neighboring context parts."""
    primary, supporting = {}, {}
    for job in jobs:
        for row in load(job):
            target = supporting if row["supporting"] else primary
            target[row["ref"]] = row
    neighbors = [row for ref, row in supporting.items() if ref not in primary]
    # Prefer context immediately around the claimed range over distant neighbors.
    low, high = (
        min(r["source_id"] for r in primary.values()),
        max(r["source_id"] for r in primary.values()),
    )
    neighbors.sort(key=lambda row: min(abs(row["source_id"] - low), abs(row["source_id"] - high)))
    return sorted(
        [*primary.values(), *neighbors[:6]], key=lambda row: (row["source_id"], row["part"])
    )
