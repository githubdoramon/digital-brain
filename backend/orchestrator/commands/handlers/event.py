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
    from llm_prompts import get_time_context
    from llm_helpers import call_llm_json
    from tags_manager import MAJOR_TAGS

    # Get current time context
    time_context = get_time_context()
    user_email = context.get("user_email", "")

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
        extracted = call_llm_json(extraction_prompt, timeout=30)

        # Parse datetime if provided
        when = None
        if extracted.get("when"):
            try:
                when = datetime.fromisoformat(extracted["when"].replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                pass

        return {
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

    except Exception as e:
        print(f"[event_command] LLM extraction failed: {e}")
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


def _resolve_existing_entities(entities: dict[str, Any]) -> dict[str, Any]:
    """
    Search database for existing entities using existing resolution tools.

    Args:
        entities: Extracted entities from message

    Returns:
        Dict with matched and new entities
    """
    import contacts as contacts_service

    resolution = {
        "contacts": [],
        "places": [],
        "documents": [],
        "new_entities": {
            "contacts": [],
            "places": [],
            "documents": [],
        },
    }

    # Resolve contacts using existing search_contacts function
    for person_name in entities.get("who", []):
        if not person_name or not isinstance(person_name, str):
            continue

        matches = contacts_service.search_contacts(
            person_name,
            search_by="name",
            fuzzy_threshold=75,
            limit=3,
        )

        if matches:
            # Add first match with confidence
            best_match = matches[0]
            resolution["contacts"].append(
                {
                    "contact_id": best_match["contact_id"],
                    "display_name": best_match["display_name"],
                    "query": person_name,
                    "confidence": "high" if best_match.get("similarity_score", 0) > 90 else "medium",
                }
            )
        else:
            # Mark as new contact to create
            resolution["new_entities"]["contacts"].append(
                {
                    "display_name": person_name,
                    "query": person_name,
                }
            )

    # Resolve places (simple implementation for now)
    where = entities.get("where")
    if where:
        # TODO: Search for existing places
        # For now, always create new places
        resolution["new_entities"]["places"].append(
            {
                "name": where,
                "query": where,
            }
        )

    return resolution


def handle_event(parsed: ParsedCommand, context: dict) -> dict[str, Any]:
    """
    Handle the /event command.

    Flow:
    1. Extract entities using LLM
    2. Check if clarification is needed
    3. Search for existing entities
    4. Store data for confirmation
    5. Return confirmation request or ask for clarification

    Args:
        parsed: Parsed command with event description as args
        context: Context dict with user info

    Returns:
        Dict with event_confirmation or clarification_needed type
    """
    if not parsed.args:
        return {
            "type": "error",
            "message": "Please provide an event description. Example: /event met with John at the cafe yesterday",
        }

    # Extract entities using LLM with time context
    extracted = _extract_event_entities_with_llm(parsed.args, context)

    # Check if clarification is needed
    if extracted.get("needs_clarification"):
        return {
            "type": "clarification_needed",
            "questions": extracted.get("clarification_questions", []),
            "partial_extraction": extracted,
            "original_message": parsed.args,
        }

    # Resolve existing entities
    resolution = _resolve_existing_entities(extracted)

    # Generate a preview ID and store the data
    preview_id = f"event:preview:{uuid4().hex[:8]}"

    from commands.storage import store_command_data

    store_command_data(
        preview_id,
        {
            "extracted": extracted,
            "resolution": resolution,
            "user_email": context.get("user_email"),
        },
    )

    return {
        "type": "event_confirmation",
        "preview_id": preview_id,
        "extracted": extracted,
        "resolution": resolution,
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
