from __future__ import annotations

import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from db import get_conn
from observability.logger import get_runtime_logger
from search_normalization import normalize_search_text

logger = get_runtime_logger(__name__)

_MAX_WEIGHT_ABS = 8.0
_SOURCE_DELTA_SCALE = 0.8


class NewsInteractionType(str, Enum):
    ARTICLE_OPENED = "article_opened"
    ARTICLE_FEEDBACK_UP = "article_feedback_up"
    ARTICLE_FEEDBACK_DOWN = "article_feedback_down"


_EVENT_WEIGHT_DELTA: dict[NewsInteractionType, float] = {
    NewsInteractionType.ARTICLE_OPENED: 0.25,
    NewsInteractionType.ARTICLE_FEEDBACK_UP: 1.0,
    NewsInteractionType.ARTICLE_FEEDBACK_DOWN: -1.0,
}


def record_user_interactions(*, user_email: str, events: list[dict[str, Any]]) -> int:
    """Persist interaction events and update the lightweight user profile."""
    if not user_email or not events:
        return 0

    topic_weights, source_weights = get_user_preference_weights(user_email=user_email)

    written = 0
    try:
        with get_conn() as conn, conn.cursor() as cur:
            for event in events:
                raw_type = str(event.get("event_type") or "").strip()
                try:
                    interaction_type = NewsInteractionType(raw_type)
                except ValueError:
                    logger.warning(
                        "[news.personalization] Ignoring invalid interaction type: %s", raw_type
                    )
                    continue

                metadata = event.get("metadata")
                if not isinstance(metadata, dict):
                    metadata = {}

                interaction_id = f"news_interaction:{uuid4().hex}"
                cur.execute(
                    """
                    INSERT INTO news_user_interactions (
                        interaction_id,
                        user_email,
                        event_type,
                        briefing_id,
                        briefing_item_id,
                        cluster_id,
                        topic_label,
                        source,
                        source_domain,
                        metadata,
                        created_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, NOW())
                    """,
                    (
                        interaction_id,
                        user_email,
                        interaction_type.value,
                        _clean_text(event.get("briefing_id")),
                        _clean_text(event.get("briefing_item_id")),
                        _clean_text(event.get("cluster_id")),
                        _clean_text(event.get("topic_label")),
                        _clean_text(event.get("source")),
                        _clean_text(event.get("source_domain")),
                        json.dumps(metadata),
                    ),
                )

                delta = _EVENT_WEIGHT_DELTA[interaction_type]
                _bump_weight(topic_weights, _clean_key(event.get("topic_label")), delta)

                source_key = _clean_key(event.get("source_domain") or event.get("source"))
                _bump_weight(source_weights, source_key, delta * _SOURCE_DELTA_SCALE)
                written += 1

            if written > 0:
                cur.execute(
                    """
                    INSERT INTO news_user_profiles (
                        user_email,
                        topic_weights,
                        source_weights,
                        last_interaction_at,
                        updated_at
                    )
                    VALUES (%s, %s::jsonb, %s::jsonb, NOW(), NOW())
                    ON CONFLICT (user_email) DO UPDATE
                      SET topic_weights = EXCLUDED.topic_weights,
                          source_weights = EXCLUDED.source_weights,
                          last_interaction_at = NOW(),
                          updated_at = NOW()
                    """,
                    (
                        user_email,
                        json.dumps(topic_weights),
                        json.dumps(source_weights),
                    ),
                )
            conn.commit()
    except Exception:
        logger.warning("[news.personalization] Failed to record interactions", exc_info=True)
        return 0

    return written


def get_user_preference_weights(*, user_email: str | None) -> tuple[dict[str, float], dict[str, float]]:
    if not user_email:
        return {}, {}

    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT topic_weights, source_weights, updated_at
                FROM news_user_profiles
                WHERE user_email = %s
                LIMIT 1
                """,
                (user_email,),
            )
            row = cur.fetchone()
    except Exception:
        logger.warning("[news.personalization] Failed loading user profile", exc_info=True)
        return {}, {}

    if not row:
        return {}, {}

    row_data = dict(row)
    topic_weights = _coerce_weight_map(row_data.get("topic_weights"))
    source_weights = _coerce_weight_map(row_data.get("source_weights"))
    updated_at = row_data.get("updated_at")
    _apply_decay(topic_weights, source_weights, updated_at)
    return topic_weights, source_weights


def _apply_decay(
    topic_weights: dict[str, float],
    source_weights: dict[str, float],
    updated_at: Any,
) -> None:
    if not updated_at:
        return
    if not isinstance(updated_at, datetime):
        try:
            updated_at = datetime.fromisoformat(str(updated_at).replace("Z", "+00:00"))
        except Exception:
            return
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=timezone.utc)
    age_days = max((datetime.now(timezone.utc) - updated_at).total_seconds() / 86400.0, 0.0)
    if age_days <= 0:
        return

    # ~15-day half-life keeps preferences adaptive while retaining signal.
    decay = 0.5 ** (age_days / 15.0)
    _decay_map(topic_weights, decay)
    _decay_map(source_weights, decay)


def _decay_map(weights: dict[str, float], decay: float) -> None:
    for key in list(weights.keys()):
        weights[key] = float(weights.get(key, 0.0)) * decay
        if abs(weights[key]) < 0.01:
            weights.pop(key, None)


def _coerce_weight_map(raw: Any) -> dict[str, float]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, float] = {}
    for key, value in raw.items():
        normalized_key = _clean_key(key)
        if not normalized_key:
            continue
        try:
            out[normalized_key] = float(value)
        except (TypeError, ValueError):
            continue
    return out


def _bump_weight(weights: dict[str, float], key: str, delta: float) -> None:
    if not key:
        return
    value = float(weights.get(key, 0.0)) + delta
    value = max(-_MAX_WEIGHT_ABS, min(_MAX_WEIGHT_ABS, value))
    weights[key] = value


def _clean_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _clean_key(value: Any) -> str:
    return normalize_search_text(str(value or "").strip())
