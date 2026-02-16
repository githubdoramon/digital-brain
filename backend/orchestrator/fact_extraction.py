"""
Background fact extraction pipeline.

After each conversation exchange, this module extracts personal facts/preferences
about the user and reconciles them against existing facts using ADD/UPDATE/DELETE/NOOP
operations (inspired by the Mem0 architecture).

Called as a FastAPI BackgroundTask from llm.py — never blocks user responses.
"""

from __future__ import annotations

import json
from typing import Any

from observability.logger import get_runtime_logger
from user_facts import VALID_CATEGORIES

logger = get_runtime_logger(__name__)

# Minimum message length to trigger extraction (skip trivial exchanges)
MIN_USER_MESSAGE_LENGTH = 20

_CATEGORIES_LIST = ", ".join(sorted(VALID_CATEGORIES))

EXTRACTION_SYSTEM_PROMPT = """\
You are a fact extraction system for a personal memory assistant.
Your job is to identify personal facts about the USER from their conversation messages.

EXTRACT facts that are:
- Personal preferences (music, food, communication style, tools, hobbies)
- Biographical details (profession, languages spoken, education)
- Habits and routines (exercise, sleep, meal prep, commute)
- Health constraints (allergies, dietary restrictions, conditions)
- Opinions and values (technology preferences, work-life balance views)
- Long-term goals and aspirations (learning interests, career goals, savings targets)
- Behavioral preferences (prefers concise answers, likes examples)

DO NOT extract — the user's memory system already has dedicated storage for these:
- Information about other people (relationships, names, who someone is) — already stored as CONTACTS in the memory system
- Specific events or things that happened on a date ("I went to a concert Friday") — already stored as EVENTS
- Tasks, action items, or reminders ("I need to finish the report") — already stored as TODOS
- Locations, addresses, venues, or where someone lives/works ("My office is downtown", "I live in Aurora") — already stored as PLACES
- Document or file content — already stored as DOCUMENTS
- Transient states ("I'm tired today", "I'm busy right now") unless they indicate a lasting pattern
- Anything the assistant said (only extract from USER messages)

For each fact, provide:
- "content": A concise, self-contained statement (e.g., "Prefers rock music")
- "category": One of: {categories_placeholder}
- "importance": 1-10 (1-3=trivial, 4-6=useful context, 7-9=core identity/strong preference, 10=critical constraint)
- "action": One of: ADD, UPDATE, DELETE, NOOP
- "target_fact_id": (only for UPDATE/DELETE) The fact_id of the existing fact being modified

When existing facts are provided, compare new candidates against them:
- ADD: Genuinely new information not already captured
- UPDATE: Existing fact needs modification (e.g., preference changed)
- DELETE: Existing fact is now contradicted or explicitly retracted
- NOOP: Information is already captured — do nothing

Return a JSON object: {"facts": [...]} with an array of fact operations.
If no facts should be extracted, return: {"facts": []}
""".replace("{categories_placeholder}", _CATEGORIES_LIST)


def maybe_extract_facts(
    *,
    user_email: str,
    user_message: str,
    assistant_message: str,
    thread_id: str | None = None,
) -> None:
    """
    Entry point for background fact extraction.

    Includes a lightweight heuristic gate to skip trivial exchanges.
    Runs synchronously (called inside BackgroundTasks which handles threading).
    """
    # Gate: skip very short or trivial messages
    if not user_message or len(user_message.strip()) < MIN_USER_MESSAGE_LENGTH:
        logger.debug("[fact_extraction] skipping short message len=%d", len(user_message or ""))
        return

    # Gate: skip messages that look like pure commands
    stripped = user_message.strip()
    if stripped.startswith("/") and " " not in stripped:
        logger.debug("[fact_extraction] skipping command message")
        return

    try:
        _run_extraction(
            user_email=user_email,
            user_message=user_message,
            assistant_message=assistant_message,
            thread_id=thread_id,
        )
    except Exception:
        logger.exception("[fact_extraction] extraction failed for user=%s", user_email)


def _run_extraction(
    *,
    user_email: str,
    user_message: str,
    assistant_message: str,
    thread_id: str | None,
) -> None:
    """Core extraction + reconciliation pipeline."""
    import user_facts

    # Load existing facts for dedup/conflict resolution
    existing = user_facts.get_user_facts(user_email, limit=100)

    # Build context about existing contacts (to avoid extracting relationship info)
    contacts_summary = _get_contacts_summary(user_email)

    # Build the extraction prompt
    prompt = _build_extraction_prompt(
        user_message=user_message,
        assistant_message=assistant_message,
        existing_facts=existing,
        contacts_summary=contacts_summary,
    )

    # Call LLM for extraction
    from llm_helpers import call_llm_json

    try:
        result = call_llm_json(
            prompt,
            system_prompt=EXTRACTION_SYSTEM_PROMPT,
            temperature=0.0,
            use_simpler_model=True,
            timeout=60,
        )
    except (json.JSONDecodeError, RuntimeError) as exc:
        logger.warning("[fact_extraction] LLM returned unparseable response: %s", exc)
        return

    facts_list = result.get("facts", [])
    if not facts_list:
        logger.debug("[fact_extraction] no facts extracted for user=%s", user_email)
        return

    # Reconcile each candidate
    applied = 0
    for candidate in facts_list:
        try:
            action = _reconcile_fact(candidate, user_email=user_email, thread_id=thread_id)
            if action != "NOOP":
                applied += 1
        except Exception:
            logger.exception("[fact_extraction] failed to reconcile candidate: %s", candidate)

    if applied:
        logger.info(
            "[fact_extraction] extracted %d facts (%d applied) for user=%s",
            len(facts_list),
            applied,
            user_email,
        )


def _build_extraction_prompt(
    *,
    user_message: str,
    assistant_message: str,
    existing_facts: list[dict[str, Any]],
    contacts_summary: str,
) -> str:
    """Build the user prompt for the extraction LLM call."""
    parts: list[str] = []

    # Existing facts context
    if existing_facts:
        facts_lines = []
        for f in existing_facts:
            facts_lines.append(
                f'  - id="{f["fact_id"]}" [{f["category"]}] (importance={f["importance"]}): {f["content"]}'
            )
        parts.append("EXISTING USER FACTS:\n" + "\n".join(facts_lines))
    else:
        parts.append("EXISTING USER FACTS: (none yet)")

    # Contacts context (to avoid extracting relationship info)
    if contacts_summary:
        parts.append(f"USER'S KNOWN CONTACTS (do NOT extract these as facts):\n{contacts_summary}")

    # The conversation exchange
    parts.append(f"CONVERSATION EXCHANGE:\nUser: {user_message}\nAssistant: {assistant_message}")

    parts.append(
        "Extract any new personal facts about the user from the above exchange. "
        "Compare against existing facts and return appropriate actions."
    )

    return "\n\n".join(parts)


def _reconcile_fact(
    candidate: dict[str, Any],
    *,
    user_email: str,
    thread_id: str | None,
) -> str:
    """Apply a single fact candidate (ADD/UPDATE/DELETE/NOOP). Returns the action taken."""
    import user_facts

    action = (candidate.get("action") or "NOOP").upper()
    content = (candidate.get("content") or "").strip()
    category = candidate.get("category", "general")
    importance = candidate.get("importance", 5)
    target_id = candidate.get("target_fact_id")

    if action == "NOOP":
        return "NOOP"

    if action == "ADD":
        if not content:
            return "NOOP"
        user_facts.upsert_fact(
            user_email,
            content,
            category=category,
            importance=importance,
            source_thread_id=thread_id,
        )
        logger.info("[fact_extraction] ADD fact for user=%s: %s", user_email, content[:80])
        return "ADD"

    if action == "UPDATE":
        if not target_id or not content:
            # If no target, treat as ADD
            if content:
                user_facts.upsert_fact(
                    user_email,
                    content,
                    category=category,
                    importance=importance,
                    source_thread_id=thread_id,
                )
                logger.info(
                    "[fact_extraction] UPDATE->ADD (no target) for user=%s: %s",
                    user_email,
                    content[:80],
                )
                return "ADD"
            return "NOOP"
        user_facts.update_fact(
            target_id,
            content=content,
            category=category,
            importance=importance,
        )
        logger.info(
            "[fact_extraction] UPDATE fact_id=%s for user=%s: %s",
            target_id,
            user_email,
            content[:80],
        )
        return "UPDATE"

    if action == "DELETE":
        if target_id:
            user_facts.delete_fact(target_id)
            logger.info(
                "[fact_extraction] DELETE fact_id=%s for user=%s",
                target_id,
                user_email,
            )
            return "DELETE"
        return "NOOP"

    logger.warning("[fact_extraction] unknown action=%s, skipping", action)
    return "NOOP"


def _get_contacts_summary(user_email: str) -> str:
    """Build a lightweight summary of the user's contacts for dedup context."""
    try:
        import contacts

        all_contacts = contacts.list_contacts()
        if not all_contacts:
            return ""

        lines: list[str] = []
        for c in all_contacts[:30]:  # Cap to avoid huge context
            name = c.get("display_name", "")
            rels = c.get("relationships", [])
            rel_str = ""
            if rels:
                rel_labels = [
                    r.get("relationship_type", "") for r in rels[:3] if r.get("relationship_type")
                ]
                if rel_labels:
                    rel_str = f" ({', '.join(rel_labels)})"
            lines.append(f"  - {name}{rel_str}")

        return "\n".join(lines)
    except Exception:
        logger.warning("[fact_extraction] failed to load contacts summary")
        return ""
