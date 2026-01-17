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


def extract_people_from_text(text: str) -> list[str]:
    """
    Extract person mentions from text using LLM.

    Args:
        text: The input text to analyze
        user_email: User's email (used to get their name for LLM context)

    Returns:
        {
            "people": ["John", "my daughter", "my daughter's doctor"],
            "ambiguous_text": true | false
        }
    """
    prompt = f"""Extract all person references from this text.

Text: "{text}"

Extract ONLY people - all person references including:
- Proper names (e.g., "John Smith")
- Relational terms (e.g., "my daughter", "the doctor")
- Nested relationships (e.g., "my daughter's doctor", "my son's teacher", "my wife's family") - Also correct any spelling if user mistyped (for example, daughters instead of daugther's)
- The current user IF they are a participant in the event (e.g., "I visited my daughter" - both "I" and "my daughter" are participants)

SAME-PERSON APPOSITIVES (AVOID DUPLICATES):
- If a proper name and a relationship/profession describe the SAME person in the same clause/sentence, return ONLY the proper name.
- Do NOT add an extra relationship entry when it is clearly the same person.
- Examples:
  * "my daughter visited John, who is her eye doctor" → ["my daughter", "John"]
  * "I met Sarah, my therapist" → ["user", "Sarah"]
  * "John the plumber fixed the sink" → ["John"]
  * "Dr. Smith, my mother's cardiologist" → ["Dr. Smith", "my mother's cardiologist"] (two people: Dr. Smith and my mother)

CRITICAL RULES:
- If the user is a participant/actor in the event (doing something or something happening to them), include "user" to represent them
- Examples where user should be included:
  * "I visited my daughter" → ["user", "my daughter"] (user is actively doing something)
  * "John and I went to the store" → ["John", "user"] (user is part of the group)
  * "My daughter visited me" → ["my daughter", "user"] (something happened to the user)
- Examples where user should NOT be included:
  * "My daughter visited her mother" → ["my daughter", "my daughter's mother"] (user is just narrator, not participant; note pronoun resolution)
  * "John met with Mary" → ["John", "Mary"] (user is just narrator)
- Do NOT include second-person pronouns: "you", "your", "yours"

PRONOUN RESOLUTION:
- Resolve possessive pronouns (her, his, their) when they refer to someone already mentioned
- You MUST be able to identify WHO the pronoun refers to before resolving it. this is critical to the right outcome.
- If unclear or ambiguous, you sohuld fail the resolution and return a JSON like this
{{
    "people": [],
    "ambiguous_text": true
}}"

Examples of CORRECT pronoun resolution:
- "my daughter met her mother" → ["my daughter", "my daughter's mother"]
  * "her" clearly refers to "my daughter" (only one person mentioned before "her")
- "John visited his doctor" → ["John", "John's doctor"]
  * "his" clearly refers to "John" (only one person mentioned before "his")
- "Emma and her sister went out" → ["Emma", "Emma's sister"]
  * "her" clearly refers to "Emma" (closest preceding person)

Examples of INCORRECT - do NOT resolve these:
- "She met her mother" → ["she", "her mother"]
  * "her" could refer to "she" but "she" is a pronoun, not a clear person reference
- "The doctor saw her patient" → ["the doctor", "her patient"]
  * "her" clearly refers to "the doctor", so keep separate (doctor's patient, not nested)

CRITICAL: Only resolve pronouns when the referent is crystal clear and creates a valid nested relationship

IMPORTANT:
- ALWAYS keep possessive markers in relationship phrases: "my daughter" NOT "daughter"
- Keep relationship phrases intact (e.g., "my daughter's doctor" as ONE entity)
- Include both proper names and generic references
- If a person is mentioned multiple ways, include all mentions
- Use the special token "user" to represent the current user when they are a participant

Return ONLY a valid JSON, nothing more, no other text or explanation:
{{
    "people": ["person1", "my daughter", "person2's doctor"]
}}"""

    max_retries = 3
    for attempt in range(max_retries):
        try:
            # Use low temperature for consistent structured output
            result = call_llm_json(prompt, timeout=30, temperature=0.1, top_p=0.9, use_simpler_model=True)
            people = result.get("people", [])

            # Validate extraction: check for unresolved pronouns
            invalid_extractions = []
            for person in people:
                person_lower = person.lower().strip()

                # Check for unresolved third-person possessive pronouns at the start
                # These indicate failed extraction since we can only resolve "my/user" context
                if person_lower.startswith(("her ", "his ", "their ")):
                    invalid_extractions.append(person)

            if invalid_extractions and attempt < max_retries - 1:
                print(f"[contact_resolver] Attempt {attempt + 1}: Invalid extractions detected: {invalid_extractions}")
                print(f"[contact_resolver] Retrying extraction with stricter guidance...")

                # Add stricter guidance to the prompt
                prompt += f"""

CRITICAL ERROR CORRECTION:
Your previous extraction contained unresolved pronouns: {', '.join(invalid_extractions)}

These are INVALID because:
- "her X", "his X", "their X" at the start means you failed to identify WHO "her/his/their" refers to
- The ONLY person known in this system is the current user (use "user" token or "my")
- If you see "her mother", you MUST find who "her" refers to in the text and resolve it to "X's mother"
- If you cannot identify the referent, DO NOT include it

Please extract again with proper pronoun resolution or omit unclear references."""
                continue

            # Post-process: Filter out first-person pronouns and handle "user" token
            filtered_people = []
            for person in people:
                person_lower = person.lower().strip()

                # Skip invalid third-person pronouns (last safety check)
                if person_lower.startswith(("her ", "his ", "their ")):
                    print(f"[contact_resolver] Skipping invalid extraction: '{person}'")
                    continue

                # Keep the special "user" token for direct email resolution later
                if person_lower == "user":
                    filtered_people.append("user")
                    continue

                # Skip standalone first-person pronouns (these should be converted to "user" by LLM)
                if person_lower in ["i", "me", "my", "mine", "myself", "we", "us", "our", "ours"]:
                    print(f"[contact_resolver] Skipping first-person pronoun: '{person}'")
                    continue

                # Skip second-person pronouns
                if person_lower in ["you", "your", "yours", "yourself"]:
                    print(f"[contact_resolver] Skipping second-person pronoun: '{person}'")
                    continue

                filtered_people.append(person)

            return filtered_people
        except Exception as e:
            if attempt < max_retries - 1:
                print(f"[contact_resolver] Attempt {attempt + 1} failed: {e}, retrying...")
                continue
            print(f"[contact_resolver] Failed to extract people after {max_retries} attempts: {e}")
            return []

    return []


def resolve_contact(
    person_text: str,
    user_email: str,
    *,
    event_context: Optional[str] = None,
    resolution_cache: Optional[dict[str, dict[str, Any]]] = None,
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

    # Short-circuit for the current user: resolve directly by email.
    if person_text.lower().strip() == "user":
        user_contact = contacts_service.find_self_contact(user_email)
        if user_contact:
            result["status"] = "resolved"
            result["confidence"] = "high"
            result["contact_id"] = user_contact["contact_id"]
            result["display_name"] = user_contact.get("display_name")
            result["matched_via"] = "user_email"
            print(f"[contact_resolver] ✓ Resolved user via email: {result['display_name']}")
            return result
        print("[contact_resolver] User token provided but no contact found by email")
        result["status"] = "new"
        result["display_name"] = "user"
        return result

    # Step 1: Check for nested relationships (e.g., "my daughter's doctor")
    nested_parts = _parse_nested_relationship(person_text)
    print(f"[contact_resolver] Nested parts: {nested_parts}")
    print(f"[contact_resolver] User email: {user_email}")
    if nested_parts and len(nested_parts) > 1:
        print(f"[contact_resolver] Detected nested relationship: {nested_parts}")
        nested_result = _resolve_nested_relationship(nested_parts, user_email, resolution_cache)

        if nested_result["found"]:
            result["status"] = "resolved"
            result["confidence"] = nested_result["confidence"]
            result["contact_id"] = nested_result["contact_id"]
            result["display_name"] = nested_result["display_name"]
            result["matched_via"] = "nested_relationship"
            result["resolution_path"] = nested_result["path"]
            print(f"[contact_resolver] ✓ Resolved via nested: {' → '.join(nested_result['path'])}")
            return result

        # Check if first part couldn't be resolved
        if nested_result.get("first_part_unresolved"):
            first_part_status = nested_result.get("first_part_status")
            print(f"[contact_resolver] Nested: First part unresolved (status: {first_part_status})")

            # If first part is ambiguous, the whole nested relationship is ambiguous
            if first_part_status == "candidates":
                print(f"[contact_resolver] First part '{nested_parts[0]}' is ambiguous, cannot resolve nested relationship")
                result["status"] = "candidates"
                # We can't provide candidates for the nested relationship since we don't know which first part
                result["needs_clarification"] = True
                result["clarification_prompt"] = (
                    f"Cannot resolve '{person_text}' because '{nested_parts[0]}' is ambiguous. "
                    f"Please clarify who '{nested_parts[0]}' refers to first."
                )
                return result
            # If first part is new, the whole nested relationship is unresolvable
            elif first_part_status == "new":
                print(f"[contact_resolver] First part '{nested_parts[0]}' is new, cannot resolve nested relationship")
                result["status"] = "new"
                return result

        # Check if this is a user-related nested relationship (starts with "my", "user's", or equals "user")
        first_part_lower = nested_parts[0].lower().strip()
        is_user_nested = (
            first_part_lower.startswith(("my ", "user's ")) or
            first_part_lower == "user"
        )

        if not is_user_nested:
            # Non-user nested relationship failed (e.g., "Pedro's doctor")
            # Don't fall back to direct search as it will give wrong results
            print(f"[contact_resolver] Nested resolution failed for non-user relationship, marking as new")
            result["status"] = "new"
            return result

        print("[contact_resolver] Nested resolution failed, falling back")

    # Step 2: Check for simple relationship (e.g., "my daughter")
    relationship_type = _detect_relational_term(person_text)
    print(f"[contact_resolver] Relationship type: {relationship_type}")
    if relationship_type:
        # Get user's relationships
        user_contact = contacts_service.find_self_contact(user_email)
        if user_contact:
            relationships = contacts_service.get_contact_relationships(
                user_contact["contact_id"],
                include_contact_details=True,
            )

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
            elif rel_result["candidates"]:
                # Multiple relationship matches - return candidates
                result["status"] = "candidates"
                result["candidates"] = rel_result["candidates"]
                result["needs_clarification"] = True
                result["clarification_prompt"] = (
                    f"Multiple {relationship_type}s found. "
                    f"Which one did you mean: {', '.join(c['display_name'] for c in rel_result['candidates'])}?"
                )
                print(f"[contact_resolver] ⚠️  Multiple {relationship_type}s, returning candidates")
                return result

    # Step 3: Try direct fuzzy search
    search_name = person_text
    if relationship_type:
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
        print(
            "[contact_resolver] Matches found: "
            + ", ".join(
                f"{m['display_name']} ({m.get('match_reason', '')})"
                for m in matches
            )
        )
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
            "ambiguous_text": true | false
        }
    """
    print(f"\n{'='*80}")
    print("[contact_resolver] RESOLVING CONTACTS FROM TEXT")
    print(f"[contact_resolver] Text: '{text}'")
    print(f"[contact_resolver] User: {user_email}")
    print(f"{'='*80}")

    # Step 1: Extract people
    print("\n[contact_resolver] Step 1: Extracting people...")
    people = extract_people_from_text(text)
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

    # Cache to avoid re-resolving the same person text multiple times
    # This is especially useful for nested relationships like "my daughter" and "my daughter's doctor"
    resolution_cache: dict[str, dict[str, Any]] = {}

    for person_text in people:
        # Check cache first
        if person_text in resolution_cache:
            print(f"[contact_resolver] Using cached resolution for: '{person_text}'")
            resolution = resolution_cache[person_text]
        else:
            resolution = resolve_contact(person_text, user_email, event_context=text, resolution_cache=resolution_cache)
            resolution_cache[person_text] = resolution

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
    resolution_cache: Optional[dict[str, dict[str, Any]]] = None,
) -> dict[str, Any]:
    """
    Resolve nested relationship like ["my daughter", "doctor"].

    Process:
    1. Resolve first part ("my daughter") to a contact
    2. Get that contact's relationships
    3. Find the second part ("doctor") in those relationships

    Args:
        parts: List of relationship parts (e.g., ["my daughter", "doctor"])
        user_email: User's email for relationship lookups
        resolution_cache: Optional cache to avoid re-resolving same person text

    Returns:
        {
            "found": bool,
            "contact_id": Optional[str],
            "display_name": Optional[str],
            "confidence": str,
            "path": List[str],
            "first_part_unresolved": bool,  # True if first part couldn't be resolved
            "first_part_status": Optional[str]  # Status of first part resolution
        }
    """
    result = {
        "found": False,
        "contact_id": None,
        "display_name": None,
        "confidence": "low",
        "path": [],
        "first_part_unresolved": False,
        "first_part_status": None,
    }

    if len(parts) < 2:
        return result

    # Step 1: Resolve first part (check cache first)
    first_part = parts[0]
    if resolution_cache and first_part in resolution_cache:
        print(f"[contact_resolver] Nested: Using cached resolution for first part: '{first_part}'")
        first_resolution = resolution_cache[first_part]
    else:
        first_resolution = resolve_contact(first_part, user_email, resolution_cache=resolution_cache)
        if resolution_cache is not None:
            resolution_cache[first_part] = first_resolution

    if first_resolution["status"] != "resolved":
        print(f"[contact_resolver] Nested: Could not resolve first part '{parts[0]}' (status: {first_resolution['status']})")
        # Mark that the first part couldn't be resolved
        result["first_part_unresolved"] = True
        result["first_part_status"] = first_resolution["status"]
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
    # The second part is already the clean relationship type (e.g., "doctor")
    # because _parse_nested_relationship already stripped articles
    second_part = parts[1]
    print(f"[contact_resolver] Nested: Second part (relationship type): {second_part}")

    # Try relationship match directly with the second part as the relationship type
    # Use for_nested_resolution=True to ONLY match on 'other_type'
    # This ensures we find what the related contact IS to the intermediate person
    rel_result = _resolve_via_relationship(second_part, intermediate_rels, for_nested_resolution=True)
    print(f"[contact_resolver] Nested: Relationship result: {rel_result}")

    if rel_result["found"]:
        result["found"] = True
        result["contact_id"] = rel_result["contact_id"]
        result["display_name"] = rel_result["display_name"]
        result["confidence"] = "medium"
        result["path"].append(result["display_name"])
        return result

    if rel_result["candidates"]:
        # Multiple matches found - for nested relationships, return first candidate
        # (we could enhance this later to return candidates for disambiguation)
        candidate = rel_result["candidates"][0]
        result["found"] = True
        result["contact_id"] = candidate["contact_id"]
        result["display_name"] = candidate["display_name"]
        result["confidence"] = "low"
        result["path"].append(result["display_name"])
        print(f"[contact_resolver] Nested: Multiple {second_part}s found, using first: {candidate['display_name']}")
        return result

    # Try fuzzy search among related contacts as fallback
    search_name = second_part
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


def _detect_relational_term(text: str) -> Optional[str]:
    """
    Detect if text is a relational term like "my daughter", "the doctor".

    Returns:
        relationship_type if detected, None otherwise
    """
    text_lower = text.lower().strip()

    possessive_markers = ["my ", "user's ", "the ", "a ", "an ", "their ", "his ", "her "]
    for marker in possessive_markers:
        if text_lower.startswith(marker):
            rel_type = text_lower.replace(marker, "").strip()
            if rel_type:
                return rel_type

    return None


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
    for_nested_resolution: bool = False,
) -> dict[str, Any]:
    """
    Try to resolve person via relationship data.

    Args:
        relationship_type: The type of relationship to look for (e.g., "mother", "doctor")
        relationship_context: Dictionary containing relationships data
        for_nested_resolution: If True, only match on 'other_type' (what the related contact IS).
                             If False, match on both 'type' and 'other_type' for flexibility.

    Returns:
        {
            "found": bool,
            "contact_id": Optional[str],
            "display_name": Optional[str],
            "confidence": str,
            "candidates": list[dict]  # Multiple matches if they exist
        }

    Relationship directionality:
        - 'type': What THIS contact is TO the related contact
        - 'other_type': What the RELATED CONTACT is TO this contact

        Example: Jane has relationship {type: "child", other_type: "mother", related_contact: Mary}
        - Jane is a "child" to Mary
        - Mary is a "mother" to Jane
        - When looking for "mother", we match on 'other_type' because Mary IS the mother
    """
    result = {
        "found": False,
        "contact_id": None,
        "display_name": None,
        "confidence": "low",
        "candidates": [],
    }

    relationships = relationship_context.get("relationships", [])
    if not relationships:
        return result

    # Build map of relationship types to contacts
    rel_map: dict[str, list[dict]] = {}
    for rel in relationships:
        # For nested resolution, ONLY use 'other_type' because we want to find
        # what the related contact IS to the intermediate person
        if for_nested_resolution:
            other_type = (rel.get("other_type") or "").lower()
            if other_type and "related_contact" in rel:
                if other_type not in rel_map:
                    rel_map[other_type] = []
                contact_data = rel["related_contact"].copy()
                if "contact_id" not in contact_data:
                    contact_data["contact_id"] = rel.get("contact_id")
                rel_map[other_type].append(contact_data)
        else:
            # For direct user relationships, check both type and other_type for flexibility
            rel_type = (rel.get("type") or "").lower()
            if rel_type and "related_contact" in rel:
                if rel_type not in rel_map:
                    rel_map[rel_type] = []
                contact_data = rel["related_contact"].copy()
                if "contact_id" not in contact_data:
                    contact_data["contact_id"] = rel.get("contact_id")
                rel_map[rel_type].append(contact_data)

            other_type = (rel.get("other_type") or "").lower()
            if other_type and "related_contact" in rel:
                if other_type not in rel_map:
                    rel_map[other_type] = []
                contact_data = rel["related_contact"].copy()
                if "contact_id" not in contact_data:
                    contact_data["contact_id"] = rel.get("contact_id")
                rel_map[other_type].append(contact_data)

    # Direct match
    if relationship_type in rel_map and rel_map[relationship_type]:
        matches = rel_map[relationship_type]
        print(f"[contact_resolver_inner] Direct match found: {matches}")

        if len(matches) == 1:
            # Single match - resolved
            contact = matches[0]
            result["found"] = True
            result["contact_id"] = contact["contact_id"]
            result["display_name"] = contact["display_name"]
            result["confidence"] = "high"
            return result
        else:
            # Multiple matches - return candidates
            result["found"] = False
            result["candidates"] = [
                {
                    "contact_id": c["contact_id"],
                    "display_name": c["display_name"],
                }
                for c in matches
            ]
            result["confidence"] = "low"
            return result

    # Try related types (e.g., "daughter" -> "child")
    # Use the shared mapping from contacts module
    related_types = contacts_service.find_related_types(relationship_type)

    print(f"[contact_resolver_inner] Related types: {related_types}")

    for related_type in related_types:
        if related_type in rel_map and rel_map[related_type]:
            matches = rel_map[related_type]

            if len(matches) == 1:
                # Single match - resolved
                contact = matches[0]
                result["found"] = True
                result["contact_id"] = contact["contact_id"]
                result["display_name"] = contact["display_name"]
                result["confidence"] = "medium"
                return result
            else:
                # Multiple matches - return candidates
                result["found"] = False
                result["candidates"] = [
                    {
                        "contact_id": c["contact_id"],
                        "display_name": c["display_name"],
                    }
                    for c in matches
                ]
                result["confidence"] = "low"
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

    prompt = f"""Disambiguate a person reference from the list of candidates.

Person you are trying to find: "{person_text}"

Candidates:
{candidate_list}

Event context (use only if it is relevant): "{event_context}"

CRITICAL RULES:
1. You MUST choose from the candidates above or say "cannot_decide"
2. You MUST NOT invent or suggest any person not in the list
3. If there is a perfect match between person you are trying to find and a candidate in the list, return "resolved" and the candidate number.
4. If additional context is needed, consider the Event context provided.
5. If context is not enough, return "cannot_decide"

Analyze which candidate is most likely based on the context.

Return ONLY a valid JSON, nothing more, no other text or explanation:
{{
    "decision": "resolved" | "cannot_decide",
    "candidate_number": 1 or 2 or null,
    "confidence": "high" | "medium" | "low",
    "reasoning": "brief explanation"
}}"""

    try:
        # Use low temperature for consistent disambiguation
        llm_response = call_llm_json(prompt, timeout=30, temperature=0.1, top_p=0.9, use_simpler_model=True)

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
    prompt = f"""Infer profession from context. If a general term is provided, convert to a more offical term as well.

Text: "{full_text}"
Person: "{person_text}"

CRITICAL: Only return profession if EXPLICITLY stated or STRONGLY implied (e.g., "Dr." prefix).
Otherwise return null.

Return ONLY a valid JSON, nothing more, no other text or explanation:
{{
    "profession": str or null
}}"""

    try:
        # Use low temperature for consistent profession inference
        result = call_llm_json(prompt, timeout=20, temperature=0.1, top_p=0.9, use_simpler_model=True)
        return result.get("profession")
    except Exception:
        return None
