"""
Handler for the /event command.

The /event command allows users to add new memories/events to the database.
It extracts entities, checks for existing ones, and asks for confirmation.
"""

import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any
from uuid import uuid4

from commands.parser import ParsedCommand
from commands.registry import CommandRegistry


def _format_existing_extraction_for_prompt(existing: dict[str, Any] | None) -> str:
    if not existing:
        return ""

    when_value = existing.get("when")
    if isinstance(when_value, datetime):
        when_value = when_value.isoformat()

    return (
        "Existing extraction (use as base, update only if new details override):\n"
        f"- title: {existing.get('title')!r}\n"
        f"- summary: {existing.get('summary')!r}\n"
        f"- when: {when_value!r}\n"
        f"- where: {existing.get('where')!r}\n"
        f"- documents: {existing.get('documents')!r}\n"
        f"- tags: {existing.get('tags')!r}\n"
        f"- types: {existing.get('types')!r}\n"
        "\n"
    )


def _format_clarification_history(
    clarification_messages: list[dict[str, str]] | None,
) -> str:
    if not clarification_messages:
        return ""

    user_lines = []
    assistant_lines = []
    for entry in clarification_messages:
        role = entry.get("role")
        content = entry.get("content")
        if not role or not content:
            continue
        if role == "assistant":
            assistant_lines.append(f"- {content}")
        else:
            user_lines.append(f"- {content}")

    sections = []
    if user_lines:
        sections.append("User-provided details (most recent last):\n" + "\n".join(user_lines))
    if assistant_lines:
        sections.append("Assistant questions asked (not facts):\n" + "\n".join(assistant_lines))

    if not sections:
        return ""

    return "\n\n".join(sections) + "\n\n"


def _format_conversation_json(
    original_message: str,
    clarification_messages: list[dict[str, str]] | None,
) -> str:
    import json

    messages: list[dict[str, str]] = [{"role": "user", "content": original_message}]
    if clarification_messages:
        for entry in clarification_messages:
            role = entry.get("role")
            content = entry.get("content")
            if not role or not content:
                continue
            if role == "user" and content.strip().lower() == original_message.strip().lower():
                continue
            messages.append({"role": role, "content": content})

    return json.dumps(messages, ensure_ascii=True)


def _build_contact_context_message(
    original_message: str,
    clarification_messages: list[dict[str, str]] | None,
) -> str:
    user_messages = [original_message]
    if clarification_messages:
        for entry in clarification_messages:
            if entry.get("role") != "user":
                continue
            content = entry.get("content")
            if not content:
                continue
            user_messages.append(content)

    combined: list[str] = []
    for msg in user_messages:
        normalized = msg.strip()
        if not normalized:
            continue
        if any(normalized.lower() in existing.lower() for existing in combined):
            continue
        combined.append(normalized)

    return " ".join(combined).strip()


def _resolve_ambiguous_contacts_from_answer(
    ambiguous_contacts: list[dict[str, Any]],
    answer: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    resolved: list[dict[str, Any]] = []
    remaining: list[dict[str, Any]] = []
    answer_lower = answer.lower()

    for item in ambiguous_contacts:
        candidates = item.get("candidates", [])
        matches = [
            candidate
            for candidate in candidates
            if (candidate.get("display_name") or "").lower() in answer_lower
        ]
        if len(matches) == 1:
            candidate = matches[0]
            resolved.append(
                {
                    "original_text": item.get("original_text"),
                    "contact_id": candidate.get("contact_id"),
                    "display_name": candidate.get("display_name"),
                    "matched_via": "clarification",
                    "confidence": "high",
                }
            )
        else:
            remaining.append(item)

    return resolved, remaining


def _should_skip_contact_resolution(
    answer: str,
    ambiguous_contacts: list[dict[str, Any]],
) -> bool:
    if not ambiguous_contacts:
        return False

    answer_lower = answer.lower()
    candidate_names: list[str] = []
    for item in ambiguous_contacts:
        for candidate in item.get("candidates", []):
            name = (candidate.get("display_name") or "").strip()
            if name:
                candidate_names.append(name.lower())

    if not candidate_names:
        return False

    has_candidate = any(name in answer_lower for name in candidate_names)
    short_answer = len(answer.strip()) <= 48
    return has_candidate and short_answer


def _extract_event_entities_with_llm(
    message: str,
    context: dict,
    existing_extraction: dict[str, Any] | None = None,
    clarification_messages: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """
    Use the existing LLM infrastructure to extract event entities.

    Args:
        message: The event description from the user
        context: Context dict with user info and time context

    Returns:
        Dict with extracted entities or needs_clarification flag
    """
    from llm_helpers import call_llm_json
    from llm_prompts import get_time_context
    from tags_manager import MAJOR_TAGS

    print(f"\n[event_extraction] Starting extraction for: '{message}'")

    # Get current time context
    time_context = get_time_context()
    user_email = context.get("user_email", "")

    print(f"[event_extraction] Time context: {time_context}")
    print(f"[event_extraction] User: {user_email}")

    # Build tag context
    tag_examples = ", ".join(MAJOR_TAGS[:5])  # Show first 5 major tags as examples

    existing_context = _format_existing_extraction_for_prompt(existing_extraction)
    clarification_context = _format_clarification_history(clarification_messages)
    conversation_json = _format_conversation_json(message, clarification_messages)
    conversation_context = (
        f"Conversation messages (JSON array, most recent last):\n{conversation_json}\n\n"
    )

    extraction_prompt = f"""You are extracting structured information from a user's event description to create a memory entry.

Current context:
- Date/time: {time_context}
- User: {user_email}

Event description: "{message}"

{existing_context}{conversation_context}{clarification_context}

Extract the following information:
1. **What happened**: A brief title (5-10 words) and detailed summary
2. **When**: Parse date/time. If relative (e.g., "yesterday", "last Tuesday"), convert to actual datetime using current context. If not mentioned, use current time.
3. **Where**: Location/place name (if mentioned)
4. **Documents**: References to documents/files (if mentioned)
5. **Tags**: Relevant tags for categorization. Consider major categories like: {tag_examples}, etc.
6. **Event types**: Choose from: generic, meeting, communication, task, creation, consumption, travel, personal, system, financial, observation, interaction, education, celebration, purchase, health

People extraction is handled separately. Do NOT include any people/person list.

Prefer specific types over general terms WHEN POSSIBLE (e.g., "Electric Engineer" over "Engineer", "Orthopedist" over "Doctor").

If ANY critical information is missing or ambiguous (excluding people), set "needs_clarification" to true and provide "clarification_questions".
Use the clarification history to avoid repeating questions that were already answered.
Never drop previously confirmed facts from the existing extraction or clarification history; only override if the user explicitly corrects them.
Assistant questions are prompts only and are NOT facts; only treat user-provided details as facts.
Do NOT ask clarification questions about people; contact resolution handles that separately.

Return ONLY valid JSON in this exact format:
{{
    "needs_clarification": false,
    "clarification_questions": [],
    "title": "Brief title",
    "summary": "Detailed description",
    "when": "ISO 8601 datetime or null",
    "where": "Location name or null",
    "documents": [],
    "tags": ["tag1", "tag2"],
    "types": ["generic"]
}}"""

    try:
        print("[event_extraction] Calling LLM for extraction...")
        extracted = call_llm_json(extraction_prompt, timeout=60)

        print("[event_extraction] Raw LLM response:")
        print(f"  - Title: {extracted.get('title')}")
        print(f"  - Summary: {extracted.get('summary')}")
        print(f"  - When: {extracted.get('when')}")
        print(f"  - Where: {extracted.get('where')}")
        print(f"  - Tags: {extracted.get('tags')}")
        print(f"  - Types: {extracted.get('types')}")
        print(f"  - Needs clarification: {extracted.get('needs_clarification')}")

        # Parse datetime if provided
        when = None
        if extracted.get("when"):
            try:
                when = datetime.fromisoformat(extracted["when"].replace("Z", "+00:00"))
                print(f"[event_extraction] Parsed datetime: {when}")
            except (ValueError, AttributeError) as e:
                print(f"[event_extraction] Failed to parse datetime '{extracted.get('when')}': {e}")

        result = {
            "needs_clarification": extracted.get("needs_clarification", False),
            "clarification_questions": extracted.get("clarification_questions", []),
            "title": extracted.get("title", message[:100]),
            "summary": extracted.get("summary", message),
            "when": when,
            "where": extracted.get("where"),
            "who": [],
            "documents": extracted.get("documents", []),
            "tags": extracted.get("tags", []),
            "types": extracted.get("types", ["generic"]),
        }

        if existing_extraction:
            for key in ["title", "summary", "when", "where", "documents", "tags", "types"]:
                if result.get(key) in (None, "", [], ["generic"]) and existing_extraction.get(key):
                    result[key] = existing_extraction[key]

        print("[event_extraction] Extraction complete")
        return result

    except Exception as e:
        print(f"[event_extraction] ERROR: LLM extraction failed: {e}")
        import traceback

        traceback.print_exc()

        # Fallback to basic extraction
        return {
            "needs_clarification": True,
            "clarification_questions": [
                "Could you provide more details about what happened, when, and who was involved?"
            ],
            "title": message[:100],
            "summary": message,
            "when": None,
            "where": None,
            "who": [],
            "documents": [],
            "tags": [],
            "types": ["generic"],
        }


def _extract_clarification_token(message: str) -> tuple[str, str | None]:
    token_pattern = re.compile(r"\[clarification_id:(?P<id>[\w:-]+)\]", re.IGNORECASE)
    match = token_pattern.search(message)
    if not match:
        return message, None

    cleaned = token_pattern.sub("", message).strip()
    return cleaned, match.group("id")


def _resolve_generic_terms_with_relationships(
    terms: list[str],
    user_email: str,
) -> dict[str, str]:
    """
    Resolve generic relational terms to actual contact names using relationship data.

    Examples:
    - "my daughter" -> "Emma" (if user has daughter relationship)
    - "the doctor" -> "Dr. Smith" (if user has doctor relationship)
    - "my wife" -> "Sarah" (if user has spouse relationship)

    Args:
        terms: List of terms that might be generic (e.g., ["my daughter", "the doctor"])
        user_email: User's email to find their contact and relationships

    Returns:
        Dict mapping generic terms to actual names (e.g., {"my daughter": "Emma"})
    """
    import contacts as contacts_service

    print(f"\n[generic_resolution] Attempting to resolve {len(terms)} terms: {terms}")

    resolved = {}

    # Find user's contact record
    user_contact = contacts_service.find_self_contact(user_email)
    if not user_contact:
        print(f"[generic_resolution] User contact not found for: {user_email}")
        return resolved

    user_id = user_contact["contact_id"]
    print(f"[generic_resolution] User contact ID: {user_id} ({user_contact.get('display_name')})")

    # Get all relationships for the user
    relationships_result = contacts_service.get_contact_relationships(
        user_id,
        include_contact_details=True,
    )

    relationships = relationships_result.get("relationships", [])
    print(f"[generic_resolution] Found {len(relationships)} relationships")

    # Build a map of relationship types to contacts
    rel_map: dict[str, list[dict]] = {}
    for rel in relationships:
        rel_type = (rel.get("type") or "").lower()
        if rel_type and "related_contact" in rel:
            if rel_type not in rel_map:
                rel_map[rel_type] = []
            rel_map[rel_type].append(rel["related_contact"])

    if rel_map:
        print(f"[generic_resolution] Relationship types available: {list(rel_map.keys())}")
    else:
        print("[generic_resolution] No relationships with contact details found")

    # Try to resolve each term
    for term in terms:
        term_lower = term.lower().strip()
        print(f"[generic_resolution] Processing term: '{term}'")

        # Extract relationship type from phrases like "my daughter", "the doctor", "user's daughter"
        # Remove possessives, articles, and "user's"
        cleaned = (
            term_lower.replace("user's ", "")
            .replace("my ", "")
            .replace("the ", "")
            .replace("a ", "")
            .replace("an ", "")
            .strip()
        )
        print(f"[generic_resolution]   Cleaned to: '{cleaned}'")

        # Direct match first
        if cleaned in rel_map and rel_map[cleaned]:
            contact = rel_map[cleaned][0]
            resolved_name = contact.get("display_name", term)
            resolved[term] = resolved_name
            print(f"[generic_resolution]   ✓ Direct match: '{term}' -> '{resolved_name}'")
            continue

        # Smart matching: look for related relationship types
        # For example: "daughter" should match "child", "father" should match "parent"
        # Use the shared relationship type mappings from contacts module
        possible_types = contacts_service.find_related_types(cleaned)
        print(f"[generic_resolution]   Trying relationship types: {possible_types}")

        for rel_type in possible_types:
            if rel_type in rel_map and rel_map[rel_type]:
                contact = rel_map[rel_type][0]
                resolved_name = contact.get("display_name", term)
                resolved[term] = resolved_name
                print(
                    f"[generic_resolution]   ✓ Smart match via '{rel_type}': '{term}' -> '{resolved_name}'"
                )
                break
        else:
            print(f"[generic_resolution]   ✗ No match for '{cleaned}' or related types")

    print(f"[generic_resolution] Resolution complete. Resolved {len(resolved)}/{len(terms)} terms")
    return resolved


def _replace_generic_terms_in_text(
    text: str,
    replacements: dict[str, str],
) -> str:
    """
    Replace generic terms with actual names in text.

    Args:
        text: Original text with generic terms
        replacements: Dict mapping generic terms to actual names

    Returns:
        Text with generic terms replaced
    """
    result = text
    for generic, actual in replacements.items():
        # Case-insensitive replacement that preserves case structure
        import re

        pattern = re.compile(re.escape(generic), re.IGNORECASE)
        result = pattern.sub(actual, result)
    return result


def _dedupe_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for item in items:
        normalized = item.strip()
        if not normalized:
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(normalized)
    return deduped


def _dedupe_contacts(contacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen_ids: set[str] = set()
    seen_names: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for contact in contacts:
        contact_id = str(contact.get("contact_id") or "")
        display_name = (contact.get("display_name") or "").strip()
        if contact_id and contact_id in seen_ids:
            continue
        if display_name and display_name.lower() in seen_names:
            continue
        if contact_id:
            seen_ids.add(contact_id)
        if display_name:
            seen_names.add(display_name.lower())
        deduped.append(contact)
    return deduped


def _resolve_contacts_with_agent(
    message: str,
    user_email: str,
    conversation_messages: list[dict[str, str]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """
    Resolve contacts for the event using the contact resolution agent.

    Returns:
        Tuple of:
        - resolution dict in /event shape
        - raw contact agent result
    """
    from agents.contacts import resolve_contacts_from_text

    contact_result = resolve_contacts_from_text(
        message,
        user_email,
        conversation_messages=conversation_messages,
    )

    resolution = {
        "contacts": [],
        "places": [],
        "documents": [],
        "new_entities": {
            "contacts": [],
            "places": [],
            "documents": [],
        },
        "name_replacements": {},
    }

    for resolved in contact_result.get("resolved_contacts", []):
        resolution["contacts"].append(
            {
                "contact_id": resolved.get("contact_id"),
                "display_name": resolved.get("display_name"),
                "query": resolved.get("original_text"),
                "confidence": resolved.get("confidence", "medium"),
            }
        )

        original_text = resolved.get("original_text")
        display_name = resolved.get("display_name")
        if original_text and display_name and original_text.lower() != display_name.lower():
            if original_text.lower() != "user":
                resolution["name_replacements"][original_text] = display_name

    for new_contact in contact_result.get("new_contacts", []):
        original_text = new_contact.get("original_text")
        display_name = new_contact.get("display_name") or original_text
        resolution["new_entities"]["contacts"].append(
            {
                "display_name": display_name,
                "query": original_text or display_name,
                "inferred_profession": new_contact.get("inferred_profession"),
            }
        )

        if original_text and display_name and original_text.lower() != display_name.lower():
            if original_text.lower() != "user":
                resolution["name_replacements"][original_text] = display_name

    return resolution, contact_result


def _format_relationship_suggestions(
    suggestions: list[dict[str, Any]],
    resolution: dict[str, Any],
) -> list[dict[str, Any]]:
    """
    Map contact agent relationship suggestions into /event UI shape.
    """
    if not suggestions:
        return []

    id_by_text: dict[str, str] = {}
    name_by_text: dict[str, str] = {}

    for contact in resolution.get("contacts", []):
        display_name = contact.get("display_name")
        query = contact.get("query")
        contact_id = contact.get("contact_id")
        if query and display_name:
            name_by_text[query] = display_name
        if display_name:
            name_by_text[display_name] = display_name
        if contact_id:
            if query:
                id_by_text[query] = contact_id
            if display_name:
                id_by_text[display_name] = contact_id

    for contact in resolution.get("new_entities", {}).get("contacts", []):
        display_name = contact.get("display_name")
        query = contact.get("query")
        if query and display_name:
            name_by_text[query] = display_name
        if display_name:
            name_by_text[display_name] = display_name

    formatted: list[dict[str, Any]] = []
    for suggestion in suggestions:
        from_text = suggestion.get("from_text")
        to_text = suggestion.get("to_text")
        if not from_text or not to_text:
            continue

        from_display = name_by_text.get(from_text, from_text)
        to_display = name_by_text.get(to_text, to_text)

        formatted.append(
            {
                "from_contact_id": suggestion.get("from_contact_id") or id_by_text.get(from_text),
                "from_display_name": from_display,
                "to_contact_id": suggestion.get("to_contact_id") or id_by_text.get(to_text),
                "to_display_name": to_display,
                "relationship_type": suggestion.get("type") or "",
                "reciprocal_type": suggestion.get("other_type") or "",
                "confidence": "medium",
                "reasoning": suggestion.get("relationship_hint") or "",
            }
        )

    return formatted


def _suggest_relationships_from_context(
    message: str,
    extracted: dict[str, Any],
    resolution: dict[str, Any],
) -> list[dict[str, Any]]:
    """
    Analyze the event context to suggest relationships between contacts.

    Examples:
    - "took my daughter to the doctor" -> suggest doctor-patient relationship
    - "had lunch with my colleague John" -> suggest colleague relationship

    Args:
        message: Original event message
        extracted: Extracted event data
        resolution: Resolved entities

    Returns:
        List of suggested relationships with from/to contacts and type
    """
    from llm_helpers import call_llm_json

    print("\n[relationship_suggestion] Analyzing event for relationship suggestions")

    suggestions = []

    # Get all resolved contacts
    contacts = resolution.get("contacts", [])
    print(f"[relationship_suggestion] Found {len(contacts)} resolved contacts")

    if len(contacts) < 2:
        # Need at least 2 contacts to suggest relationships
        print("[relationship_suggestion] Not enough contacts (need at least 2), skipping")
        return suggestions

    # Use LLM to detect implied relationships
    contact_list = ", ".join(c["display_name"] for c in contacts)
    print(f"[relationship_suggestion] Analyzing relationships between: {contact_list}")

    prompt = f"""Analyze this event description and identify any implied relationships between the people mentioned.

Event: "{message}"
People involved: {contact_list}

Common relationship types:
- Family: parent, child, sibling, spouse, partner, grandparent, grandchild, cousin, uncle, aunt, nephew, niece
- Professional: colleague, manager, employee, client, vendor, doctor, patient, lawyer, therapist, teacher, student
- Social: friend, neighbor, acquaintance

Return ONLY valid JSON with suggested relationships. If no clear relationships, return empty array:
{{
    "relationships": [
        {{
            "from_person": "exact name from list",
            "to_person": "exact name from list",
            "relationship_type": "type",
            "reciprocal_type": "type from other perspective",
            "confidence": "high|medium|low",
            "reasoning": "brief explanation"
        }}
    ]
}}"""

    try:
        print("[relationship_suggestion] Calling LLM for relationship analysis...")
        result = call_llm_json(prompt, timeout=15)
        llm_suggestions = result.get("relationships", [])

        print(f"[relationship_suggestion] LLM returned {len(llm_suggestions)} suggestions")

        # Map names back to contact IDs
        name_to_id = {c["display_name"]: c["contact_id"] for c in contacts}

        for idx, sug in enumerate(llm_suggestions):
            from_name = sug.get("from_person")
            to_name = sug.get("to_person")
            rel_type = sug.get("relationship_type")
            reciprocal = sug.get("reciprocal_type")
            confidence = sug.get("confidence")

            print(f"[relationship_suggestion]   Suggestion {idx + 1}:")
            print(f"[relationship_suggestion]     {from_name} -> {to_name}")
            print(f"[relationship_suggestion]     Type: {rel_type} (reciprocal: {reciprocal})")
            print(f"[relationship_suggestion]     Confidence: {confidence}")
            print(f"[relationship_suggestion]     Reasoning: {sug.get('reasoning')}")

            if from_name in name_to_id and to_name in name_to_id:
                suggestions.append(
                    {
                        "from_contact_id": name_to_id[from_name],
                        "from_display_name": from_name,
                        "to_contact_id": name_to_id[to_name],
                        "to_display_name": to_name,
                        "relationship_type": rel_type or "",
                        "reciprocal_type": reciprocal or "",
                        "confidence": confidence or "medium",
                        "reasoning": sug.get("reasoning", ""),
                    }
                )
                print("[relationship_suggestion]     ✓ Added to suggestions")
            else:
                print("[relationship_suggestion]     ✗ Names not found in contact list, skipping")

        print(
            f"[relationship_suggestion] Suggestion complete. Created {len(suggestions)} suggestions"
        )

    except Exception as e:
        print(f"[relationship_suggestion] ERROR: Relationship suggestion failed: {e}")
        import traceback

        traceback.print_exc()

    return suggestions


def _resolve_existing_entities(
    entities: dict[str, Any],
    user_email: str,
) -> dict[str, Any]:
    """
    Search database for existing entities using existing resolution tools.
    Also resolves generic terms to actual names using relationship data.

    Args:
        entities: Extracted entities from message
        user_email: User's email for relationship context

    Returns:
        Dict with matched and new entities, plus name replacements
    """
    import contacts as contacts_service

    print("\n[entity_resolution] Starting entity resolution")

    resolution = {
        "contacts": [],
        "places": [],
        "documents": [],
        "new_entities": {
            "contacts": [],
            "places": [],
            "documents": [],
        },
        "name_replacements": {},  # Maps generic terms to actual names
    }

    # First, try to resolve generic terms using relationships
    who_list = entities.get("who", [])
    print(f"[entity_resolution] People to resolve: {who_list}")

    if who_list:
        replacements = _resolve_generic_terms_with_relationships(who_list, user_email)
        resolution["name_replacements"] = replacements
        if replacements:
            print(f"[entity_resolution] Name replacements: {replacements}")

    # Resolve contacts using existing search_contacts function
    for idx, person_name in enumerate(entities.get("who", []), 1):
        if not person_name or not isinstance(person_name, str):
            print(f"[entity_resolution]   Person {idx}: Skipping invalid name")
            continue

        # Use the actual name if we resolved a generic term
        search_name = resolution["name_replacements"].get(person_name, person_name)
        print(
            f"[entity_resolution]   Person {idx}: '{person_name}' -> searching for '{search_name}'"
        )

        matches = contacts_service.search_contacts(
            search_name,
            search_by="name",
            fuzzy_threshold=75,
            limit=3,
        )

        print(f"[entity_resolution]     Found {len(matches)} matches")

        if matches:
            # Add first match with confidence
            best_match = matches[0]
            match_score = best_match.get("match_score", 0)
            confidence = "high" if match_score > 90 else "medium"

            resolution["contacts"].append(
                {
                    "contact_id": best_match["contact_id"],
                    "display_name": best_match["display_name"],
                    "query": person_name,  # Original query
                    "confidence": confidence,
                }
            )
            print(
                f"[entity_resolution]     ✓ Matched to existing: {best_match['display_name']} (ID: {best_match['contact_id']}, score: {match_score}, confidence: {confidence})"
            )
        else:
            # Mark as new contact to create (use resolved name if available)
            display_name = resolution["name_replacements"].get(person_name, person_name)
            resolution["new_entities"]["contacts"].append(
                {
                    "display_name": display_name,
                    "query": person_name,
                }
            )
            print(f"[entity_resolution]     ✗ No match, will create new contact: '{display_name}'")

    # Resolve places (simple implementation for now)
    where = entities.get("where")
    if where:
        print(f"[entity_resolution] Place: '{where}' -> creating new place")
        # TODO: Search for existing places
        # For now, always create new places
        resolution["new_entities"]["places"].append(
            {
                "name": where,
                "query": where,
            }
        )

    print("[entity_resolution] Resolution complete:")
    print(f"[entity_resolution]   - Matched contacts: {len(resolution['contacts'])}")
    print(f"[entity_resolution]   - New contacts: {len(resolution['new_entities']['contacts'])}")
    print(f"[entity_resolution]   - New places: {len(resolution['new_entities']['places'])}")

    return resolution


def handle_event(parsed: ParsedCommand, context: dict) -> dict[str, Any]:
    """
    Handle the /event command.

    Flow:
    1. Extract entities using LLM
    2. Check if clarification is needed
    3. Search for existing entities and resolve generic terms
    4. Replace generic terms in titles/summaries with actual names
    5. Suggest relationships between contacts
    6. Store data for confirmation
    7. Return confirmation request or ask for clarification

    Args:
        parsed: Parsed command with event description as args
        context: Context dict with user info

    Returns:
        Dict with event_confirmation or clarification_needed type
    """
    print(f"\n{'=' * 80}")
    print("[handle_event] NEW EVENT COMMAND")
    print(f"{'=' * 80}")

    if not parsed.args:
        return {
            "type": "error",
            "message": "Please provide an event description. Example: /event met with John at the cafe yesterday",
        }

    user_email = context.get("user_email", "")
    print(f"[handle_event] User: {user_email}")
    raw_message, clarification_id = _extract_clarification_token(parsed.args)
    print(f"[handle_event] Input: '{raw_message}'")

    clarification_context = None
    if clarification_id:
        from commands.storage import delete_command_data, get_command_data

        clarification_context = get_command_data(clarification_id)
        delete_command_data(clarification_id)
        if clarification_context:
            print(f"[handle_event] Found clarification context: {clarification_id}")
        else:
            print(f"[handle_event] Clarification context missing or expired: {clarification_id}")

    clarification_messages = None
    event_message = raw_message
    contact_message = raw_message
    contact_result = None
    resolution = None
    previous_contact_result: dict[str, Any] = {}
    previous_resolution: dict[str, Any] = {}
    skip_contact_resolution = False
    original_message_to_store = raw_message
    if clarification_context:
        clarification_messages = clarification_context.get("clarification_messages")
        if raw_message:
            clarification_messages = list(clarification_messages or [])
            clarification_messages.append({"role": "user", "content": raw_message})
        original_message = clarification_context.get("original_message") or raw_message
        original_message_to_store = original_message
        event_message = original_message
        contact_message = _build_contact_context_message(
            original_message,
            clarification_messages,
        )
        previous_contact_result = clarification_context.get("contact_result") or {}
        previous_resolution = clarification_context.get("resolution") or {}
        ambiguous_contacts = previous_contact_result.get("ambiguous_contacts", [])

        if raw_message and ambiguous_contacts:
            resolved_contacts, remaining_contacts = _resolve_ambiguous_contacts_from_answer(
                ambiguous_contacts,
                raw_message,
            )
            if resolved_contacts:
                previous_contact_result = {
                    **previous_contact_result,
                    "resolved_contacts": previous_contact_result.get("resolved_contacts", [])
                    + resolved_contacts,
                    "ambiguous_contacts": remaining_contacts,
                }
                resolved_entries = previous_resolution.get("contacts", [])
                for resolved_contact in resolved_contacts:
                    if not resolved_contact.get("contact_id"):
                        continue
                    resolved_entries.append(
                        {
                            "contact_id": resolved_contact.get("contact_id"),
                            "display_name": resolved_contact.get("display_name"),
                            "query": resolved_contact.get("original_text"),
                            "confidence": resolved_contact.get("confidence", "high"),
                        }
                    )
                previous_resolution["contacts"] = resolved_entries
                resolution = previous_resolution
                contact_result = previous_contact_result
                skip_contact_resolution = _should_skip_contact_resolution(
                    raw_message,
                    ambiguous_contacts,
                )

    # Extract entities using LLM with time context
    print("\n[handle_event] STEP 1: Extracting entities with LLM...")
    with ThreadPoolExecutor(max_workers=2) as executor:
        extraction_future = executor.submit(
            _extract_event_entities_with_llm,
            event_message,
            context,
            clarification_context.get("extracted") if clarification_context else None,
            clarification_messages,
        )
        contact_future = None
        if not skip_contact_resolution:
            contact_future = executor.submit(
                _resolve_contacts_with_agent,
                contact_message,
                user_email,
                clarification_messages,
            )

        extracted = extraction_future.result()
        if contact_future:
            resolution, contact_result = contact_future.result()
        else:
            resolution = resolution or previous_resolution
            contact_result = contact_result or previous_contact_result

    # Check if clarification is needed
    clarification_questions = (
        extracted.get("clarification_questions", []) if extracted.get("needs_clarification") else []
    )
    if extracted.get("needs_clarification") and not clarification_questions:
        clarification_questions = [
            "Could you clarify the missing details (what happened, when, and where)?",
        ]

    ambiguous_contacts = contact_result.get("ambiguous_contacts", []) if contact_result else []
    if ambiguous_contacts:
        print("[handle_event] ⚠️  Contact disambiguation needed")
        clarification_questions.extend(
            [
                contact.get("clarification_prompt")
                for contact in ambiguous_contacts
                if contact.get("clarification_prompt")
            ]
        )
        if not clarification_questions:
            clarification_questions.append(
                "I found multiple matching contacts. Can you clarify who you meant?"
            )

    if clarification_questions:
        print("[handle_event] ⚠️  Clarification needed, returning questions to user")
        clarification_preview_id = f"event:clarification:{uuid4().hex[:8]}"
        from commands.storage import store_command_data

        if clarification_messages is None:
            clarification_messages = [
                {"role": "user", "content": raw_message},
            ]
        clarification_messages.append(
            {
                "role": "assistant",
                "content": " ".join(clarification_questions),
            }
        )

        store_command_data(
            clarification_preview_id,
            {
                "extracted": extracted,
                "resolution": resolution,
                "contact_result": contact_result,
                "user_email": user_email,
                "original_message": original_message_to_store,
                "clarification_messages": clarification_messages,
            },
        )
        return {
            "type": "clarification_needed",
            "questions": clarification_questions,
            "partial_extraction": extracted,
            "original_message": raw_message,
            "clarification_id": clarification_preview_id,
        }

    # Resolve existing entities and generic terms
    print("\n[handle_event] STEP 2: Contact resolution complete")

    # Keep place handling aligned with existing flow
    where = extracted.get("where")
    if where:
        resolution["new_entities"]["places"].append(
            {
                "name": where,
                "query": where,
            }
        )

    # Replace generic terms with actual names in title and summary
    name_replacements = resolution.get("name_replacements", {})
    if name_replacements:
        print("\n[handle_event] STEP 3: Replacing generic terms in text...")
        original_title = extracted.get("title", "")
        original_summary = extracted.get("summary", "")

        extracted["title"] = _replace_generic_terms_in_text(original_title, name_replacements)
        extracted["summary"] = _replace_generic_terms_in_text(original_summary, name_replacements)

        if extracted["title"] != original_title:
            print(f"[handle_event]   Title: '{original_title}' -> '{extracted['title']}'")
        if extracted["summary"] != original_summary:
            print(f"[handle_event]   Summary: '{original_summary}' -> '{extracted['summary']}'")
    else:
        print("\n[handle_event] STEP 3: No generic terms to replace")

    # Suggest relationships between contacts based on context
    print("\n[handle_event] STEP 4: Suggesting relationships...")
    relationship_suggestions = _format_relationship_suggestions(
        contact_result.get("suggested_relationships", []),
        resolution,
    )

    # Update extracted "who" from contact agent results
    resolution["contacts"] = _dedupe_contacts(resolution.get("contacts", []))
    resolution["new_entities"]["contacts"] = _dedupe_contacts(
        resolution.get("new_entities", {}).get("contacts", [])
    )

    extracted["who"] = _dedupe_preserve_order(
        [
            contact["display_name"]
            for contact in resolution.get("contacts", [])
            if contact.get("display_name")
        ]
        + [
            contact["display_name"]
            for contact in resolution.get("new_entities", {}).get("contacts", [])
            if contact.get("display_name")
        ]
    )

    # Generate a preview ID and store the data
    preview_id = f"event:preview:{uuid4().hex[:8]}"

    from commands.storage import store_command_data

    print(f"\n[handle_event] STEP 5: Storing preview data (ID: {preview_id})")
    store_command_data(
        preview_id,
        {
            "extracted": extracted,
            "resolution": resolution,
            "user_email": user_email,
            "relationship_suggestions": relationship_suggestions,
            "original_message": original_message_to_store,
            "thread_id": context.get("thread_id"),
            "clarification_messages": clarification_messages,
        },
    )

    pending_key = context.get("event_pending_key")
    if pending_key:
        from commands.storage import store_pending_event

        store_pending_event(pending_key, preview_id)

    print("\n[handle_event] ✓ Event processing complete!")
    print("[handle_event] Summary:")
    print(f"  - Title: {extracted.get('title')}")
    print(f"  - Contacts found: {len(resolution.get('contacts', []))}")
    print(f"  - New contacts: {len(resolution.get('new_entities', {}).get('contacts', []))}")
    print(f"  - Relationship suggestions: {len(relationship_suggestions)}")
    print(f"{'=' * 80}\n")

    return {
        "type": "event_confirmation",
        "preview_id": preview_id,
        "extracted": extracted,
        "resolution": resolution,
        "relationship_suggestions": relationship_suggestions,
        "requires_confirmation": True,
        "message": "I've extracted the following information from your event. Please review and confirm:",
    }


def register(registry: CommandRegistry) -> None:
    """Register the /event command."""
    registry.register(
        name="event",
        handler=handle_event,
        description="Add a new memory/event to the database",
        requires_args=True,
    )
