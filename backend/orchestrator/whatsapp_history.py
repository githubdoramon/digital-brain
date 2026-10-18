"""Bounded primary-phone history requests with durable per-chat progress."""

from __future__ import annotations

from enum import Enum, IntEnum
from uuid import uuid4

from db import get_conn
from messaging_history import history_cutoff
from messaging_normalization import jid, present


class HistoryState(str, Enum):
    READY = "ready"
    WAITING = "waiting"
    COMPLETE = "complete"
    STALLED = "stalled"


class TransferEnd(IntEnum):
    EXHAUSTED = 1
    MORE_AVAILABLE = 2
    UNAVAILABLE = 3


REQUEST_COUNT = 100
MAX_ATTEMPTS = 3


def _eligible(cur, account_id: str, generation: int) -> bool:
    cur.execute(
        """SELECT account_id FROM messaging_accounts WHERE account_id = %s
        AND provider = 'whatsapp' AND enabled AND generation = %s FOR UPDATE""",
        (account_id, generation),
    )
    return cur.fetchone() is not None


def _observe(
    cur,
    account_id: str,
    chat: str,
    message_id: str,
    outgoing: bool,
    stamp: int,
    cutoff_ms: int,
    *,
    terminal: TransferEnd | None = None,
    on_demand: bool = False,
):
    cur.execute(
        "SELECT * FROM whatsapp_history_cursors WHERE account_id = %s AND external_chat_id = %s",
        (account_id, chat),
    )
    old = cur.fetchone()
    seen = old["seen_anchor_ids"] if old else []
    progress = (
        not old
        or stamp < old["oldest_timestamp_ms"]
        or (stamp == old["oldest_timestamp_ms"] and on_demand and message_id not in seen)
    )
    if old and not progress:
        if terminal is None and stamp >= cutoff_ms:
            return  # Duplicate/recent chunks cannot reset retries or postpone timeout.
        stamp, message_id, outgoing = (
            old["oldest_timestamp_ms"],
            old["oldest_message_id"],
            old["oldest_from_me"],
        )
    complete = stamp < cutoff_ms or terminal == TransferEnd.EXHAUSTED
    state = (
        HistoryState.COMPLETE
        if complete
        else HistoryState.STALLED
        if terminal == TransferEnd.UNAVAILABLE
        else HistoryState.READY
    )
    reason = (
        "cutoff_reached"
        if stamp < cutoff_ms
        else "source_exhausted"
        if terminal == TransferEnd.EXHAUSTED
        else "source_unavailable"
        if terminal == TransferEnd.UNAVAILABLE
        else None
    )
    seen = [*seen, message_id][-128:]
    cur.execute(
        """INSERT INTO whatsapp_history_cursors
        (account_id, external_chat_id, oldest_message_id, oldest_from_me, oldest_timestamp_ms, seen_anchor_ids, state, reason)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (account_id, external_chat_id) DO UPDATE SET
            oldest_message_id = EXCLUDED.oldest_message_id, oldest_from_me = EXCLUDED.oldest_from_me,
            oldest_timestamp_ms = EXCLUDED.oldest_timestamp_ms, seen_anchor_ids = EXCLUDED.seen_anchor_ids,
            state = EXCLUDED.state, reason = EXCLUDED.reason, attempts = 0,
            request_token = NULL, next_attempt_at = NOW() + INTERVAL '10 seconds'""",
        (account_id, chat, message_id, outgoing, stamp, seen, state.value, reason),
    )


def observe_history(account_id: str, generation: int, proto):
    """Call only after every accepted projection in the chunk has committed."""
    cutoff_ms = int(history_cutoff().timestamp() * 1000)
    on_demand = present(proto, "syncType") and proto.syncType == 6
    with get_conn() as conn, conn.cursor() as cur:
        if not _eligible(cur, account_id, generation):
            return
        for conversation in proto.conversations:
            try:
                chat = jid(conversation.id)
                if not chat:
                    continue
            except ValueError:
                continue
            anchors = [
                (
                    int(item.message.messageTimestamp) * 1000,
                    item.message.key.id,
                    bool(item.message.key.fromMe),
                )
                for item in conversation.messages
                if item.message.messageTimestamp and item.message.key.id
            ]
            terminal = None
            if present(conversation, "endOfHistoryTransferType"):
                value = conversation.endOfHistoryTransferType
                if value in (TransferEnd.EXHAUSTED, TransferEnd.UNAVAILABLE):
                    terminal = TransferEnd(value)
            if anchors:
                cur.execute(
                    "SELECT oldest_timestamp_ms, seen_anchor_ids FROM whatsapp_history_cursors WHERE account_id = %s AND external_chat_id = %s",
                    (account_id, chat),
                )
                current = cur.fetchone()
                fresh = [
                    item
                    for item in anchors
                    if current
                    and (
                        item[0] < current["oldest_timestamp_ms"]
                        or (
                            on_demand
                            and item[0] == current["oldest_timestamp_ms"]
                            and item[1] not in current["seen_anchor_ids"]
                        )
                    )
                ]
                stamp, key, outgoing = min(fresh or anchors)
                _observe(
                    cur,
                    account_id,
                    chat,
                    key,
                    outgoing,
                    stamp,
                    cutoff_ms,
                    terminal=terminal,
                    on_demand=on_demand,
                )
            elif terminal:
                cur.execute(
                    """UPDATE whatsapp_history_cursors SET state = %s, reason = %s,
                    request_token = NULL WHERE account_id = %s AND external_chat_id = %s""",
                    (
                        HistoryState.COMPLETE.value
                        if terminal == TransferEnd.EXHAUSTED
                        else HistoryState.STALLED.value,
                        "source_exhausted"
                        if terminal == TransferEnd.EXHAUSTED
                        else "source_unavailable",
                        account_id,
                        chat,
                    ),
                )
        conn.commit()


def capture_history(account_id: str, generation: int, proto, self_jid: str):
    from messaging_archive import stage_records
    from messaging_normalization import MAX_BATCH, history_records

    batch = []
    errors = []
    for record in history_records(
        proto, self_jid, on_error=lambda: errors.append(True), cutoff=history_cutoff()
    ):
        batch.append(record)
        if len(batch) == MAX_BATCH:
            stage_records(account_id, generation, batch)
            batch = []
    if batch:
        stage_records(account_id, generation, batch)
    if errors:
        raise ValueError("history_incomplete")
    observe_history(account_id, generation, proto)


def claim_request(account_id: str, generation: int) -> dict | None:
    cutoff_ms = int(history_cutoff().timestamp() * 1000)
    with get_conn() as conn, conn.cursor() as cur:
        if not _eligible(cur, account_id, generation):
            return None
        # Also seed existing sessions/chats when no new bootstrap callback arrives.
        cur.execute(
            """INSERT INTO whatsapp_history_cursors
            (account_id, external_chat_id, oldest_message_id, oldest_from_me, oldest_timestamp_ms, seen_anchor_ids)
            SELECT DISTINCT ON (c.external_id) m.account_id, c.external_id, m.external_id,
                m.outgoing, (extract(epoch FROM m.sent_at) * 1000)::bigint, ARRAY[m.external_id]
            FROM messaging_messages m JOIN messaging_conversations c USING(conversation_id)
            WHERE m.account_id = %s AND NOT m.deleted
            ORDER BY c.external_id, m.sent_at, m.message_id ON CONFLICT DO NOTHING""",
            (account_id,),
        )
        cur.execute(
            """UPDATE whatsapp_history_cursors SET state = 'complete',
            reason = 'cutoff_reached', request_token = NULL WHERE account_id = %s
            AND oldest_timestamp_ms < %s AND state IN ('ready', 'waiting')""",
            (account_id, cutoff_ms),
        )
        cur.execute(
            """UPDATE whatsapp_history_cursors SET state = 'stalled', reason = 'response_timeout',
            request_token = NULL WHERE account_id = %s AND state IN ('ready', 'waiting')
            AND attempts >= %s AND next_attempt_at <= NOW()""",
            (account_id, MAX_ATTEMPTS),
        )
        # One outstanding phone request per account; no unbounded per-chat fan-out.
        cur.execute(
            "SELECT 1 FROM whatsapp_history_cursors WHERE account_id = %s AND state = 'waiting' AND next_attempt_at > NOW()",
            (account_id,),
        )
        if cur.fetchone():
            conn.commit()
            return None
        cur.execute(
            """SELECT * FROM whatsapp_history_cursors WHERE account_id = %s
            AND state IN ('ready', 'waiting') AND attempts < %s AND next_attempt_at <= NOW()
            ORDER BY next_attempt_at, external_chat_id LIMIT 1 FOR UPDATE""",
            (account_id, MAX_ATTEMPTS),
        )
        row = cur.fetchone()
        if row:
            row["request_token"] = uuid4().hex
            cur.execute(
                """UPDATE whatsapp_history_cursors SET state = 'waiting', attempts = attempts + 1,
                request_token = %s, request_id = NULL, reason = NULL,
                next_attempt_at = NOW() + INTERVAL '2 minutes'
                WHERE account_id = %s AND external_chat_id = %s""",
                (row["request_token"], account_id, row["external_chat_id"]),
            )
        conn.commit()
        return row


def finish_send(account_id: str, generation: int, request: dict, request_id: str | None):
    with get_conn() as conn, conn.cursor() as cur:
        if not _eligible(cur, account_id, generation):
            return
        if request_id:
            cur.execute(
                """UPDATE whatsapp_history_cursors SET request_id = %s
                WHERE account_id = %s AND external_chat_id = %s AND request_token = %s AND state = 'waiting'""",
                (request_id, account_id, request["external_chat_id"], request["request_token"]),
            )
        else:
            cur.execute(
                """UPDATE whatsapp_history_cursors SET state = 'ready', reason = 'request_failed',
                next_attempt_at = NOW() + make_interval(secs => 60 * attempts)
                WHERE account_id = %s AND external_chat_id = %s AND request_token = %s AND state = 'waiting'""",
                (account_id, request["external_chat_id"], request["request_token"]),
            )
        conn.commit()


async def request_once(client, account_id: str, generation: int) -> bool:
    import asyncio

    from tryx.types import JID

    request = await asyncio.to_thread(claim_request, account_id, generation)
    if not request:
        return False
    request_id = None
    try:
        user, server = jid(request["external_chat_id"]).split("@")
        request_id = await asyncio.wait_for(
            client.advanced.fetch_message_history(
                JID(user, server),
                request["oldest_message_id"],
                request["oldest_from_me"],
                request["oldest_timestamp_ms"],
                REQUEST_COUNT,
            ),
            timeout=30,
        )
    finally:
        await asyncio.to_thread(finish_send, account_id, generation, request, request_id)
    return True


def retry(owner: str):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """UPDATE whatsapp_history_cursors h SET state = 'ready', attempts = 0,
            request_token = NULL, next_attempt_at = NOW(), reason = NULL
            FROM messaging_accounts a WHERE h.account_id = a.account_id
            AND a.user_email = %s AND a.provider = 'whatsapp' AND h.state = 'stalled'""",
            (owner,),
        )
        conn.commit()


def counts(owner: str) -> dict:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT count(*) FILTER (WHERE h.state IN ('ready', 'waiting')) AS history_pending,
            count(*) FILTER (WHERE h.state = 'stalled') AS history_stalled
            FROM whatsapp_history_cursors h JOIN messaging_accounts a USING(account_id)
            WHERE a.user_email = %s AND a.provider = 'whatsapp'""",
            (owner,),
        )
        return cur.fetchone()
