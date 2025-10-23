# Mem0 Integration Guide

## Overview

This project now includes **Mem0**, an advanced memory layer for LLM applications that enables persistent, context-aware conversations. Mem0 complements the existing RAG (Retrieval-Augmented Generation) system by adding conversational memory and learning capabilities.

## What Mem0 Does

- **Persistent Memory**: Remembers user preferences, conversation context, and key information across sessions
- **Intelligent Retrieval**: Automatically retrieves relevant memories based on the current question
- **Auto-Learning**: Extracts and stores important information from conversations automatically
- **Hybrid Storage**: Uses vector databases, graph databases, and key-value stores for efficient memory management

## Architecture

### How It Works with Your Existing System

1. **Your RAG System**: Retrieves factual information from your personal knowledge base (contacts, events, places)
2. **Mem0 Layer**: Provides conversational context and remembers what you've discussed before

```
User Question → Mem0 (retrieve memories) → LLM (with memories + RAG context) → Answer → Mem0 (store new memories)
```

## API Changes

### Updated `/ask` Endpoint

The `/ask` endpoint now supports additional optional parameters:

**Request Body:**
```json
{
  "question": "What did we discuss about my trip to Paris?",
  "limit": 3,
  "user_id": "user_123",           // Optional, defaults to "default_user"
  "session_id": "session_abc"      // Optional, for tracking conversations
}
```

**Response:**
```json
{
  "question": "What did we discuss about my trip to Paris?",
  "answer": "Based on our previous conversations...",
  "resolution": {...},
  "search_results": [...],
  "detailed_events": [...],
  "session_id": "session_abc",
  "memories_used": [
    "User asked about Paris trip on 2024-01-15",
    "User prefers detailed historical context"
  ]
}
```

## Configuration

Mem0 is configured in `llm.py` to use your existing Ollama setup and Qdrant for persistent storage:

```python
MEM0_CONFIG = {
    "llm": {
        "provider": "ollama",
        "config": {
            "model": "qwen2.5:32b-instruct",  // Uses your configured model
            "ollama_base_url": "http://localhost:11434"
        }
    },
    "vector_store": {
        "provider": "qdrant",
        "config": {
            "host": "qdrant",  // Qdrant service in docker-compose
            "port": 6333,
            "collection_name": "mem0_memories"
        }
    },
    "version": "v1.1"
}
```

### Persistent Storage

Memories are stored in **Qdrant**, a high-performance vector database that persists to disk:
- **Container**: `mem-qdrant`
- **Port**: 6333 (API), 6334 (gRPC)
- **Volume**: `qdrant-data` (persists across restarts)
- **UI**: Access Qdrant dashboard at `http://localhost:6333/dashboard`

## Installation

1. Install the new dependency:
   ```bash
   pip install -r requirements.txt
   ```

2. Mem0 will automatically initialize on first API call

## Usage Examples

### Example 1: Simple Question (No Session)
```bash
curl -X POST http://localhost:8000/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "Who is my closest friend?"}'
```

### Example 2: Conversation with Session Tracking
```bash
# First question
curl -X POST http://localhost:8000/ask \
  -H "Content-Type: application/json" \
  -d '{
    "question": "Tell me about my meetings last week",
    "user_id": "john_doe",
    "session_id": "conv_001"
  }'

# Follow-up question (will have context from previous)
curl -X POST http://localhost:8000/ask \
  -H "Content-Type: application/json" \
  -d '{
    "question": "Which of those meetings involved Sarah?",
    "user_id": "john_doe",
    "session_id": "conv_001"
  }'
```

### Example 3: Multi-User Support
```bash
# User 1's conversation
curl -X POST http://localhost:8000/ask \
  -H "Content-Type: application/json" \
  -d '{
    "question": "I prefer technical explanations",
    "user_id": "tech_user"
  }'

# User 2's conversation (separate memory)
curl -X POST http://localhost:8000/ask \
  -H "Content-Type: application/json" \
  -d '{
    "question": "I prefer simple explanations",
    "user_id": "casual_user"
  }'
```

## Benefits

### Performance Improvements
- **26% higher accuracy** compared to OpenAI's memory system
- **91% lower latency** than full-context approaches
- **90% token cost savings** by only sending relevant memories

### User Experience
- Remembers user preferences across sessions
- Understands context from previous conversations
- Provides personalized responses based on conversation history
- No need to repeat information

## How Memories Are Used

1. **Automatic Extraction**: When you ask a question, the system automatically stores:
   - The question and answer
   - Key entities and facts mentioned
   - User preferences expressed

2. **Smart Retrieval**: For each new question, Mem0:
   - Searches previous conversations
   - Finds the 5 most relevant memories
   - Includes them in the LLM context

3. **Context Enrichment**: The system prompt includes:
   - Your factual data (from RAG)
   - Relevant memories (from Mem0)
   - Current question context

## Debugging

Mem0 operations are logged with the `[mem0]` prefix:

```
[mem0] Memory instance initialized successfully
[mem0] Retrieving memories for user_id=default_user
[mem0] Found 3 relevant memories
[mem0] Added 3 memories to context
[mem0] Stored conversation in memory for user_id=default_user
```

## Troubleshooting

### Mem0 Fails to Initialize
- The system will continue working without Mem0
- Check that Ollama is running: `ollama list`
- Verify the model is available: `ollama run qwen2.5:32b-instruct`

### No Memories Retrieved
- This is normal for first-time conversations
- Memories build up over time as you interact with the system

### Memory Not Persisting
- By default, Mem0 uses local storage
- Memories are stored per `user_id`
- Make sure you're using the same `user_id` across requests

## Advanced Configuration

### Custom Storage Backend
To use a different storage backend (PostgreSQL, Redis, etc.), update `MEM0_CONFIG` in `llm.py`:

```python
MEM0_CONFIG = {
    "llm": {...},
    "vector_store": {
        "provider": "pgvector",
        "config": {
            "host": "localhost",
            "port": 5432,
            "database": "memories"
        }
    }
}
```

### Adjusting Memory Retrieval
To change how many memories are retrieved, modify line 217 in `llm.py`:

```python
memories = memory.search(question, user_id=user_id, limit=5)  # Change 5 to desired number
```

## Future Enhancements

Potential improvements to consider:

1. **Memory Management API**: Endpoints to view, edit, or delete memories
2. **Memory Categories**: Tag memories by type (preferences, facts, conversations)
3. **Cross-User Memories**: Share relevant context across team members
4. **Memory Decay**: Implement forgetting curve for old/irrelevant memories
5. **Memory Visualization**: UI to explore the knowledge graph

## Resources

- [Mem0 Documentation](https://mem0.ai)
- [Mem0 GitHub](https://github.com/mem0ai/mem0)
- [Ollama Documentation](https://ollama.ai)

