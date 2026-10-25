"""Sanitized Telegram projections; attachment payloads never enter the archive."""

from __future__ import annotations

from messaging_normalization import MAX_TEXT, Origin, RecordKind, timestamp


def peer_id(peer) -> str:
    from telethon.tl.types import PeerChannel, PeerChat, PeerUser

    if isinstance(peer, PeerUser):
        return f"{peer.user_id}@telegram.user"
    if isinstance(peer, PeerChat):
        return f"{peer.chat_id}@telegram.group"
    if isinstance(peer, PeerChannel):
        return f"{peer.channel_id}@telegram.channel"
    raise ValueError("unsupported_identity")


def display_name(entity) -> str:
    return str(
        getattr(entity, "title", "")
        or " ".join(
            part
            for part in (getattr(entity, "first_name", ""), getattr(entity, "last_name", ""))
            if part
        )
        or getattr(entity, "username", "")
        or ""
    )[:512]


def supported_chat(entity) -> bool:
    from telethon.tl.types import Channel, Chat, User

    return isinstance(entity, (User, Chat, Channel))


def message_record(message, self_id: int, chat, sender=None, origin: Origin = Origin.LIVE):
    from telethon.tl.types import Message, MessageMediaDocument, MessageMediaPhoto, PeerUser

    if not isinstance(message, Message) or not supported_chat(chat):
        return None
    media = message.media
    # Skip disappearing content and never download media.
    if getattr(message, "ttl_period", None) or getattr(media, "ttl_seconds", None):
        return None
    body = message.message or ""
    if len(body) > MAX_TEXT:
        raise ValueError("message_too_large")
    attachments = []
    if isinstance(media, MessageMediaPhoto):
        attachments.append({"type": "image", "filename": "", "size": 0})
    elif isinstance(media, MessageMediaDocument) and media.document:
        document = media.document
        filename = ""
        kind = "document"
        for attribute in getattr(document, "attributes", []):
            name = type(attribute).__name__
            if name == "DocumentAttributeFilename":
                filename = attribute.file_name[:512]
            elif name == "DocumentAttributeAudio":
                kind = "audio"
            elif name == "DocumentAttributeVideo":
                kind = "video"
            elif name == "DocumentAttributeSticker":
                kind = "sticker"
        attachments.append(
            {"type": kind, "filename": filename, "size": max(0, int(getattr(document, "size", 0)))}
        )
    if not body and not attachments:
        return None
    actor = message.from_id or (PeerUser(self_id) if message.out else message.peer_id)
    reply = getattr(message.reply_to, "reply_to_msg_id", None)
    from messaging_identity import telegram_profile

    profiles = {}
    for entity in (chat, sender):
        profile = telegram_profile(entity)
        if profile and profile['external_id'] in (peer_id(actor), peer_id(message.peer_id)):
            profiles[profile['external_id']] = profile
    return {
        "profiles": list(profiles.values()),
        "kind": RecordKind.EDIT.value if message.edit_date else RecordKind.MESSAGE.value,
        "origin": origin.value,
        "chat": peer_id(message.peer_id),
        "conversation_kind": "channel"
        if getattr(chat, "broadcast", False)
        else "group"
        if hasattr(chat, "title")
        else "direct",
        "sender": peer_id(actor),
        "id": str(message.id),
        "sent_at": timestamp(message.date),
        "effective_at": timestamp(message.edit_date or message.date),
        "outgoing": bool(message.out),
        "forwarded": bool(getattr(message, "fwd_from", None)),
        "name": display_name(sender),
        "title": display_name(chat),
        "source_username": getattr(chat, "username", None),
        "source_phone": getattr(chat, "phone", None),
        "source_metadata_known": True,
        "self_jid": f"{self_id}@telegram.user",
        "body": body,
        "attachments": attachments,
        "reply_id": str(reply) if reply else None,
    }
