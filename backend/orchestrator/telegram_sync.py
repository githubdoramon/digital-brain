"""Durable history cursors and safe, account-scoped Telegram deletion lookup."""

from __future__ import annotations

from datetime import datetime, timezone

from db import get_conn
from messaging_archive import stage_records
from messaging_normalization import Origin, Provider, RecordKind, stable_id


def cursor(account_id: str, chat: str) -> dict:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM messaging_sync_cursors WHERE account_id = %s AND external_chat_id = %s",
            (account_id, chat),
        )
        return cur.fetchone() or {"oldest_id": 0, "newest_id": 0, "backfill_done": False}


def checkpoint(
    account_id: str,
    generation: int,
    chat: str,
    *,
    oldest: int = 0,
    newest: int = 0,
    done: bool = False,
):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO messaging_sync_cursors(account_id, external_chat_id, oldest_id, newest_id, backfill_done)
            SELECT account_id, %s, %s, %s, %s FROM messaging_accounts
            WHERE account_id = %s AND generation = %s AND enabled AND provider = 'telegram'
            ON CONFLICT (account_id, external_chat_id) DO UPDATE SET
                oldest_id = CASE WHEN EXCLUDED.oldest_id = 0 THEN messaging_sync_cursors.oldest_id
                    WHEN messaging_sync_cursors.oldest_id = 0 THEN EXCLUDED.oldest_id
                    ELSE LEAST(EXCLUDED.oldest_id, messaging_sync_cursors.oldest_id) END,
                newest_id = GREATEST(EXCLUDED.newest_id, messaging_sync_cursors.newest_id),
                backfill_done = messaging_sync_cursors.backfill_done OR EXCLUDED.backfill_done""",
            (chat, oldest, newest, done, account_id, generation),
        )
        conn.commit()


def stage_deletions(account_id: str, generation: int, ids: list[int], chat: str | None):
    # Non-channel Telegram message IDs are account-wide. Channel IDs are scoped
    # to their chat; a chatless update must never revoke a channel's same ID.
    ids = [str(i) for i in ids[:100]]
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT c.external_id AS chat, i.external_id AS sender, m.external_id AS id,
            m.sent_at, m.outgoing, a.self_jid FROM messaging_messages m
            JOIN messaging_accounts a USING(account_id)
            JOIN messaging_conversations c ON c.conversation_id = m.conversation_id
            JOIN messaging_identities i ON i.identity_id = m.sender_identity_id
            WHERE m.account_id = %s AND a.provider = %s AND m.external_id = ANY(%s)
                AND ((%s::text IS NOT NULL AND c.external_id = %s)
                    OR (%s::text IS NULL AND c.external_id NOT LIKE '%%@telegram.channel'))""",
            (account_id, Provider.TELEGRAM.value, ids, chat, chat, chat),
        )
        rows = cur.fetchall()
        cur.execute(
            """SELECT payload FROM messaging_inbox WHERE account_id = %s AND processed_at IS NULL
            AND payload->>'id' = ANY(%s)
            AND ((%s::text IS NOT NULL AND payload->>'chat' = %s)
                OR (%s::text IS NULL AND payload->>'chat' NOT LIKE '%%@telegram.channel'))""",
            (account_id, ids, chat, chat, chat),
        )
        rows.extend(row["payload"] for row in cur.fetchall())
    now = datetime.now(timezone.utc).isoformat()
    records = {}
    for row in rows:
        key = stable_id(row["chat"], row["sender"], row["id"])
        records[key] = {
            "kind": RecordKind.REVOKE.value,
            "origin": Origin.LIVE.value,
            "chat": row["chat"],
            "sender": row["sender"],
            "id": row["id"],
            "sent_at": str(row["sent_at"]),
            "effective_at": now,
            "outgoing": row["outgoing"],
            "name": "",
            "title": "",
            "self_jid": row["self_jid"],
            "body": "",
            "attachments": [],
            "reply_id": None,
        }
    values = list(records.values())
    for start in range(0, len(values), 100):
        stage_records(account_id, generation, values[start : start + 100])
