#!/usr/bin/env python3
"""Simple CLI to query the orchestrator's /ask endpoint."""

import json
import os
from typing import Any, Dict, Optional

import requests

API_BASE = os.getenv("ORCHESTRATOR_URL", "http://localhost:8000")


def ask(
    question: str, 
    limit: int = 3, 
    user_id: Optional[str] = None,
    session_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Ask a question to the digital brain orchestrator.
    
    Args:
        question: The question to ask
        limit: Maximum number of search results to return
        user_id: Optional user ID for personalized memory (default: "default_user")
        session_id: Optional session ID for conversation tracking
    
    Returns:
        Response dict with answer, memories_used, and other context
    """
    payload = {"question": question, "limit": limit}
    if user_id:
        payload["user_id"] = user_id
    if session_id:
        payload["session_id"] = session_id
    
    resp = requests.post(
        f"{API_BASE}/ask",
        json=payload,
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()


def main() -> None:
    # Example 1: Basic questions (no memory)
    print("\n" + "="*80)
    print("EXAMPLE 1: Basic Questions (No Memory)")
    print("="*80)
    
    questions = [
        "Which of my contacts do you think are closer to me? From a relationship perspective, not location. Give me their names.",
        "Show all contacts and their relationships"
    ]
    for q in questions:
        print("\n" + "-"*80)
        print(f"❓ Question: {q}")
        bundle = ask(q)
        print("\n🤖 Answer:\n" + bundle["answer"])
    
    # Example 2: Conversation with memory
    print("\n\n" + "="*80)
    print("EXAMPLE 2: Conversation with Memory")
    print("="*80)
    print("(This demonstrates how the system remembers context across questions)")
    
    session = "demo_session_001"
    user = "demo_user"
    
    # First question - establish context
    print("\n" + "-"*80)
    q1 = "I prefer detailed explanations with examples"
    print(f"❓ Question: {q1}")
    bundle1 = ask(q1, user_id=user, session_id=session)
    print(f"\n🤖 Answer:\n{bundle1['answer']}")
    
    # Second question - should remember the preference
    print("\n" + "-"*80)
    q2 = "How does the database schema work?"
    print(f"❓ Question: {q2}")
    bundle2 = ask(q2, user_id=user, session_id=session)
    print(f"\n🤖 Answer:\n{bundle2['answer']}")
    
    memories = bundle2.get('memories_used', [])
    if memories:
        print(f"\n📝 Memories Used ({len(memories)}):")
        for i, mem in enumerate(memories, 1):
            print(f"  {i}. {mem}")
    
    # Uncomment to see full response details
    # print("\n📊 Debug bundle:")
    # print(json.dumps(bundle2, indent=2))


if __name__ == "__main__":
    main()
