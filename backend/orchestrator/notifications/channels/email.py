from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage
from typing import Any


def send_email_notification(to_email: str, subject: str, message: str) -> dict[str, Any]:
    host = os.getenv("EMAIL_HOST", "").strip()
    port = int(os.getenv("EMAIL_PORT", "0") or 0)
    username = os.getenv("EMAIL_USERNAME", "").strip()
    password = os.getenv("EMAIL_PASSWORD", "").strip()
    sender = os.getenv("EMAIL_FROM", "").strip()
    use_tls = _parse_bool(os.getenv("EMAIL_USE_TLS", "true"))

    if not host or not sender:
        return {"sent": 0, "errors": ["Email is not configured"]}

    message_obj = EmailMessage()
    message_obj["From"] = sender
    message_obj["To"] = to_email
    message_obj["Subject"] = subject
    message_obj.set_content(message)

    try:
        with smtplib.SMTP(host, port or 587, timeout=15) as smtp:
            if use_tls:
                smtp.starttls()
            if username:
                smtp.login(username, password)
            smtp.send_message(message_obj)
    except Exception as exc:
        return {"sent": 0, "errors": [str(exc)]}

    return {"sent": 1, "errors": []}


def _parse_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "y"}
