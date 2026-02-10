"""
HTTP endpoint for contact resolution.

Thin wrapper that calls the resolver and formats the response.
"""

from typing import Any

from agents.contacts.resolver import resolve_contacts_from_text
from observability.logger import get_runtime_logger
from ui_dsl.clarification import normalize_need_user_input

logger = get_runtime_logger(__name__)


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
        "status": "success" | "need_user_input" | "no_people" | "error",
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

        status = str(result.get("status") or "").strip().lower()
        need_user_input = normalize_need_user_input(result.get("need_user_input"))
        if not status:
            if need_user_input:
                status = "need_user_input"
            elif not result.get("people_mentioned"):
                status = "no_people"
            else:
                status = "success"
            result["status"] = status

        if need_user_input:
            result["need_user_input"] = need_user_input

        return result

    except Exception as e:
        logger.exception("[contact_endpoint] Error: %s", e)
        return {
            "status": "error",
            "message": f"Internal error: {str(e)}",
        }
