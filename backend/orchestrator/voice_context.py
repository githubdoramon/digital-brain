"""Request-scoped spoken replies. Never inferred from user text or chat history."""

import re
from contextlib import contextmanager
from contextvars import ContextVar

_VOICE = ContextVar("glasses_voice_request", default=False)
# Device actions remain owned by native matching and the fixed command endpoint.
DEVICE_TOOLS = {"home_assistant", "bash", "run_skill_script"}


def is_voice_request() -> bool:
    return _VOICE.get()


@contextmanager
def spoken_reply():
    token = _VOICE.set(True)
    try:
        yield
    finally:
        _VOICE.reset(token)


def tool_allowed(name: str) -> bool:
    return not is_voice_request() or name not in DEVICE_TOOLS


def voice_messages() -> list[dict[str, str]]:
    if not is_voice_request():
        return []
    return [
        {
            "role": "system",
            "content": (
                "This request came through the user's glasses and your answer will be spoken verbatim. "
                "Be concise: usually one or two short sentences, around twenty seconds or less. "
                "Use natural spoken English, without Markdown, URLs, citations, raw identifiers or long lists. "
                "Ask one short question when clarification is needed. Keep normal evidence and confirmation rules. "
                "Gate and camera actions belong exclusively to the fixed glasses command matcher. "
                "You cannot execute them through chat, including ambiguous, negated or multiple commands. "
                "If asked for one, clarify the request or ask the user to say the single supported command again. "
                "Never claim a device action happened through this chat."
            ),
        }
    ]


def speech_text(text: str) -> str:
    """Use one readable, spoken answer for both persistence and synthesis."""
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"(?m)^\s*(?:#{1,6}\s+|[-*+]\s+|\d+[.)]\s+)", "", text)
    text = re.sub(r"[*`_]+", "", text)
    return re.sub(r"\s+", " ", text).strip()
