#!/usr/bin/env python3
"""Simple CLI to query the orchestrator's /ask endpoint."""

import json
import os
from typing import Any, Dict

import requests

API_BASE = os.getenv("ORCHESTRATOR_URL", "http://localhost:8000")


def ask(question: str, limit: int = 3) -> Dict[str, Any]:
    resp = requests.post(
        f"{API_BASE}/ask",
        json={"question": question, "limit": limit},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()


def main() -> None:
    questions = [
        "Where and when did I discuss the product roadmap? And with whom?",
        "How many contacts do I have?",
    ]
    for q in questions:
        print("=" * 80)
        print(f"❓ Question: {q}")
        bundle = ask(q)
        print("\n🤖 Answer:\n" + bundle["answer"])
        # print("\n📊 Debug bundle:")
        # print(json.dumps(bundle, indent=2))


if __name__ == "__main__":
    main()
