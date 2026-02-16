"""
User facts service — persistent personal preferences, traits, and knowledge.

Stores atomic facts about the user that don't belong in contacts, events,
places, todos, or documents. Examples: "Prefers rock music", "Is a software
engineer", "Allergic to peanuts".

Facts are extracted automatically from conversations by the background
extraction pipeline (see fact_extraction.py) and injected into every LLM
prompt so the agent can personalise responses.
"""

from __future__ import annotations

import math
import uuid
from datetime import datetime, timezone
from typing import Any

from db import get_conn
from embeddings import embed_text
from observability.logger import get_runtime_logger

logger = get_runtime_logger(__name__)

# Retrieval scoring weights (inspired by Generative Agents)
WEIGHT_SEMANTIC = 0.50
WEIGHT_IMPORTANCE = 0.25
WEIGHT_RECENCY = 0.25

# Recency decay: half-life of ~30 days (in hours)
RECENCY_DECAY_RATE = 0.001

# Maximum facts to embed in prompt context
DEFAULT_CONTEXT_LIMIT = 8

VALID_CATEGORIES = frozenset(
    ["preference", "biographical", "behavioral", "goal", "opinion", "constraint", "general"]
)


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def get_user_facts(user_email: str, *, limit: int = 100) -> list[dict[str, Any]]:
    """Return all facts for a user, ordered by importance desc then newest."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT fact_id, user_email, content, category, importance,
                   source_thread_id, access_count, last_accessed_at,
                   created_at, updated_at
            FROM user_facts
            WHERE user_email = %s
            ORDER BY importance DESC, created_at DESC
            LIMIT %s
            """,
            (user_email, limit),
        )
        return [_row_to_dict(row) for row in cur.fetchall()]


def get_fact(fact_id: str) -> dict[str, Any] | None:
    """Get a single fact by ID."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT fact_id, user_email, content, category, importance,
                   source_thread_id, access_count, last_accessed_at,
                   created_at, updated_at
            FROM user_facts
            WHERE fact_id = %s
            """,
            (fact_id,),
        )
        row = cur.fetchone()
        return _row_to_dict(row) if row else None


def upsert_fact(
    user_email: str,
    content: str,
    *,
    category: str = "general",
    importance: int = 5,
    source_thread_id: str | None = None,
    fact_id: str | None = None,
) -> dict[str, Any]:
    """Create or fully replace a user fact. Generates embedding."""
    if category not in VALID_CATEGORIES:
        category = "general"
    importance = max(1, min(10, importance))

    fid = fact_id or f"uf_{uuid.uuid4().hex[:12]}"
    embedding = _generate_embedding(content)
    now = datetime.now(timezone.utc)

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO user_facts
                (fact_id, user_email, content, category, importance,
                 content_embed, source_thread_id, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (fact_id) DO UPDATE SET
                content = EXCLUDED.content,
                category = EXCLUDED.category,
                importance = EXCLUDED.importance,
                content_embed = EXCLUDED.content_embed,
                source_thread_id = EXCLUDED.source_thread_id,
                updated_at = EXCLUDED.updated_at
            RETURNING fact_id, user_email, content, category, importance,
                      source_thread_id, access_count, last_accessed_at,
                      created_at, updated_at
            """,
            (fid, user_email, content, category, importance, embedding, source_thread_id, now, now),
        )
        row = cur.fetchone()
        conn.commit()
    logger.info("[user_facts] upsert fact_id=%s user=%s category=%s", fid, user_email, category)
    return _row_to_dict(row)


def update_fact(
    fact_id: str,
    *,
    content: str | None = None,
    category: str | None = None,
    importance: int | None = None,
) -> dict[str, Any] | None:
    """Partial update of a fact. Re-embeds if content changes."""
    updates: list[str] = ["updated_at = NOW()"]
    params: list[Any] = []

    if content is not None:
        updates.append("content = %s")
        params.append(content)
        embedding = _generate_embedding(content)
        updates.append("content_embed = %s")
        params.append(embedding)

    if category is not None:
        if category not in VALID_CATEGORIES:
            category = "general"
        updates.append("category = %s")
        params.append(category)

    if importance is not None:
        importance = max(1, min(10, importance))
        updates.append("importance = %s")
        params.append(importance)

    params.append(fact_id)

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            UPDATE user_facts
            SET {", ".join(updates)}
            WHERE fact_id = %s
            RETURNING fact_id, user_email, content, category, importance,
                      source_thread_id, access_count, last_accessed_at,
                      created_at, updated_at
            """,
            tuple(params),
        )
        row = cur.fetchone()
        conn.commit()
    if row:
        logger.info("[user_facts] updated fact_id=%s", fact_id)
    return _row_to_dict(row) if row else None


def delete_fact(fact_id: str) -> bool:
    """Hard-delete a fact."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM user_facts WHERE fact_id = %s", (fact_id,))
        deleted = cur.rowcount > 0
        conn.commit()
    if deleted:
        logger.info("[user_facts] deleted fact_id=%s", fact_id)
    return deleted


def record_fact_access(fact_ids: list[str]) -> None:
    """Bump access_count and last_accessed_at for retrieved facts."""
    if not fact_ids:
        return
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE user_facts
            SET access_count = access_count + 1,
                last_accessed_at = NOW()
            WHERE fact_id = ANY(%s)
            """,
            (fact_ids,),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# Hybrid retrieval (vector + FTS + importance + recency)
# ---------------------------------------------------------------------------


def search_user_facts(
    user_email: str,
    query: str,
    *,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """
    Hybrid search: vector similarity + FTS + importance + recency scoring.

    Returns facts ranked by composite score.
    """
    query_embedding = _generate_embedding(query)
    now_utc = datetime.now(timezone.utc)

    with get_conn() as conn, conn.cursor() as cur:
        # Vector similarity search
        cur.execute(
            """
            SELECT fact_id, content, category, importance,
                   access_count, last_accessed_at, created_at, updated_at,
                   1 - (content_embed <=> %s) AS semantic_score
            FROM user_facts
            WHERE user_email = %s
              AND content_embed IS NOT NULL
            ORDER BY content_embed <=> %s
            LIMIT %s
            """,
            (query_embedding, user_email, query_embedding, limit * 3),
        )
        vector_rows = {row["fact_id"]: dict(row) for row in cur.fetchall()}

        # FTS search
        cur.execute(
            """
            SELECT fact_id, content, category, importance,
                   access_count, last_accessed_at, created_at, updated_at,
                   ts_rank_cd(content_tsv, plainto_tsquery('english', unaccent(%s))) AS fts_score
            FROM user_facts
            WHERE user_email = %s
              AND content_tsv @@ plainto_tsquery('english', unaccent(%s))
            ORDER BY fts_score DESC
            LIMIT %s
            """,
            (query, user_email, query, limit * 3),
        )
        fts_rows = {row["fact_id"]: dict(row) for row in cur.fetchall()}

    # Merge candidates
    all_ids = set(vector_rows.keys()) | set(fts_rows.keys())
    scored: list[dict[str, Any]] = []

    for fid in all_ids:
        vr = vector_rows.get(fid)
        fr = fts_rows.get(fid)
        base = vr or fr  # guaranteed one exists
        assert base is not None

        semantic = float(vr["semantic_score"]) if vr else 0.0
        fts = float(fr["fts_score"]) if fr else 0.0
        # Normalise FTS to 0-1 range (ts_rank_cd is unbounded but typically < 1)
        fts_norm = min(fts, 1.0)

        importance_norm = float(base["importance"]) / 10.0

        # Recency score based on last_accessed_at (or created_at as fallback)
        ref_time = base["last_accessed_at"] or base["created_at"]
        if ref_time:
            if hasattr(ref_time, "tzinfo") and ref_time.tzinfo is None:
                ref_time = ref_time.replace(tzinfo=timezone.utc)
            hours_ago = max(0, (now_utc - ref_time).total_seconds() / 3600)
        else:
            hours_ago = 720  # 30 days default
        recency = math.exp(-RECENCY_DECAY_RATE * hours_ago)

        # Composite score
        signal = max(semantic, fts_norm)  # best text relevance signal
        composite = (
            WEIGHT_SEMANTIC * signal
            + WEIGHT_IMPORTANCE * importance_norm
            + WEIGHT_RECENCY * recency
        )

        scored.append(
            {
                "fact_id": base["fact_id"],
                "content": base["content"],
                "category": base["category"],
                "importance": base["importance"],
                "access_count": base["access_count"],
                "last_accessed_at": base["last_accessed_at"],
                "created_at": base["created_at"],
                "updated_at": base["updated_at"],
                "score": round(composite, 4),
                "score_breakdown": {
                    "semantic": round(semantic, 4),
                    "fts": round(fts_norm, 4),
                    "importance": round(importance_norm, 4),
                    "recency": round(recency, 4),
                },
            }
        )

    scored.sort(key=lambda r: r["score"], reverse=True)
    return scored[:limit]


def get_facts_for_context(
    user_email: str,
    query: str,
    *,
    limit: int = DEFAULT_CONTEXT_LIMIT,
) -> str | None:
    """
    Retrieve relevant facts and format as a prompt-ready string.

    Also records access for the returned facts.
    Returns None if no facts exist.
    """
    results = search_user_facts(user_email, query, limit=limit)
    if not results:
        return None

    # Record access for retrieval freshness
    record_fact_access([r["fact_id"] for r in results])

    lines: list[str] = []
    for r in results:
        cat = r["category"]
        content = r["content"]
        lines.append(f"- [{cat}] {content}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _generate_embedding(text: str) -> list[float]:
    """Generate embedding vector for fact content."""
    return embed_text(text)


def _row_to_dict(row: Any) -> dict[str, Any]:
    """Convert a DB row to a clean dict (no embedding)."""
    d = dict(row)
    # Strip vector columns from API responses
    d.pop("content_embed", None)
    d.pop("content_tsv", None)
    d.pop("semantic_score", None)
    d.pop("fts_score", None)
    # Serialise datetimes
    for key in ("created_at", "updated_at", "last_accessed_at"):
        val = d.get(key)
        if val and hasattr(val, "isoformat"):
            d[key] = val.isoformat()
    return d
