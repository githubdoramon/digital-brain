#!/usr/bin/env python3
"""
Test script for Mem0 integration with the digital-brain orchestrator.

This script tests the memory functionality by simulating a conversation
with multiple questions that build on each other.
"""

import requests
import json
import time
from typing import Dict, Any

API_BASE_URL = "http://localhost:8000"
USER_ID = "test_user"
SESSION_ID = "test_session_001"


def ask_question(question: str, user_id: str = USER_ID, session_id: str = SESSION_ID) -> Dict[str, Any]:
    """Send a question to the /ask endpoint."""
    payload = {
        "question": question,
        "limit": 3,
        "user_id": user_id,
        "session_id": session_id
    }
    
    print(f"\n{'='*60}")
    print(f"Q: {question}")
    print(f"{'='*60}")
    
    try:
        response = requests.post(f"{API_BASE_URL}/ask", json=payload, timeout=120)
        response.raise_for_status()
        result = response.json()
        
        print(f"\nA: {result.get('answer', 'No answer')}")
        
        memories = result.get('memories_used', [])
        if memories:
            print(f"\n📝 Memories used ({len(memories)}):")
            for i, mem in enumerate(memories, 1):
                print(f"  {i}. {mem}")
        else:
            print("\n📝 No memories used (first time conversation or no relevant context)")
        
        return result
    
    except requests.exceptions.ConnectionError:
        print(f"\n❌ Error: Could not connect to {API_BASE_URL}")
        print("   Make sure the orchestrator is running:")
        print("   cd backend/orchestrator && uvicorn app:api --reload")
        return {}
    except requests.exceptions.Timeout:
        print("\n❌ Error: Request timed out (LLM taking too long)")
        return {}
    except requests.exceptions.HTTPError as e:
        print(f"\n❌ HTTP Error: {e}")
        print(f"   Response: {e.response.text}")
        return {}


def test_conversation_memory():
    """Test that the system remembers context across questions."""
    print("\n" + "="*60)
    print("TEST 1: Conversation Memory")
    print("="*60)
    
    # Question 1: Establish a preference
    ask_question(
        "I prefer detailed technical explanations with examples.",
        user_id=USER_ID,
        session_id=SESSION_ID
    )
    
    time.sleep(2)  # Give Mem0 time to process
    
    # Question 2: Should remember the preference
    ask_question(
        "How do SQL queries work?",
        user_id=USER_ID,
        session_id=SESSION_ID
    )
    
    print("\n✓ Test 1 complete. The second answer should reflect your preference for detailed explanations.")


def test_multi_turn_context():
    """Test multi-turn conversation with context building."""
    print("\n" + "="*60)
    print("TEST 2: Multi-Turn Context")
    print("="*60)
    
    session = "test_session_002"
    
    # Question 1: Ask about something
    ask_question(
        "What are the main benefits of using Mem0?",
        user_id=USER_ID,
        session_id=session
    )
    
    time.sleep(2)
    
    # Question 2: Follow-up question (should understand context)
    ask_question(
        "How does that compare to traditional approaches?",
        user_id=USER_ID,
        session_id=session
    )
    
    print("\n✓ Test 2 complete. The system should understand 'that' refers to Mem0 benefits.")


def test_user_separation():
    """Test that different users have separate memories."""
    print("\n" + "="*60)
    print("TEST 3: User Separation")
    print("="*60)
    
    # User 1
    ask_question(
        "My favorite color is blue.",
        user_id="user_alice",
        session_id="alice_session"
    )
    
    time.sleep(2)
    
    # User 2
    ask_question(
        "My favorite color is red.",
        user_id="user_bob",
        session_id="bob_session"
    )
    
    time.sleep(2)
    
    # Ask User 1 again
    result = ask_question(
        "What's my favorite color?",
        user_id="user_alice",
        session_id="alice_session"
    )
    
    answer = result.get('answer', '').lower()
    if 'blue' in answer:
        print("\n✓ Test 3 PASSED: User memories are properly separated")
    else:
        print("\n⚠ Test 3 WARNING: Expected 'blue' in answer, user separation may not be working")


def test_without_memory():
    """Test that the system still works without session/user tracking."""
    print("\n" + "="*60)
    print("TEST 4: Without Memory (Backwards Compatibility)")
    print("="*60)
    
    payload = {"question": "What is a database?"}
    
    try:
        response = requests.post(f"{API_BASE_URL}/ask", json=payload, timeout=60)
        response.raise_for_status()
        result = response.json()
        print(f"\nA: {result.get('answer', 'No answer')}")
        print("\n✓ Test 4 PASSED: System works without memory parameters")
    except Exception as e:
        print(f"\n❌ Test 4 FAILED: {e}")


def main():
    """Run all tests."""
    print("\n" + "="*60)
    print("Mem0 Integration Test Suite")
    print("="*60)
    print(f"API URL: {API_BASE_URL}")
    print(f"Test User: {USER_ID}")
    
    # Check if API is running
    try:
        response = requests.get(f"{API_BASE_URL}/docs", timeout=5)
        print("✓ API is running")
    except:
        print("\n❌ ERROR: API is not running!")
        print("Please start the orchestrator:")
        print("  cd backend/orchestrator")
        print("  uvicorn app:api --reload")
        return
    
    # Run tests
    try:
        test_without_memory()
        time.sleep(3)
        
        test_conversation_memory()
        time.sleep(3)
        
        test_multi_turn_context()
        time.sleep(3)
        
        test_user_separation()
        
        print("\n" + "="*60)
        print("All tests completed!")
        print("="*60)
        print("\nNote: Review the answers to verify memory is working correctly.")
        print("The system should reference previous conversations and user preferences.")
        
    except KeyboardInterrupt:
        print("\n\nTests interrupted by user")
    except Exception as e:
        print(f"\n\n❌ Test suite failed with error: {e}")


if __name__ == "__main__":
    main()

