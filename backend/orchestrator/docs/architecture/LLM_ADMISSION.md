# LLM background admission

`llm_admission.py` controls chat-completion traffic from the knowledge learner,
scheduled intelligence and summary worker. Sync requests, sync SSE collection,
async chat streams, warm-up and compaction requests use the shared transport gate.
Chat, briefings and other unclassified callers retain foreground behavior; embedding
requests are outside this gate. No model or reasoning defaults change.

One background workflow per endpoint may hold admission in the orchestrator process.
A workflow retains its slot across extraction, repairs, reconciliation and commit,
so another worker cannot discard already extracted candidates merely by occupying
the next phase's slot. Waiting worker lanes rotate in arrival order. A readiness
check occurs before claiming work; stale waiters expire after 60 seconds, while
live polling refreshes their place. A worker with no eligible work releases its turn.
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
