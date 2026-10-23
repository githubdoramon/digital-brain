# LLM background admission

`llm_admission.py` controls chat-completion traffic from the knowledge learner,
scheduled intelligence, summary worker and asynchronous LLM job workers. Sync requests, sync SSE collection,
async chat streams, warm-up and compaction requests use the shared transport gate.
Interactive chat/briefing requests and other unclassified callers retain foreground
behavior; scheduled briefings use background admission. Embedding requests are outside
this gate. No model or reasoning defaults change.

One background workflow per endpoint may hold admission in the orchestrator process.
A workflow retains its slot across extraction, repairs, reconciliation and commit,
so another worker cannot discard already extracted candidates merely by occupying
the next phase's slot. Waiting worker lanes rotate in arrival order. A readiness
check occurs before claiming work. Waiters expire after the larger of 60 seconds
or three configured polling intervals. All asynchronous LLM workers pass their
actual polling cadence to admission. Refreshing a waiter extends its deadline
without changing its FIFO position, so a 60-second briefing poll plus overhead
cannot lose its turn to 2-second history/tagging polls. Stopped lanes still expire;
a worker with no eligible work releases its turn immediately.
The durable per-owner knowledge scheduler still guarantees a historical turn after
at most two current turns.

Foreground requests start immediately and keep their transport retries. New background
workflows yield while foreground requests are active, except for one reserved
opportunity after five minutes without a background admission. This bounds starvation
under continuous foreground traffic. An already running model request is not cancelled
or preempted when a chat arrives. Priority means admission control, not a guarantee
about the private server's internal queue or interactive latency.

Background requests make one transport attempt. A timeout, connection failure, HTTP
429 or server error opens an endpoint cooldown: approximately 30/60/120/240/300
seconds, with bounded jitter and a five-minute ceiling. No sleeping worker performs
three consecutive five-minute attempts. After cooldown, one actual queued request
probes recovery; no additional LLM health-check request is generated. A successful
foreground request can also clear cooldown. Requests to another endpoint have an
independent state. Admission does not store/export endpoint URLs in progress data.

A provider or admission deferral releases the job lease, schedules a later claim,
and reverses that claim's source-part attempt increment. Findings and receipts do
not advance. Evidence/schema failures retain the ordinary bounded validation budget.
Migration 0086 re-enables previously exhausted provider-outage failures once.
Summary version/token checks and source revisions continue to guard all writes.
Async stream closure immediately closes the underlying provider iterator and releases
foreground admission; tracing exports only its existing safe metadata.

The gate is process-local and resets on restart; durable job leases and receipts
remain authoritative across processes. The Compose orchestrator runs one process.
Multiple orchestrator replicas would require a distributed admission coordinator to
provide an aggregate one-workflow limit. Stop the old worker before deploying the
new ownership policy; the migration revokes the retired historical run tokens.

`[llm_admission] provider_cooldown` logs failure streak and retry delay. The independent
learning observer includes active foreground/background counts, cooldown, waiting lane
count and refused admissions. Job deferral logs carry a static reason and delay.
There is no prompt, output, owner identity, credential or endpoint export in these
admission summaries. Existing opted-in local learning diagnostics remain separate.

Validation uses disposable PostgreSQL and controlled HTTP/async-stream fixtures.
It establishes receipt migration, no-budget provider deferral, summary coalescing,
queue rotation, reservation and cancellation behavior. It does not measure real-server
capacity, latency improvements or extraction accuracy; compare calls/tokens per
completed source part and foreground latency after deployment.

## All asynchronous language-model workers

`async_jobs.background_worker` puts event/contact/document tagging, document
enhancement, contact enrichment, meeting transcripts, proposed events, daily
briefings and emergency-stock jobs into the same admission coordinator. It checks
readiness before claiming a row. Provider deferrals release the exact job revision,
return it to pending and reverse the claim's attempt increment. A newer job revision
cannot be deferred by an older worker. Deferrals remain visible even when a legacy
fallback catches the exception; they cannot mark the job successful or clear its
input payload. Jobs retain their existing durable retry path for other failures.

Briefing worker-thread submissions already copy contextvars. Each background
workflow now shares a request lock across its child threads, serializing physical
LLM calls while retaining workflow ownership. Interactive requests keep foreground
priority. Non-LLM calendar sync, embeddings, backups and messaging capture are not
classified as language-model workers or blocked by this gate.

Tag suggestions use a durable exact-input cache from migration 0087. The key hashes
the endpoint, model, complete effective tagging prompts and request options. Editing
source text or tags, changing prompts/category rules/model/options invalidates it.
Document tag translations use the same cache. Only successfully parsed tag results
are cached, including valid empty results; failed calls and fallback guesses are
not. The cache stores hashes and tags, never source snapshots or credentials. Tags
still go through the existing merge/persistence path, and a missing cache does not
prevent ordinary tagging.

Knowledge extraction and summaries collect SSE responses. `JSONOutputGuard` cancels
structured streams after 512 consecutive whitespace characters outside JSON strings,
repeated closing tool/function syntax, excessive trailing output, or a one-MiB content
bound. String escapes and source quotations do not count as syntax loops. Cancellation
closes the HTTP response in `finally`; partial output is discarded. Generation
failure (`output_degenerate`) is distinct from endpoint unavailability and does not
open a provider outage cooldown. Stream collection also has an overall request deadline.
Local logs record the cancellation reason and received character count, never those
values in external telemetry. The provider may continue server-side generation after
socket closure; actual cancellation/resource release needs live-server verification.
