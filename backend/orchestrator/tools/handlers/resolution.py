"""
Entity resolution and contact lookup tool handlers.

Handles:
- resolve_contacts: Extract and resolve people from free-form text
- lookup_contact: Smart contact search with fuzzy matching and relationship filtering
- lookup_places / lookup_contact_places / lookup_place_contacts: place-centric resolution helpers

Note: Tracing/logging is handled at the controller level via the centralized
trace module. Handlers focus purely on execution logic.
"""

from typing import TYPE_CHECKING, Any, Optional

from search_normalization import normalize_search_text
from tools.action_enums import LookupContactAction, SelectContactsAction
from tools.limit_policy import wants_all_results

if TYPE_CHECKING:
    from agent.state import AgentState


_ROLE_HINT_KEYWORDS: dict[str, set[str]] = {
    "home": {
        "home",
        "house",
        "residence",
        "apartment",
        "apt",
        "condo",
        "flat",
        "address",
        "live",
        "lives",
    },
    "work": {"work", "office", "workplace", "job", "company", "hq", "headquarters"},
    "school": {"school", "college", "university", "campus"},
}


def _infer_role_hint_from_text(text: str) -> str | None:
    normalized = normalize_search_text(text or "")
    if not normalized:
        return None
    token_set = {token for token in normalized.replace("-", " ").split() if token}
    if not token_set:
        return None

    best_role: str | None = None
    best_score = 0
    for role, keywords in _ROLE_HINT_KEYWORDS.items():
        score = len(token_set.intersection(keywords))
        if score > best_score:
            best_score = score
            best_role = role

    return best_role


def handle_resolve_contacts(
    args: dict[str, Any],
    state: Optional["AgentState"] = None,
    conversation_history: Optional[list[dict[str, str]]] = None,
    user_email: str = "",
    **kwargs,
) -> dict[str, Any]:
    """
    Execute resolve_contacts tool.

    Extracts and resolves people from free-form text using the contacts resolver pipeline.
    """
    from contact_resolution_service import resolve_contacts_request

    text = args.get("text", "")
    if not text:
        text = kwargs.get("question", "")

    # user_email should be injected by the controller, not authored by the model.
    runtime_email = user_email or args.get("user_email", "")

    if not text:
        return {"error": "text is required"}
    if not runtime_email:
        return {"error": "user_email is required"}

    payload: dict[str, Any] = {"text": text, "user_email": runtime_email}
    if conversation_history:
        payload["conversation_messages"] = conversation_history[-8:]

    result = resolve_contacts_request(payload)

    if state is not None:
        status = result.get("status", "unknown")
        people_count = len(result.get("people_mentioned", []))
        resolved_count = len(result.get("resolved_contacts", []))
        ambiguous_count = len(result.get("ambiguous_contacts", []))
        state.add_fact(
            "Resolved "
            f"{people_count} mentions to {resolved_count} contacts "
            f"(ambiguous: {ambiguous_count}, status: {status})"
        )
        state.resolution["contact_resolution"] = result

    return result


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

    raw_action = args.get("action")
    action = LookupContactAction.from_value(
        raw_action,
        default=LookupContactAction.SEARCH if raw_action is None else None,
    )
    query = args.get("query", "")

    if action is LookupContactAction.SEARCH:
        if not query:
            return {"error": "query is required for search action"}

        search_by = args.get("search_by", "any")
        fuzzy_threshold = args.get("fuzzy_threshold", 75)
        question = str(kwargs.get("question") or "")
        limit = None if wants_all_results(question) else args.get("limit", 10)

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
                state.add_fact(
                    f"Found {len(results)} contacts matching '{query}': {', '.join(names)}"
                )
            else:
                state.add_fact(f"No contacts found matching '{query}'")

        return {
            "action": LookupContactAction.SEARCH.value,
            "query": query,
            "found": len(results) > 0,
            "count": len(results),
            "contacts": results,
        }

    elif action is LookupContactAction.GET_RELATIONSHIPS:
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
            "action": LookupContactAction.GET_RELATIONSHIPS.value,
            **result,
        }

    elif action is LookupContactAction.FIND_RELATED:
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
            "action": LookupContactAction.FIND_RELATED.value,
            **result,
        }

    else:
        return {
            "error": f"Unknown action: {raw_action}",
            "valid_actions": [member.value for member in LookupContactAction],
        }


def handle_select_contacts(
    args: dict[str, Any],
    state: Optional["AgentState"] = None,
    user_email: str = "",
    **kwargs,
) -> dict[str, Any]:
    """Execute selector-based contact lookup and group management operations."""

    import contact_groups
    import contacts
    from ui_dsl.clarification import build_need_user_input

    raw_action = args.get("action")
    action = SelectContactsAction.from_value(
        raw_action,
        default=SelectContactsAction.SELECT if raw_action is None else None,
    )

    runtime_email = user_email or kwargs.get("user_email", "")
    if not runtime_email:
        return {"error": "user_email is required"}

    if action is SelectContactsAction.SELECT:
        selector_kind = str(args.get("selector_kind") or "").strip().lower()
        value = str(args.get("value") or "").strip()
        auto_activate = bool(args.get("auto_activate", True))
        question = str(kwargs.get("question") or "")
        if wants_all_results(question):
            limit: int | None = None
        else:
            limit = int(args.get("limit", 120) or 120)

        if selector_kind not in {"email_domain", "company", "group", "tag"}:
            return {"error": "selector_kind must be one of: email_domain, company, group, tag"}
        if not value:
            return {"error": "value is required"}

        contacts_result: list[dict[str, Any]] = []
        group_info: dict[str, Any] | None = None
        deterministic = False

        if selector_kind == "email_domain":
            contacts_result = contacts.search_contacts_by_email_domain(value, limit=limit)
            deterministic = True
            if auto_activate and contacts_result:
                group_info = contact_groups.upsert_group_from_selector(
                    user_email=runtime_email,
                    name=f"People at @{value.lstrip('@')}",
                    member_contact_ids=[
                        str(contact.get("contact_id") or "")
                        for contact in contacts_result
                        if str(contact.get("contact_id") or "").strip()
                    ],
                    aliases=[f"@{value.lstrip('@')}", value.lstrip("@")],
                    description=f"Contacts matched by email domain @{value.lstrip('@')}.",
                    source="deterministic",
                    confirmed=True,
                    replace_members=True,
                    added_via="selector_email_domain",
                    confidence=0.95,
                )

        elif selector_kind == "company":
            contacts_result = contacts.search_contacts_by_company(value, limit=limit)
            deterministic = True
            if auto_activate and contacts_result:
                group_info = contact_groups.upsert_group_from_selector(
                    user_email=runtime_email,
                    name=f"{value} team",
                    member_contact_ids=[
                        str(contact.get("contact_id") or "")
                        for contact in contacts_result
                        if str(contact.get("contact_id") or "").strip()
                    ],
                    aliases=[value, f"company {value}"],
                    description=f"Contacts matched for company '{value}'.",
                    source="deterministic",
                    confirmed=True,
                    replace_members=True,
                    added_via="selector_company",
                    confidence=0.9,
                )

        elif selector_kind == "group":
            group_lookup = contact_groups.resolve_group_members(runtime_email, value, limit=limit)
            if group_lookup.get("found"):
                contacts_result = list(group_lookup.get("contacts") or [])
                deterministic = True
            else:
                contacts_result = contacts.search_contacts_by_group_hint(value, limit=limit)

        need_user_input = None
        if selector_kind == "group" and not deterministic and contacts_result:
            need_user_input = build_need_user_input(
                kind="confirmation",
                source="contact_groups",
                prompt=f'Should I save "{value}" as a reusable contact group?',
                questions=[
                    f'Should I save "{value}" as a reusable contact group for future queries?'
                ],
                fields=[
                    {
                        "id": "confirm_group",
                        "kind": "select",
                        "label": f'Save group "{value}"?',
                        "required": True,
                        "options": [
                            {"id": "yes", "label": "Yes"},
                            {"id": "no", "label": "No"},
                        ],
                    }
                ],
                submission_mode="ui_submission",
            )

        elif selector_kind == "tag":
            contacts_result = contacts.search_contacts_by_group_hint(value, limit=limit)

        if state is not None:
            state.add_fact(
                f"Selector '{selector_kind}:{value}' matched {len(contacts_result)} contacts"
            )

        return {
            "action": SelectContactsAction.SELECT.value,
            "selector_kind": selector_kind,
            "value": value,
            "deterministic": deterministic,
            "count": len(contacts_result),
            "contacts": contacts_result,
            "group": group_info,
            "need_user_input": need_user_input,
        }

    if action is SelectContactsAction.LIST_GROUPS:
        include_archived = bool(args.get("include_archived", False))
        groups = contact_groups.list_contact_groups(
            runtime_email, include_archived=include_archived
        )
        return {
            "action": SelectContactsAction.LIST_GROUPS.value,
            "count": len(groups),
            "groups": groups,
        }

    if action is SelectContactsAction.GET_GROUP:
        group_id = str(args.get("group_id") or "").strip()
        if not group_id:
            return {"error": "group_id is required for get_group"}
        group = contact_groups.get_contact_group(runtime_email, group_id)
        return {
            "action": SelectContactsAction.GET_GROUP.value,
            "found": bool(group),
            "group": group,
        }

    if action is SelectContactsAction.CREATE_GROUP:
        name = str(args.get("name") or "").strip()
        member_contact_ids = args.get("member_contact_ids") or []
        aliases = args.get("aliases") or []
        description = str(args.get("description") or "").strip() or None
        if not name:
            return {"error": "name is required for create_group"}
        if not isinstance(member_contact_ids, list) or not member_contact_ids:
            return {"error": "member_contact_ids must be a non-empty array"}
        if not isinstance(aliases, list):
            return {"error": "aliases must be an array when provided"}

        created = contact_groups.create_contact_group(
            user_email=runtime_email,
            name=name,
            member_contact_ids=[
                str(item).strip() for item in member_contact_ids if str(item).strip()
            ],
            aliases=[str(item).strip() for item in aliases if str(item).strip()],
            description=description,
        )
        return {
            "action": SelectContactsAction.CREATE_GROUP.value,
            "created": bool(created),
            "group": created,
        }

    if action is SelectContactsAction.ARCHIVE_GROUP:
        group_id = str(args.get("group_id") or "").strip()
        if not group_id:
            return {"error": "group_id is required for archive_group"}
        archived = contact_groups.archive_contact_group(runtime_email, group_id)
        return {
            "action": SelectContactsAction.ARCHIVE_GROUP.value,
            "archived": archived,
            "group_id": group_id,
        }

    return {
        "error": f"Unknown action: {raw_action}",
        "valid_actions": [member.value for member in SelectContactsAction],
    }


def handle_lookup_places(
    args: dict[str, Any],
    state: Optional["AgentState"] = None,
    **kwargs,
) -> dict[str, Any]:
    """Lookup places by name/alias with optional proximity context."""
    import places as places_service

    query = str(args.get("query") or "").strip()
    if not query:
        return {"error": "query is required"}

    near_lat = args.get("near_lat")
    near_lon = args.get("near_lon")
    client_location = None
    if near_lat is not None and near_lon is not None:
        client_location = {"lat": near_lat, "lon": near_lon}

    limit = int(args.get("limit", 5) or 5)
    fuzzy_threshold = int(args.get("fuzzy_threshold", 80) or 80)

    results = places_service.search_places(
        query,
        client_location=client_location,
        fuzzy_threshold=fuzzy_threshold,
        limit=limit,
    )

    if state is not None:
        if results:
            state.add_fact(f"Found {len(results)} place matches for '{query}'")
        else:
            state.add_fact(f"No places found for '{query}'")

    return {
        "query": query,
        "found": bool(results),
        "count": len(results),
        "places": results,
    }


def handle_lookup_contact_places(
    args: dict[str, Any],
    state: Optional["AgentState"] = None,
    **kwargs,
) -> dict[str, Any]:
    """Lookup places linked to a contact, with optional role hint."""
    import contact_groups
    import contacts
    import places as places_service

    runtime_email = str(kwargs.get("user_email") or "").strip()
    contact_id = str(args.get("contact_id") or "").strip()
    contact_query = str(args.get("contact_query") or "").strip()
    group_query = str(args.get("group_query") or "").strip()
    role_hint = str(args.get("role_hint") or "").strip() or None
    where_text = str(args.get("where_text") or "").strip() or None

    question_text = str(kwargs.get("question") or "").strip().lower()
    if not role_hint:
        hint_text = f"{where_text or ''} {question_text}".strip()
        role_hint = _infer_role_hint_from_text(hint_text)

    def _resolve_group_places(query: str) -> dict[str, Any]:
        if not runtime_email:
            return {
                "found": False,
                "error": "user_email is required for group_query lookups",
                "contact_places": [],
            }
        group_lookup = contact_groups.resolve_group_members(runtime_email, query, limit=120)
        if not group_lookup.get("found"):
            return {
                "found": False,
                "error": f"No contact group found matching '{query}'",
                "contact_places": [],
                "group": None,
            }

        member_contacts = list(group_lookup.get("contacts") or [])
        place_votes: dict[str, dict[str, Any]] = {}
        for member in member_contacts:
            member_contact_id = str(member.get("contact_id") or "").strip()
            if not member_contact_id:
                continue
            member_places = places_service.list_contact_places(
                member_contact_id, role_hint=role_hint
            )
            for place in member_places:
                place_id = str(place.get("place_id") or "").strip()
                if not place_id:
                    continue
                vote = place_votes.get(place_id)
                if vote is None:
                    vote = {
                        "place_id": place_id,
                        "name": place.get("name"),
                        "city": place.get("city"),
                        "country": place.get("country"),
                        "lat": place.get("lat"),
                        "lon": place.get("lon"),
                        "roles": set(),
                        "member_count": 0,
                    }
                    place_votes[place_id] = vote
                role_value = str(place.get("role") or "").strip()
                if role_value:
                    vote["roles"].add(role_value)
                vote["member_count"] = int(vote.get("member_count") or 0) + 1

        ranked_places = list(place_votes.values())
        ranked_places.sort(
            key=lambda row: (
                -int(row.get("member_count") or 0),
                str(row.get("name") or ""),
            )
        )
        for row in ranked_places:
            row["roles"] = sorted(row.get("roles") or [])

        suggested_place = ranked_places[0] if ranked_places else None
        if state is not None:
            if ranked_places:
                state.add_fact(
                    f"Found {len(ranked_places)} place candidates for group '{query}' from {len(member_contacts)} members"
                )
            else:
                state.add_fact(f"No linked places found for group '{query}'")

        return {
            "found": bool(ranked_places),
            "group_query": query,
            "group": group_lookup.get("group"),
            "member_count": len(member_contacts),
            "role_hint": role_hint,
            "count": len(ranked_places),
            "contact_places": ranked_places,
            "suggested_place": suggested_place,
        }

    matched_contact: dict[str, Any] | None = None
    if group_query:
        return _resolve_group_places(group_query)

    if not contact_id and not contact_query:
        return {"error": "contact_id, contact_query, or group_query is required"}

    if not contact_id and contact_query:
        contacts_found = contacts.search_contacts(contact_query, limit=3)
        if not contacts_found:
            if runtime_email:
                return _resolve_group_places(contact_query)
            return {
                "found": False,
                "error": f"No contact found matching '{contact_query}'",
                "contact_places": [],
            }
        matched_contact = contacts_found[0]
        contact_id = str(matched_contact.get("contact_id") or "").strip()

    if not contact_id:
        return {"error": "Unable to resolve contact_id"}

    contact_places = places_service.list_contact_places(contact_id, role_hint=role_hint)
    suggested = places_service.resolve_contact_place(
        contact_id=contact_id,
        role_hint=role_hint,
        where_text=where_text,
    )

    if state is not None:
        if contact_places:
            state.add_fact(f"Found {len(contact_places)} linked places for contact {contact_id}")
        else:
            state.add_fact(f"No linked places found for contact {contact_id}")

    return {
        "found": bool(contact_places),
        "contact_id": contact_id,
        "contact": matched_contact,
        "role_hint": role_hint,
        "count": len(contact_places),
        "contact_places": contact_places,
        "suggested_place": suggested,
    }


def handle_lookup_place_contacts(
    args: dict[str, Any],
    state: Optional["AgentState"] = None,
    **kwargs,
) -> dict[str, Any]:
    """Lookup contacts linked to a place, with optional role hint."""
    import places as places_service

    place_id = str(args.get("place_id") or "").strip()
    place_query = str(args.get("place_query") or "").strip()
    role_hint = str(args.get("role_hint") or "").strip() or None
    limit = int(args.get("limit", 25) or 25)

    near_lat = args.get("near_lat")
    near_lon = args.get("near_lon")
    client_location = None
    if near_lat is not None and near_lon is not None:
        client_location = {"lat": near_lat, "lon": near_lon}

    resolved_place: dict[str, Any] | None = None
    if not place_id:
        if not place_query:
            return {"error": "place_id or place_query is required"}
        matches = places_service.search_places(
            place_query,
            client_location=client_location,
            fuzzy_threshold=int(args.get("fuzzy_threshold", 80) or 80),
            limit=3,
        )
        if not matches:
            return {
                "found": False,
                "error": f"No known place found matching '{place_query}'",
                "contacts": [],
            }
        resolved_place = matches[0]
        place_id = str(resolved_place.get("place_id") or "").strip()

    if not place_id:
        return {"error": "Unable to resolve place_id"}

    if resolved_place is None:
        place_record = places_service.get_place(place_id)
        if place_record:
            resolved_place = {
                "place_id": place_id,
                "name": place_record.get("name"),
                "city": place_record.get("city"),
                "country": place_record.get("country"),
                "lat": place_record.get("lat"),
                "lon": place_record.get("lon"),
            }

    place_contacts = places_service.list_place_contacts(
        place_id,
        role_hint=role_hint,
        limit=limit,
    )

    if state is not None:
        if place_contacts:
            state.add_fact(f"Found {len(place_contacts)} contacts linked to place {place_id}")
        else:
            state.add_fact(f"No contacts linked to place {place_id}")

    return {
        "found": bool(place_contacts),
        "place_id": place_id,
        "place": resolved_place,
        "role_hint": role_hint,
        "count": len(place_contacts),
        "contacts": place_contacts,
    }
