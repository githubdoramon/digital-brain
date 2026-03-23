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

import json
import math
import uuid
from datetime import datetime, timezone
from typing import Any

from db import get_conn
from embeddings import embed_text
from observability.logger import get_runtime_logger
from user_fact_rules import (
    VALID_FACT_MODE_VALUES,
    FactMode,
    RuleScope,
    RuleType,
    normalize_rule_scope,
    normalize_rule_type,
)

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
VALID_FACT_MODES = VALID_FACT_MODE_VALUES


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def get_user_facts(user_email: str, *, limit: int = 100) -> list[dict[str, Any]]:
    """Return all facts for a user, ordered by importance desc then newest."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT fact_id, user_email, content, category, importance,
                   fact_mode, rule_type, rule_scope, rule_payload,
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
                   fact_mode, rule_type, rule_scope, rule_payload,
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
    fact_mode: FactMode | str = FactMode.SOFT,
    rule_type: RuleType | str | None = None,
    rule_scope: list[RuleScope | str] | None = None,
    rule_payload: dict[str, Any] | None = None,
    source_thread_id: str | None = None,
    fact_id: str | None = None,
) -> dict[str, Any]:
    """Create or fully replace a user fact. Generates embedding."""
    if category not in VALID_CATEGORIES:
        category = "general"
    importance = max(1, min(10, importance))
    fact_mode = _normalize_fact_mode(fact_mode)
    invalid_scopes = _find_invalid_rule_scopes(rule_scope)
    if invalid_scopes:
        raise ValueError(f"Invalid rule_scope values: {', '.join(invalid_scopes)}")

    normalized_rule_type = _normalize_rule_type(rule_type)
    normalized_rule_scope = _normalize_rule_scope(rule_scope)
    normalized_rule_payload = _normalize_rule_payload(rule_payload)

    if fact_mode == FactMode.HARD_RULE.value:
        if not normalized_rule_type:
            raise ValueError("hard_rule facts require a valid rule_type")
        if not normalized_rule_scope:
            raise ValueError("hard_rule facts require at least one valid rule_scope")
    else:
        normalized_rule_type = None
        normalized_rule_scope = []
        normalized_rule_payload = {}

    fid = fact_id or f"uf_{uuid.uuid4().hex[:12]}"
    embedding = _generate_embedding(content)
    now = datetime.now(timezone.utc)

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO user_facts
                (fact_id, user_email, content, category, importance,
                 fact_mode, rule_type, rule_scope, rule_payload,
                 content_embed, source_thread_id, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s::text[], %s::jsonb, %s, %s, %s, %s)
            ON CONFLICT (fact_id) DO UPDATE SET
                content = EXCLUDED.content,
                category = EXCLUDED.category,
                importance = EXCLUDED.importance,
                fact_mode = EXCLUDED.fact_mode,
                rule_type = EXCLUDED.rule_type,
                rule_scope = EXCLUDED.rule_scope,
                rule_payload = EXCLUDED.rule_payload,
                content_embed = EXCLUDED.content_embed,
                source_thread_id = EXCLUDED.source_thread_id,
                updated_at = EXCLUDED.updated_at
            RETURNING fact_id, user_email, content, category, importance,
                      fact_mode, rule_type, rule_scope, rule_payload,
                      source_thread_id, access_count, last_accessed_at,
                      created_at, updated_at
            """,
            (
                fid,
                user_email,
                content,
                category,
                importance,
                fact_mode,
                normalized_rule_type,
                normalized_rule_scope,
                json.dumps(normalized_rule_payload),
                embedding,
                source_thread_id,
                now,
                now,
            ),
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
    fact_mode: FactMode | str | None = None,
    rule_type: RuleType | str | None = None,
    rule_scope: list[RuleScope | str] | None = None,
    rule_payload: dict[str, Any] | None = None,
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

    next_mode = _normalize_fact_mode(fact_mode) if fact_mode is not None else None
    invalid_scopes = _find_invalid_rule_scopes(rule_scope)
    if invalid_scopes:
        raise ValueError(f"Invalid rule_scope values: {', '.join(invalid_scopes)}")

    if next_mode is not None:
        updates.append("fact_mode = %s")
        params.append(next_mode)

    normalized_rule_type = _normalize_rule_type(rule_type) if rule_type is not None else None
    if next_mode == FactMode.HARD_RULE.value and rule_type is None:
        raise ValueError("hard_rule updates must provide rule_type")
    if rule_type is not None and not normalized_rule_type:
        raise ValueError("Invalid rule_type value")

    if rule_type is not None:
        updates.append("rule_type = %s")
        params.append(normalized_rule_type)

    normalized_rule_scope = _normalize_rule_scope(rule_scope) if rule_scope is not None else None
    if next_mode == FactMode.HARD_RULE.value and rule_scope is None:
        raise ValueError("hard_rule updates must provide rule_scope")
    if rule_scope is not None:
        if next_mode == FactMode.HARD_RULE.value and not normalized_rule_scope:
            raise ValueError("hard_rule facts require at least one valid rule_scope")
        updates.append("rule_scope = %s::text[]")
        params.append(normalized_rule_scope)

    if rule_payload is not None:
        updates.append("rule_payload = %s::jsonb")
        params.append(json.dumps(_normalize_rule_payload(rule_payload)))

    if next_mode == FactMode.HARD_RULE.value and rule_type is not None and not normalized_rule_type:
        raise ValueError("hard_rule facts require a valid rule_type")

    if next_mode == FactMode.SOFT.value:
        updates.append("rule_type = NULL")
        updates.append("rule_scope = '{}'::text[]")
        updates.append("rule_payload = '{}'::jsonb")

    params.append(fact_id)

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            UPDATE user_facts
            SET {", ".join(updates)}
            WHERE fact_id = %s
            RETURNING fact_id, user_email, content, category, importance,
                      fact_mode, rule_type, rule_scope, rule_payload,
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
                   fact_mode, rule_type, rule_scope, rule_payload,
                   access_count, last_accessed_at, created_at, updated_at,
                   1 - (content_embed <=> %s::vector) AS semantic_score
            FROM user_facts
            WHERE user_email = %s
              AND COALESCE(fact_mode, 'soft') = 'soft'
              AND content_embed IS NOT NULL
            ORDER BY content_embed <=> %s::vector
            LIMIT %s
            """,
            (query_embedding, user_email, query_embedding, limit * 3),
        )
        vector_rows = {row["fact_id"]: dict(row) for row in cur.fetchall()}

        # FTS search
        cur.execute(
            """
            SELECT fact_id, content, category, importance,
                   fact_mode, rule_type, rule_scope, rule_payload,
                   access_count, last_accessed_at, created_at, updated_at,
                   ts_rank_cd(content_tsv, plainto_tsquery('english', unaccent(%s))) AS fts_score
            FROM user_facts
            WHERE user_email = %s
              AND COALESCE(fact_mode, 'soft') = 'soft'
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
                "fact_mode": base.get("fact_mode") or "soft",
                "rule_type": base.get("rule_type"),
                "rule_scope": base.get("rule_scope") or [],
                "rule_payload": base.get("rule_payload") or {},
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


def get_hard_rules_for_scope(
    user_email: str,
    *,
    scope: RuleScope | str,
    rule_type: RuleType | str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Retrieve deterministic hard rules for a runtime scope."""
    normalized_scope = normalize_rule_scope(scope)
    if not user_email or not normalized_scope:
        return []

    params: list[Any] = [user_email, normalized_scope, RuleScope.AGENT_GLOBAL.value]
    extra_filter = ""
    if rule_type:
        normalized_rule_type = _normalize_rule_type(rule_type)
        if not normalized_rule_type:
            return []
        extra_filter = " AND COALESCE(rule_type, '') = %s"
        params.append(normalized_rule_type)
    params.append(limit)

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT fact_id, user_email, content, category, importance,
                   fact_mode, rule_type, rule_scope, rule_payload,
                   source_thread_id, access_count, last_accessed_at,
                   created_at, updated_at
            FROM user_facts
            WHERE user_email = %s
              AND COALESCE(fact_mode, %s) = %s
              AND (
                    COALESCE(array_length(rule_scope, 1), 0) = 0
                    OR %s = ANY(rule_scope)
                    OR %s = ANY(rule_scope)
              )
              {extra_filter}
            ORDER BY importance DESC, updated_at DESC
            LIMIT %s
            """,
            (
                params[0],
                FactMode.SOFT.value,
                FactMode.HARD_RULE.value,
                params[1],
                params[2],
                *params[3:],
            ),
        )
        rows = [_row_to_dict(row) for row in cur.fetchall()]

    if rows:
        record_fact_access([str(item.get("fact_id") or "") for item in rows])
    return rows


def get_hard_rules_context(
    user_email: str,
    *,
    scope: RuleScope | str,
    limit: int = 12,
) -> str | None:
    """Format hard deterministic rules for prompt context."""
    rules = get_hard_rules_for_scope(user_email, scope=scope, limit=limit)
    if not rules:
        return None

    lines = ["Deterministic user rules (apply before disambiguation):"]
    for rule in rules:
        rendered = _render_rule_for_prompt(rule)
        if rendered:
            lines.append(f"- {rendered}")
    if len(lines) == 1:
        return None
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
    if d.get("rule_scope") is None:
        d["rule_scope"] = []
    if d.get("rule_payload") is None:
        d["rule_payload"] = {}
    return d


def _normalize_fact_mode(fact_mode: str | None) -> str:
    normalized = str(fact_mode or FactMode.SOFT.value).strip().lower()
    if normalized in VALID_FACT_MODES:
        return normalized
    return FactMode.SOFT.value


def _normalize_rule_type(rule_type: RuleType | str | None) -> str | None:
    return normalize_rule_type(rule_type)


def _normalize_rule_scope(rule_scope: list[RuleScope | str] | None) -> list[str]:
    if not rule_scope:
        return []
    cleaned: list[str] = []
    seen: set[str] = set()
    for scope in rule_scope:
        item = normalize_rule_scope(scope)
        if not item or item in seen:
            continue
        seen.add(item)
        cleaned.append(item)
    return cleaned


def _find_invalid_rule_scopes(rule_scope: list[RuleScope | str] | None) -> list[str]:
    if not rule_scope:
        return []
    invalid: list[str] = []
    for scope in rule_scope:
        raw = str(scope or "").strip()
        if not raw:
            continue
        if normalize_rule_scope(scope) is None:
            invalid.append(raw)
    return invalid


def _normalize_rule_payload(rule_payload: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(rule_payload, dict):
        return {}
    return dict(rule_payload)


def _render_rule_for_prompt(rule: dict[str, Any]) -> str:
    rule_type = normalize_rule_type(rule.get("rule_type"))
    payload = rule.get("rule_payload") or {}
    if rule_type == RuleType.ENTITY_ALIAS.value:
        alias = str(payload.get("alias_text") or "").strip()
        target = str(payload.get("target_text") or "").strip()
        if alias and target:
            return f"If the user says '{alias}', interpret it as '{target}'."
    content = str(rule.get("content") or "").strip()
    return content
