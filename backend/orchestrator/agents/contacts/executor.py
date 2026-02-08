"""
HTTP endpoint for contact resolution.

Thin wrapper that calls the resolver and formats the response.
"""

import logging
from typing import Any

from agents.contacts.resolver import resolve_contacts_from_text

logger = logging.getLogger(__name__)


def handle_resolve_contacts_request(data: dict[str, Any]) -> dict[str, Any]:
    """
    Handle HTTP request for contact resolution.

    Expected request body:
    {
        "text": "visited my daughter's eye doctor",
        "user_email": "user@example.com"  # Added by app.py from auth
    }

    Returns:
    {
        "status": "success" | "needs_clarification" | "no_people" | "error",
        "text": str,
        "people_mentioned": list[str],
        "resolved_contacts": [...],
        "new_contacts": [...],
        "ambiguous_contacts": [...]
        "ambiguous_text": true | false
    }
    """
    text = data.get("text")
    user_email = data.get("user_email")

    if not text:
        return {
            "status": "error",
            "message": "Missing required field: text",
        }

    if not user_email:
        return {
            "status": "error",
            "message": "Missing required field: user_email",
        }

    try:
        # Call the resolver - it handles everything
        result = resolve_contacts_from_text(
            text,
            user_email,
            conversation_messages=data.get("conversation_messages"),
        )

        # Add status field based on results
        if result["ambiguous_contacts"]:
            result["status"] = "needs_clarification"
        elif not result["people_mentioned"]:
            result["status"] = "no_people"
        else:
            result["status"] = "success"

        return result

    except Exception as e:
        logger.exception("[contact_endpoint] Error: %s", e)
        return {
            "status": "error",
            "message": f"Internal error: {str(e)}",
        }
