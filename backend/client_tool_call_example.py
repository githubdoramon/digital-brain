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
        session_id: Optional session ID for conversation tracking (backend manages history)
    
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
    
    # Example 2: Multi-turn conversation with session memory
    print("\n\n" + "="*80)
    print("EXAMPLE 2: Multi-turn Conversation with Session Context")
    print("="*80)
    print("(Backend manages conversation history via session_id)")
    
    session = "demo_session_001"
    user = "demo_user"
    
    # First question - establish context
    print("\n" + "-"*80)
    q1 = "Hello! I want to ask some questions about my memories."
    print(f"❓ Question: {q1}")
    bundle1 = ask(q1, user_id=user, session_id=session)
    print(f"\n🤖 Answer:\n{bundle1['answer']}")
    
    # Second question - backend remembers context via session_id
    print("\n" + "-"*80)
    q2 = "Who are my closest contacts?"
    print(f"❓ Question: {q2}")
    bundle2 = ask(q2, user_id=user, session_id=session)
    print(f"\n🤖 Answer:\n{bundle2['answer']}")
    
    # Third question - full conversation context maintained by backend
    print("\n" + "-"*80)
    q3 = "From those people you just mentioned, who did I meet most recently?"
    print(f"❓ Question: {q3}")
    bundle3 = ask(q3, user_id=user, session_id=session)
    print(f"\n🤖 Answer:\n{bundle3['answer']}")
    
    # Show long-term memories that were used
    memories = bundle3.get('memories_used', [])
    if memories:
        print(f"\n🧠 Long-term Memories Used ({len(memories)}):")
        for i, mem in enumerate(memories, 1):
            print(f"  {i}. {mem}")
    
    # Example 3: Long-term memory across sessions
    print("\n\n" + "="*80)
    print("EXAMPLE 3: Long-term Memory Across Sessions")
    print("="*80)
    print("(New session - no short-term context, but long-term memories persist)")
    
    # New session with same user
    new_session = "demo_session_002"
    
    print("\n" + "-"*80)
    q4 = "I prefer vegetarian restaurants"
    print(f"❓ Question: {q4}")
    bundle4 = ask(q4, user_id=user, session_id=new_session)
    print(f"\n🤖 Answer:\n{bundle4['answer']}")
    print("\n💡 mem0 will extract and store relevant facts for long-term memory")
    
    # Later, in another new session
    print("\n" + "-"*80)
    print("(Later, in a completely new session...)")
    another_session = "demo_session_003"
    
    q5 = "Recommend a restaurant for dinner"
    print(f"❓ Question: {q5}")
    bundle5 = ask(q5, user_id=user, session_id=another_session)
    print(f"\n🤖 Answer:\n{bundle5['answer']}")
    
    memories = bundle5.get('memories_used', [])
    if memories:
        print(f"\n🧠 Retrieved Long-term Memories:")
        for i, mem in enumerate(memories, 1):
            print(f"  {i}. {mem}")
        print("\n✨ Notice how the system remembered your vegetarian preference!")
    
    # Uncomment to see full response details
    # print("\n📊 Debug bundle:")
    # print(json.dumps(bundle2, indent=2))


if __name__ == "__main__":
    main()
