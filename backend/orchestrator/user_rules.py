"""Explicit, scoped user instructions. Learned facts belong in knowledge.py.

The historical table name remains for saved IDs and migration provenance. This
service only reads/writes hard_rule rows; it never extracts or ranks soft facts.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any
from uuid import uuid4

from psycopg.types.json import Jsonb

from db import get_conn


class RuleType(StrEnum):
    ENTITY_ALIAS = "entity_alias"


class RuleScope(StrEnum):
    AGENT_GLOBAL = "agent_global"
    CONTACT_RESOLUTION = "contact_resolution"
    EVENT_COMMAND = "event_command"
    DAILY_BRIEFING = "daily_briefing"


def normalize_rule_scope(scope):
    value = str(scope or "").strip().lower()
    return value if value in RuleScope._value2member_map_ else None


def normalize_rule_type(rule_type):
    value = str(rule_type or "").strip().lower()
    return value if value in RuleType._value2member_map_ else None


_COLUMNS = """fact_id AS rule_id, content, importance, rule_type AS type,
    rule_scope AS scope, rule_payload AS payload, source_thread_id, created_at, updated_at"""


def list_rules(user_email: str, *, limit=100):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"""SELECT {_COLUMNS} FROM user_facts
            WHERE user_email=%s AND fact_mode='hard_rule'
            ORDER BY importance DESC,updated_at DESC LIMIT %s""",
            (user_email, limit),
        )
        return [dict(row) for row in cur.fetchall()]


def upsert_rule(
    user_email,
    content,
    *,
    rule_type,
    scope,
    payload,
    importance=9,
    source_thread_id=None,
    rule_id=None,
):
    normalized_type = normalize_rule_type(rule_type)
    if not normalized_type:
        raise ValueError("A valid rule type is required")
    if not scope or any(normalize_rule_scope(value) is None for value in scope):
        raise ValueError("A valid nonempty rule scope is required")
    scopes = list(dict.fromkeys(normalize_rule_scope(value) for value in scope))
    alias = str(payload.get("alias_text") or "").strip()
    target = str(payload.get("target_text") or "").strip()
    if not content.strip() or not alias or not target:
        raise ValueError("Alias rules require content, alias text and target text")
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"""INSERT INTO user_facts(fact_id,user_email,content,category,importance,
                fact_mode,rule_type,rule_scope,rule_payload,source_thread_id)
            VALUES(%s,%s,%s,'behavioral',%s,'hard_rule',%s,%s,%s,%s)
            ON CONFLICT(fact_id) DO UPDATE SET content=EXCLUDED.content,
                importance=EXCLUDED.importance,rule_type=EXCLUDED.rule_type,
                rule_scope=EXCLUDED.rule_scope,rule_payload=EXCLUDED.rule_payload,
                source_thread_id=EXCLUDED.source_thread_id,updated_at=NOW()
            WHERE user_facts.user_email=EXCLUDED.user_email AND user_facts.fact_mode='hard_rule'
            RETURNING {_COLUMNS}""",
            (
                rule_id or "rule_" + uuid4().hex,
                user_email,
                content.strip(),
                max(1, min(10, importance)),
                normalized_type,
                scopes,
                Jsonb({"alias_text": alias, "target_text": target}),
                source_thread_id,
            ),
        )
        row = cur.fetchone()
        if not row:
            raise ValueError("Rule is unavailable")
        conn.commit()
        return dict(row)


def delete_rule(user_email, rule_id):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "DELETE FROM user_facts WHERE user_email=%s AND fact_id=%s AND fact_mode='hard_rule'",
            (user_email, rule_id),
        )
        changed = bool(cur.rowcount)
        conn.commit()
        return changed


def get_rules_for_scope(user_email, *, scope, rule_type=None, limit=20):
    normalized_scope = normalize_rule_scope(scope)
    if not user_email or not normalized_scope:
        return []
    normalized_type = normalize_rule_type(rule_type) if rule_type else None
    if rule_type and not normalized_type:
        return []
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"""SELECT {_COLUMNS} FROM user_facts
            WHERE user_email=%s AND fact_mode='hard_rule'
                AND (COALESCE(array_length(rule_scope,1),0)=0 OR %s=ANY(rule_scope) OR 'agent_global'=ANY(rule_scope))
                AND (%s::text IS NULL OR rule_type=%s)
            ORDER BY importance DESC,updated_at DESC LIMIT %s""",
            (user_email, normalized_scope, normalized_type, normalized_type, limit),
        )
        return [dict(row) for row in cur.fetchall()]


def get_rules_context(user_email, *, scope, limit=12):
    lines = []
    for rule in get_rules_for_scope(user_email, scope=scope, limit=limit):
        payload = rule.get("payload") or {}
        alias, target = payload.get("alias_text"), payload.get("target_text")
        rendered = (
            f"If the user says '{alias}', interpret it as '{target}'."
            if alias and target
            else rule["content"]
        )
        if rendered:
            lines.append("- " + rendered)
    return (
        "Deterministic user rules (apply before disambiguation):\n" + "\n".join(lines)
        if lines
        else None
    )


_EXPLICIT_ALIAS_RULE_PATTERNS = [
    re.compile(
        r"(?:^|\b)(?:when|whenever)\s+i\s+say\s+[\"']?(?P<alias>[^\"',.;:!?\n]+)[\"']?\s*,?\s*i\s+mean\s+[\"']?(?P<target>[^\"'\n]+?)[\"']?(?:[\.,;:!?]|$)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:^|\b)[\"']?(?P<alias>[a-z0-9][^\"',.;:!?\n]{0,80}?)[\"']?\s+means\s+[\"']?(?P<target>[a-z0-9][^\"'\n]{0,160}?)[\"']?(?:[\.,;:!?]|$)",
        re.IGNORECASE,
    ),
]


def extract_explicit_rules(message: str) -> list[dict[str, Any]]:
    rules, seen = [], set()
    for pattern in _EXPLICIT_ALIAS_RULE_PATTERNS:
        for match in pattern.finditer(message or ""):
            alias, target = (_clean_fragment(match.group(field)) for field in ("alias", "target"))
            key = (alias.casefold(), target.casefold())
            if not alias or not target or key in seen:
                continue
            seen.add(key)
            rules.append(
                {
                    "content": f"If user says '{alias}', resolve as '{target}'.",
                    "type": RuleType.ENTITY_ALIAS.value,
                    "scope": [RuleScope.CONTACT_RESOLUTION.value, RuleScope.AGENT_GLOBAL.value],
                    "payload": {"alias_text": alias, "target_text": target},
                }
            )
    return rules


def _clean_fragment(value):
    return re.sub(r"\s+", " ", value.strip().strip("\"'")).strip(" .,:;!?")
