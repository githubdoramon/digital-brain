"""Small phase-specific prompt projections and a complete input/output budget."""

from __future__ import annotations

import json
import os
import re

from llm_config import get_smart_model
from llm_context import estimate_input, request_budget
from llm_policy import LLMRequestRejected, model_constraints

FIELDS = {
    "people": ("identity_id", "contact_id", "display_name", "is_self"),
    "contacts": (
        "contact_id",
        "display_name",
        "aliases",
        "birthday",
        "birthday_month",
        "birthday_day",
    ),
    "contact_relationships": (
        "relationship_id",
        "from_contact_id",
        "to_contact_id",
        "relationship_type",
        "reciprocal_type",
    ),
    "entities": ("entity_ref", "kind", "label"),
    "knowledge": (
        "knowledge_id",
        "statement",
        "subject_ref",
        "predicate",
        "status",
        "applicable_at",
        "valid_from",
        "valid_until",
        "inferred",
        "manual",
        "deleted_at",
        "updated_at",
    ),
    "tasks": (
        "todo_id",
        "description",
        "status",
        "due_date",
        "assignee_kind",
        "assignee_ref",
        "project_id",
        "created_at",
        "updated_at",
        "intelligence_manual_at",
        "is_proposal",
    ),
    "proposals": ("proposal_id", "kind", "status", "payload"),
    "projects": ("project_id", "title", "description", "status"),
    "lookback": ("message_id", "body", "sent_at", "sender_identity_id", "outgoing"),
    "related": ("kind", "id", "title"),
}
IDENTITY_SECTIONS = {"people", "contacts", "contact_relationships", "entities"}
REFERENCE_FIELDS = (
    "identity_id",
    "contact_id",
    "from_contact_id",
    "to_contact_id",
    "knowledge_id",
    "todo_id",
    "proposal_id",
    "project_id",
    "entity_ref",
)


def json_for(value):
    return json.dumps(value, default=str, ensure_ascii=False, separators=(",", ":"))


def input_estimate(payload, system, response_format):
    # UTF-8 wire bytes are deliberately conservative for text tokenizers. Include
    # the message envelope and schema, not only the user-source character count.
    envelope = {
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": json_for(payload)},
        ],
        "response_format": response_format,
    }
    return estimate_input(envelope)


def request_limits():
    limits = model_constraints(get_smart_model())
    window = min(
        int(os.getenv("KNOWLEDGE_CONTEXT_WINDOW_TOKENS", "65536")),
        limits.get("context_limit", 10000000),
    )
    output = min(int(os.getenv("KNOWLEDGE_MAX_OUTPUT_TOKENS", "12000")), window // 4)
    margin = int(os.getenv("KNOWLEDGE_CONTEXT_MARGIN_TOKENS", "4096"))
    budget = request_budget(get_smart_model(), output=output, window=window, margin=margin)
    return budget.input_limit, budget.output_limit


def source_pins(sources, people, relationships):
    pinned = set()
    for source in sources:
        metadata = source.get("metadata") or source.get("payload") or {}
        pinned.update(
            value for key in ("identity_id", "contact_id") if (value := metadata.get(key))
        )
        pinned.update(metadata.get("confirmed_contact_ids") or [])
    for person in people:
        if person.get("identity_id") in pinned and person.get("contact_id"):
            pinned.add(person["contact_id"])
    direct = set(pinned)
    for relation in relationships:
        pair = {relation.get("from_contact_id"), relation.get("to_contact_id")} - {None}
        if pair & direct:
            pinned.update(pair)
    return pinned


def compact_context(context, *, extraction=False, query="", anchors=()):
    words = set(re.findall(r"[^\W_]{3,}", query.casefold()))
    pinned = source_pins(
        context.get("source_context", []),
        context.get("people", []),
        context.get("contact_relationships", []),
    ) | set(anchors)

    def compact(value):
        if isinstance(value, str):
            return value[:1200]
        if isinstance(value, list):
            return [compact(v) for v in value[:8]]
        return value

    result = {}
    for section, fields in FIELDS.items():
        result[section] = []
        if extraction and section not in IDENTITY_SECTIONS:
            continue
        rows = []
        for row in context.get(section, []):
            item = {key: compact(row[key]) for key in fields if key in row}
            if section == "proposals" and isinstance(item.get("payload"), dict):
                item["payload"] = {
                    key: compact(value)
                    for key, value in item["payload"].items()
                    if key
                    in {
                        "statement",
                        "reason",
                        "existing_id",
                        "assignee_kind",
                        "assignee_ref",
                        "project_id",
                        "due_date",
                        "requested_status",
                    }
                }
            score = len(words & set(re.findall(r"[^\W_]{3,}", json_for(item).casefold())))
            if any(item.get(key) in pinned for key in REFERENCE_FIELDS if item.get(key)):
                score += 1000
            rows.append((score, item))
        rows.sort(key=lambda row: row[0], reverse=True)
        # No embeddings, evidence histories, raw metadata or source snapshots.
        budget = 6000 if section in IDENTITY_SECTIONS else 8000
        for _, item in rows[:40]:
            size = len(json_for(item).encode("utf-8"))
            if size <= budget:
                result[section].append(item)
                budget -= size
    return result


def fit_payload(payload, budget, system, response_format):
    context = {key: list(rows) for key, rows in payload.get("context", {}).items()}
    fitted = {**payload, "context": context}
    # Evidence stays intact. Primary sources are split before sacrificing the
    # identity map required to interpret them; optional background goes first.
    removable = ("related", "lookback", "projects", "entities", "proposals", "tasks", "knowledge")
    pinned = source_pins(
        payload.get("sources", []),
        context.get("people", []),
        context.get("contact_relationships", []),
    )
    for candidate in payload.get("candidates", []):
        for field in ("existing_id", "project_id", "assignee_ref", "subject_ref"):
            if value := candidate.get(field):
                pinned.update((value, value.partition(":")[2]))
    # Names present in evidence retain all matching candidates, including ambiguous
    # first names. Removing unrelated group members must not select an identity.
    source_words = set(re.findall(r"[^\W_]{3,}", json_for(payload.get("sources", [])).casefold()))
    for section in ("people", "contacts"):
        for row in context.get(section, []):
            if source_words & set(
                re.findall(r"[^\W_]{3,}", str(row.get("display_name", "")).casefold())
            ):
                pinned.update(
                    value for key in ("identity_id", "contact_id") if (value := row.get(key))
                )
    while input_estimate(fitted, system, response_format) > budget:
        removed = False
        for section in (*removable, "contact_relationships", "contacts", "people"):
            for index in range(len(context.get(section, [])) - 1, -1, -1):
                row = context[section][index]
                if not any(row.get(key) in pinned for key in REFERENCE_FIELDS if row.get(key)):
                    context[section].pop(index)
                    removed = True
                    break
            if removed:
                break
        if not removed:
            raise LLMRequestRejected("context_length_exceeded")
    return fitted
