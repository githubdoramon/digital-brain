#!/usr/bin/env python3
"""Test script with more fact-rich content"""

import os
from mem0 import Memory

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://host.docker.internal:11434")
OLLAMA_CHAT_MODEL = os.getenv("OLLAMA_CHAT_MODEL", "qwen2.5:32b-instruct")
OLLAMA_EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text")
QDRANT_HOST = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", "6333"))

print(f"Using models: {OLLAMA_CHAT_MODEL} / {OLLAMA_EMBED_MODEL}")

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

print("\nInitializing Memory...")
memory = Memory.from_config(MEM0_CONFIG)
print("✓ Memory created\n")

# Test with fact-rich content
print("="*60)
print("TEST: Adding fact-rich memory")
print("="*60)

messages = [
    "My name is John Smith",
    "I work at Google as a software engineer",
    "My favorite color is blue",
    "I live in San Francisco",
    "I have a dog named Max"
]

for msg in messages:
    print(f"\nAdding: {msg}")
    result = memory.add(msg, user_id="test_user")
    print(f"Result: {result}")
    
    # Check if anything was extracted
    if result and 'results' in result and len(result['results']) > 0:
        print(f"  ✓ Extracted {len(result['results'])} memories!")
        for mem in result['results']:
            print(f"    - {mem.get('memory', 'N/A')}")
    else:
        print(f"  ✗ No memories extracted")

# Now search
print("\n" + "="*60)
print("Searching for memories about the user")
print("="*60)
result = memory.search("Tell me about the user", user_id="test_user", limit=10)
print(f"\nSearch results: {result}")

# Get all
print("\n" + "="*60)
print("Getting all memories")
print("="*60)
all_mems = memory.get_all(user_id="test_user")
print(f"\nAll memories: {all_mems}")

