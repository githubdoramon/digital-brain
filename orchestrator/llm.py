from __future__ import annotations

import json
import os
from typing import Dict, List

import requests

from retrieval import run_pipeline

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_CHAT_MODEL = os.getenv("OLLAMA_CHAT_MODEL", "gpt-oss:20b")


def answer_question(question: str, search_limit: int = 3) -> Dict[str, object]:
    bundle = run_pipeline(question, search_limit=search_limit)
    answer = answer_with_ollama(question, bundle)
    return {
        "question": question,
        "answer": answer,
        "resolution": bundle.get("resolution"),
        "search_results": bundle.get("search_results"),
        "detailed_events": bundle.get("detailed_events"),
    }


def answer_with_ollama(question: str, bundle: Dict) -> str:
    payload = {
        "model": OLLAMA_CHAT_MODEL,
        "messages": _build_messages(question, bundle),
        "stream": False,
    }
    resp = requests.post(f"{OLLAMA_HOST}/api/chat", json=payload, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    message = data.get("message", {}).get("content")
    if not message:
        raise RuntimeError(f"Unexpected Ollama response: {data}")
    return message.strip()


def _build_messages(question: str, bundle: Dict) -> List[Dict[str, str]]:
    context = _format_context(bundle.get("detailed_events", []))
    return [
        {
            "role": "system",
            "content": (
                "You are a helpful assistant that answers questions using the provided context. "
                "Only rely on the evidence given. If nothing relevant is found, say you have no data."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Question: {question}\n\n"
                f"Resolved filters: {json.dumps(bundle.get('resolution', {}), ensure_ascii=False, indent=2)}\n\n"
                f"Context events:\n{context}\n\n"
                "Answer succinctly."
            ),
        },
    ]


def _format_context(events: List[Dict]) -> str:
    if not events:
        return "<no matching events>"
    lines = []
    for evt in events:
        place = evt.get("place") or {}
        place_bits = [place.get("name"), place.get("city"), place.get("country")]
        place_str = ", ".join([p for p in place_bits if p]) or "Unknown place"
        people = ", ".join(evt.get("people") or []) or "No people listed"
        line = (
            f"- {evt.get('ts')} — {people} at {place_str}. Tags: {', '.join(evt.get('tags') or [])}.\n"
            f"  Notes: {evt.get('what_text') or evt.get('snippet') or '<no description>'}"
        )
        lines.append(line)
    return "\n".join(lines)
