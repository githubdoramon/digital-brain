"""Allow-listed WhatsApp projections. Never serialize raw protocol objects."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from enum import Enum, IntEnum
from typing import Any


class Provider(str, Enum):
    WHATSAPP = "whatsapp"
    TELEGRAM = "telegram"


def group_identity(value: str) -> bool:
    return value.endswith(("@g.us", "@telegram.group", "@telegram.channel"))


class Origin(str, Enum):
    LIVE = "live"
    HISTORY = "history"


class RecordKind(str, Enum):
    MESSAGE = "message"
    EDIT = "edit"
    REVOKE = "revoke"


class ConnectionState(str, Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    PAIRING = "pairing"
    CONNECTED = "connected"
    NEEDS_PAIRING = "needs_pairing"
    ATTENTION = "attention"
    PASSWORD_REQUIRED = "password_required"


class ProtocolKind(IntEnum):
    REVOKE = 0
    EDIT = 14


MAX_TEXT = 65536
MAX_BATCH = 100


def stable_id(*parts: str) -> str:
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode()).hexdigest()


def jid(value: Any) -> str:
    if value is None or value == "":
        return ""
    result = value if isinstance(value, str) else f"{value.user}@{value.server}"
    # Device suffixes are transport addresses, not distinct people.
    user, separator, server = result.partition("@")
    if (
        not separator
        or not user
        or server
        not in {
            "s.whatsapp.net",
            "lid",
            "g.us",
            "telegram.user",
            "telegram.group",
            "telegram.channel",
        }
    ):
        raise ValueError("unsupported_identity")
    if server.startswith("telegram.") and (not user.isdigit() or int(user) <= 0):
        raise ValueError("unsupported_identity")
    return f"{user.split(':')[0]}@{server}"


def timestamp(value: Any) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("missing_timezone")
        return value.astimezone(timezone.utc).isoformat()
    numeric = float(value)
    if numeric <= 0:
        raise ValueError("missing_timestamp")
    return datetime.fromtimestamp(numeric, timezone.utc).isoformat()


def present(obj: Any, name: str) -> bool:
    try:
        return obj.HasField(name)
    except (ValueError, AttributeError):
        return False


def content(proto: Any) -> tuple[str, list[dict], str | None]:
    # Unwrap only known message wrappers; do not recurse through arbitrary fields.
    for _ in range(4):
        wrapper = next(
            (
                name
                for name in (
                    "ephemeralMessage",
                    "viewOnceMessage",
                    "viewOnceMessageV2",
                    "documentWithCaptionMessage",
                    "editedMessage",
                )
                if present(proto, name)
            ),
            None,
        )
        if not wrapper:
            break
        proto = getattr(proto, wrapper).message
    text = getattr(proto, "conversation", "") or ""
    attachments = []
    reply = None
    if present(proto, "extendedTextMessage"):
        extended = proto.extendedTextMessage
        text = extended.text
        if present(extended, "contextInfo"):
            reply = extended.contextInfo.stanzaId or None
    for field, kind in (
        ("imageMessage", "image"),
        ("videoMessage", "video"),
        ("audioMessage", "audio"),
        ("documentMessage", "document"),
        ("stickerMessage", "sticker"),
    ):
        if present(proto, field):
            media = getattr(proto, field)
            text = getattr(media, "caption", "") or text
            attachments.append(
                {
                    "type": kind,
                    "filename": str(getattr(media, "fileName", "") or "")[:512],
                    "size": int(getattr(media, "fileLength", 0) or 0),
                }
            )
            if present(media, "contextInfo"):
                reply = media.contextInfo.stanzaId or reply
    if len(text) > MAX_TEXT:
        raise ValueError("message_too_large")
    return text, attachments, reply


def normalize_message(
    *,
    proto: Any,
    chat: Any,
    sender: Any,
    message_id: str,
    sent_at: Any,
    outgoing: bool,
    origin: Origin,
    name: str = "",
    title: str = "",
    self_jid: str = "",
) -> dict | None:
    chat_id, sender_id = jid(chat), jid(sender)
    if not chat_id or not sender_id or not message_id or len(message_id) > 256:
        raise ValueError("missing_message_identity")
    time = timestamp(sent_at)
    record = {
        "kind": RecordKind.MESSAGE.value,
        "origin": origin.value,
        "chat": chat_id,
        "sender": sender_id,
        "id": message_id,
        "sent_at": time,
        "effective_at": time,
        "outgoing": bool(outgoing),
        "name": str(name or "")[:512],
        "title": str(title or "")[:512],
        "self_jid": jid(self_jid),
    }
    if present(proto, "protocolMessage"):
        control = proto.protocolMessage
        # REVOKE=0 and MESSAGE_EDIT=14 in the protocol enum. Unknown control
        # messages are not user text and cannot become commands or memories.
        if (
            not present(control, "type")
            or control.type not in (ProtocolKind.REVOKE, ProtocolKind.EDIT)
            or not present(control, "key")
        ):
            return None
        key = control.key
        target_sender = key.participant or (self_jid if key.fromMe else "")
        if not target_sender and not chat_id.endswith("@g.us"):
            target_sender = chat_id
        if not target_sender:
            raise ValueError("unresolved_mutation_sender")
        record.update(
            kind=RecordKind.REVOKE.value
            if control.type == ProtocolKind.REVOKE
            else RecordKind.EDIT.value,
            id=key.id,
            sender=jid(target_sender),
            outgoing=bool(key.fromMe),
        )
        if control.timestampMs:
            record["effective_at"] = timestamp(control.timestampMs / 1000)
        proto = control.editedMessage if control.type == ProtocolKind.EDIT else None
    body, attachments, reply = content(proto) if proto is not None else ("", [], None)
    record.update(body=body, attachments=attachments, reply_id=reply)
    # Only a boolean authorship signal is retained; never forwarding authors,
    # quoted bodies, media keys or the rest of ContextInfo.
    record["forwarded"] = False
    if proto is not None:
        for _ in range(4):
            wrapper = next((field for field in (
                "ephemeralMessage", "viewOnceMessage", "viewOnceMessageV2",
                "viewOnceMessageV2Extension", "documentWithCaptionMessage", "editedMessage",
            ) if present(proto, field)), None)
            if not wrapper:
                break
            proto = getattr(proto, wrapper).message
        for _, value in proto.ListFields():
            if hasattr(value, "contextInfo"):
                info = value.contextInfo
                record["forwarded"] |= bool(info.isForwarded or info.forwardingScore)
    if not body and not attachments and record["kind"] == RecordKind.MESSAGE.value:
        return None
    return record


def live_record(event: Any, self_jid: str) -> dict | None:
    data = event.data
    info = data.message_info
    source = info.source
    record = normalize_message(
        proto=data.raw_proto,
        chat=source.chat,
        sender=source.sender,
        message_id=info.id,
        sent_at=info.timestamp,
        outgoing=source.is_from_me,
        origin=Origin.LIVE,
        name=info.push_name,
        self_jid=self_jid,
    )
    if record:
        from messaging_identity import verified_pair

        pair = verified_pair(source.sender, getattr(source, 'sender_alt', None))
        record['identity_mappings'] = [pair] if pair else []
    return record


def history_records(proto: Any, self_jid: str, on_error=None, *, cutoff: datetime | None = None):
    """Stream projections; never persist SerializeToString()/raw HistorySync."""
    for conversation in proto.conversations:
        try:
            chat = jid(conversation.id)
        except ValueError:
            # Status/newsletter/broadcast containers are outside this connector's scope.
            continue
        for item in conversation.messages:
            message = item.message
            if (
                cutoff is not None
                and message.messageTimestamp
                and message.messageTimestamp < cutoff.timestamp()
            ):
                continue
            key = message.key
            sender = key.participant or message.participant
            if not sender:
                sender = self_jid if key.fromMe else chat
            try:
                record = normalize_message(
                    proto=message.message,
                    chat=chat,
                    sender=sender,
                    message_id=key.id,
                    sent_at=message.messageTimestamp,
                    outgoing=key.fromMe,
                    origin=Origin.HISTORY,
                    name=message.pushName,
                    title=conversation.name,
                    self_jid=self_jid,
                )
            except ValueError:
                if on_error is None:
                    raise
                on_error()
                continue
            if record:
                yield record
