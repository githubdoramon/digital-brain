"""Controller-derived links; never accept a URL from message text or the model."""

import re


def trusted_url(value):
    return isinstance(value, str) and bool(
        re.fullmatch(
            r"https://(?:wa\.me/[1-9][0-9]{6,14}|t\.me/(?:c/[1-9][0-9]*/[1-9][0-9]*|[A-Za-z][A-Za-z0-9_]{0,31}|\+[1-9][0-9]{6,14}))",
            value,
        )
    )


def username(value):
    return (
        value
        if isinstance(value, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,31}", value)
        else None
    )


def phone(value):
    if not isinstance(value, str):
        return None
    digits = value.removeprefix("+")
    return digits if re.fullmatch(r"[1-9][0-9]{6,14}", digits) else None


def source_link(
    provider, chat, message_id=None, *, source_username=None, source_phone=None, deleted=False
):
    if provider == "whatsapp":
        match = re.fullmatch(r"([1-9][0-9]{6,14})@s\.whatsapp\.net", chat or "")
        return (
            {
                "url": f"https://wa.me/{match[1]}",
                "label": "Open in WhatsApp",
                "target": "conversation",
            }
            if match
            else None
        )
    if provider != "telegram":
        return None
    channel = re.fullmatch(r"([1-9][0-9]*)@telegram\.channel", chat or "")
    if channel and not deleted and re.fullmatch(r"[1-9][0-9]*", message_id or ""):
        return {
            "url": f"https://t.me/c/{channel[1]}/{message_id}",
            "label": "Open in Telegram",
            "target": "message",
        }
    handle = username(source_username)
    number = phone(source_phone) if (chat or "").endswith("@telegram.user") else None
    target = handle or (f"+{number}" if number else None)
    return (
        {"url": f"https://t.me/{target}", "label": "Open in Telegram", "target": "conversation"}
        if target
        else None
    )


def enrich(row):
    return {
        **row,
        "source_link": source_link(
            row.get("provider"),
            row.get("conversation_external_id"),
            row.get("external_id"),
            source_username=row.get("source_username"),
            source_phone=row.get("source_phone"),
            deleted=row.get("deleted", False),
        ),
    }
