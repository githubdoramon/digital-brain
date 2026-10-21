"""Durable exact-input tag results. Model/prompt/options changes invalidate the key."""

import hashlib
import json
import os

from db import get_conn
from observability.logger import get_runtime_logger

logger = get_runtime_logger(__name__)


def key(**request):
    value = {"endpoint": os.getenv("LLM_BASE_URL", ""), **request}
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, default=str, ensure_ascii=False).encode()
    ).hexdigest()


def load(input_hash):
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT tags FROM tag_generation_cache WHERE input_hash=%s", (input_hash,))
            row = cur.fetchone()
            if row:
                logger.info("[tag_cache] hit")
                return row["tags"]
    except Exception:
        # Cache availability must not disable ordinary tagging. No raw DB errors.
        logger.debug("[tag_cache] unavailable")
    return None


def save(input_hash, tags):
    if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
        raise ValueError("invalid_tags")
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO tag_generation_cache(input_hash,tags) VALUES(%s,%s::jsonb)
                ON CONFLICT(input_hash) DO NOTHING""",
                (input_hash, json.dumps(tags)),
            )
            conn.commit()
    except Exception:
        logger.debug("[tag_cache] write_unavailable")
