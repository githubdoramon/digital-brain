"""Owner-scoped external archive and transactional, replay-safe inbox draining."""

from __future__ import annotations

import json
from datetime import datetime
from enum import Enum

from psycopg.types.json import Jsonb

from db import get_conn
from messaging_history import history_cutoff, within_history_window
from messaging_normalization import (
    MAX_BATCH,
    MAX_TEXT,
    ConnectionState,
    Origin,
    Provider,
    RecordKind,
    group_identity,
    jid,
    stable_id,
)


class Context(str, Enum):
    WORK = "work"
    PERSONAL = "personal"
    MIXED = "mixed"
    UNCLASSIFIED = "unclassified"


def account(owner: str, provider: Provider = Provider.WHATSAPP) -> dict:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM messaging_accounts WHERE user_email = %s AND provider = %s",
            (owner, provider.value),
        )
        row = cur.fetchone()
        return row or {"enabled": False, "state": "disconnected", "context": "mixed"}


def set_connection(owner: str, enabled: bool, provider: Provider = Provider.WHATSAPP) -> dict:
    account_id = stable_id(owner, provider.value)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO messaging_accounts(account_id, user_email, provider, enabled, state)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (user_email, provider) DO UPDATE SET
                enabled = EXCLUDED.enabled,
                generation = messaging_accounts.generation + 1,
                state = CASE WHEN EXCLUDED.enabled THEN 'connecting' ELSE 'disconnected' END,
                error_code = NULL
            RETURNING *
        """,
            (
                account_id,
                owner,
                provider.value,
                enabled,
                ConnectionState.CONNECTING.value if enabled else ConnectionState.DISCONNECTED.value,
            ),
        )
        row = cur.fetchone()
        conn.commit()
        return row


def set_context(
    owner: str,
    context: Context,
    conversation_id: str | None = None,
    provider: Provider = Provider.WHATSAPP,
):
    with get_conn() as conn, conn.cursor() as cur:
        if conversation_id:
            cur.execute(
                """
                UPDATE messaging_conversations c SET context = %s
                FROM messaging_accounts a WHERE c.account_id = a.account_id
                  AND a.user_email = %s AND c.conversation_id = %s AND a.provider = %s
            """,
                (context.value, owner, conversation_id, provider.value),
            )
        else:
            cur.execute(
                "UPDATE messaging_accounts SET context = %s WHERE user_email = %s AND provider = %s",
                (context.value, owner, provider.value),
            )
        if cur.rowcount != 1:
            raise LookupError("not_found")
        conn.commit()


def bind_self(account_id: str, generation: int, provider: Provider, external_id: str):
    """Pin the authenticated account before any capture or cursor can advance."""
    external_id = jid(external_id)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT self_jid FROM messaging_accounts WHERE account_id = %s AND generation = %s AND enabled AND provider = %s FOR UPDATE",
            (account_id, generation, provider.value),
        )
        row = cur.fetchone()
        if not row or (row["self_jid"] and row["self_jid"] != external_id):
            raise ValueError("account_mismatch")
        cur.execute(
            "UPDATE messaging_accounts SET self_jid = %s WHERE account_id = %s",
            (external_id, account_id),
        )
        conn.commit()


def runtime_state(
    account_id: str, generation: int, state: ConnectionState, error: str | None = None
):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """UPDATE messaging_accounts SET state = %s, error_code = %s,
            capture_failures = capture_failures + CASE WHEN %s IN ('capture_incomplete', 'history_incomplete') THEN 1 ELSE 0 END
            WHERE account_id = %s AND generation = %s AND enabled""",
            (ConnectionState(state).value, error, error, account_id, generation),
        )
        conn.commit()


def validate_record(record: dict) -> dict:
    """Rebuild the allow-list at the persistence boundary, including replay input."""
    result = {
        key: record[key]
        for key in (
            "kind",
            "origin",
            "chat",
            "sender",
            "id",
            "sent_at",
            "effective_at",
            "outgoing",
            "name",
            "title",
            "self_jid",
            "body",
            "attachments",
            "reply_id",
        )
    }
    result["conversation_kind"] = record.get("conversation_kind")
    result["forwarded"] = bool(record.get("forwarded", True))
    if result["conversation_kind"] not in (None, "direct", "group", "channel"):
        raise ValueError("unsupported_conversation_kind")
    RecordKind(result["kind"])
    Origin(result["origin"])
    for key in ("chat", "sender", "self_jid"):
        result[key] = jid(result[key])
    if not result["chat"] or not result["sender"] or not result["id"]:
        raise ValueError("missing_message_identity")
    for key in ("sent_at", "effective_at"):
        parsed = datetime.fromisoformat(result[key])
        if parsed.tzinfo is None:
            raise ValueError("missing_timezone")
    if len(result["body"]) > MAX_TEXT or len(result["attachments"]) > 5:
        raise ValueError("record_too_large")
    result["attachments"] = [
        {
            "type": str(item["type"])[:32],
            "filename": str(item.get("filename", ""))[:512],
            "size": max(0, int(item.get("size", 0))),
        }
        for item in result["attachments"]
    ]
    return result


def stage_records(account_id: str, generation: int, records: list[dict]) -> int:
    if len(records) > MAX_BATCH:
        raise ValueError("batch_too_large")
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT generation, enabled, self_jid, provider FROM messaging_accounts WHERE account_id = %s FOR UPDATE",
            (account_id,),
        )
        row = cur.fetchone()
        if not row or not row["enabled"] or row["generation"] != generation:
            return 0  # Late native callbacks cannot repopulate a stopped connection.
        cutoff = history_cutoff()
        records = [validate_record(record) for record in records]
        records = [record for record in records if within_history_window(record, cutoff)]
        for record in records:
            for key in ("chat", "sender", "self_jid"):
                if record[key] and ("@telegram." in record[key]) != (
                    row["provider"] == Provider.TELEGRAM.value
                ):
                    raise ValueError("provider_mismatch")
        if row["self_jid"] and any(
            r["self_jid"] and r["self_jid"] != row["self_jid"] for r in records
        ):
            raise ValueError("account_mismatch")
        own_ids = {r["self_jid"] for r in records if r["self_jid"]}
        if len(own_ids) > 1:
            raise ValueError("account_mismatch")
        if own_ids and not row["self_jid"]:
            cur.execute(
                "UPDATE messaging_accounts SET self_jid = %s WHERE account_id = %s",
                (next(iter(own_ids)), account_id),
            )
        cur.execute(
            "SELECT count(*) AS n, COALESCE(sum(pg_column_size(payload)), 0) AS bytes FROM messaging_inbox WHERE account_id = %s AND processed_at IS NULL",
            (account_id,),
        )
        size = cur.fetchone()
        if (
            size["n"] + len(records) > 50000
            or size["bytes"] + sum(len(json.dumps(r).encode()) for r in records) > 128 * 1024 * 1024
        ):
            raise ValueError("queue_full")
        for record in records:
            digest = stable_id(json.dumps(record, sort_keys=True))
            cur.execute(
                """INSERT INTO messaging_inbox(account_id, generation, record_hash, payload)
                VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING""",
                (account_id, generation, digest, Jsonb(record)),
            )
        if records:
            cur.execute(
                "UPDATE messaging_accounts SET last_capture_at = NOW() WHERE account_id = %s",
                (account_id,),
            )
        conn.commit()
    return len(records)


def _apply(cur, account_id: str, record: dict):
    record = validate_record(record)
    conversation_id = stable_id(account_id, record["chat"])
    identity_id = stable_id(account_id, record["sender"])
    message_id = stable_id(account_id, record["chat"], record["sender"], record["id"])
    is_mutation = record["kind"] != RecordKind.MESSAGE.value
    name = "" if is_mutation else record["name"]
    cur.execute(
        """INSERT INTO messaging_identities
        (identity_id, account_id, external_id, display_name, is_self)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (identity_id) DO UPDATE SET
            display_name = COALESCE(NULLIF(messaging_identities.display_name, ''), EXCLUDED.display_name),
            is_self = messaging_identities.is_self OR EXCLUDED.is_self""",
        (
            identity_id,
            account_id,
            record["sender"],
            name,
            record["sender"] == record["self_jid"],
        ),
    )
    cur.execute(
        """INSERT INTO messaging_conversations
        (conversation_id, account_id, external_id, title, kind) VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (conversation_id) DO UPDATE SET
        title = COALESCE(NULLIF(messaging_conversations.title, ''), EXCLUDED.title),
        kind = CASE WHEN %s THEN messaging_conversations.kind ELSE EXCLUDED.kind END""",
        (
            conversation_id,
            account_id,
            record["chat"],
            record["title"],
            record.get("conversation_kind")
            or ("group" if group_identity(record["chat"]) else "direct"),
            is_mutation,
        ),
    )
    if not group_identity(record["chat"]):
        cur.execute(
            """INSERT INTO messaging_identities
            (identity_id, account_id, external_id, display_name)
            VALUES (%s, %s, %s, %s) ON CONFLICT (identity_id) DO NOTHING""",
            (stable_id(account_id, record["chat"]), account_id, record["chat"], record["title"]),
        )
    if record["self_jid"]:
        cur.execute(
            "UPDATE messaging_accounts SET self_jid = %s WHERE account_id = %s",
            (record["self_jid"], account_id),
        )
    if not is_mutation:
        cur.execute(
            """INSERT INTO messaging_memberships
            (account_id, conversation_id, identity_id, first_observed_at, last_observed_at)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (conversation_id, identity_id) DO UPDATE SET
                first_observed_at = LEAST(messaging_memberships.first_observed_at, EXCLUDED.first_observed_at),
                last_observed_at = GREATEST(messaging_memberships.last_observed_at, EXCLUDED.last_observed_at)""",
            (account_id, conversation_id, identity_id, record["sent_at"], record["sent_at"]),
        )
    priority = 2 if record["kind"] == RecordKind.REVOKE.value else int(is_mutation)
    cur.execute(
        """INSERT INTO messaging_messages
        (message_id, account_id, conversation_id, sender_identity_id, external_id,
         sent_at, effective_at, revision_priority, body, deleted, outgoing, reply_id, attachments, forwarded)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (message_id) DO UPDATE SET
            body = CASE WHEN EXCLUDED.deleted THEN messaging_messages.body ELSE EXCLUDED.body END,
            deleted = EXCLUDED.deleted,
            effective_at = EXCLUDED.effective_at, revision_priority = EXCLUDED.revision_priority,
            reply_id = EXCLUDED.reply_id,
            forwarded = CASE WHEN EXCLUDED.deleted THEN messaging_messages.forwarded ELSE EXCLUDED.forwarded END,
            attachments = CASE WHEN EXCLUDED.deleted THEN messaging_messages.attachments ELSE EXCLUDED.attachments END
        WHERE (EXCLUDED.effective_at, EXCLUDED.revision_priority)
            > (messaging_messages.effective_at, messaging_messages.revision_priority)
          AND (EXCLUDED.revision_priority > 0 OR messaging_messages.revision_priority = 0)
          AND (NOT messaging_messages.deleted OR EXCLUDED.deleted)
    """,
        (
            message_id,
            account_id,
            conversation_id,
            identity_id,
            record["id"],
            record["sent_at"],
            record["effective_at"],
            priority,
            record["body"],
            record["kind"] == RecordKind.REVOKE.value,
            record["outgoing"],
            record["reply_id"],
            Jsonb(record["attachments"]),
            record["forwarded"],
        ),
    )
    if not is_mutation:
        cur.execute(
            """UPDATE messaging_messages SET sent_at = LEAST(sent_at, %s)
            WHERE message_id = %s""",
            (record["sent_at"], message_id),
        )
        cur.execute(
            """UPDATE messaging_messages SET body = %s, attachments = %s
            WHERE message_id = %s AND deleted AND body = '' """,
            (record["body"], Jsonb(record["attachments"]), message_id),
        )
    revision_hash = stable_id(
        record["kind"],
        record["effective_at"],
        record["body"],
        json.dumps(record["attachments"], sort_keys=True),
    )
    cur.execute(
        """INSERT INTO messaging_revisions
        (message_id, revision_hash, kind, source_time, body, attachments, origin)
        VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING""",
        (
            message_id,
            revision_hash,
            record["kind"],
            record["effective_at"],
            record["body"],
            Jsonb(record["attachments"]),
            record["origin"],
        ),
    )


def drain_once() -> int:
    """A committed record is its checkpoint; SKIP LOCKED allows safe worker overlap."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("""SELECT * FROM messaging_inbox WHERE processed_at IS NULL
            AND attempts < 5 AND next_attempt_at <= NOW()
            ORDER BY (payload->>'origin' = 'live') DESC, inbox_id
            LIMIT 50 FOR UPDATE SKIP LOCKED""")
        rows = cur.fetchall()
        for row in rows:
            # A savepoint permits one malformed item to fail without losing peers.
            try:
                with conn.transaction():
                    if within_history_window(row["payload"]):
                        _apply(cur, row["account_id"], row["payload"])
                    cur.execute(
                        """UPDATE messaging_inbox SET processed_at = NOW(),
                        payload = '{}'::jsonb WHERE inbox_id = %s""",
                        (row["inbox_id"],),
                    )
            except Exception:
                cur.execute(
                    """UPDATE messaging_inbox SET attempts = attempts + 1,
                    next_attempt_at = NOW() + INTERVAL '1 minute' WHERE inbox_id = %s""",
                    (row["inbox_id"],),
                )
        conn.commit()
        return len(rows)


def identities(
    owner: str, contact_id: str | None = None, provider: Provider = Provider.WHATSAPP
) -> list[dict]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT i.*, c.display_name AS contact_name,
            COALESCE((SELECT jsonb_agg(suggestion) FROM (
                SELECT p.contact_id, p.display_name FROM contacts p
                WHERE i.contact_id IS NULL AND i.external_id LIKE '%%@s.whatsapp.net'
                  AND EXISTS (SELECT 1 FROM unnest(p.phones) AS phone
                    WHERE regexp_replace(phone, '[^0-9]', '', 'g') = split_part(i.external_id, '@', 1))
                ORDER BY p.contact_id LIMIT 3) suggestion), '[]'::jsonb) AS suggested_contacts
            FROM messaging_identities i
            JOIN messaging_accounts a USING (account_id)
            LEFT JOIN contacts c USING (contact_id)
            WHERE a.user_email = %s AND a.provider = %s AND (%s::text IS NULL OR i.contact_id = %s)
            ORDER BY i.display_name, i.external_id LIMIT 500""",
            (owner, provider.value, contact_id, contact_id),
        )
        return cur.fetchall()


def link_identity(
    owner: str, identity_id: str, contact_id: str | None, provider: Provider = Provider.WHATSAPP
):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT i.contact_id FROM messaging_identities i
            JOIN messaging_accounts a USING(account_id)
            WHERE a.user_email = %s AND i.identity_id = %s AND a.provider = %s FOR UPDATE OF i""",
            (owner, identity_id, provider.value),
        )
        old = cur.fetchone()
        if not old:
            raise LookupError("not_found")
        if contact_id:
            cur.execute("SELECT contact_id FROM contacts WHERE contact_id = %s", (contact_id,))
            if not cur.fetchone():
                raise LookupError("contact_not_found")
        cur.execute(
            """UPDATE messaging_identities SET contact_id = %s, link_origin = %s
            WHERE identity_id = %s""",
            (contact_id, "manual" if contact_id else None, identity_id),
        )
        cur.execute(
            """INSERT INTO messaging_identity_links(identity_id, old_contact_id, new_contact_id)
            VALUES (%s, %s, %s)""",
            (identity_id, old["contact_id"], contact_id),
        )
        conn.commit()


def retry_pending(owner: str, provider: Provider = Provider.WHATSAPP):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """UPDATE messaging_inbox i SET attempts = 0, next_attempt_at = NOW()
            FROM messaging_accounts a WHERE i.account_id = a.account_id
            AND a.user_email = %s AND a.provider = %s AND i.processed_at IS NULL""",
            (owner, provider.value),
        )
        conn.commit()


def conversations(owner: str, provider: Provider = Provider.WHATSAPP) -> list[dict]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT c.*, COALESCE(c.context, a.context) AS effective_context,
            (SELECT count(*) FROM messaging_messages m WHERE m.conversation_id = c.conversation_id) AS message_count
            FROM messaging_conversations c JOIN messaging_accounts a USING(account_id)
            WHERE a.user_email = %s AND a.provider = %s ORDER BY c.title, c.external_id LIMIT 500""",
            (owner, provider.value),
        )
        return cur.fetchall()


def search(
    owner: str,
    query: str = "",
    *,
    conversation_id: str | None = None,
    contact_ids: list[str] | None = None,
    limit: int = 30,
    time_start: str | None = None,
    time_end: str | None = None,
    before_id: str | None = None,
    provider: Provider | None = None,
) -> list[dict]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT m.message_id, m.conversation_id, m.sent_at,
            m.body, m.deleted, m.outgoing, m.attachments, m.reply_id,
            i.display_name AS sender_name, i.external_id AS sender_external_id,
            i.contact_id, c.title AS conversation_title, c.external_id AS conversation_external_id, a.provider,
            COALESCE(c.context, a.context) AS context,
            CASE WHEN %s = '' THEN 0 ELSE
                ts_rank(m.body_tsv, websearch_to_tsquery('simple', %s)) END AS score
            FROM messaging_messages m JOIN messaging_accounts a USING(account_id)
            JOIN messaging_identities i ON i.identity_id = m.sender_identity_id
            JOIN messaging_conversations c ON c.conversation_id = m.conversation_id
            WHERE a.user_email = %s AND (%s::text IS NULL OR a.provider = %s)
              AND (%s = '' OR m.body_tsv @@ websearch_to_tsquery('simple', %s))
              AND (%s::text IS NULL OR m.conversation_id = %s)
              AND (%s::text[] = '{}'::text[] OR i.contact_id = ANY(%s)
                OR (c.kind = 'direct' AND EXISTS (
                    SELECT 1 FROM messaging_identities peer
                    WHERE peer.account_id = m.account_id AND peer.external_id = c.external_id
                      AND peer.contact_id = ANY(%s))))
              AND (%s::timestamptz IS NULL OR m.sent_at >= %s::timestamptz)
              AND (%s::timestamptz IS NULL OR m.sent_at <= %s::timestamptz)
              AND (%s::text IS NULL OR (m.sent_at, m.message_id) <
                (SELECT t.sent_at, t.message_id FROM messaging_messages t
                 WHERE t.message_id = %s AND t.account_id = m.account_id
                   AND t.conversation_id = m.conversation_id))
            ORDER BY score DESC, m.sent_at DESC, m.message_id DESC LIMIT %s""",
            (
                query,
                query,
                owner,
                provider.value if provider else None,
                provider.value if provider else None,
                query,
                query,
                conversation_id,
                conversation_id,
                contact_ids or [],
                contact_ids or [],
                contact_ids or [],
                time_start,
                time_start,
                time_end,
                time_end,
                before_id,
                before_id,
                max(1, min(limit, 100)),
            ),
        )
        return cur.fetchall()


def message_context(owner: str, message_id: str, provider: Provider | None = None) -> dict:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT m.conversation_id FROM messaging_messages m
            JOIN messaging_accounts a USING(account_id)
            WHERE a.user_email = %s AND m.message_id = %s AND (%s::text IS NULL OR a.provider = %s)""",
            (
                owner,
                message_id,
                provider.value if provider else None,
                provider.value if provider else None,
            ),
        )
        row = cur.fetchone()
        if not row:
            raise LookupError("not_found")
        # Bounded neighbors centered on the matched message, not newest messages.
        cur.execute(
            """WITH target AS (SELECT sent_at, message_id FROM messaging_messages
                WHERE message_id = %s), neighbors AS (
                (SELECT m.* FROM messaging_messages m, target t
                 WHERE m.conversation_id = %s AND (m.sent_at, m.message_id) <= (t.sent_at, t.message_id)
                 ORDER BY m.sent_at DESC, m.message_id DESC LIMIT 6)
                UNION ALL
                (SELECT m.* FROM messaging_messages m, target t
                 WHERE m.conversation_id = %s AND (m.sent_at, m.message_id) > (t.sent_at, t.message_id)
                 ORDER BY m.sent_at, m.message_id LIMIT 5))
            SELECT n.message_id, n.sent_at, n.body, n.deleted, n.attachments,
                n.outgoing, i.display_name AS sender_name, i.external_id AS sender_external_id
            FROM neighbors n JOIN messaging_identities i ON i.identity_id = n.sender_identity_id
            ORDER BY n.sent_at, n.message_id""",
            (message_id, row["conversation_id"], row["conversation_id"]),
        )
        return {
            "message_id": message_id,
            "conversation_id": row["conversation_id"],
            "messages": cur.fetchall(),
        }


def counts(owner: str, provider: Provider = Provider.WHATSAPP) -> dict:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT
            (SELECT count(*) FROM messaging_messages m WHERE m.account_id = a.account_id) AS messages,
            (SELECT count(*) FROM messaging_inbox i WHERE i.account_id = a.account_id AND processed_at IS NULL) AS pending,
            (SELECT count(*) FROM messaging_inbox i WHERE i.account_id = a.account_id AND processed_at IS NULL AND attempts >= 5) AS failed
            FROM messaging_accounts a WHERE a.user_email = %s AND a.provider = %s""",
            (owner, provider.value),
        )
        return cur.fetchone() or {"messages": 0, "pending": 0, "failed": 0}
