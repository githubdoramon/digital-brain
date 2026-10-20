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

`knowledge_references.py` owns contact subject references. A contact ID is opaque:
an ID of `contact:alex` has the existing typed subject reference
`contact:contact:alex`. Stored references and links are preserved. Both model phases
receive explicit `subject_ref` values on contacts/people and `from_ref`/`to_ref` on
relationships; contact-note metadata also supplies `contact_ref`. Models copy these
values verbatim. At controller boundaries an exact supplied opaque contact ID may
resolve to its typed reference when it cannot collide with an already valid canonical
reference. Unknown IDs and names are never guessed. The same normalization applies
before ambiguity checks, knowledge validation and structured contact graph validation.
Task assignees continue to store opaque IDs rather than typed subject references.

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

`knowledge_jobs` prioritizes new chat with bounded historical turns. It uses the same
`intelligence_model.analyze` and controller `_save_knowledge` as scheduled analysis.
Per-part receipts, leases, checked source revisions, transactional effects and
bounded retries survive restarts. Neighbor retrieval is bounded. All model calls
use `llm_helpers`. Detailed local diagnostic logs now include analysis/source IDs,
source text, model results, validation details and targeted repair feedback. They
are enabled by default at the user's request, bounded, credential-redacted and
excluded from telemetry. See [LLM_CONTEXT.md](LLM_CONTEXT.md).

Migration `0081_learning_reliability.sql` requeues previously failed learning and
paused intelligence parts once, without resetting successful receipts or ambiguous
identity deferrals. The authenticated knowledge retry endpoint can also requeue
learning, historical failures and failed summaries for its owner. Historical runs
defer rejected HTTP requests per batch, allowing later sources to proceed; transient
provider outages still back off the run. Repeated batch failures pause after three
attempts and retain their evidence for explicit recovery.

Intelligence uses two bounded phases. Extraction receives primary source text and
compact identity/entity hints; it does not receive archive facts, task lists,
proposal histories or unrelated documents. Reconciliation retrieves matching records
for at most three candidates at a time and receives only their cited excerpts,
retaining original source refs and span indices. It cannot cite omitted excerpts.
Both phases use explicit field projections: embeddings, evidence histories, source
snapshots, raw metadata and manual contact Notes never enter retrieved prompt context.
Controller validation still uses complete original records, including correction,
deletion and task-override protections.

Budget calculation and request measurement are shared with the global LLM preflight
in [LLM_CONTEXT.md](LLM_CONTEXT.md). Knowledge keeps its source-preserving splitting
policy; conversational working summaries are never extraction evidence.

`KNOWLEDGE_CONTEXT_WINDOW_TOKENS` defaults to a combined 65,536-token window.
Generated output and reasoning reserve up to 12,000 tokens, with another 4,096-token
margin (`KNOWLEDGE_CONTEXT_MARGIN_TOKENS`), leaving at most 49,440 estimated input
tokens. A local BPE estimate with 20% headroom includes both messages, instructions,
JSON escaping and the response schema. Vocabulary data is cached at image build
time; no runtime tokenizer network request is needed. Lower input/context limits reported by
the provider override the defaults per model/provider for one hour; a smaller
budget that successfully repairs a rejection is also reused. Optional lower provider
output caps are respected when reserving the window. Logs contain phase, counts,
estimated input, input budget and output reserve, never prompt text.

Primary source text is never truncated to fit. Oversized primary batches split while
retaining nearby clarification as supporting evidence. Chat learning reads the
nearest three earlier/later messages, with at most 1,500 characters per supporting
message; those messages keep their own independent full learning jobs. Supporting
messages can clarify a primary assertion but cannot independently create findings
for that job. A rejection reduces optional context before retrying/splitting; an
individual unsplittable input retains its failure instead of receiving a success
receipt. Migration `0082_intelligence_context_recovery.sql` requeues retained
context-limit failures once, leaving other paused failures and successful receipts
unchanged. No `.env` update is needed for the default 64k deployment.

Raw source payload internals are not duplicated into model metadata. Sources expose an ordered array
of exact text spans, without duplicating the source text. Models can select a span
index and the controller copies its original text into the saved evidence, avoiding
quote transcription errors. Exact quote validation runs before reconciliation,
and rejected evidence gets one repair attempt with detailed field/citation feedback;
the controller never accepts paraphrased or fabricated supporting quotes.

An exact excerpt plus its correct span index is accepted together. Reconciliation
retains resolved subject references and restores uniquely bound new-entity
declarations from extraction when the model omits them; changed or conflicting
bindings still fail validation. Unreferenced entity declarations are neither saved
nor allowed to invalidate an otherwise supported fact. Failed repairs carry the
actual rejected ref/citation, allowed values and relevant source span into one
bounded repair attempt. Persisted error codes remain static.

Migration `0083_learning_validation_recovery.sql` retries only retained
`invalid_subject`/`invalid_evidence` failures. It preserves successful receipts,
active leases, ambiguous-identity deferrals and manual corrections. Chat learning
logs committed effects after the transaction, including protected or stale-source
skips; recovered jobs clear their previous error and lease token.

The shared transport keeps allow-listed rejection categories and bounded numeric
output limits for persistent status and telemetry. Detailed local diagnostics also
show model content, provider rejection bodies and controller values. JSON calls can adapt
once per reported output limit or unsupported schema format, and remember those
constraints for one hour per provider/model. Unknown HTTP 400s are not retried
unchanged. Strict schema normalization preserves local controller validation when
a provider requires the simpler JSON-object response format.

Model read timeouts default to 180 seconds; medium reasoning has a configurable
300-second floor and high/xhigh a 900-second floor, across synchronous and streaming
transports (`LLM_TIMEOUT`, `LLM_REASONING_TIMEOUT`,
`LLM_HIGH_REASONING_TIMEOUT`). Explicit longer timeouts remain longer. Optional
`LLM_MAX_OUTPUT_TOKENS` caps output for known provider limits, including reasoning.
Extraction requests up to `KNOWLEDGE_MAX_OUTPUT_TOKENS` (12,000 by default).
Learning, summary and historical workers use shared two-minute leases, renewed by
an independent thread every 20 seconds, including during long reasoning requests.
Abandoned work becomes eligible again after at most two minutes. Polling
(10 seconds for learning, 30 seconds for intelligence) and already active work
can delay the next claim. Token and
expiry checks reject late writes from an earlier owner. Learning renewals cover
inference through the transaction that commits findings and per-part receipts.
There is no startup reset of other servers' live leases. On the first deployment,
leases left by the older version may still need their original 15-minute expiry;
subsequent claims and renewals use the shorter policy. No environment changes or
schema migration are required.

Historical chat/message learning claims up to 12 eligible parts from the same
owner, source kind and conversation, with a 16,000-character primary-source budget.
Known source timestamps keep each batch within 24 hours of its first message.
Other source kinds and current chat learning retain individual claims. Each batch
counts as one fairness turn. Primary parts retain their complete source chunk;
at most six deduplicated neighboring parts provide supporting context. The shared
extractor still enforces its model input budget and knowledge-only permissions.
Every claimed part has its own receipt, retry budget and deferred identity state.
All claimed leases are checked before applying findings atomically: losing one
lease discards the whole result for safe retry, while extraction failure advances
no receipt. A successful batch can commit clear facts while only ambiguous parts
wait for clarification. Completed, delayed, exhausted and deferred parts are never
consumed as new primary work. Start/completion logs include batch part counts,
reclaimed part counts and bounded source sizes; worker status exposes lease,
heartbeat and batch limits.

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
Summary claims also carry a renewable lease token, preventing concurrent workers
from processing the same subject. Malformed, empty or reasoning-only responses get
one schema-focused retry at low reasoning; failures retain a static reason and a
five-minute delay. Completion logs distinguish published output from stale output
discarded after a concurrent change.
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
make seeding repeatable without a commit-order-sensitive ID cursor; claims follow
the fair turn policy below. No historical
tasks, actions or automatic task completion are produced by this pass. Original
scheduled processing receipts are not reset.

Migration `0085_learning_fairness_note_dedup.sql` excludes consecutive contact-note
snapshots that differ only in capture time. `duplicate_of_source_id` points to the
first snapshot in each unchanged episode. Original snapshots, timestamps, fingerprints,
evidence links, manual corrections, completed receipts and active leases remain intact.
Duplicate work is omitted from seeding, claiming, retries and coverage; it is not
reported as newly processed. An actual A → B → A content change retains three
episodes. Title/identity metadata changes also retain new observations. Future no-op
contact saves compare the semantic note payload before creating a source, including
legacy seeds with an unknown original date. Existing duplicate evidence remains valid.
Only retained reference-format failures are requeued, not ambiguity deferrals or
successful receipts.

Both extractors use `learning_fairness.py`: while ongoing and historical work remain
eligible, history receives a turn after at most two current-work claims. The counters
and last claim/history timestamps persist per owner in the database and update under
the same row lock as job claiming, so restarts and concurrent servers cannot reset
the policy. When only one lane is ready it can use all turns; retry delays and paused
parts remain respected. Knowledge turns claim one source part; intelligence turns
run at most one model batch before yielding their run lease. Owners rotate by last
claim time. This guarantees bounded turns, rather than equal wall-clock duration:
an in-flight model request finishes before another lane can run. Historical action
restrictions, evidence validation, lease tokens and transactional receipts still apply.

Migration 0080 requeues previously completed shared-learning parts once so retained
evidence also receives contact structuring. Here knowledge-only includes contact
details and relationships; it continues to exclude historical task/project creation,
task changes and completion. Existing automatic/review decision receipts suppress
replayed graph changes, including dismissed unchanged suggestions.

Legacy facts may acquire proper subject links during revisit. Unknown original
applicability/provenance remains unknown. Old overwritten/deleted user-fact versions
were not retained and cannot be reconstructed reliably; backfill can rediscover a
previously deleted statement from retained history.

### Progress and activity

`GET /mobile/knowledge/progress` retains its flat durable job-state counts and adds
`details`, produced by the shared read-only `learning_progress.snapshot`. The existing
authenticated frontend `/api/mobile/*` proxy carries every client call. The app's
Knowledge screen refreshes this snapshot every 15 seconds while focused.

The snapshot separates ongoing knowledge learning, the all-source historical revisit,
historical external intelligence, scheduled external intelligence, subject summaries,
and fact indexing. Each extraction path has its own source-part coverage; these are
independent passes and must not be added into a unique-source total. Historical
coverage includes explicitly historical work and sources whose original or ingestion
date precedes activation, even when their durable queue flag prioritizes them as
ongoing. Active items also expose that queue flag; reporting does not change execution
order or action permissions. Source and ingestion dates are reported separately. Coverage is of retained
source snapshots, not a claim that a messaging provider supplied its complete archive.

Learning coverage includes eligible parts not yet queued by the 500-part seeder.
Counts distinguish ready, actively leased, delayed, automatic retry, paused after
exhausted attempts, and waiting for clarification. Expired running leases count as
ready for recovery rather than active. Intelligence reports active runs separately
from source parts, with latest-run cutoff/status, elapsed time and current leases.
Its coverage includes eligible late imports beyond a previous run's cutoff, so a
previously succeeded run cannot hide new backlog. Summary debounce waits are separate
from errors. Indexing reports facts still waiting for embeddings; lexical retrieval
is already available.

`history_caught_up` requires initialized source seeding and no remaining work or active
run in either historical path. Paused, retrying or ambiguous parts prevent that state.
This concerns extraction coverage; summaries and indexing have separate progress.
Migration `0084_learning_progress.sql` adds activity/success timestamps without
inventing completion times for old receipts or requeuing successful work.

A third, independent `knowledge-progress` observer thread logs `[learning] progress`
immediately on startup and every 60 seconds, even while extraction/summarization is
blocked in a long model call. One header records snapshot time, process-local worker
liveness, cutoffs and historical catch-up state; individual lane lines record counts,
active refs, elapsed time, oldest waiting dates, next retry and last successful commit.
The header also reports durable fairness counters and `last_history_claimed_at` for
each extractor, plus `ignored_note_versions` for retained redundant snapshots.
Snapshots use one read-only repeatable-read database transaction. They contain no
source bodies or model output and are not exported as telemetry. Learning, summary
and intelligence start logs identify work before the model request begins. The
observer uses the same shutdown signal as the knowledge workers.

Failed parts retry up to three times; `POST /mobile/knowledge/retry` resets that owner's
failed-part budget and requeues retained intelligence/summary failures. Deferred
identity work waits for new context; retrying does not invent an identity.

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


## Readable knowledge prose

Contact, identity and entity references remain stable structured links. They do not
belong in a learned statement or subject summary. Extraction and reconciliation
share concise prose guidance with summarization: use supplied names, preserve
attribution and uncertainty, keep exact quotations only in evidence, and omit
internal IDs and related-record pointers from prose. Controller normalization
removes ID-only annotations and replaces inline references with supplied labels;
unknown people retain a neutral description without inventing an identity.

Read paths return stored text unchanged. To repair previously learned prose, run
`scripts/repair_knowledge_prose.py` once in the updated orchestrator container:

```bash
docker compose exec -T orchestrator python scripts/repair_knowledge_prose.py --dry-run
docker compose exec -T orchestrator python scripts/repair_knowledge_prose.py --apply
```

The default is a dry run; JSON lines preview statement/reason changes followed by
counts. Both commands accept `--owner reader@example.com` to restrict the repair.
`--batch-size` controls transaction size (default 100, maximum 1,000).
Apply preserves each original fact in `intelligence_knowledge_revisions`, cleans
only derived statement/inference prose, increments its revision and clears changed
statement embeddings. Normal triggers invalidate affected summaries; separately
polluted summaries also invalidate their versions and leases for regeneration by
the existing worker. Identity labels resolve only inside their owner's archive.
Manual edits, deleted facts, evidence quotes and structured links are untouched.
No LLM runs in the script. Batches commit independently; failures roll back the
current batch, and rerunning resumes safely without revising already repaired facts.
Dry-run and apply summary counts can differ if concurrent workers regenerate them.
New facts and summaries retain normalization before persistence, so no recurring
repair or read-time rewriting is needed.

The mobile Knowledge screen retains search, its paginated fact list, and existing
contact-specific navigation. The subject carousel and its entity-list request are
removed; no replacement browsing interface is introduced.
