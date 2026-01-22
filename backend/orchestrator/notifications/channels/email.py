from __future__ import annotations

import os
from typing import Any

import resend


def send_email_notification(to_email: str, subject: str, message: str) -> dict[str, Any]:
    api_key = os.getenv("RESEND_API_KEY", "").strip()
    sender = os.getenv("RESEND_FROM", "").strip()

    if not api_key or not sender:
        return {"sent": 0, "errors": ["Email is not configured"]}

    resend.api_key = api_key
    try:
        response = resend.Emails.send(
            {
                "from": sender,
                "to": [to_email],
                "subject": subject,
                "text": message,
            }
        )
    except Exception as exc:
        return {"sent": 0, "errors": [str(exc)]}

    return {
        "sent": 1,
        "errors": [],
        "id": response.get("id") if isinstance(response, dict) else None,
    }
