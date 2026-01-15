"""
Handler for the /event command.

The /event command allows users to add new memories/events to the database.
It extracts entities, checks for existing ones, and asks for confirmation.
"""

from datetime import datetime
from typing import Any
from uuid import uuid4

from commands.parser import ParsedCommand
from commands.registry import CommandRegistry


def _extract_event_entities_with_llm(message: str, context: dict) -> dict[str, Any]:
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

    extraction_prompt = f"""You are extracting structured information from a user's event description to create a memory entry.

Current context:
- Date/time: {time_context}
- User: {user_email}

Event description: "{message}"

Extract the following information:
1. **What happened**: A brief title (5-10 words) and detailed summary
2. **When**: Parse date/time. If relative (e.g., "yesterday", "last Tuesday"), convert to actual datetime using current context. If not mentioned, use current time.
3. **Where**: Location/place name (if mentioned)
4. **Who**: Names of people involved (if mentioned)
5. **Documents**: References to documents/files (if mentioned)
6. **Tags**: Relevant tags for categorization. Consider major categories like: {tag_examples}, etc.
7. **Event types**: Choose from: generic, meeting, communication, task, creation, consumption, travel, personal, system, financial, observation, interaction, education, celebration, purchase, health

If ANY critical information is missing or ambiguous, set "needs_clarification" to true and provide "clarification_questions".

Return ONLY valid JSON in this exact format:
{{
    "needs_clarification": false,
    "clarification_questions": [],
    "title": "Brief title",
    "summary": "Detailed description",
    "when": "ISO 8601 datetime or null",
    "where": "Location name or null",
    "who": ["person1", "person2"],
    "documents": [],
    "tags": ["tag1", "tag2"],
    "types": ["generic"]
}}"""

    try:
        print("[event_extraction] Calling LLM for extraction...")
        extracted = call_llm_json(extraction_prompt, timeout=30)

        print("[event_extraction] Raw LLM response:")
        print(f"  - Title: {extracted.get('title')}")
        print(f"  - Summary: {extracted.get('summary')}")
        print(f"  - When: {extracted.get('when')}")
        print(f"  - Where: {extracted.get('where')}")
        print(f"  - Who: {extracted.get('who')}")
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
            "who": extracted.get("who", []),
            "documents": extracted.get("documents", []),
            "tags": extracted.get("tags", []),
            "types": extracted.get("types", ["generic"]),
        }

        print(f"[event_extraction] Extraction complete. Found {len(result['who'])} people")
        return result

    except Exception as e:
        print(f"[event_extraction] ERROR: LLM extraction failed: {e}")
        import traceback
        traceback.print_exc()

        # Fallback to basic extraction
        return {
            "needs_clarification": True,
            "clarification_questions": ["Could you provide more details about what happened, when, and who was involved?"],
            "title": message[:100],
            "summary": message,
            "when": None,
            "where": None,
            "who": [],
            "documents": [],
            "tags": [],
            "types": ["generic"],
        }


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

        # Extract relationship type from phrases like "my daughter", "the doctor"
        # Remove possessives and articles
        cleaned = term_lower.replace("my ", "").replace("the ", "").replace("a ", "").strip()
        print(f"[generic_resolution]   Cleaned to: '{cleaned}'")

        # Check if this maps to a known relationship type
        if cleaned in rel_map and rel_map[cleaned]:
            # Use the first matching contact
            contact = rel_map[cleaned][0]
            resolved_name = contact.get("display_name", term)
            resolved[term] = resolved_name
            print(f"[generic_resolution]   ✓ Resolved '{term}' -> '{resolved_name}'")
        else:
            print(f"[generic_resolution]   ✗ No match for '{cleaned}' in relationship types")

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
                suggestions.append({
                    "from_contact_id": name_to_id[from_name],
                    "from_display_name": from_name,
                    "to_contact_id": name_to_id[to_name],
                    "to_display_name": to_name,
                    "relationship_type": rel_type or "",
                    "reciprocal_type": reciprocal or "",
                    "confidence": confidence or "medium",
                    "reasoning": sug.get("reasoning", ""),
                })
                print("[relationship_suggestion]     ✓ Added to suggestions")
            else:
                print("[relationship_suggestion]     ✗ Names not found in contact list, skipping")

        print(f"[relationship_suggestion] Suggestion complete. Created {len(suggestions)} suggestions")

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
        print(f"[entity_resolution]   Person {idx}: '{person_name}' -> searching for '{search_name}'")

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
            print(f"[entity_resolution]     ✓ Matched to existing: {best_match['display_name']} (ID: {best_match['contact_id']}, score: {match_score}, confidence: {confidence})")
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
    print(f"\n{'='*80}")
    print("[handle_event] NEW EVENT COMMAND")
    print(f"{'='*80}")

    if not parsed.args:
        return {
            "type": "error",
            "message": "Please provide an event description. Example: /event met with John at the cafe yesterday",
        }

    user_email = context.get("user_email", "")
    print(f"[handle_event] User: {user_email}")
    print(f"[handle_event] Input: '{parsed.args}'")

    # Extract entities using LLM with time context
    print("\n[handle_event] STEP 1: Extracting entities with LLM...")
    extracted = _extract_event_entities_with_llm(parsed.args, context)

    # Check if clarification is needed
    if extracted.get("needs_clarification"):
        print("[handle_event] ⚠️  Clarification needed, returning questions to user")
        return {
            "type": "clarification_needed",
            "questions": extracted.get("clarification_questions", []),
            "partial_extraction": extracted,
            "original_message": parsed.args,
        }

    # Resolve existing entities and generic terms
    print("\n[handle_event] STEP 2: Resolving entities and generic terms...")
    resolution = _resolve_existing_entities(extracted, user_email)

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
    relationship_suggestions = _suggest_relationships_from_context(
        parsed.args,
        extracted,
        resolution,
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
        },
    )

    print("\n[handle_event] ✓ Event processing complete!")
    print("[handle_event] Summary:")
    print(f"  - Title: {extracted.get('title')}")
    print(f"  - Contacts found: {len(resolution.get('contacts', []))}")
    print(f"  - New contacts: {len(resolution.get('new_entities', {}).get('contacts', []))}")
    print(f"  - Relationship suggestions: {len(relationship_suggestions)}")
    print(f"{'='*80}\n")

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
