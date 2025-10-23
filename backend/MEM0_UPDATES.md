# Mem0 Integration - Frontend & Storage Updates

## Overview

This document covers the additional updates made to address:
1. **Frontend Integration** - Adding memory awareness to the UI
2. **Persistent Storage** - Ensuring memories survive restarts

## 1. Frontend Changes

### What Changed

The frontend (`frontend/web/src/app/page.tsx`) now fully supports Mem0 memory features:

#### Session Management
- Each chat session gets a unique `session_id` 
- Generated on component mount: `session_${timestamp}_${random}`
- Enables conversation continuity across multiple questions

#### User Identification
- Sends `user_id: "web_user"` with each request
- Can be made dynamic with authentication in the future
- Allows personalized memories per user

#### Memory Visualization
- **Memory Badge**: Shows "🧠 X memories" on responses that used context
- **Toggle Button**: "Memories On/Off" in chat header
- **Memory Details**: Expandable list showing which memories were used

### Updated Message Type

```typescript
type Message = {
  role: "user" | "assistant";
  content: string;
  timestamp: Date;
  memories?: string[];  // NEW: memories used for this response
};
```

### API Call Changes

**Before:**
```javascript
body: JSON.stringify({
  question: userMessage.content,
  limit: 5,
})
```

**After:**
```javascript
body: JSON.stringify({
  question: userMessage.content,
  limit: 5,
  session_id: sessionId,      // NEW
  user_id: "web_user",         // NEW
})
```

### UI Features

1. **Memory Indicator Badge**
   - Appears next to timestamp when memories are used
   - Shows count of memories
   - Tooltip explains what it means

2. **Memory Toggle Button**
   - Located in chat header
   - Shows/hides memory details for all messages
   - Visual feedback (blue when on, gray when off)

3. **Memory Details Panel**
   - Expands below assistant messages
   - Lists all memories used for that response
   - Light blue background to distinguish from content

### Example User Experience

```
User: "I prefer detailed technical explanations"
Assistant: "I'll remember that preference..."

[Next question]
User: "How does the database work?"
Assistant: "Here's a detailed technical explanation..." 
           🧠 1 memory [shows the preference was used]
```

## 2. Persistent Storage Solution

### The Problem

By default, Mem0 stores memories in local files which are:
- ❌ Lost when container restarts
- ❌ Not shared across multiple instances
- ❌ Difficult to backup/restore

### The Solution: Qdrant

Added **Qdrant** - a production-ready vector database with:
- ✅ Persistent disk storage
- ✅ High-performance vector similarity search
- ✅ Built-in clustering and replication support
- ✅ Web UI for monitoring

### Docker Compose Changes

**Added Qdrant Service:**
```yaml
qdrant:
  image: qdrant/qdrant:latest
  container_name: mem-qdrant
  ports:
    - "6333:6333"  # REST API
    - "6334:6334"  # gRPC
  volumes:
    - qdrant-data:/qdrant/storage  # Persistent storage
  healthcheck:
    test: ["CMD-SHELL", "curl -f http://localhost:6333/health || exit 1"]
```

**Added Volumes:**
```yaml
volumes:
  postgres-data:  # For main database
    driver: local
  qdrant-data:    # For Mem0 memories (NEW)
    driver: local
```

**Updated Orchestrator:**
- Added dependency on Qdrant
- Added environment variables: `QDRANT_HOST` and `QDRANT_PORT`

### Backend Configuration Changes

**Requirements.txt:**
- Added `qdrant-client==1.11.3`

**Mem0 Configuration (`llm.py`):**
```python
MEM0_CONFIG = {
    "llm": {
        "provider": "ollama",
        "config": {
            "model": OLLAMA_CHAT_MODEL,
            "ollama_base_url": OLLAMA_HOST,
        }
    },
    "vector_store": {
        "provider": "qdrant",
        "config": {
            "host": QDRANT_HOST,
            "port": QDRANT_PORT,
            "collection_name": "mem0_memories",
        }
    },
    "version": "v1.1"
}
```

### Storage Architecture

```
┌─────────────────────────────────────────────┐
│           Digital Brain System              │
├─────────────────────────────────────────────┤
│                                             │
│  ┌──────────────┐      ┌─────────────────┐│
│  │ PostgreSQL   │      │ Qdrant          ││
│  │ (pgvector)   │      │ Vector DB       ││
│  ├──────────────┤      ├─────────────────┤│
│  │ • Contacts   │      │ • Conversations ││
│  │ • Events     │      │ • Preferences   ││
│  │ • Places     │      │ • User memories ││
│  │ • Embeddings │      │ • Semantic      ││
│  │              │      │   search        ││
│  └──────────────┘      └─────────────────┘│
│         ↓                       ↓          │
│  postgres-data            qdrant-data      │
│  (Docker volume)          (Docker volume)  │
└─────────────────────────────────────────────┘
```

### Why Qdrant?

1. **Purpose-Built for Vectors**
   - Optimized for embedding storage and similarity search
   - Better performance than general-purpose databases

2. **Mem0 Native Support**
   - Officially supported by Mem0
   - Well-tested integration

3. **Production Ready**
   - Used by major companies
   - Proven at scale
   - Active development and support

4. **Developer Friendly**
   - Web UI at `http://localhost:6333/dashboard`
   - REST API for debugging
   - Easy to monitor and manage

### Data Persistence

**What Gets Stored:**
- User conversations (Q&A pairs)
- Extracted facts and preferences
- Semantic embeddings for retrieval
- Metadata (timestamps, user_id, session_id)

**Storage Location:**
- Docker volume: `qdrant-data`
- Physical location: `/var/lib/docker/volumes/digital-brain_qdrant-data`

**Backup Strategy:**
```bash
# Backup Qdrant data
docker run --rm -v digital-brain_qdrant-data:/data -v $(pwd):/backup \
  alpine tar czf /backup/qdrant-backup.tar.gz /data

# Restore Qdrant data
docker run --rm -v digital-brain_qdrant-data:/data -v $(pwd):/backup \
  alpine tar xzf /backup/qdrant-backup.tar.gz -C /
```

## 3. Complete Setup

### Initial Setup

```bash
# 1. Stop existing containers
docker compose down

# 2. Pull latest images
docker compose pull

# 3. Start all services (including Qdrant)
docker compose up -d

# 4. Check Qdrant is running
curl http://localhost:6333/health
```

### Verify Installation

```bash
# Check all containers are running
docker compose ps

# Should show:
# - memdb (PostgreSQL)
# - mem-qdrant (Qdrant)
# - mem-orchestrator (API)
# - mem-frontend (Next.js)

# Check Qdrant collections
curl http://localhost:6333/collections

# Access Qdrant UI
open http://localhost:6333/dashboard
```

### Testing Memory Persistence

```bash
# 1. Start a conversation
curl -X POST http://localhost:8000/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "I prefer short answers", "user_id": "test"}'

# 2. Restart containers
docker compose restart orchestrator

# 3. Ask a follow-up (should remember preference)
curl -X POST http://localhost:8000/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "How does Mem0 work?", "user_id": "test"}'

# 4. Check memories_used in response
```

## 4. Monitoring & Management

### Qdrant Dashboard

Access at `http://localhost:6333/dashboard`:
- View collections
- Browse stored vectors
- Monitor performance
- Debug search queries

### Logs

```bash
# View Mem0/Qdrant logs
docker compose logs qdrant

# Follow logs in real-time
docker compose logs -f orchestrator

# Look for these log messages:
# [mem0] Memory instance initialized successfully
# Using Qdrant at qdrant:6333
```

### Data Inspection

```bash
# Get collection info
curl http://localhost:6333/collections/mem0_memories

# Count vectors
curl http://localhost:6333/collections/mem0_memories/points/count

# Search memories (example)
curl -X POST http://localhost:6333/collections/mem0_memories/points/search \
  -H "Content-Type: application/json" \
  -d '{
    "vector": [...],
    "limit": 5
  }'
```

### Maintenance

```bash
# Clear all memories (development only!)
curl -X DELETE http://localhost:6333/collections/mem0_memories

# Restart Qdrant
docker compose restart qdrant

# View Qdrant resource usage
docker stats mem-qdrant
```

## 5. Environment Variables

### New Variables

Add to `backend/.env` if needed:

```bash
# Qdrant Configuration
QDRANT_HOST=qdrant  # Use 'localhost' for local development
QDRANT_PORT=6333

# These are already set in docker-compose.yml
```

### For Local Development (Outside Docker)

If running the orchestrator locally:

```bash
export QDRANT_HOST=localhost
export QDRANT_PORT=6333

# Then start Qdrant in Docker
docker run -p 6333:6333 -p 6334:6334 \
  -v $(pwd)/qdrant_storage:/qdrant/storage \
  qdrant/qdrant
```

## 6. Troubleshooting

### Qdrant Not Starting

```bash
# Check logs
docker compose logs qdrant

# Common issues:
# - Port 6333 already in use
# - Volume permissions
# - Insufficient disk space

# Solution: Stop conflicting services
lsof -i :6333
```

### Memories Not Persisting

```bash
# Verify volume exists
docker volume ls | grep qdrant

# Check volume is mounted
docker inspect mem-qdrant | grep -A 10 Mounts

# Ensure data is being written
docker compose exec qdrant ls -la /qdrant/storage
```

### Frontend Not Showing Memories

1. Check browser console for errors
2. Verify API response includes `memories_used`
3. Check network tab for session_id in request
4. Try toggling the "Memories" button

### Performance Issues

```bash
# Check Qdrant memory usage
docker stats mem-qdrant

# Optimize collection (if needed)
curl -X POST http://localhost:6333/collections/mem0_memories/optimize

# Consider increasing Docker resources in Docker Desktop
```

## 7. Security Considerations

### Production Deployment

For production, consider:

1. **Qdrant Authentication**
   ```yaml
   qdrant:
     environment:
       - QDRANT__SERVICE__API_KEY=your_secret_key
   ```

2. **User Authentication**
   - Replace hardcoded `"web_user"` with real user IDs
   - Implement JWT or session-based auth

3. **Network Security**
   - Don't expose Qdrant port (6333) publicly
   - Use internal Docker networks

4. **Data Encryption**
   - Enable at-rest encryption for volumes
   - Use TLS for Qdrant connections

## 8. Migration Path

### From File Storage to Qdrant

If you were using default file storage:

```bash
# Old memories are in: ~/.mem0/ or /tmp/.mem0/
# These won't automatically migrate to Qdrant

# Options:
# 1. Start fresh (memories will rebuild over time)
# 2. Export from files and import to Qdrant (custom script needed)
```

### Upgrading Mem0

```bash
# Update requirements.txt version
# Rebuild container
docker compose build orchestrator
docker compose up -d orchestrator
```

## Summary

### What You Get Now

✅ **Frontend**
- Session-based conversations
- Visual memory indicators
- User-specific memory isolation
- Toggle to view memory details

✅ **Storage**
- Persistent memory across restarts
- High-performance vector search
- Scalable architecture
- Easy monitoring and management

✅ **Production Ready**
- Docker volumes for data persistence
- Health checks for all services
- Graceful degradation if services fail
- Clear logging and debugging

### Next Steps

1. **Test the full flow**
   ```bash
   cd frontend/web
   npm run dev
   # Visit http://localhost:3000
   # Have a conversation and see memories in action
   ```

2. **Monitor Qdrant**
   ```bash
   open http://localhost:6333/dashboard
   ```

3. **Try the test script**
   ```bash
   cd backend
   python test_mem0_integration.py
   ```

Enjoy your fully persistent, memory-enabled digital brain! 🧠

