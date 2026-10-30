"""Request-scoped spoken replies. Never inferred from user text or chat history."""

import logging
import re
from contextlib import contextmanager
from contextvars import ContextVar

_VOICE = ContextVar("glasses_voice_request", default=False)
# Device actions remain owned by native matching and the fixed command endpoint.
DEVICE_TOOLS = {"home_assistant", "bash", "run_skill_script"}


def voice_stage(stage: str, reason: str = "none") -> None:
    """Only fixed status enums may leave the speech privacy boundary."""
    if stage not in {
        "started",
        "synthesis_local",
        "synthesis_remote",
        "answer_ready",
        "speech_ready",
        "speech_failed",
        "cancelled",
        "timeout",
    }:
        return
    if reason not in {
        "none",
        "tts_not_configured",
        "tts_failed",
        "tts_invalid_audio",
        "tts_text_too_long",
        "tts_audio_too_long",
        "tts_audio_empty",
    }:
        reason = "tts_failed"
    token = _VOICE.set(False)
    try:
        logging.getLogger(__name__).info("[glasses-chat] stage=%s reason=%s", stage, reason)
    finally:
        _VOICE.reset(token)


def is_voice_request() -> bool:
    return _VOICE.get()


def voice_audio(frames: int, rate: int) -> None:
    """Audio shape only; no waveform, text, request IDs or device information."""
    frames, rate = int(frames), int(rate)
    token = _VOICE.set(False)
    try:
        logging.getLogger(__name__).info(
            "[glasses-chat] stage=audio_generated frames=%d sample_rate=%d duration_ms=%d",
            frames,
            rate,
            frames * 1000 // max(1, rate),
        )
    finally:
        _VOICE.reset(token)


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
                "Be concise: one or two short sentences, at most sixty words, around twenty seconds or less. "
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
    text = re.sub(r"\s+", " ", text).strip()
    # The model's requested brevity is not a reliable output bound. Apply this
    # before persistence so chat and speech still contain the same answer.
    words = text.split()
    if len(words) > 60 or len(text) > 600:
        prefix = " ".join(words[:60])[:600]
        sentences = list(re.finditer(r"[.!?](?:\s|$)", prefix))
        if sentences:
            return prefix[: sentences[-1].start() + 1]
        return prefix.rsplit(" ", 1)[0].rstrip(",;:") + "."
    return text
