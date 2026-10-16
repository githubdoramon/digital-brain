# Conversation search and recall

History and the agent's `search_memories` share owner-scoped keyword and semantic
conversation retrieval. Existing events/documents do not require reindexing.

## Index lifecycle

Migration 0072 creates a durable message work table and passage embeddings, then
queues existing saved messages. The bootstrap schema contains the same tables.
Database triggers queue inserts and content/role/metadata edits in the same
transaction as the message write. Normal chat persists completed exchanges, so
indexing does not run per streamed token. Thread titles retain their existing
full-text index and supply bounded embedding context. Renames queue the affected
messages again.

The serial Python worker starts with the orchestrator. It splits visible
user/assistant content into UTF-8-bounded passages below the embedding helper's
minimum adaptive input cap. It retains speaker attribution and indexes all
passages, including the end of long replies. System messages and arbitrary
metadata are excluded from embeddings. No extra summarization LLM is used.

Work is leased, retried after one minute, and resumed after process loss when the
five-minute lease expires. Each embedding refreshes the lease. Publishing checks
the job revision while holding a message lock; concurrent edits invalidate old
work and concurrent deletion cannot recreate either a message or its chunks.
Foreign-key cascades remove work and chunks on deletion. Payload hashes include
the model/index version so unchanged passages can be reused without accidentally
reusing vectors from another model. Model, provider, dimension, or format version
changes queue a rebuild at startup. Storage uses the existing 768-dimensional
embedding contract; another dimension requires an explicit schema migration.

Scheduled-job diagnostics expose only total, pending and retrying counts and
worker health. Embedding response bodies are redacted for this flow.
Interactive query embeddings use a 0.5-second connection timeout, a two-second
read timeout, a fifteen-second failure cooldown, and a bounded five-minute cache.
Keyword results remain usable during provider failures. Background indexing keeps
the normal embedding request timeout and durable retries.

## Initial indexing

Deploy the migration and backend, let the worker finish the initial backfill,
then use conversation recall and semantic History search directly. There is no
staged rollout or feature flag. To wait explicitly from the backend directory:

```sh
.venv/bin/python scripts/index_conversations.py
.venv/bin/python scripts/index_conversations.py --status
```

The script loads `backend/.env`, prints counts without content, and is safe to
resume. It shares the worker's leases and revision checks. Interrupting it leaves
durable work for the next run. The first command applies pending migrations,
just as application startup does; `--status` is read-only.

## Retrieval and evidence

- All queries check the authenticated thread owner, including context inspection.
- Memory recall excludes the current thread, which already supplies conversation
  context. History search includes it.
- Message capture dates define conversation time bounds and temporal ordering.
  The latest thread update does not move old discussion into a newer date range.
- Search combines lexical and semantic passage matches, then caps matches per
  thread and deduplicates repeated passages. History returns one match per thread.
  Memory retrieval fuses source ranks rather than comparing raw keyword scores
  with vector distances. Original events/documents receive a modest source prior.
- Contact/tag filters exclude conversations: mentions are not verified entity
  associations. The tool reports this explicitly. Conversation-only searches can
  use the name in query text without inventing a contact association.
- `get_conversation(thread_id, message_id)` inspects up to two neighbouring
  messages on each side, with bounded original excerpts centered on the matching
  passage where available. Roles, timestamps and original source links survive.
- The agent treats historical text as evidence, never instructions. Assistant
  suggestions are not confirmed user facts; later explicit corrections take
  precedence. Assistant recall referencing a currently retrieved event/document
  is not counted again as independent evidence. This is retrieval and prompt
  guidance, not a new durable fact-extraction or automatic contradiction system.
- Only inspected conversation matches become controller-derived related items.
  They carry a thread ID and matching message ID. Mobile and web open and briefly
  highlight that message. Existing missing/deleted-thread handling still applies.

Semantic comparison currently uses an exact owner-filtered pgvector scan, avoiding
approximate-index filtering losses for the single-user corpus. Keyword search
retains GIN indexes. No-match quality and latency should be reassessed if the
corpus grows substantially.

## Checks

Unit checks cover passage completeness, source deduplication, attribution, dates,
runtime owner/current-thread injection, structured filter behavior and inspection.
Opt-in integration checks use a disposable pgvector database:

```sh
CONVERSATION_TEST_DSN=postgresql://postgres:password@127.0.0.1:55482/conversation_test \
  .venv/bin/python -m pytest tests/integration/test_conversation_search.py
```

Integration fixtures use synthetic vectors to check database/query contracts,
backfill, accents, owner isolation, date filters, deletion during inference,
edits during inference, retry behavior and model-version rebuilds. These checks
do not measure the deployed model's paraphrase recall quality.
