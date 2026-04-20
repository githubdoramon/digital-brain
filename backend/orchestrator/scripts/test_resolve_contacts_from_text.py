#!/usr/bin/env python3
"""
Simple CLI to test agents.contacts.resolver.resolve_contacts_from_text.

Usage:
  python scripts/test_resolve_contacts_from_text.py "I met Acme's CEO" user@example.com
  python scripts/test_resolve_contacts_from_text.py "What did he say?" user@example.com --conversation-file scripts/conversation.json
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from typing import Any

logger = logging.getLogger(__name__)

# Add backend/orchestrator to import path when running from scripts/
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Test resolve_contacts_from_text from the command line"
    )
    parser.add_argument("text", help="Input text to resolve contacts from")
    parser.add_argument("user_email", help="User email used for relationship and self lookups")
    parser.add_argument(
        "--conversation-file",
        default=None,
        help="Optional path to JSON array of conversation messages",
    )
    parser.add_argument(
        "--llm-base-url",
        default=os.getenv("LLM_BASE_URL"),
        help="LLM_BASE_URL override (defaults to env, then http://localhost:11434)",
    )
    parser.add_argument(
        "--llm-chat-model-fast",
        default=os.getenv("LLM_CHAT_MODEL_FAST"),
        help="LLM_CHAT_MODEL_FAST override (defaults to env, then mistral)",
    )
    parser.add_argument(
        "--llm-chat-model-smart",
        default=os.getenv("LLM_CHAT_MODEL_SMART"),
        help="LLM_CHAT_MODEL_SMART override (defaults to env, then mistral)",
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        help="Print compact JSON instead of pretty output",
    )
    return parser.parse_args()


def _load_conversation_messages(path: str | None) -> list[dict[str, str]] | None:
    if not path:
        return None

    with open(path, encoding="utf-8") as handle:
        data: Any = json.load(handle)

    if not isinstance(data, list):
        raise ValueError("conversation file must be a JSON array")

    messages: list[dict[str, str]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = item.get("content")
        if isinstance(role, str) and isinstance(content, str):
            messages.append({"role": role, "content": content})
    return messages


def main() -> int:
    args = parse_args()

    # resolver imports llm_helpers at module import time and requires these vars.
    os.environ["LLM_BASE_URL"] = args.llm_base_url or "http://localhost:11434"
    os.environ["LLM_CHAT_MODEL_FAST"] = args.llm_chat_model_fast or "mistral"
    os.environ["LLM_CHAT_MODEL_SMART"] = args.llm_chat_model_smart or "mistral"

    from agents.contacts.resolver import resolve_contacts_from_text

    try:
        messages = _load_conversation_messages(args.conversation_file)
        result = resolve_contacts_from_text(
            text=args.text,
            user_email=args.user_email,
            conversation_messages=messages,
        )
    except Exception as exc:
        logger.error("resolve_contacts_from_text failed: %s", exc, exc_info=exc)
        return 1

    if args.compact:
        print(json.dumps(result, ensure_ascii=True))
    else:
        print(json.dumps(result, indent=2, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
