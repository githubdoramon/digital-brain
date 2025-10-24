#!/usr/bin/env python3
"""Test script to debug mem0 memory storage"""

import os
import sys
from mem0 import Memory

# Use the same config as llm.py
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://host.docker.internal:11434")
OLLAMA_CHAT_MODEL = os.getenv("OLLAMA_CHAT_MODEL", "qwen2.5:32b-instruct")
OLLAMA_EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text")
QDRANT_HOST = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", "6333"))

print(f"Testing mem0 with:")
print(f"  OLLAMA_HOST: {OLLAMA_HOST}")
print(f"  OLLAMA_CHAT_MODEL: {OLLAMA_CHAT_MODEL}")
print(f"  OLLAMA_EMBED_MODEL: {OLLAMA_EMBED_MODEL}")
print(f"  QDRANT_HOST: {QDRANT_HOST}")
print(f"  QDRANT_PORT: {QDRANT_PORT}")
print()

MEM0_CONFIG = {
    "llm": {
        "provider": "ollama",
        "config": {
            "model": OLLAMA_CHAT_MODEL,
            "ollama_base_url": OLLAMA_HOST,
        }
    },
    "embedder": {
        "provider": "ollama",
        "config": {
            "model": OLLAMA_EMBED_MODEL,
            "ollama_base_url": OLLAMA_HOST,
            "embedding_dims": 768,
        }
    },
    "vector_store": {
        "provider": "qdrant",
        "config": {
            "host": QDRANT_HOST,
            "port": QDRANT_PORT,
            "collection_name": "digital_brain_interaction_memories",
            "embedding_model_dims": 768,
        }
    },
    "version": "v1.1"
}

print("Initializing Memory...")
try:
    memory = Memory.from_config(MEM0_CONFIG)
    print("✓ Memory instance created successfully")
except Exception as e:
    print(f"✗ Failed to create Memory instance: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# Test 1: Add a memory
print("\n" + "="*60)
print("TEST 1: Adding a memory")
print("="*60)
try:
    conversation_text = "User asked: What is my name?\nAssistant answered: I don't have that information stored yet."
    print(f"Adding memory: {conversation_text[:50]}...")
    result = memory.add(
        conversation_text,
        user_id="test_user",
        metadata={"session_id": "test_session_123"}
    )
    print(f"✓ Memory added successfully!")
    print(f"Result: {result}")
except Exception as e:
    print(f"✗ Failed to add memory: {e}")
    import traceback
    traceback.print_exc()

# Test 2: Search for the memory
print("\n" + "="*60)
print("TEST 2: Searching for memories")
print("="*60)
try:
    query = "What is my name?"
    print(f"Searching for: {query}")
    results = memory.search(query, user_id="test_user", limit=5)
    print(f"✓ Search completed successfully!")
    print(f"Results: {results}")
    
    if isinstance(results, dict) and "results" in results:
        print(f"Found {len(results['results'])} memories")
        for i, result in enumerate(results["results"][:3]):
            print(f"  Memory {i+1}: {result.get('memory', 'N/A')[:80]}...")
    else:
        print(f"Unexpected results format: {type(results)}")
except Exception as e:
    print(f"✗ Failed to search memory: {e}")
    import traceback
    traceback.print_exc()

# Test 3: Get all memories for user
print("\n" + "="*60)
print("TEST 3: Getting all memories for user")
print("="*60)
try:
    all_memories = memory.get_all(user_id="test_user")
    print(f"✓ Retrieved all memories successfully!")
    print(f"Total memories: {len(all_memories) if isinstance(all_memories, list) else 'Unknown'}")
    print(f"All memories: {all_memories}")
except Exception as e:
    print(f"✗ Failed to get all memories: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "="*60)
print("Tests completed!")
print("="*60)

