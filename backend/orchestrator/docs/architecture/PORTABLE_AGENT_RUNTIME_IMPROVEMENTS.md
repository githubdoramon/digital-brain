# Portable Agent Runtime Improvements

This document captures implementation ideas that are portable to Digital Brain
after reviewing another mature agent runtime. These are not direct feature-copy
goals. They are architectural patterns that can improve our bounded-agent
orchestrator, tool execution quality, debugging workflow, and end-user
experience.

The goal is to keep this list implementation-oriented so each item can later be
turned into a scoped engineering task.

## Guiding idea

The most valuable takeaways are runtime patterns, not CLI-specific features.
Digital Brain should borrow designs that strengthen:

- bounded execution,
- predictable tool orchestration,
- long-term memory quality,
- observability,
- and follow-up continuity across sessions and clients.

## Suggested implementation order

1. Richer tool capability contracts
2. Explicit prompt assembly and cache-stable prompt prefixing
3. Prompt ingredient observability
4. Selective long-term memory and user-fact injection
5. Stronger session continuity metadata
6. Interaction-level tracing across LLM and tool phases
7. Deterministic parallel tool execution improvements
8. Deferred tool discovery for future scale

## 1. Richer tool capability contracts

### What to improve

Expand the tool registry so each tool declares more runtime semantics beyond
schema and handler name.

Suggested capabilities:

- `read_only`
- `concurrency_safe`
- `destructive`
- `requires_confirmation`
- `supports_clarification`
- `result_budget_policy`
- `preferred_ui_rendering`
- `can_stream_progress`
- `follow_up_context_keys`

### Technical rationale

Today, some of this behavior exists implicitly across the router, controller,
validators, and handlers. Making it explicit in the registry would let the
controller reason about tool behavior generically instead of relying on
per-tool conditionals and scattered conventions.

That gives us a cleaner foundation for:

- deciding which calls may run in parallel,
- enforcing confirmation requirements consistently,
- applying result compaction rules per tool category,
- deciding whether clarification should be surfaced to the user or retried,
- and deriving UI behavior from a single source of truth.

This would also reduce architectural drift between routing metadata,
validation behavior, and runtime controller logic.

### UX impact

Users get more predictable assistant behavior:

- safer handling of mutating actions,
- fewer accidental over-fetches or noisy tool calls,
- better clarification prompts,
- and more consistent responses across chat, mobile, and slash-command flows.

In practice, the assistant should feel less arbitrary and more trustworthy.

## 2. Deterministic parallel tool execution with explicit context merging

### What to improve

Keep parallel read-only execution, but formalize how context updates produced by
those tool calls are merged back into agent state.

### Technical rationale

We already support parallel read-only batches. The next improvement is to make
the merge strategy explicit and deterministic:

- parallel calls should execute against the same parent context snapshot,
- context mutations should be queued separately from raw results,
- queued mutations should be replayed in a stable order,
- and the merge layer should be idempotent and traceable.

This matters because retrieval tools often enrich context with inspected IDs,
resolution metadata, or follow-up hints. Without a disciplined merge phase,
parallelism can create subtle ordering bugs, inconsistent completion checks, or
state that depends on timing.

This is especially relevant for Digital Brain because the controller reasons
about documents, events, contacts, places, and linked items in the same turn.

### UX impact

Users should see faster answers on retrieval-heavy queries without losing
consistency. The assistant becomes both faster and less likely to produce:

- duplicate follow-up questions,
- inconsistent linked items,
- or answers that cite stale or partially merged evidence.

## 3. A more explicit tool execution lifecycle

### What to improve

Model the full tool execution lifecycle as a first-class pipeline:

1. argument normalization,
2. schema validation,
3. tool-specific validation,
4. policy/permission evaluation,
5. execution,
6. post-execution validation,
7. result shaping/compaction,
8. state update,
9. telemetry emission.

### Technical rationale

Digital Brain already has strong pre/post validation. The improvement is to
make the full lifecycle more explicit in code and more visible in traces and
logs.

Benefits:

- fewer ambiguous failures where the model cannot tell what went wrong,
- easier reasoning about which layer owns an error,
- consistent handling of retriable vs clarification-required vs fatal issues,
- and easier introduction of future policies such as rate limits, approvals, or
  tool-specific retries.

This would be a natural evolution of `tool_executor.py`, validators, and the
controller repair loop.

### UX impact

Users get better recovery behavior. Instead of generic failures, the assistant
can more often:

- repair bad arguments automatically,
- ask a targeted follow-up only when needed,
- and avoid looping on the same failed call.

That translates into fewer frustrating dead ends in chat.

## 4. Central prompt assembly with explicit precedence

### What to improve

Create a single prompt assembly layer that defines the exact ordering and merge
rules for all prompt ingredients.

Suggested precedence groups:

1. hard runtime safety rules,
2. profile/base system prompt,
3. intent-specific behavioral guidance,
4. slash-command or flow-specific instructions,
5. hard user rules from persistent facts,
6. soft user facts,
7. conversation/session context,
8. inspected entity evidence,
9. dynamic operational hints.

### Technical rationale

Digital Brain already composes many prompt sources: route/profile guidance,
user facts, linked-items behavior, command-specific extraction rules, tool
feedback, and context continuity. Right now, understanding the final prompt can
require reading several modules.

Centralizing prompt assembly would give us:

- a single place to document precedence,
- fewer accidental prompt conflicts,
- easier debugging when behavior changes,
- and a cleaner base for prompt-budget management.

It also prepares us for more advanced prompt caching or prefix stabilization.

### UX impact

Users should see more stable assistant behavior across turns and surfaces.
Examples:

- fewer regressions where one feature prompt quietly overrides another,
- better consistency between `/ask`, streaming, and slash-command flows,
- and more faithful personalization when user facts should matter.

## 5. Cache-stable prompt prefixes and prompt budgeting boundaries

### What to improve

Define a stable boundary between cache-friendly prompt content and dynamic,
per-turn content.

### Technical rationale

Even without adopting a vendor-specific prompt cache, this separation is useful.
It forces discipline around what changes every turn versus what should remain
structurally stable.

For Digital Brain, likely stable sections include:

- profile/system behavior,
- tool definitions,
- static safety policies,
- linked-items protocol,
- and durable formatting rules.

Dynamic sections include:

- user question,
- current tool outputs,
- selected user facts,
- inferred continuity metadata,
- and fresh clarification context.

This boundary improves maintainability and makes prompt-size regressions easier
to detect.

### UX impact

Users benefit indirectly through:

- more stable answer quality across long sessions,
- less prompt churn that changes behavior unexpectedly,
- and lower latency/cost if we later add provider-side prompt caching.

## 6. Prompt ingredient observability

### What to improve

Emit structured observability events for prompt construction, especially for
which instruction or context sources were loaded into a turn.

Suggested event payloads:

- active profile,
- selected intent,
- user fact IDs injected,
- hard-rule scopes applied,
- command/preview mode flags,
- continuity metadata carried forward,
- and major prompt sections included/skipped.

### Technical rationale

Prompt-debugging is one of the hardest parts of agent development. Many bugs are
not logic bugs; they are prompt-composition bugs. When the model behaves oddly,
we need to know what context it actually saw.

This is especially important in Digital Brain because behavior may vary based on:

- intent routing confidence,
- pre-resolved contacts,
- user fact scopes,
- slash-command state,
- linked-item derivation,
- and follow-up continuity metadata.

Without prompt ingredient observability, these failures are hard to diagnose.

### UX impact

The user-facing effect is faster bug fixing and fewer mysterious regressions.
Internally, it gives us much better leverage when users report:

- "you forgot my preference",
- "you asked the same clarification twice",
- or "you lost the context from the previous step".

## 7. Selective long-term memory and user-fact injection

### What to improve

Move toward a two-stage long-term context injection flow:

1. deterministic shortlist generation,
2. small selector/ranker pass to choose the few facts or memory snippets that
   truly matter for this turn.

### Technical rationale

Digital Brain already has persistent `user_facts` and strong retrieval systems.
The improvement is not more memory; it is sharper memory selection.

Benefits:

- reduces irrelevant personalization noise,
- protects prompt budget,
- lowers the chance of one stale fact dominating a turn,
- and lets us distinguish hard rules from soft hints more cleanly.

This is especially useful for queries where many facts could match loosely but
only one or two should actually affect the output.

### UX impact

Users should experience personalization that feels more accurate and less
intrusive:

- the assistant remembers what matters,
- avoids overfitting on unrelated prior context,
- and feels less repetitive when recalling long-term preferences.

## 8. Stronger session continuity metadata

### What to improve

Persist more structured per-session and per-turn metadata to support better
follow-up continuity across web, mobile, and resumed conversations.

Candidates include:

- resolved entity selections,
- place continuity state,
- last inspected event/document IDs,
- pending clarification forms,
- slash-command draft state,
- preview/edit confirmation state,
- and linked-item context from the previous answer.

### Technical rationale

Digital Brain already carries some continuity in assistant metadata and session
behavior, but many flows still rely on the model reconstructing prior context
from text alone.

Persisting structured continuity metadata makes follow-ups more deterministic and
reduces the need for the model to infer what "that event", "this place", or
"the previous draft" refers to.

This is particularly important for mobile UX, where users often return to a
session after interruptions and send short deictic follow-ups.

### UX impact

Users get smoother follow-ups with less repetition. The assistant is more likely
to correctly understand short turns such as:

- "use the same place",
- "open that document again",
- "edit the contact draft",
- or "what about the earlier event?"

This directly improves the feeling of continuity and memory.

## 9. Interaction-level tracing across LLM and tool phases

### What to improve

Introduce end-to-end traces for each interaction, with spans for:

- routing,
- prompt assembly,
- pre-resolution,
- LLM turns,
- tool batches,
- validation/repair loops,
- completion checks,
- and final response construction.

### Technical rationale

We already log extensively, but traces would make multi-step failures much
easier to reason about than flat log streams alone.

Tracing is especially valuable when debugging:

- why a route escalated,
- why the controller retried instead of answering,
- why a streamed turn diverged from sync behavior,
- or why a final answer used one evidence branch over another.

It would also improve performance tuning by showing where latency is actually
spent inside the orchestrator.

### UX impact

This mostly improves developer velocity, but the user-visible payoff is real:

- fewer long-tail failures,
- faster fixes for strange agent behavior,
- and better latency tuning for complex retrieval-heavy questions.

## 10. Deferred tool discovery for future tool-surface growth

### What to improve

If the available tool inventory grows significantly, move toward a model where
rare or specialized tools are not always exposed in the first prompt.

Instead, the model first uses a lightweight discovery mechanism to surface the
right specialized tool only when needed.

### Technical rationale

This is not urgent today, but it becomes valuable when tool count, tool schema
size, or provider prompt limits start to create real pressure.

For Digital Brain, candidates for deferred exposure in the future might include:

- niche automation tools,
- admin/debug-only tools,
- specialized ingest helpers,
- or high-schema external integrations.

Deferring them would keep the main prompt lean while preserving extensibility.

### UX impact

If implemented well, users should notice:

- faster and more focused reasoning on common tasks,
- fewer irrelevant tool choices by the model,
- and more reliable behavior as the product grows in capability.

## 11. Schema-described configuration and policy surfaces

### What to improve

Use richer typed schemas and field descriptions for agent-facing and admin-facing
configuration surfaces where behavior matters at runtime.

Examples:

- notification policy settings,
- tool visibility policies,
- slash-command behavior flags,
- briefing generation settings,
- and future plugin/integration definitions.

### Technical rationale

Schema-first configuration helps in three ways:

- stronger validation,
- self-documenting configuration contracts,
- and easier reuse in generated docs, UI forms, and diagnostics.

This aligns well with Digital Brain's architectural preference for explicit
contracts and validated runtime behavior.

### UX impact

Users benefit through fewer configuration-related surprises, and internal tools
benefit from clearer validation errors and easier admin/debug UX.

## Practical note

We should not import CLI-specific ideas that do not match our product shape.
Examples like terminal UX conventions, shell-specific safety heuristics, or git
worktree isolation are only worth copying if our product scope expands in that
direction.

The right approach is to adopt the runtime patterns that strengthen Digital
Brain's bounded-agent architecture, not to mimic another product's surface area.
