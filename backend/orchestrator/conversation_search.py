"""Shared, owner-scoped conversation retrieval and durable semantic indexing.

Only visible user/assistant text is embedded. Original messages remain the source
of truth; metadata is returned solely as bounded source references, never embedded.
"""

from __future__ import annotations

import hashlib
import json
import math
import threading
import time
from collections import OrderedDict
from typing import Any

import embeddings
from db import get_conn
from search_normalization import normalize_search_text

_QUERY_LOCK = threading.Lock()
_QUERY_CACHE: OrderedDict[tuple[str, str], tuple[float, list[float]]] = OrderedDict()
_QUERY_FAILURE_UNTIL = 0.0


def query_embedding(query: str) -> list[float] | None:
    """Bound interactive embedding latency and avoid hammering an unavailable provider."""
    global _QUERY_FAILURE_UNTIL
    key = (index_version(), query)
    with _QUERY_LOCK:
        cached = _QUERY_CACHE.get(key)
        if cached and cached[0] > time.monotonic():
            _QUERY_CACHE.move_to_end(key)
            return cached[1]
        if time.monotonic() < _QUERY_FAILURE_UNTIL:
            return None
    try:
        # Short search topics fit below the minimum adaptive input cap. This also
        # prevents context-length retries from multiplying the interactive timeout.
        topic = query.encode()[:600].decode("utf-8", errors="ignore")
        vector = embeddings.embed_text(topic, log_response_body=False, request_timeout=(0.5, 2.0))
        if len(vector) != 768 or not all(math.isfinite(v) for v in vector) or not any(vector):
            raise ValueError("Invalid conversation query embedding")
    except (embeddings.requests.RequestException, RuntimeError, ValueError):
        with _QUERY_LOCK:
            _QUERY_FAILURE_UNTIL = time.monotonic() + 15
        return None
    with _QUERY_LOCK:
        _QUERY_CACHE[key] = (time.monotonic() + 300, vector)
        _QUERY_CACHE.move_to_end(key)
        while len(_QUERY_CACHE) > 64:
            _QUERY_CACHE.popitem(last=False)
    return vector


def index_version() -> str:
    settings = f"conversation-v1|{embeddings.EMBEDDINGS_HOST}|{embeddings.EMBEDDINGS_MODEL}|{embeddings.EMBED_DIM}"
    return hashlib.sha256(settings.encode()).hexdigest()


def split_passages(content: str, max_bytes: int = 600) -> list[str]:
    """Split losslessly by UTF-8 budget, preferring whitespace boundaries."""
    remaining = str(content or "").strip()
    passages = []
    while remaining:
        raw = remaining.encode("utf-8")
        part = raw[:max_bytes].decode("utf-8", errors="ignore")
        if len(raw) > max_bytes:
            boundary = part.rfind(" ")
            if boundary >= len(part) // 2:
                part = part[:boundary]
        if not part:
            raise ValueError("Passage budget is too small")
        passages.append(part)
        remaining = remaining[len(part) :].lstrip()
    return passages


def source_items(metadata: Any) -> list[dict[str, Any]]:
    """Preserve source identities without replaying arbitrary message metadata."""
    if not isinstance(metadata, dict):
        return []
    refs = metadata.get("linked_items")
    if not isinstance(refs, list):
        return []
    return [
        {key: ref.get(key) for key in ("entity_type", "entity_id", "message_id", "title")}
        for ref in refs[:8]
        if isinstance(ref, dict)
        and ref.get("entity_type") in {"event", "document", "contact", "place", "conversation"}
        and isinstance(ref.get("entity_id"), str)
    ]


def prepare_backfill() -> None:
    """Queue a version change once; inserts and edits are queued transactionally."""
    version = index_version()
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """UPDATE conversation_message_index
               SET revision = revision + 1, index_version = %s,
                   next_run_at = NOW(), attempts = 0
               WHERE index_version <> %s""",
            (version, version),
        )
        conn.commit()


def process_due_once() -> bool:
    version = index_version()
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT message_id, revision FROM conversation_message_index
               WHERE indexed_revision <> revision AND next_run_at <= NOW()
                 AND lease_until <= NOW()
               ORDER BY next_run_at, message_id FOR UPDATE SKIP LOCKED LIMIT 1"""
        )
        job = cur.fetchone()
        if not job:
            return False
        message_id, revision = job["message_id"], job["revision"]
        cur.execute(
            """UPDATE conversation_message_index SET lease_until = NOW() + INTERVAL '5 minutes'
               WHERE message_id = %s""",
            (message_id,),
        )
        conn.commit()

    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT m.role, m.content, m.metadata, t.title
                   FROM conversation_messages m JOIN conversation_threads t ON t.id = m.thread_id
                   WHERE m.message_id = %s""",
                (message_id,),
            )
            message = cur.fetchone()
            if not message:
                return True
            cur.execute(
                """SELECT c.payload_hash, c.chunk_embed::text AS embedding
                   FROM conversation_chunks c JOIN conversation_message_index i USING (message_id)
                   WHERE c.message_id = %s AND i.index_version = %s""",
                (message_id, version),
            )
            cached = {row["payload_hash"]: json.loads(row["embedding"]) for row in cur.fetchall()}

        chunks = []
        if message["role"] in {"user", "assistant"}:
            for position, passage in enumerate(split_passages(message["content"])):
                # Kept below even the embedding helper's minimum adaptive byte cap.
                title = str(message["title"] or "").encode()[:100].decode("utf-8", errors="ignore")
                payload = f"Conversation: {title}\n{message['role']}: {passage}"
                digest = hashlib.sha256(f"{version}|{payload}".encode()).hexdigest()
                vector = cached.get(digest)
                if vector is None:
                    vector = embeddings.embed_text(payload, log_response_body=False)
                if (
                    len(vector) != 768
                    or not all(math.isfinite(v) for v in vector)
                    or not any(vector)
                ):
                    raise ValueError("Invalid conversation embedding dimensions or values")
                chunks.append((message_id, position, passage, digest, vector))
                with get_conn() as conn, conn.cursor() as cur:
                    cur.execute(
                        """UPDATE conversation_message_index
                           SET lease_until = NOW() + INTERVAL '5 minutes'
                           WHERE message_id = %s AND revision = %s""",
                        (message_id, revision),
                    )
                    conn.commit()

        with get_conn() as conn, conn.cursor() as cur:
            # Message first, then job: same lock order as the write trigger.
            cur.execute(
                "SELECT message_id FROM conversation_messages WHERE message_id = %s FOR SHARE",
                (message_id,),
            )
            if not cur.fetchone():
                return True
            cur.execute(
                "SELECT revision FROM conversation_message_index WHERE message_id = %s FOR UPDATE",
                (message_id,),
            )
            current = cur.fetchone()
            if not current or current["revision"] != revision:
                cur.execute(
                    "UPDATE conversation_message_index SET lease_until = NOW() WHERE message_id = %s",
                    (message_id,),
                )
                conn.commit()
                return True
            cur.execute("DELETE FROM conversation_chunks WHERE message_id = %s", (message_id,))
            if chunks:
                cur.executemany(
                    """INSERT INTO conversation_chunks
                       (message_id, chunk_index, chunk_text, payload_hash, chunk_embed)
                       VALUES (%s, %s, %s, %s, %s::vector)""",
                    chunks,
                )
            cur.execute(
                """UPDATE conversation_message_index
                   SET indexed_revision = revision, index_version = %s, attempts = 0,
                       lease_until = NOW() WHERE message_id = %s""",
                (version, message_id),
            )
            conn.commit()
        return True
    except Exception:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """UPDATE conversation_message_index
                   SET attempts = attempts + 1, next_run_at = NOW() + INTERVAL '1 minute',
                       lease_until = NOW()
                   WHERE message_id = %s AND revision = %s""",
                (message_id, revision),
            )
            conn.commit()
        raise


def search_messages(
    user_email: str,
    query: str,
    *,
    limit: int | None = 30,
    exclude_thread_id: str | None = None,
    time_start: str | None = None,
    time_end: str | None = None,
    per_thread: int | None = 2,
    sort_order: str = "relevance",
) -> list[dict[str, Any]]:
    if not user_email or not str(query or "").strip():
        return []
    query = normalize_search_text(query)
    params: list[Any] = [user_email]
    clauses = ["t.user_email = %s", "m.role IN ('user', 'assistant')"]
    if exclude_thread_id:
        clauses.append("m.thread_id <> %s")
        params.append(exclude_thread_id)
    for value, operator in ((time_start, ">="), (time_end, "<=")):
        if value:
            clauses.append(f"m.created_at {operator} %s::timestamptz")
            params.append(value)
    where = " AND ".join(clauses)
    ranking_order = "score DESC, created_at DESC, message_id DESC"
    if sort_order == "newest":
        ranking_order = "created_at DESC, score DESC, message_id DESC"
    elif sort_order == "oldest":
        ranking_order = "created_at ASC, score DESC, message_id ASC"
    candidate_limit = max(50, limit * 5) if limit is not None else None
    base = f"""FROM conversation_messages m JOIN conversation_threads t ON t.id = m.thread_id
               WHERE {where}"""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"""WITH search_query AS (
                  SELECT plainto_tsquery('simple', unaccent(%s)) AS query
                ), hits AS (
                  SELECT m.message_id, m.thread_id, m.created_at, m.content,
                    ts_rank_cd(m.content_tsv, q.query) + 2 * ts_rank_cd(t.title_tsv, q.query) AS score
                  FROM conversation_messages m JOIN conversation_threads t ON t.id = m.thread_id
                  CROSS JOIN search_query q
                  WHERE {where} AND m.content_tsv @@ q.query
                ), ranked AS (
                  SELECT *, ROW_NUMBER() OVER (
                    PARTITION BY thread_id ORDER BY {ranking_order}
                  ) AS thread_rank FROM hits
                )
                SELECT m.message_id, m.score,
                       COALESCE((SELECT c.chunk_text FROM conversation_chunks c
                         JOIN conversation_message_index i USING (message_id)
                         WHERE c.message_id = m.message_id
                           AND i.indexed_revision = i.revision
                           AND POSITION(c.chunk_text IN m.content) > 0
                           AND to_tsvector('simple', unaccent(c.chunk_text)) @@ q.query
                         ORDER BY c.chunk_index LIMIT 1), LEFT(m.content, 600)) AS snippet
                FROM ranked m CROSS JOIN search_query q
                WHERE (%s::integer IS NULL OR m.thread_rank <= %s)
                ORDER BY {ranking_order} LIMIT %s""",
            [query, *params, per_thread, per_thread, candidate_limit],
        )
        lexical = cur.fetchall()
    semantic = []
    vector = query_embedding(query)
    if vector is not None:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"""WITH scored AS MATERIALIZED (
                      SELECT c.message_id, m.thread_id, m.created_at, c.chunk_index, c.chunk_text AS snippet,
                        1 - (c.chunk_embed <=> %s::vector) AS score
                      FROM conversation_chunks c JOIN conversation_messages m USING (message_id)
                      JOIN conversation_threads t ON t.id = m.thread_id
                      JOIN conversation_message_index i USING (message_id)
                      WHERE {where} AND i.indexed_revision = i.revision AND i.index_version = %s
                    ), best_messages AS (
                      SELECT DISTINCT ON (message_id) message_id, thread_id, created_at, snippet, score
                      FROM scored WHERE score >= 0.45
                      ORDER BY message_id, score DESC, chunk_index
                    ), ranked AS (
                      SELECT *, ROW_NUMBER() OVER (
                        PARTITION BY thread_id ORDER BY {ranking_order}
                      ) AS thread_rank FROM best_messages
                    )
                    SELECT message_id, snippet, score FROM ranked
                    WHERE (%s::integer IS NULL OR thread_rank <= %s)
                    ORDER BY {ranking_order} LIMIT %s""",
                [vector, *params, index_version(), per_thread, per_thread, candidate_limit],
            )
            semantic = cur.fetchall()

    scores: dict[int, dict[str, Any]] = {}
    for source, rows in (("keyword", lexical), ("semantic", semantic)):
        seen = set()
        for row in rows:
            message_id = row["message_id"]
            if message_id in seen:
                continue
            seen.add(message_id)
            item = scores.setdefault(
                message_id, {"score": 0.0, "match_sources": [], "snippet": row["snippet"]}
            )
            item["score"] += 20 / (20 + len(seen))
            item["match_sources"].append(source)
            if source == "semantic" and "keyword" not in item["match_sources"]:
                item["snippet"] = row["snippet"]
    if not scores:
        return []
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"""SELECT m.message_id, m.thread_id, m.role, m.created_at, m.metadata, t.title
                {base} AND m.message_id = ANY(%s)""",
            [*params, list(scores)],
        )
        rows = cur.fetchall()
    rows.sort(
        key=lambda row: (scores[row["message_id"]]["score"], row["created_at"], row["message_id"]),
        reverse=True,
    )
    if sort_order == "newest":
        rows.sort(key=lambda row: row["created_at"], reverse=True)
    elif sort_order == "oldest":
        rows.sort(key=lambda row: row["created_at"])
    results, counts, texts = [], {}, set()
    for row in rows:
        match = scores[row["message_id"]]
        content_key = normalize_search_text(match["snippet"])
        if (
            per_thread is not None and counts.get(row["thread_id"], 0) >= per_thread
        ) or content_key in texts:
            continue
        texts.add(content_key)
        counts[row["thread_id"]] = counts.get(row["thread_id"], 0) + 1
        results.append(
            {
                **match,
                "id": str(row["message_id"]),
                "kind": "conversation",
                "thread_id": row["thread_id"],
                "message_id": row["message_id"],
                "title": row["title"] or "Untitled conversation",
                "role": row["role"],
                "created_at": row["created_at"].isoformat(),
                "source_items": source_items(row["metadata"]),
            }
        )
        if limit is not None and len(results) >= limit:
            break
    return results


def merge_memory_results(
    memories: list[dict], conversations: list[dict], *, limit: int | None, sort_order: str
) -> list[dict]:
    """Fuse source ranks rather than comparing incompatible keyword/vector scores."""
    if not conversations:
        return memories
    original_ids = {(row.get("kind"), str(row.get("id"))) for row in memories}
    merged, seen_text = [], set()
    for weight, rows in ((1.0, memories), (0.95, conversations)):
        for rank, row in enumerate(rows, 1):
            if row.get("role") == "assistant" and any(
                (ref.get("entity_type"), str(ref.get("entity_id"))) in original_ids
                for ref in row.get("source_items", [])
            ):
                continue
            text_key = normalize_search_text(str(row.get("summary") or row.get("snippet") or ""))
            if text_key and text_key in seen_text:
                continue
            seen_text.add(text_key)
            merged.append(
                {**row, "retrieval_score": row.get("score"), "score": weight * 20 / (20 + rank)}
            )
    merged.sort(key=lambda row: (-row["score"], str(row["id"])))
    if sort_order in {"newest", "oldest"}:
        from datetime import datetime, timezone

        def date_key(row):
            raw = row.get("start_date") or row.get("document_date") or row.get("created_at")
            try:
                parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
                return (
                    parsed.replace(tzinfo=timezone.utc).timestamp()
                    if parsed.tzinfo is None
                    else parsed.timestamp()
                )
            except (ValueError, TypeError):
                return float("-inf") if sort_order == "newest" else float("inf")

        merged.sort(key=date_key, reverse=sort_order == "newest")
    return merged if limit is None else merged[:limit]


def get_context(
    user_email: str, thread_id: str, message_id: int, *, focus: str = ""
) -> dict[str, Any] | None:
    """Inspect the matching passage plus at most two neighbouring messages each side."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT t.title FROM conversation_threads t JOIN conversation_messages m ON m.thread_id = t.id
               WHERE t.user_email = %s AND t.id = %s AND m.message_id = %s
                 AND m.role IN ('user', 'assistant')""",
            (user_email, thread_id, message_id),
        )
        thread = cur.fetchone()
        if not thread:
            return None
        cur.execute(
            """WITH before AS (
                 SELECT message_id FROM conversation_messages WHERE thread_id = %s AND message_id < %s AND role IN ('user', 'assistant')
                 ORDER BY message_id DESC LIMIT 2
               ), after AS (
                 SELECT message_id FROM conversation_messages WHERE thread_id = %s AND message_id > %s AND role IN ('user', 'assistant')
                 ORDER BY message_id LIMIT 2
               )
               SELECT m.message_id, m.role,
                 CASE WHEN m.message_id = %s AND %s <> '' THEN
                   SUBSTRING(m.content FROM GREATEST(1, POSITION(%s IN m.content) - 1500) FOR 6000)
                 ELSE LEFT(m.content, 6000) END AS content,
                 LENGTH(m.content) > 6000 AS excerpted, m.created_at, m.metadata
               FROM conversation_messages m JOIN conversation_threads t ON t.id = m.thread_id
               WHERE t.user_email = %s AND t.id = %s AND m.role IN ('user', 'assistant') AND (m.message_id = %s
                 OR m.message_id IN (SELECT message_id FROM before UNION ALL SELECT message_id FROM after))
               ORDER BY m.message_id""",
            (
                thread_id,
                message_id,
                thread_id,
                message_id,
                message_id,
                focus,
                focus,
                user_email,
                thread_id,
                message_id,
            ),
        )
        messages = [
            {
                "message_id": row["message_id"],
                "role": row["role"],
                "content": row["content"],
                "created_at": row["created_at"].isoformat(),
                "excerpted": row["excerpted"],
                "source_items": source_items(row["metadata"]),
            }
            for row in cur.fetchall()
        ]
    if not any(row["message_id"] == message_id for row in messages):
        return None
    return {
        "thread_id": thread_id,
        "message_id": message_id,
        "title": thread["title"],
        "messages": messages,
    }
