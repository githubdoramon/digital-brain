# Scheduled memory analysis

Shared storage, Brain chat learning, summaries, deletion and the versioned all-source
revisit are described in [KNOWLEDGE.md](KNOWLEDGE.md). Scheduled task/project effects
retain the contracts below.

The orchestrator's `intelligence_jobs.py` worker runs analysis at **05:00 and
19:00 Europe/Lisbon**, following local daylight-saving changes. It is independent
of briefing generation. There is no silence timer or per-message inference worker.
Results are quiet: knowledge updates, Proposed items, and evidenced task completion.
The shared extractor also emits connected contact creation, birthday and relationship
changes. Current high-confidence changes with resolved identities and no conflicts
apply automatically; uncertain changes go to Proposed items. The historical
knowledge-only lane includes these personal details, while preserving the prohibition
on historical task/project actions. See the structured contact changes contract in
[KNOWLEDGE.md](KNOWLEDGE.md).

## Activation and evidence

Migration `0076_scheduled_intelligence.sql` adds transactional source capture for
messages, event titles/summaries and document text. Snapshots are private database
evidence, not logs. Existing contacts, tasks, projects, knowledge, and related
events/documents supply bounded context. Two days of neighboring messages supply
conversation context. Attachment files remain in their original apps.

On the first worker tick each authorized owner gets a durable activation boundary.
Existing messages/events/documents are seeded once and a knowledge-only backlog
run is queued. That pass starts automatically and can continue across restarts.
Messages whose original sent time precedes activation remain knowledge-only even
when imported later. No task proposals, task edits or completions can come from
that historical evidence alone. New observations of historical events/documents
are likewise knowledge-only. Messages first observed as mutations without an
original also remain knowledge-only. The shared-domain access model for events,
documents and tasks matches their existing authenticated APIs. Private messaging
snapshots, knowledge, projects, proposals and processing
receipts retain owner scope.

Broadcast channels are excluded from analysis. WhatsApp newsletters/status feeds
are already outside capture. Where provider metadata cannot distinguish an
announcement-only group, mark it in web System → Messengers → Archive using the
group's announcement checkbox. Direct conversations are always included. Existing
derived knowledge is retained when a group is subsequently excluded.

## Runs, batching and recovery

- Slot uniqueness prevents duplicate morning/evening runs. On recovery, the latest
  due slot processes all outstanding observations, rather than requiring a replay
  of every missed calendar slot.
- Per-owner, reclaimable fifteen-minute leases serialize backlog and scheduled
  runs across processes. Each successful batch and a periodic heartbeat renew
  the lease. Inference failure persists only `analysis_failed` and retries after
  five minutes.
- Sources are split into 5,000-character parts with 400 characters of overlap;
  twelve parts form a model batch. Four batches are handled per worker turn, then
  the persisted run continues. Scheduled runs select fresh evidence only, so a
  large backlog cannot hold them open. Late historical imports extend the separate
  knowledge-only queue; backlog work yields to queued scheduled runs.
- A source/part receipt commits in the same transaction as its findings. Failed
  inference or a lost lease advances no receipts. Receipts, rather than a sequence
  high-water mark, also cover transactions that commit out of order.
- Model output is schema-checked (including knowledge status and ASCII predicate)
  and controller-checked. A rejection gets one fresh extraction/reconciliation
  attempt with safe validation feedback. Exact quote matching remains mandatory;
  evidence is never fuzzily matched or invented to pass validation.
- Persistently rejected batches are retained in `intelligence_failures`, without
  successful receipts, so other source parts can proceed. Retry after five minutes;
  after three failed attempts pause those parts. `/mobile/intelligence/status`
  reports retrying/paused part counts even if a run has drained all eligible work.
  Authenticated POST `/mobile/intelligence/retry-failures` resets only that owner's
  failure budget; the worker reopens the matching run, including final reviews.
  Transport/provider errors still retry the run without advancing receipts.
- Scheduled runs finish with a bounded review of the previous two days' evidence
  and related open work, even if that evidence was already processed. Semantic
  reconciliation and controller replay keys suppress duplicate suggestions.
- Retrieval and inference are bounded; this is not a daily full-archive prompt.
  Semantic reconciliation is model-assisted, not a proof of perfect deduplication.

## Controller-owned effects

`intelligence_model.py` extracts structured candidates, retrieves related existing
items again using their descriptions, and reconciles them in a second pass.
Completed tasks and dismissed proposals are included in that retrieval. Source
text is evidence, never a system instruction or a hard behavioral preference.

Every accepted finding needs an exact quote from an owner-accessible input part.
Subjects, task targets, assignees and project references must come from supplied
context. Source content and monitoring eligibility are checked again at commit.

Knowledge is subject-scoped and has a canonical predicate, attribution, status,
applicability, optional exact validity bounds, source evidence, observation time
and revisions. Known expiry is reflected as historical at retrieval. Possible or
historical changes cannot replace settled current knowledge; delayed older
evidence cannot roll it back. Manual corrections retain a revision and block later
automatic overwrites. Identity-scoped knowledge follows confirmed contact links
dynamically, including corrections to those links, without name-based merging.

New tasks/projects and proposed changes remain pending until accepted. Acceptance
rechecks source revisions; task changes also require the unchanged target version.
Accepted task/project creation and proposal resolution are atomic and replay-safe.
Tasks support user/contact/identity/unknown assignees and one optional primary
project. Accepted cancellations use `cancelled`, distinct from completed.

Automatic completion requires all of:

1. An explicit completion candidate and a separate, narrow model verification.
2. Direct, non-forwarded new message evidence by the task's assignee.
3. Evidence after task creation and after any manual edit/reopening.
4. An unchanged, still-pending task when the update commits.

Older observations without forwarding metadata are conservatively ineligible for
automatic completion. A boolean forwarding signal is captured for new Telegram and
WhatsApp observations without retaining forwarding authors or raw protocol data.
Cancellations, reassignment and deadline changes never execute automatically.
Later contradictory evidence may propose reopening a completed task. Clear
completion can also withdraw a still-pending task suggestion, preventing it from
becoming new work after it has already been accomplished.

## Retrieval and UI

- Contact detail APIs include owner-scoped knowledge. Mobile contact pages display
  statements, uncertainty/history, source inspection and manual corrections.
- `lookup_contact` includes linked knowledge; normal agent/briefing context includes
  relevant attributed knowledge separately from user-authored hard rules.
- Existing mobile Proposed items includes task/project/change suggestions with
  reasons, assignees, dates and inspectable evidence, alongside contact suggestions.
- Task details expose assignment, project and analysis evidence.
- `/mobile/knowledge`, `/mobile/rules` and intelligence `/projects`, `/sources/{id}` and `/status` use
  existing authenticated frontend proxy routing. Knowledge corrections use PATCH.
  No message-sending or external-action execution endpoint is introduced.

All LLM calls use the configured smart model through `llm_helpers`, with a static
`intelligence.run` parent span. External telemetry never includes source text,
model output, identities, task descriptions or raw exception strings. At the user's
request, local authenticated system logs now include bounded diagnostic records:
analysis/source IDs, source text, model output, exact validation failures, repair
feedback and committed effects. `LLM_DIAGNOSTICS_ENABLED` defaults to true and can
be disabled; configured credentials remain redacted. Persisted error codes and
telemetry remain bounded. See [LLM_CONTEXT.md](LLM_CONTEXT.md).

Progress logs distinguish `model_stage_completed` (candidate counts and elapsed
time) from `batch_committed` (durable effects after the database commit). Batch
summaries count new processed-part receipts, messages seen, facts created/updated,
older evidence added, protected facts, proposals created, tasks completed,
proposals expired and skipped findings. Duplicate proposals are not creations;
repeated receipts are not newly processed parts. Fact updates include revisions
and evidence changes, not just a changed statement. `repair_requested` records
the static rejection code; a correlated diagnostic record carries the details.

After each worker turn, `run_yielded`, `run_failed` or `run_drained` reports a
snapshot through that run's cutoff: total/processed/remaining source parts and
ready/retrying/paused counts, with separate failed final-review counts.
`run_drained` means no currently eligible work; nonzero remaining or paused counts
still need retry or attention. Counts cover captured snapshots/parts, not unique
messages or a claim of complete messenger history. No owner, run/source IDs,
predicates, fact statements, quotes or model reasoning enter these summaries.
Snapshot failure emits `progress_snapshot_unavailable` and never fails the work.

The independent knowledge progress observer also emits `[learning] progress` at
startup and every minute during long model calls. Its shared API snapshot reports
separate historical/scheduled coverage through the current snapshot time, including
late imports beyond a previous run's cutoff, retry reasons, active runs, elapsed time,
oldest waiting source dates and latest successful receipts. `/mobile/intelligence/status`
adds these two lanes under `progress`; `/mobile/knowledge/progress` includes them
alongside ongoing/historical knowledge learning and summaries. See
[Knowledge progress and activity](KNOWLEDGE.md#progress-and-activity). Local activity
logs include owner and source/run references; source bodies remain in the explicitly
enabled local diagnostics, never telemetry.

Model warmup falls back to the configured `/chat/completions` API with a one-token
request when the Ollama-native endpoint returns 404/405 (for example, a reverse
proxy exposing only `/v1`). Provider/auth failures remain errors. A completion
request does not guarantee Ollama-native keep-alive behavior.

## Verification boundary

Focused tests use disposable PostgreSQL and deterministic model responses. They
exercise real migration, source capture, scheduling, receipts, scope, revisions,
proposal effects and completion guards. They do not establish real-account model
quality, production throughput/cost, deployment or native UI acceptance. The new
worker starts after migrations during normal orchestrator startup; deploying this
source activates the historical knowledge pass and subsequent scheduled runs.

Local verification on 2026-10-19: 376 targeted backend tests passed, including
34 analysis contracts using disposable PostgreSQL and the existing messaging,
fact, memory-tool, controller and daily-briefing regressions. Changed Python files
passed Ruff; mobile/web passed TypeScript, focused ESLint and formatting checks;
the CI workflow YAML parsed successfully and `git diff --check` passed. No
production migration/deployment, live-model archive run, or device/visual UI
acceptance was performed.

Recovery follow-up on 2026-10-19: the full backend suite passed with 1,122 tests
and eight existing integration skips, including regression cases for non-exact
quotes, invalid/null statuses, non-ASCII predicates, bounded repairs, lease-safe
deferral, continued archive progress, paused retries, owner-scoped retry/status,
private LLM logging, and warmup API fallback. The new migration was exercised on
disposable PostgreSQL. Model responses and provider HTTP responses were mocked;
this is not a deployed or live-model verification.
