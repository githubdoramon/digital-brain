"""
Contact resolution module.

This module resolves person mentions in text to specific contacts in the database.
It supports:
- Direct name matching (fuzzy search)
- Relationship resolution ("my daughter" → Emma)
- Nested relationships ("my daughter's doctor" → Dr. Smith via Emma)
- LLM disambiguation when multiple matches exist

CRITICAL: This module NEVER hallucinates. It only returns:
- Resolved contacts that exist in the database
- Candidates when multiple matches exist
- "new" status when no match is found

Design principles:
1. No database writes - only reads for matching
2. Returns resolution candidates, not decisions
3. Uses LLM only for disambiguation, never creation
4. Clear confidence scoring
"""

from typing import Any, Optional

import contacts as contacts_service
from llm_helpers import call_llm_json


def extract_people_from_text(text: str, user_email: Optional[str] = None) -> list[str]:
    """
    Extract person mentions from text using LLM.

    Args:
        text: The input text to analyze
        user_email: User's email (used to get their name for LLM context)

    Returns:
        List of person mentions (e.g., ["John", "my daughter", "my daughter's doctor"])
    """
    # Get user's name for better LLM context
    user_context = ""
    if user_email:
        user_contact = contacts_service.find_self_contact(user_email)
        if user_contact:
            user_name = user_contact.get("display_name", "User")
            user_context = f"\nCurrent user asking about contacts: {user_name}"

    prompt = f"""Extract all person references from this text.

Text: "{text}"{user_context}

Extract ONLY people - all person references including:
- Proper names (e.g., "John Smith")
- Relational terms (e.g., "my daughter", "the doctor")
- Nested relationships (e.g., "my daughter's doctor", "my son's teacher", "my wife's family")

IMPORTANT:
- Keep relationship phrases intact (e.g., "my daughter's doctor" as ONE entity)
- Include both proper names and generic references
- If a person is mentioned multiple ways, include all mentions

Return ONLY valid JSON:
{{
    "people": ["person1", "my daughter", "person2's doctor"]
}}"""

    try:
        result = call_llm_json(prompt, timeout=15)
        return result.get("people", [])
    except Exception as e:
        print(f"[contact_resolver] Failed to extract people: {e}")
        return []


def resolve_contact(
    person_text: str,
    user_email: str,
    *,
    event_context: Optional[str] = None,
) -> dict[str, Any]:
    """
    Resolve a person mention to a specific contact.

    This is the main resolution function. It handles all resolution strategies:
    1. Nested relationships ("my daughter's doctor")
    2. Direct relationships ("my daughter")
    3. Fuzzy name search
    4. LLM disambiguation (when multiple matches)

    Args:
        person_text: The person reference to resolve (e.g., "John", "my daughter's doctor")
        user_email: User's email for relationship lookups
        event_context: Optional full event text for LLM disambiguation

    Returns:
        {
            "status": "resolved" | "candidates" | "new",
            "confidence": "high" | "medium" | "low",
            "contact_id": Optional[str],
            "display_name": Optional[str],
            "matched_via": "direct_match" | "relationship" | "nested_relationship" | "llm_disambiguation",
            "resolution_path": Optional[List[str]],  # For nested: ["user", "Emma", "Dr. Smith"]
            "candidates": [{"contact_id": str, "display_name": str, "match_score": float}],
            "needs_clarification": bool,
            "clarification_prompt": Optional[str]
        }
    """
    print(f"\n[contact_resolver] Resolving: '{person_text}'")

    result: dict[str, Any] = {
        "status": "new",
        "confidence": "low",
        "contact_id": None,
        "display_name": None,
        "matched_via": None,
        "resolution_path": None,
        "candidates": [],
        "needs_clarification": False,
        "clarification_prompt": None,
    }

    # Step 1: Check for nested relationships (e.g., "my daughter's doctor")
    nested_parts = _parse_nested_relationship(person_text)
    print(f"[contact_resolver] Nested parts: {nested_parts}")
    if nested_parts and len(nested_parts) > 1:
        print(f"[contact_resolver] Detected nested relationship: {nested_parts}")
        nested_result = _resolve_nested_relationship(nested_parts, user_email)

        if nested_result["found"]:
            result["status"] = "resolved"
            result["confidence"] = nested_result["confidence"]
            result["contact_id"] = nested_result["contact_id"]
            result["display_name"] = nested_result["display_name"]
            result["matched_via"] = "nested_relationship"
            result["resolution_path"] = nested_result["path"]
            print(f"[contact_resolver] ✓ Resolved via nested: {' → '.join(nested_result['path'])}")
            return result

        print("[contact_resolver] Nested resolution failed, falling back")

    # Step 2: Check for simple relationship (e.g., "my daughter")
    is_generic, relationship_type = _detect_relational_term(person_text)
    if is_generic and relationship_type:
        # Get user's relationships
        user_contact = contacts_service.find_self_contact(user_email)
        if user_contact:
            relationships = contacts_service.get_contact_relationships(
                user_contact["contact_id"],
                include_contact_details=True,
            )
            print(f"[contact_resolver] Relationships: {relationships}")

            rel_result = _resolve_via_relationship(relationship_type, relationships)
            print(f"[contact_resolver] Relationship result: {rel_result}")
            if rel_result["found"]:
                result["status"] = "resolved"
                result["confidence"] = rel_result["confidence"]
                result["contact_id"] = rel_result["contact_id"]
                result["display_name"] = rel_result["display_name"]
                result["matched_via"] = "relationship"
                print(f"[contact_resolver] ✓ Resolved via relationship: {rel_result['display_name']}")
                return result

    # Step 3: Try direct fuzzy search
    search_name = person_text
    if is_generic:
        # Strip generic markers for better search
        search_name = _strip_generic_markers(person_text)

    print(f"[contact_resolver] Searching for: '{search_name}'")
    matches = contacts_service.search_contacts(
        search_name,
        search_by="name",
        fuzzy_threshold=75,
        limit=5,
    )

    if len(matches) == 0:
        # No matches - new contact
        print("[contact_resolver] ✗ No matches, marking as new")
        result["status"] = "new"
        result["display_name"] = person_text
        return result

    elif len(matches) == 1:
        # Single match - resolved!
        match = matches[0]
        result["status"] = "resolved"
        result["confidence"] = "high" if match.get("match_score", 0) > 90 else "medium"
        result["contact_id"] = match["contact_id"]
        result["display_name"] = match["display_name"]
        result["matched_via"] = "direct_match"
        print(f"[contact_resolver] ✓ Single match: {match['display_name']} (score: {match.get('match_score')})")
        return result

    else:
        # Multiple matches - need disambiguation
        print(f"[contact_resolver] Found {len(matches)} matches, attempting disambiguation")
        result["candidates"] = [
            {
                "contact_id": m["contact_id"],
                "display_name": m["display_name"],
                "match_score": m.get("match_score", 0),
            }
            for m in matches
        ]

        # Try LLM disambiguation if we have event context
        if event_context:
            llm_result = _llm_disambiguate_contact(
                person_text=person_text,
                candidates=result["candidates"],
                event_context=event_context,
                user_email=user_email,
            )

            if llm_result["resolved"]:
                result["status"] = "resolved"
                result["confidence"] = llm_result["confidence"]
                result["contact_id"] = llm_result["contact_id"]
                result["display_name"] = llm_result["display_name"]
                result["matched_via"] = "llm_disambiguation"
                print(f"[contact_resolver] ✓ LLM resolved: {llm_result['display_name']}")
                return result

        # Still ambiguous - return candidates
        result["status"] = "candidates"
        result["needs_clarification"] = True
        result["clarification_prompt"] = (
            f"Multiple contacts match '{person_text}'. "
            f"Which one did you mean: {', '.join(c['display_name'] for c in result['candidates'])}?"
        )
        print("[contact_resolver] ⚠️  Ambiguous, returning candidates")
        return result


def resolve_contacts_from_text(
    text: str,
    user_email: str,
) -> dict[str, Any]:
    """
    Complete pipeline: extract people from text and resolve them to contacts.

    This is the main entry point. It:
    1. Extracts person mentions from text (LLM)
    2. Resolves each mention to a contact (or marks as new/ambiguous)
    3. Returns structured results

    Args:
        text: The text to analyze
        user_email: User's email for relationship lookups

    Returns:
        {
            "text": str,
            "people_mentioned": List[str],
            "resolved_contacts": [
                {
                    "original_text": str,
                    "contact_id": str,
                    "display_name": str,
                    "matched_via": str,
                    "confidence": str,
                    "resolution_path": Optional[List[str]]
                }
            ],
            "new_contacts": [
                {
                    "original_text": str,
                    "display_name": str,
                    "inferred_profession": Optional[str]
                }
            ],
            "ambiguous_contacts": [
                {
                    "original_text": str,
                    "candidates": List[dict],
                    "clarification_prompt": str
                }
            ]
        }
    """
    print(f"\n{'='*80}")
    print("[contact_resolver] RESOLVING CONTACTS FROM TEXT")
    print(f"[contact_resolver] Text: '{text}'")
    print(f"[contact_resolver] User: {user_email}")
    print(f"{'='*80}")

    # Step 1: Extract people
    print("\n[contact_resolver] Step 1: Extracting people...")
    people = extract_people_from_text(text, user_email)
    print(f"[contact_resolver] Extracted {len(people)} people: {people}")

    if not people:
        return {
            "text": text,
            "people_mentioned": [],
            "resolved_contacts": [],
            "new_contacts": [],
            "ambiguous_contacts": [],
        }

    # Step 2: Resolve each person
    print(f"\n[contact_resolver] Step 2: Resolving {len(people)} people...")
    resolved_contacts = []
    new_contacts = []
    ambiguous_contacts = []

    for person_text in people:
        resolution = resolve_contact(person_text, user_email, event_context=text)

        if resolution["status"] == "resolved":
            resolved_contacts.append({
                "original_text": person_text,
                "contact_id": resolution["contact_id"],
                "display_name": resolution["display_name"],
                "matched_via": resolution["matched_via"],
                "confidence": resolution["confidence"],
                "resolution_path": resolution.get("resolution_path"),
            })
            print(f"[contact_resolver]   ✓ '{person_text}' → {resolution['display_name']}")

        elif resolution["status"] == "candidates":
            ambiguous_contacts.append({
                "original_text": person_text,
                "candidates": resolution["candidates"],
                "clarification_prompt": resolution["clarification_prompt"],
            })
            print(f"[contact_resolver]   ⚠️  '{person_text}' → ambiguous ({len(resolution['candidates'])} candidates)")

        elif resolution["status"] == "new":
            # Infer profession if mentioned
            profession = _infer_profession_from_text(person_text, text)
            new_contacts.append({
                "original_text": person_text,
                "display_name": person_text,
                "inferred_profession": profession,
            })
            print(f"[contact_resolver]   ✗ '{person_text}' → new contact")

    print("\n[contact_resolver] ✓ Resolution complete:")
    print(f"[contact_resolver]   - Resolved: {len(resolved_contacts)}")
    print(f"[contact_resolver]   - New: {len(new_contacts)}")
    print(f"[contact_resolver]   - Ambiguous: {len(ambiguous_contacts)}")
    print(f"{'='*80}\n")

    return {
        "text": text,
        "people_mentioned": people,
        "resolved_contacts": resolved_contacts,
        "new_contacts": new_contacts,
        "ambiguous_contacts": ambiguous_contacts,
    }


# ---------------------------------------------------------------------------
# Private helper functions
# ---------------------------------------------------------------------------


def _parse_nested_relationship(text: str) -> Optional[list[str]]:
    """
    Parse nested relationship like "my daughter's doctor" into ["my daughter", "doctor"].

    Returns:
        List of parts or None if not nested
    """
    text_lower = text.lower().strip()

    if "'s " in text_lower or "s' " in text_lower:
        if "'s " in text_lower:
            parts = text.split("'s ", 1)
        else:
            parts = text.split("s' ", 1)

        if len(parts) == 2:
            part1 = parts[0].strip()
            part2 = parts[1].strip()

            # Remove articles from second part
            for prefix in ["the ", "a ", "an "]:
                if part2.lower().startswith(prefix):
                    part2 = part2[len(prefix):].strip()

            return [part1, part2]

    return None


def _resolve_nested_relationship(
    parts: list[str],
    user_email: str,
) -> dict[str, Any]:
    """
    Resolve nested relationship like ["my daughter", "doctor"].

    Process:
    1. Resolve first part ("my daughter") to a contact
    2. Get that contact's relationships
    3. Find the second part ("doctor") in those relationships

    Returns:
        {
            "found": bool,
            "contact_id": Optional[str],
            "display_name": Optional[str],
            "confidence": str,
            "path": List[str]
        }
    """
    result = {
        "found": False,
        "contact_id": None,
        "display_name": None,
        "confidence": "low",
        "path": [],
    }

    if len(parts) < 2:
        return result

    # Step 1: Resolve first part
    first_resolution = resolve_contact(parts[0], user_email)

    if first_resolution["status"] != "resolved":
        print(f"[contact_resolver] Nested: Could not resolve first part '{parts[0]}'")
        return result

    intermediate_contact_id = first_resolution["contact_id"]
    intermediate_name = first_resolution["display_name"]
    result["path"] = ["user", intermediate_name]

    print(f"[contact_resolver] Nested: First part resolved to {intermediate_name}")

    # Step 2: Get intermediate contact's relationships
    try:
        intermediate_rels = contacts_service.get_contact_relationships(
            intermediate_contact_id,
            include_contact_details=True,
        )
    except Exception as e:
        print(f"[contact_resolver] Nested: Failed to get relationships: {e}")
        return result

    # Step 3: Resolve second part within those relationships
    second_part = parts[1]
    is_generic, rel_type = _detect_relational_term(second_part)

    if is_generic and rel_type:
        # Try relationship match
        rel_result = _resolve_via_relationship(rel_type, intermediate_rels)
        if rel_result["found"]:
            result["found"] = True
            result["contact_id"] = rel_result["contact_id"]
            result["display_name"] = rel_result["display_name"]
            result["confidence"] = "medium"
            result["path"].append(result["display_name"])
            return result

    # Try fuzzy search among related contacts
    search_name = _strip_generic_markers(second_part) if is_generic else second_part
    matches = contacts_service.search_contacts(search_name, search_by="name", fuzzy_threshold=75, limit=3)

    if matches:
        # Filter to only those in intermediate contact's relationships
        relationships = intermediate_rels.get("relationships", [])
        related_ids = {rel["related_contact"]["contact_id"] for rel in relationships if "related_contact" in rel}

        filtered = [m for m in matches if m["contact_id"] in related_ids]

        if filtered:
            best_match = filtered[0]
            result["found"] = True
            result["contact_id"] = best_match["contact_id"]
            result["display_name"] = best_match["display_name"]
            result["confidence"] = "medium"
            result["path"].append(result["display_name"])
            return result

    return result


def _detect_relational_term(text: str) -> tuple[bool, Optional[str]]:
    """
    Detect if text is a relational term like "my daughter", "the doctor".

    Returns:
        (is_relational: bool, relationship_type: Optional[str])
    """
    text_lower = text.lower().strip()

    possessive_markers = ["my ", "user's ", "the ", "a ", "an ", "their ", "his ", "her "]
    for marker in possessive_markers:
        if text_lower.startswith(marker):
            rel_type = text_lower.replace(marker, "").strip()
            if rel_type:
                return True, rel_type

    return False, None


def _strip_generic_markers(text: str) -> str:
    """Strip markers like 'my', 'the' from text."""
    text_lower = text.lower().strip()
    for prefix in ["my ", "user's ", "the ", "a ", "an ", "their ", "his ", "her "]:
        if text_lower.startswith(prefix):
            return text[len(prefix):].strip()
    return text


def _resolve_via_relationship(
    relationship_type: str,
    relationship_context: dict[str, Any],
) -> dict[str, Any]:
    """
    Try to resolve person via relationship data.

    Returns:
        {"found": bool, "contact_id": Optional[str], "display_name": Optional[str], "confidence": str}
    """
    result = {
        "found": False,
        "contact_id": None,
        "display_name": None,
        "confidence": "low",
    }

    relationships = relationship_context.get("relationships", [])
    print(f"[contact_resolver] Relationships: {relationships}")
    if not relationships:
        return result

    # Build map of relationship types to contacts
    rel_map: dict[str, list[dict]] = {}
    for rel in relationships:
        rel_type = (rel.get("type") or "").lower()
        if rel_type and "related_contact" in rel:
            if rel_type not in rel_map:
                rel_map[rel_type] = []
            rel_map[rel_type].append(rel["related_contact"])

    # Direct match
    if relationship_type in rel_map and rel_map[relationship_type]:
        contact = rel_map[relationship_type][0]
        result["found"] = True
        result["contact_id"] = contact["contact_id"]
        result["display_name"] = contact["display_name"]
        result["confidence"] = "high"
        return result

    # Try related types (e.g., "daughter" -> "child")
    # Use the shared mapping from contacts module
    related_types = contacts_service.find_related_types(relationship_type)

    for related_type in related_types:
        if related_type in rel_map and rel_map[related_type]:
            contact = rel_map[related_type][0]
            result["found"] = True
            result["contact_id"] = contact["contact_id"]
            result["display_name"] = contact["display_name"]
            result["confidence"] = "medium"
            return result

    return result


def _llm_disambiguate_contact(
    person_text: str,
    candidates: list[dict[str, Any]],
    event_context: str,
    user_email: str,
) -> dict[str, Any]:
    """
    Use LLM to disambiguate between multiple contact candidates.

    CRITICAL: LLM can ONLY choose from candidates or say it cannot decide.
    It MUST NOT hallucinate a new contact.

    Returns:
        {"resolved": bool, "contact_id": Optional[str], "display_name": Optional[str], "confidence": str}
    """
    # Get user's name for context
    user_name = "User"
    user_contact = contacts_service.find_self_contact(user_email)
    if user_contact:
        user_name = user_contact.get("display_name", "User")

    candidate_list = "\n".join(
        f"- {i+1}. {c['display_name']} (ID: {c['contact_id']})"
        for i, c in enumerate(candidates)
    )

    prompt = f"""Disambiguate a person reference using event context.

Event: "{event_context}"
Person reference: "{person_text}"
User: {user_name}

Candidates:
{candidate_list}

CRITICAL RULES:
1. You MUST choose from the candidates above or say "cannot_decide"
2. You MUST NOT invent or suggest any person not in the list
3. If context is insufficient, return "cannot_decide"

Analyze which candidate is most likely based on the event context.

Return ONLY valid JSON:
{{
    "decision": "resolved" | "cannot_decide",
    "candidate_number": 1 or 2 or null,
    "confidence": "high" | "medium" | "low",
    "reasoning": "brief explanation"
}}"""

    try:
        llm_response = call_llm_json(prompt, timeout=15)

        decision = llm_response.get("decision")
        candidate_number = llm_response.get("candidate_number")

        if decision == "resolved" and candidate_number and 1 <= candidate_number <= len(candidates):
            chosen = candidates[candidate_number - 1]
            return {
                "resolved": True,
                "contact_id": chosen["contact_id"],
                "display_name": chosen["display_name"],
                "confidence": llm_response.get("confidence", "medium"),
            }

        return {
            "resolved": False,
            "contact_id": None,
            "display_name": None,
            "confidence": "low",
        }

    except Exception as e:
        print(f"[contact_resolver] LLM disambiguation failed: {e}")
        return {
            "resolved": False,
            "contact_id": None,
            "display_name": None,
            "confidence": "low",
        }


def _infer_profession_from_text(person_text: str, full_text: str) -> Optional[str]:
    """
    Infer profession from context if explicitly stated.

    Examples:
    - "Dr. Smith" -> "doctor"
    - "lawyer John" -> "lawyer"
    - "met with teacher" -> "teacher"

    Returns:
        Profession string or None
    """
    prompt = f"""Infer profession from context.

Text: "{full_text}"
Person: "{person_text}"

CRITICAL: Only return profession if EXPLICITLY stated or STRONGLY implied (e.g., "Dr." prefix).
Otherwise return null.

Return ONLY valid JSON:
{{
    "profession": str or null
}}"""

    try:
        result = call_llm_json(prompt, timeout=10)
        return result.get("profession")
    except Exception:
        return None
