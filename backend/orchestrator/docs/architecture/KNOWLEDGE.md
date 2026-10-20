# Shared knowledge

Implemented source contract, 2026-10-20. Migration `0079_shared_knowledge.sql`
extends `intelligence_knowledge`; it does not create a competing fact store.
Deployment, live model quality and native visual acceptance are separate checks.

## Ownership and representation

Every knowledge item, entity label, summary, learning receipt and backfill belongs
to its learner. Existing shared contact/place/event/document access is unchanged.
Knowledge references canonical contacts, messaging identities, projects, events,
documents and places. New products, devices, software, games, organizations and
topics have owner-scoped lightweight entities. A fact can link several subjects;
for example an observation can link a game, an owned laptop and its user.
New non-person entity labels must appear literally in cited evidence. People
require supplied confirmed contact IDs or provider identities; names alone do not
merge identities. Distinguish a product model from an individually owned item.

Facts preserve category, direct/inferred origin and inference explanation,
importance, evidence, applicability, status, revisions and manual corrections.
Inferred patterns may coexist with supporting experiences. A single disliked
product does not establish a brand-wide preference. Knowledge never becomes an
instruction. Explicit deterministic aliases use `user_rules.py`, with stable keys
and runtime scopes. This service accesses only hard-rule rows in the historical
`user_facts` table; migrated soft rows remain provenance and cannot be edited through
it. The retired `fact_extraction.py`, `user_facts.py` and soft retrieval/ranking code
have been removed. No LLM extraction or embeddings run on the instruction path.

## Extraction and asynchronous execution

Brain user messages capture private `chat` sources and durable learning jobs in
the exchange transaction. Assistant text is never captured as evidence. Chat
routing no longer decides whether a personal-fact-only extractor runs. The chat
callback parses only explicitly stated deterministic aliases.

`knowledge_jobs` processes live chat before historical work. It uses the same
`intelligence_model.analyze` and controller `_save_knowledge` as scheduled analysis.
Per-part receipts, leases, checked source revisions, transactional effects and
bounded retries survive restarts. Neighbor retrieval is bounded. All model calls
use `llm_helpers` and sensitive payload handling; logs contain counts/static codes.

External messages/events/documents retain their 05:00/19:00 Europe/Lisbon scheduled
analysis and existing task/project proposal and completion contracts. Contact notes
are evidence, not an append target for learned facts. Event enrichment sends source
evidence to shared learning. Previously
pending profession and relationship proposals remain reviewable; accepting a
profession writes knowledge. New event/contact enrichment queues the shared learner
instead of independently inferring a second set of relationship proposals. Confirmed
contact commands preserve the original user text and confirmed contact references
as private evidence, never a generated proposal or summary as evidence.

### Structured contact changes

Migration `0080_learned_contact_changes.sql` adds the shared `contact_change`
extraction output: named people, birthday updates and reciprocal relationships.
Chat, contact-note, scheduled external-source and historical learning all use
`contact_learning.py`. A connected change commits contacts, relationships, linked
knowledge and its durable decision receipt in one transaction. Contacts retain
their existing installation-wide access; knowledge and decision receipts are private
to the learner. Learned details never append to manual Notes.

High-confidence current changes apply automatically when controller identity checks
succeed and existing birthdays/relationships do not conflict. Supported inferences
retain their inferred label and explanation. Full names, confirmed source identities,
and an existing parent/child relationship can establish identity; a shared first name
alone cannot choose between people. Possible duplicates, uncertain evidence and
conflicts become contact changes in Proposed items, with source inspection and
explicit existing-person/new-contact choices where needed. Review acceptance checks
the source revision and selected contact snapshots again. The controller writes
through the existing contact relationship helper; the extractor receives no general
write or execution tool. Explicit `/contact` command confirmation remains unchanged.

Recurring birthdays preserve unknown years: the API accepts `--MM-DD` alongside
full `YYYY-MM-DD` dates. Partial birthdays occupy dedicated month/day columns and
appear in normal birthday reminders. A partial observation cannot erase an already
known matching birth year. Birthday-party dates do not establish birth dates.
Applying a graph change writes evidence-linked birthday and relationship knowledge
so summaries and retrieval see the same information as the structured contacts.
Contact changes also wake deferred learning for their supporting sources. A worker
that creates a contact retries its own deferred facts once with fresh contact context;
an unchanged replay does not repeat that wakeup. Further facts about that person can
therefore attach without waiting for a new user message.

Ambiguous person facts are omitted, while clear facts from the same source may
commit. Chat asks for clarification. Deferred source parts are retried when the
same conversation receives new context or an identity is linked. No unresolved
knowledge item is created. Original assertions and later clarifications retain
their own source dates and exact evidence links.

## Summaries, retrieval and deletion

Summaries derive from active subject-linked knowledge. Changes invalidate summaries;
rapid changes coalesce. Publish compares the captured version against the current
version, preventing stale in-flight output after corrections/deletion/relinking.
Generated summaries are never sources for extraction. Known validity expiry moves
current facts to historical and schedules a refreshed summary.

Shared retrieval combines lexical relevance, linked entity labels and cosine
similarity over asynchronously indexed embeddings. Missing embeddings leave lexical
search available. Chat, briefing context, contact role/company discovery, About me
and Knowledge use the same owner-scoped records. About me filters out individual
experiences/observations/compatibility/decisions; its instructions remain separately
manageable. Entity/person pages expose sources, inferred status, correction and deletion.

Deleting knowledge retains a private revision/deletion boundary. The same old
supporting evidence cannot recreate the canonical claim. Genuinely newer evidence
can relearn it; historical imports do not acquire new event dates. Corrections
remain manually protected. Semantic reconciliation is model-assisted, not a proof
that arbitrary paraphrases will always deduplicate perfectly.

## Migration and historical revisit

The SQL migration imports current soft user facts with stable IDs, exact text,
category, importance, timestamps and source-thread links. The source is explicitly
a legacy record, not an invented original message quote. Hard rules are preserved.
Existing archive knowledge/evidence/revisions and proposal decisions remain intact.
Contact note text remains unchanged; derived summaries are stored separately.

On first worker activation for an authorized owner, a versioned seeding transaction
queues all retained eligible Brain chats, external messages, events, documents,
notes and imported legacy facts for a knowledge-only revisit. Existing source
exclusions/deletions are checked again before committing. Source/part uniqueness and bounded 500-part enqueue batches
make seeding repeatable without a commit-order-sensitive ID cursor; live chat jobs precede historical parts. No historical
tasks, actions or automatic task completion are produced by this pass. Original
scheduled processing receipts are not reset.

Migration 0080 requeues previously completed shared-learning parts once so retained
evidence also receives contact structuring. Here knowledge-only includes contact
details and relationships; it continues to exclude historical task/project creation,
task changes and completion. Existing automatic/review decision receipts suppress
replayed graph changes, including dismissed unchanged suggestions.

Legacy facts may acquire proper subject links during revisit. Unknown original
applicability/provenance remains unknown. Old overwritten/deleted user-fact versions
were not retained and cannot be reconstructed reliably; backfill can rediscover a
previously deleted statement from retained history.

`GET /mobile/knowledge/progress` reports durable part states. Failed parts retry up
to three times; `POST /mobile/knowledge/retry` resets that owner's failed-part budget.
Deferred identity work is distinct from failures. Summary failures retry separately.
The existing authenticated frontend `/api/mobile/*` proxy carries every client call.

Knowledge has one API: `/mobile/knowledge` (listing, correction, deletion and subject
summaries). Explicit instructions use `GET /mobile/rules` and
`DELETE /mobile/rules/{rule_id}`, with a rule-specific response schema. The app's
instruction card uses this contract. The old `/user/facts`, `/mobile/user/facts`
and duplicate `/mobile/intelligence/knowledge` endpoints are removed; there is no
compatibility adapter. Web had no callers of the old fact API. Both clients continue
to use the authenticated frontend proxy. Existing persisted instruction IDs/values
are preserved without rewriting historical evidence.

Both conversational profiles import `KNOWLEDGE_LEARNING_SYSTEM_PROMPT` from
`prompts/system.py`. Shared user context and evidence formatting live in
`prompts/context.py`; the profiles keep their own base prompts and tool protocols.

## Rollout and verification

Before deployment, run backend regressions with disposable PostgreSQL, including
`tests/test_knowledge.py` and `tests/test_intelligence.py`; run mobile TypeScript and
focused ESLint/format checks. Inspect migration preservation and source coverage
before claiming production success. Normal orchestrator startup applies the SQL
migration, starts learning and activates the resumable historical revisit.

Keep an encrypted database backup before deployment. For rollback, stop the new
worker and restore the previous application plus its compatible database snapshot;
do not drop new tables or erase captured source data as a rollback shortcut.
Review anonymized, manually labeled examples with the configured live model before
claiming extraction accuracy. Deterministic fixture tests establish controller,
storage, migration and queue behavior, not real-model quality or device acceptance.
