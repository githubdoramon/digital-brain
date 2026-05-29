from __future__ import annotations

import json
from typing import Any

from prompts.clarification import append_clarification_guidelines
from search_normalization import normalize_search_text


def build_event_participant_filter_prompt(
    *,
    text: str,
    people: list[str],
    conversation_block: str,
    user_facts_block: str,
) -> str:
    prompt = f"""You are reviewing extracted people for an event command.

Text: "{text}"

Conversation context:
{conversation_block}{user_facts_block}
Candidate people extracted from the text:
{json.dumps(people, ensure_ascii=True)}

Task:
- Keep ONLY people who are actual participants of the event/request.
- Exclude people who are merely mentioned as background context, examples, employers, relatives of someone else, or third parties not actually part of the event.
- Treat explicit clarification like "do not add", "should not be added", "was not part of the call", or equivalent as a hard exclusion.
- If the user says a person was worked with, talked about, or referenced, that alone does NOT make them a participant.
- The current user may remain as "user" only when they are actually part of the event.
- You MUST choose only from the provided candidate list; do not invent new people.

Return ONLY valid JSON:
{{
  "participants": ["person1", "user"],
  "excluded": ["person2"]
}}"""
    return append_clarification_guidelines(prompt)


def _dedupe_people_preserve_order(people: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for person in people:
        normalized = normalize_search_text(person)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(person)
    return deduped


def parse_event_participant_filter_result(
    *,
    people: list[str],
    result: dict[str, Any],
) -> tuple[list[str], list[str]]:
    allowed = {normalize_search_text(person): person for person in people}
    participants: list[str] = []
    excluded: list[str] = []

    for key, target in (("participants", participants), ("excluded", excluded)):
        values = result.get(key, [])
        if not isinstance(values, list):
            continue
        for value in values:
            normalized = normalize_search_text(str(value or ""))
            if not normalized or normalized not in allowed:
                continue
            target.append(allowed[normalized])

    participants = _dedupe_people_preserve_order(participants)
    excluded = [
        person for person in _dedupe_people_preserve_order(excluded) if person not in participants
    ]
    return participants, excluded
