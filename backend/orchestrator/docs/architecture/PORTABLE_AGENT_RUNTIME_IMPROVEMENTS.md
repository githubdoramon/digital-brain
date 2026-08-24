# Portable Agent Runtime Improvements

Status: design backlog; no implementation is implied by this document.

This document records selected runtime improvements for Digital Brain after a
source-level review of the open-source Codex CLI agent runtime. The goal is not
to reproduce Codex or import coding-agent features. It is to adopt portable
runtime practices that strengthen Digital Brain's bounded-agent architecture.

Digital Brain remains governed by its core rule:

> The model proposes. The controller validates, executes, and decides.

Because this is a personal system, the near-term priorities are runtime
correctness, continuity, debuggability, and maintainability. Broader sandbox and
approval infrastructure is not part of this backlog unless Digital Brain's
consequential tool surface grows enough to justify it.

Research basis: [`openai/codex`](https://github.com/openai/codex), reviewed on
2026-08-23 at commit
[`aec653d`](https://github.com/openai/codex/commit/aec653daa9873bf44517a623fd033722737817a8).

## Relationship to other architecture documents

This is the future-work document for cross-cutting agent-runtime improvements.
The following documents describe the system as it exists today:

- [MAIN_AGENT_FLOW.md](MAIN_AGENT_FLOW.md): current bounded request and streaming
  flows.
- [STATE_MANAGEMENT.md](STATE_MANAGEMENT.md): current request-scoped
  `AgentState` ownership and lifecycle.
- [ADDING_TOOLS.md](ADDING_TOOLS.md): current tool registration workflow.
- [TOOL_GROUPS.md](TOOL_GROUPS.md): current tool-group ownership and visibility.
- [VALIDATION.md](VALIDATION.md): current pre/post validation semantics.
- [AGENT_LIMITS.md](AGENT_LIMITS.md): current budgets, stop rules, and
  no-progress handling.

Those documents are normative for current behavior. This document is a backlog
and should be updated as items are accepted, implemented, rejected, or split
into more detailed designs.

## Selected priorities

| Order | Improvement | Timing | Primary outcome |
|---|---|---|---|
| 1 | Tool contracts as the runtime source of truth | Near term | Remove duplicated tool-policy knowledge and reject invalid registries |
| 2 | One canonical event-producing turn engine | Near term | Eliminate sync/stream drift and emit prompt manifests plus lifecycle telemetry |
| 3 | Durable run journal and deterministic reconstruction | Near term | Recover runs and derive privacy-aware interaction traces across restarts |
| 4 | Model-transcript invariant normalization | Near term | Assemble ordered prompt sections and produce a valid, bounded provider transcript |
| 5 | Conversation context checkpoints and compaction | Deferred | Bound long-session context without losing goals, evidence, or command state |
| 6 | Pending-input steering and cancellation | Deferred | Let users safely redirect or cancel an active run |

The ordering is architectural rather than purely user-visible. Later items may
be prototyped earlier, but they should not introduce a second turn model,
history representation, or tool-policy source.

## 1. Tool contracts as the runtime source of truth

### Objective

Make each registered tool contract the single authoritative description of its
model-facing schema and controller-facing runtime behavior.

### Current situation

Tool schemas and groups live in the registry, while some execution semantics
are maintained elsewhere. Examples include static parallel-safe and dedupe-safe
tool-name collections in the executor. This creates a drift risk when a tool is
added or its behavior changes.

The registry and contract builders should also fail closed on duplicate tool or
parameter names rather than allowing dictionary assignment to hide a collision.

### Proposed contract metadata

The exact field names should be settled during implementation, but the contract
needs to express at least:

- whether the tool is read-only,
- whether independent calls may execute concurrently,
- whether equivalent calls may reuse a prior result,
- whether retries are safe,
- whether the operation is idempotent,
- the side-effect class,
- the result-budget/compaction policy,
- the timeout policy,
- and whether clarification or explicit confirmation may be required.

Use enums for multi-valued policy fields. Avoid a growing collection of loosely
related booleans when one closed policy type is clearer.

### Runtime shape

The registry should bind three things together:

1. the model-visible specification,
2. the executable handler,
3. the controller-visible runtime policy.

The executor, parallel batch coordinator, dedupe layer, validator, prompt tool
selection, and diagnostics must read these properties from the same resolved
contract. They must not maintain separate lists keyed by tool name.

### Startup and CI validation

Add a registry-wide validation pass that rejects:

- duplicate tool names,
- duplicate parameter names,
- missing handlers,
- handlers with incompatible shared-context signatures,
- invalid group references,
- contradictory policies such as mutating plus dedupe-safe without an explicit
  idempotency contract,
- and tools marked parallel-safe when their handler or policy forbids it.

The validation should run during application startup and in a focused test so a
bad registry cannot reach production.

### Acceptance criteria

- All current tool execution classifications are expressed in contracts.
- The executor has no static parallel-safe or dedupe-safe tool-name list.
- Duplicate registration and duplicate parameters fail with actionable errors.
- Tool visibility continues to derive from the registry's group ownership.
- Existing pre/post validation and canonicalization semantics are unchanged.
- Contract tests cover every production tool.
- `ADDING_TOOLS.md`, `TOOL_GROUPS.md`, and `VALIDATION.md` are updated with the
  final contract fields.

## 2. One canonical event-producing turn engine

### Objective

Run synchronous and streaming requests through one bounded state machine and
represent progress as typed lifecycle events.

### Current situation

The sync and stream paths are expected to preserve behavioral and observability
parity, but they still own duplicated orchestration logic. Every new repair,
limit, visibility, validation, or completion rule therefore carries a parity
risk.

### Proposed design

Extract a single asynchronous turn engine that owns:

- routing/profile inputs already selected for the run,
- request-scoped state,
- limit and no-progress decisions,
- model calls,
- tool batches,
- clarification exits,
- repair turns,
- completion checks,
- and final verification.

The engine emits typed internal events. A provisional event vocabulary is:

- `run_started`
- `step_started`
- `model_request_started`
- `model_output_delta`
- `model_response_completed`
- `tool_proposed`
- `tool_started`
- `tool_completed`
- `validation_completed`
- `state_updated`
- `clarification_required`
- `final_candidate`
- `final_verified`
- `run_completed`
- `run_aborted`

These are internal runtime events, not automatically the public SSE protocol.
The existing HTTP response and SSE shapes should be adapters over this stream.
Public protocol changes require their own compatibility decision.

### Request-scoped context

Each run should capture an immutable context snapshot containing the exact:

- profile and routing decision,
- visible tool contracts,
- model-routing policy inputs,
- limits,
- user and thread identity,
- client context,
- prompt-policy/config versions,
- and request correlation identifiers.

Mutable progress remains controller-owned in `AgentState` or its successor. A
step must not accidentally mix configuration from different snapshots.

### Event ownership

Events must be emitted by the layer that owns the transition. Observability,
SSE, persistence, and tests consume events; they do not infer lifecycle state
by parsing log text.

Events should carry references or bounded previews by default. They must not
duplicate full personal prompts, memories, or tool results into operational
telemetry.

### Prompt manifest

Prompt-ingredient observability belongs in this engine rather than in scattered
message-builder logs. Before each model request, emit a typed manifest that
records:

- profile and prompt-policy version,
- ordered prompt-section kinds,
- visible tool names and contract versions,
- injected hard-rule and soft-fact IDs,
- activated skill names and versions,
- included evidence/entity references,
- included history/checkpoint range,
- per-section character or token estimates,
- compaction/omission decisions,
- and a stable request correlation ID.

The manifest is metadata about what the model saw, not a copy of the prompt.
Use source IDs, hashes, sizes, and bounded redacted previews. Full personal
content should remain in its authoritative store unless a separate, explicit
debug policy permits capture.

### Lifecycle tracing projection

Interaction-level tracing should be derived from the same lifecycle events. It
must not become a parallel hand-authored trace model. Routing, prompt assembly,
LLM calls, tool batches, validation, repair, completion, and final verification
should appear as related spans or equivalent structured timing records under
one run ID.

Logs, metrics, development traces, SSE adapters, and the durable journal may
project different views of the event stream, but they must agree on event IDs,
ordering, status, and duration ownership.

### Acceptance criteria

- Sync and streaming endpoints call the same turn engine.
- Existing user-visible response semantics remain compatible.
- Limit, repair, clarification, tool visibility, and verifier behavior have one
  implementation path.
- Sync and stream lifecycle logging is structurally equivalent.
- Every model request emits a privacy-aware prompt manifest with ordered section
  provenance and budget measurements.
- Interaction traces are projections of lifecycle events, not separately
  inferred log sequences.
- Client disconnect handling is explicit and does not silently cancel a run
  intended to continue in the background.
- Deterministic tests can drive the engine without HTTP or a real model.
- `MAIN_AGENT_FLOW.md`, `STATE_MANAGEMENT.md`, and observability documentation
  reflect the final lifecycle.

## 3. Durable run journal and deterministic reconstruction

### Objective

Persist enough normalized lifecycle information to inspect and reconstruct an
agent run after a client disconnect, worker restart, or process failure.

### Dependency

The canonical turn-event model should be defined first. Persistence must record
that model rather than create a second, database-specific interpretation of a
run.

### Proposed storage model

Use PostgreSQL rather than a local rollout file. The eventual schema may use a
run table plus append-only event rows, or an equivalent design with the same
properties:

- stable run, step, and event IDs,
- monotonic event ordering within a run,
- explicit terminal states,
- immutable recorded events,
- schema/version markers,
- idempotent append behavior,
- and queryable timestamps and event kinds.

A minimal run should distinguish:

- created/running,
- waiting for user input,
- completed,
- aborted,
- failed,
- and superseded, if supersession is introduced.

### Reconstruction contract

Given a run ID, reconstruction should deterministically recover:

- the latest valid run status,
- the request/configuration snapshot,
- step and tool-call order,
- validation and repair outcomes,
- inspected evidence references,
- pending clarification state,
- final response metadata when completed,
- and the latest recoverable model-context checkpoint once checkpoints exist.

Reconstruction must not rerun tools or infer missing mutations from prose.
Interrupted operations should remain explicitly incomplete until a policy marks
them retryable, failed, or reconciled.

### Privacy and retention

This is a personal-memory system, so replayability must not become uncontrolled
data duplication.

- Store entity IDs and typed references where raw payloads are unnecessary.
- Redact or omit secrets, credentials, access tokens, coordinates, and sensitive
  connector payloads.
- Keep full prompts/results only when required for a defined recovery or debug
  capability.
- Apply an explicit retention policy to high-volume event payloads.
- Keep application conversation history and operational run telemetry as
  distinct concepts even when they reference each other.

The journal should persist enough lifecycle and prompt-manifest metadata to
derive an end-to-end interaction trace after restart. It should not persist a
second copy of every trace payload when the same view can be reconstructed from
canonical events.

### Recovery behavior

The first implementation can be diagnostic-only: runs survive restart and can
be read back, but interrupted execution is not automatically resumed. Automatic
resume should be added only after tool idempotency and mutation reconciliation
are trustworthy.

### Acceptance criteria

- Active run status is no longer exclusively process-local.
- A completed run can be reconstructed into the same ordered lifecycle summary
  after restart.
- Pending clarification survives restart without relying on in-memory state.
- Interrupted tool calls are visible and never presented as completed.
- Event append is safe under retries and duplicate delivery.
- Retention and redaction behavior have tests.
- Migration, operational, and recovery documentation is included.

## 4. Model-transcript invariant normalization

### Objective

Build a provider-independent transcript immediately before every LLM request
and guarantee that it is structurally valid, bounded, and internally
consistent.

### Current situation

Digital Brain preserves conversation text and current-turn tool messages, but
does not yet have one explicit transcript-normalization boundary responsible
for repairing interrupted or partially persisted tool interactions.

### Required invariants

The normalizer should enforce, as applicable to the selected provider format:

- every tool call has exactly one corresponding result or an explicit
  controller-generated interrupted result,
- every tool result references an existing call,
- call and result ordering is valid,
- call IDs are unique and stable,
- duplicate stream fragments are not replayed,
- unsupported content/media is removed or represented safely,
- validation errors remain visible to the model,
- inspected evidence is preserved according to the existing depth-before-
  breadth budget policy,
- and the final transcript fits the selected model's context budget.

Synthetic interruption results must be clearly controller-authored and stable.
They must never claim that an external mutation succeeded.

### Separation of concerns

Keep three representations distinct:

1. durable conversation history intended for the user,
2. durable run events intended for reconstruction and diagnostics,
3. the normalized model transcript for the next provider request.

The transcript is derived. It should not become a second uncontrolled source of
truth.

### Prompt-section assembly

Central prompt assembly belongs at this normalization boundary. Profiles retain
ownership of their content; the shared assembler owns ordering, provenance,
budgeting, and provider adaptation.

Use typed section kinds with explicit precedence, provisionally:

1. runtime safety,
2. profile behavior,
3. bounded-agent protocol,
4. deterministic hard user rules,
5. relevant soft user facts,
6. time/location/client context,
7. active skills,
8. controller-owned agent state,
9. inspected evidence and active entity scope,
10. conversation history or checkpoint,
11. current user input.

This must not become one universal prompt that erases profile boundaries.
Message builders should produce typed sections; the assembler should apply the
shared precedence and budget contract. The resulting section manifest is
emitted by the turn engine before provider conversion.

Stable section ordering should be preserved where practical so provider prompt
caching can benefit, but cache optimization is measurement-driven. Record input
tokens, cached tokens when the provider exposes them, prefix changes, and
latency before introducing cache-specific complexity.

### Provider adapters

Normalize into one internal item model first, then adapt to OpenAI-compatible
chat messages or other provider formats. Provider-specific limitations should
not leak into controller state or conversation persistence.

### Acceptance criteria

- Every model request passes through the normalizer.
- Main and memory-expert builders emit typed prompt sections while retaining
  profile ownership of their content.
- Section precedence, provenance, and budget decisions are deterministic and
  represented in the prompt manifest.
- Missing-call, missing-result, orphan-result, duplicate-ID, and interrupted-
  stream cases have regression tests.
- Raw validation failures remain available for argument repair.
- Synthetic results are deterministic and visibly marked as interrupted.
- A transcript invariant failure is observable and fails closed rather than
  producing a malformed provider request.
- Normalization does not alter normal completed conversations.

## 5. Deferred: conversation context checkpoints and compaction

### Objective

Bound long-session model context while preserving the structured state needed
for reliable follow-ups.

### Why it is deferred

Current tool-result budgeting addresses the more immediate large-result risk.
Conversation-level compaction becomes more valuable after the run journal and
normalized transcript exist, because those provide an immutable source and a
safe derived-context boundary.

### Proposed checkpoint contents

A checkpoint should preserve structured fields rather than only a prose
summary:

- the current user goal,
- settled constraints and corrections,
- unresolved questions,
- selected contacts/places and other entity IDs,
- inspected evidence and provenance,
- important negative findings and caveats,
- execution-plan progress,
- pending slash-command or clarification state,
- linked-item continuity,
- and a bounded conversational summary.

### Safety properties

- Raw history remains immutable and recoverable.
- Compaction creates a new versioned checkpoint; it does not rewrite history.
- A generation/version check prevents a checkpoint produced from stale history
  from replacing a newer one.
- Recent turns remain available verbatim within a bounded window.
- Evidence depth is preserved before broad candidate context.
- The controller can explain which checkpoint and history range formed a model
  request.

### Triggering and measurement

Compaction should be driven by measured token pressure, not an arbitrary turn
count. Track:

- tokens before and after,
- retained recent turns,
- retained evidence items,
- checkpoint version,
- compaction latency,
- and post-compaction task/evidence completion regressions.

### Acceptance criteria

- Long conversations remain within the configured model budget.
- Goals, hard corrections, pending confirmations, and evidence references
  survive compaction tests.
- Concurrent new messages cannot be overwritten by a stale checkpoint.
- Resume and follow-up behavior is equivalent before and after compaction for
  the scenario suite.

## 6. Deferred: pending-input steering and cancellation

### Objective

Allow a user to add relevant instructions to an active run or cancel it without
starting a conflicting second run.

### Why it is deferred

Steering depends on an explicit run lifecycle, durable run identity, and safe
event boundaries. It should be layered on the canonical engine and journal,
not implemented as a separate in-memory message queue.

### Input classes

The controller should distinguish:

- **steer active run**: relevant additional guidance that can be applied at the
  next safe boundary,
- **cancel active run**: stop future model/tool work and mark the run aborted,
- **answer pending clarification**: resume a run waiting for user input,
- **defer to next turn**: unrelated input that should begin a new run after the
  current run reaches a terminal state.

The model may help classify ambiguous additions, but the controller owns the
queue, run identity, and final transition.

### Safe boundaries

Cancellation and steering may be applied:

- before a model request,
- after a model response is fully assembled,
- before a tool begins,
- after a tool returns,
- or before final verification.

An already-started external mutation cannot be treated as if cancellation
rolled it back. Its actual outcome must be recorded and reconciled before the
run terminates.

### Client behavior

Web and mobile should address additions to an explicit active run ID. Reconnect
must restore queued input and current status from durable state. Duplicate
delivery must be idempotent.

### Acceptance criteria

- A relevant follow-up can steer a long-running retrieval flow at a safe
  boundary.
- Cancellation prevents later model/tool steps and records the exact terminal
  reason.
- Clarification answers resume the intended run after process restart.
- Unrelated messages are deferred without being lost or merged accidentally.
- Mutation, reconnect, duplicate-delivery, and race scenarios have tests.

## Cross-cutting implementation rules

All selected improvements must preserve these constraints:

- The controller remains the only authority that mutates runtime state.
- Tool arguments are canonicalized before policy, dedupe, and execution.
- Validation-required user input remains distinct from empty-result retries.
- Validation/error payloads remain visible enough for model repair.
- Tool-result compaction remains field-aware and depth-preserving.
- Persistent payloads are anonymized in tests and do not leak personal data into
  logs or fixtures.
- Prompt manifests and lifecycle traces use identifiers, hashes, sizes, and
  redacted previews by default rather than copying personal prompt content.
- New statuses, actions, modes, and event kinds use shared enums.
- Runtime behavior changes update the applicable architecture documents and
  `AGENTS.md` in the same work.

## Scenario test matrix

The implementation sequence should build a reusable deterministic model/tool
harness covering at least:

| Scenario | Contracts | Turn engine | Journal | Transcript | Checkpoint | Steering |
|---|---:|---:|---:|---:|---:|---:|
| Invalid tool arguments repaired after visible validation feedback | yes | yes | yes | yes |  |  |
| Independent reads execute concurrently with deterministic merge | yes | yes | yes |  |  |  |
| A serial/non-concurrent tool acts as a batch barrier | yes | yes | yes |  |  |  |
| Equivalent dedupe-safe call reuses prior evidence | yes | yes | yes | yes |  |  |
| Prompt manifest identifies sections, sources, budgets, and omissions without copying personal content |  | yes | yes | yes |  |  |
| Interrupted model stream does not create an orphan tool result |  | yes | yes | yes |  |  |
| Process restart reconstructs completed and interrupted runs |  |  | yes | yes |  |  |
| Pending clarification survives restart |  | yes | yes | yes |  | yes |
| Compaction preserves goal, correction, caveat, and evidence |  | yes | yes | yes | yes |  |
| User steers a retrieval run at a safe boundary |  | yes | yes | yes |  | yes |
| Cancellation during an external mutation records the real outcome | yes | yes | yes | yes |  | yes |

## Disposition of previously recorded topics

The earlier backlog topics are retained with an explicit treatment rather than
becoming separate architecture projects:

| Topic | Treatment |
|---|---|
| Central prompt assembly and precedence | Incorporated into model-transcript normalization as typed prompt-section assembly |
| Prompt-ingredient observability | Incorporated into the canonical turn engine as a privacy-aware prompt manifest |
| Interaction-level tracing | Incorporated as a projection of lifecycle events and the durable journal |
| Cache-stable prompt prefixes | Preserve stable ordering and measure provider cache behavior; optimize only when evidence justifies it |
| Selective long-term memory and user-fact injection | Continue the existing bounded relevance/importance/recency approach; improve through retrieval evaluations rather than adding another LLM selector by default |
| Deferred tool discovery | Keep deferred while confidence-based tool-group visibility and escalation remain adequate |
| Schema-described configuration and policy surfaces | Adopt incrementally for persisted, user-editable, cross-service, or behavior-critical policies; do not build a generic configuration platform |

This disposition is intentional: prompt assembly, observability, and tracing
are supporting capabilities of the selected runtime design, while the other
topics remain measured design principles rather than standalone initiatives.

## Explicitly out of scope for this backlog

- Replacing the bounded controller with autonomous or multi-agent execution.
- Copying Codex's local JSONL rollout format instead of using PostgreSQL.
- Adding conversation compaction before there is a durable source and transcript
  normalization boundary.
- Automatically resuming arbitrary interrupted mutations.
- Exposing internal lifecycle events directly as a new public API without a
  compatibility design.
- Introducing deferred tool discovery while the current bounded visibility
  policy remains adequate.

## Maintenance

When work begins on an item:

1. verify the current code paths and related architecture documents,
2. settle the public and persistence compatibility boundaries,
3. add deterministic scenario coverage before or alongside the refactor,
4. update this document with implementation status and links,
5. update normative current-state documentation when behavior actually changes.

Do not mark an item complete merely because its types or storage schema exist.
Completion requires the intended runtime path, recovery behavior, observability,
and regression tests to be active.
