"""
Entity resolution and contact lookup tool handlers.

Handles:
- resolve_query: Extract contacts, places, and time ranges from natural language
- lookup_contact: Smart contact search with fuzzy matching and relationship filtering

Note: Tracing/logging is handled at the controller level via the centralized
trace module. Handlers focus purely on execution logic.
"""

from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from agent.state import AgentState


def handle_resolve_query(
    args: dict[str, Any],
    state: Optional["AgentState"] = None,
    question: str = "",
    **kwargs,
) -> dict[str, Any]:
    """
    Execute resolve_query tool.

    Extracts structured entities (contacts, places, time ranges) from natural language.
    """
    # Lazy import to avoid circular dependencies
    import contacts

    query = args.get("query", question)

    if not query:
        return {"error": "query is required"}

    resolution = contacts.resolve_query(query)

    # Update state if provided
    if state is not None:
        state.resolution = resolution
        contacts_found = len(resolution.get("contacts", []))
        places_found = len(resolution.get("places", []))
        if contacts_found or places_found:
            state.add_fact(
                f"Resolved {contacts_found} contacts and {places_found} places from query"
            )

    return resolution


def handle_lookup_contact(
    args: dict[str, Any],
    state: Optional["AgentState"] = None,
    **kwargs,
) -> dict[str, Any]:
    """
    Execute lookup_contact tool.

    Smart contact lookup with:
    - Fuzzy name matching (handles typos, partial names, nicknames)
    - Case-insensitive search
    - Email and phone lookup
    - Relationship filtering (family, colleagues, friends)
    - Automatic relationship expansion

    Actions:
    - "search": Find contacts matching a query
    - "get_relationships": Get a contact's relationships (optionally filtered)
    - "find_related": Find a contact and their related contacts in one call
    """
    # Lazy import to avoid circular dependencies
    import contacts

    action = args.get("action", "search")
    query = args.get("query", "")

    if action == "search":
        if not query:
            return {"error": "query is required for search action"}

        search_by = args.get("search_by", "any")
        fuzzy_threshold = args.get("fuzzy_threshold", 75)
        limit = args.get("limit", 10)

        results = contacts.search_contacts(
            query,
            search_by=search_by,
            fuzzy_threshold=fuzzy_threshold,
            limit=limit,
        )

        # Update state
        if state is not None:
            if results:
                names = [r.get("display_name", "Unknown") for r in results[:3]]
                state.add_fact(f"Found {len(results)} contacts matching '{query}': {', '.join(names)}")
            else:
                state.add_fact(f"No contacts found matching '{query}'")

        return {
            "action": "search",
            "query": query,
            "found": len(results) > 0,
            "count": len(results),
            "contacts": results,
        }

    elif action == "get_relationships":
        contact_id = args.get("contact_id")
        if not contact_id:
            # Try to resolve from query
            if query:
                search_results = contacts.search_contacts(query, limit=1)
                if search_results:
                    contact_id = search_results[0]["contact_id"]
                else:
                    return {"error": f"No contact found matching '{query}'"}
            else:
                return {"error": "contact_id or query is required for get_relationships action"}

        relationship_types = args.get("relationship_types")

        result = contacts.get_contact_relationships(
            contact_id,
            relationship_types=relationship_types,
            include_contact_details=True,
        )

        # Update state
        if state is not None and result.get("found"):
            contact_name = result.get("contact", {}).get("display_name", "Unknown")
            rel_count = result.get("relationship_count", 0)
            filter_desc = ""
            if relationship_types:
                filter_desc = f" (filtered: {', '.join(relationship_types)})"
            state.add_fact(f"Found {rel_count}{filter_desc} relationships for {contact_name}")

        return {
            "action": "get_relationships",
            **result,
        }

    elif action == "find_related":
        if not query:
            return {"error": "query is required for find_related action"}

        relationship_types = args.get("relationship_types")
        fuzzy_threshold = args.get("fuzzy_threshold", 75)

        result = contacts.find_related_contacts(
            query,
            relationship_types=relationship_types,
            fuzzy_threshold=fuzzy_threshold,
        )

        # Update state
        if state is not None:
            if result.get("found"):
                contact_name = result.get("primary_contact", {}).get("display_name", "Unknown")
                rel_count = result.get("relationship_count", 0)
                state.add_fact(f"Found {rel_count} related contacts for {contact_name}")
            else:
                state.add_fact(f"No contact found matching '{query}'")

        return {
            "action": "find_related",
            **result,
        }

    else:
        return {
            "error": f"Unknown action: {action}",
            "valid_actions": ["search", "get_relationships", "find_related"],
        }
