"""Bounded owner-scoped conversation context and controller completion checks."""

from datetime import datetime

from psycopg.types.json import Jsonb

from evidence_quotes import source_quote


def surrounding_messages(cur, owner, batch, now):
    anchors = [
        {"message_id": r["entity_id"], "ref": r["ref"]}
        for r in batch
        if r["kind"] == "message" and not r.get("skip")
    ]
    if not anchors:
        return []
    # Separate quotas keep earlier discussion from displacing all later replies.
    # Explicit replies are preferred even when the conversation is busy.
    cur.execute(
        """WITH anchors AS (
        SELECT m.*,x.ref FROM jsonb_to_recordset(%s) x(message_id TEXT,ref TEXT)
        JOIN messaging_messages m USING(message_id)
        JOIN messaging_accounts a USING(account_id)
        JOIN messaging_conversations c USING(account_id,conversation_id)
        WHERE a.user_email=%s AND NOT m.deleted AND NOT c.analysis_excluded AND c.kind<>'channel'
    ), neighbors AS (
        SELECT a.ref AS anchor_ref,n.* FROM anchors a CROSS JOIN LATERAL (
            (SELECT m.*, 'before' AS direction,
                row_number() OVER (ORDER BY (m.external_id=a.reply_id) DESC NULLS LAST,m.sent_at DESC,m.message_id) AS rank
             FROM messaging_messages m WHERE m.account_id=a.account_id AND m.conversation_id=a.conversation_id
                AND NOT m.deleted AND (m.sent_at,m.message_id)<(a.sent_at,a.message_id)
                AND (m.sent_at>=a.sent_at-INTERVAL '2 days' OR m.external_id=a.reply_id)
                AND m.sent_at<=%s ORDER BY (m.external_id=a.reply_id) DESC NULLS LAST,m.sent_at DESC,m.message_id LIMIT 6)
            UNION ALL
            (SELECT m.*, 'after' AS direction,
                row_number() OVER (ORDER BY (m.reply_id=a.external_id) DESC NULLS LAST,m.sent_at,m.message_id) AS rank
             FROM messaging_messages m WHERE m.account_id=a.account_id AND m.conversation_id=a.conversation_id
                AND NOT m.deleted AND (m.sent_at,m.message_id)>(a.sent_at,a.message_id)
                AND (m.sent_at<=a.sent_at+INTERVAL '7 days' OR m.reply_id=a.external_id)
                AND m.sent_at<=%s ORDER BY (m.reply_id=a.external_id) DESC NULLS LAST,m.sent_at,m.message_id LIMIT 12)
        ) n
    ) SELECT message_id,left(body,1200) AS body,md5(body) AS body_hash,sent_at,sender_identity_id,outgoing,
        conversation_id,reply_id,forwarded,anchor_ref,direction
    FROM neighbors ORDER BY rank,anchor_ref,direction,message_id LIMIT 60""",
        (Jsonb(anchors), owner, now, now),
    )
    return [dict(row) for row in cur.fetchall()]


def request_bounds(rows):
    stamps, conversations = [], set()
    for row in rows:
        payload = row.get("payload") or {}
        try:
            stamp = datetime.fromisoformat(payload.get("sent_at", ""))
            if stamp.tzinfo is not None:
                stamps.append(stamp)
        except (ValueError, TypeError):
            pass
        if payload.get("conversation_id"):
            conversations.add(payload["conversation_id"])
    return (max(stamps) if stamps else None), conversations


def proposal_requested_at(cur, owner, proposal, sources=None):
    ids = [e["source_id"] for e in proposal.get("evidence", []) if e.get("source_id")]
    if sources is None:
        cur.execute(
            "SELECT source_id,payload FROM intelligence_sources WHERE source_id=ANY(%s) AND (owner_email IS NULL OR owner_email=%s)",
            (ids, owner),
        )
        sources = cur.fetchall()
    return (
        request_bounds([r for r in sources if r["source_id"] in ids])[0] or proposal["created_at"]
    )


def actor_matches(cur, task, message):
    kind, assignee = task["assignee_kind"], task.get("assignee_ref")
    if kind == "user":
        return bool(message.get("outgoing"))
    if kind == "identity":
        return assignee == message["sender_identity_id"]
    if kind == "contact":
        cur.execute(
            "SELECT contact_id FROM messaging_identities WHERE identity_id=%s FOR SHARE",
            (message["sender_identity_id"],),
        )
        actor = cur.fetchone()
        return bool(actor and actor["contact_id"] == assignee)
    return False


def completion_messages(cur, task, batch, context):
    ids = {e["source_id"] for e in task["evidence"]}
    requested, conversations = request_bounds([r for r in batch if r["source_id"] in ids])
    if not requested:
        return []
    return [
        m
        for m in context.get("lookback", [])
        if m["conversation_id"] in conversations
        and m["sent_at"] > requested
        and not m.get("forwarded", True)
        and actor_matches(cur, task, m)
    ]


def resolution_is_current(cur, owner, finding, batch):
    """A model verdict alone never suppresses a task: recheck its exact evidence."""
    resolution = finding.get("verified_resolution")
    if finding["kind"] != "task" or not resolution:
        return False
    cur.execute(
        """SELECT m.*,md5(m.body) AS body_hash FROM messaging_messages m JOIN messaging_accounts a USING(account_id)
        JOIN messaging_conversations c USING(account_id,conversation_id)
        WHERE m.message_id=%s AND a.user_email=%s AND NOT m.deleted
          AND NOT c.analysis_excluded AND c.kind<>'channel' FOR SHARE OF m,c""",
        (resolution["message_id"], owner),
    )
    message = cur.fetchone()
    if not message:
        return False
    requested, conversations = request_bounds(
        [r for r in batch if r["source_id"] in {e["source_id"] for e in finding["evidence"]}]
    )
    return bool(
        requested
        and message["sent_at"] > requested
        and message["body_hash"] == resolution["body_hash"]
        and message["conversation_id"] in conversations
        and not message["forwarded"]
        and actor_matches(cur, finding, message)
        and source_quote(resolution["quote"], message["body"])
    )
