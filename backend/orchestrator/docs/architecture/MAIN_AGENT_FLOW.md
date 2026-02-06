# Main Agent Flow (Current)

This document describes the **current** runtime behavior of the main bounded agent, including guardrails and special flows.

Primary implementation files:
- `backend/orchestrator/app.py`
- `backend/orchestrator/llm.py`
- `backend/orchestrator/agent/controller.py`
- `backend/orchestrator/agent/router.py`
- `backend/orchestrator/agent/limits.py`
- `backend/orchestrator/tools/validators/pre_execution.py`
- `backend/orchestrator/tools/validators/post_execution.py`
- `backend/orchestrator/retrieval.py`

## 1) End-to-End Request Path

```text
POST /ask or /mobile/ask
  -> app.py
    -> command interception (/event etc.)
    -> session resolution (/new, idle timeout, main session thread)
    -> llm.answer_question(...)
      -> AgentController.run(...)
        -> Intent routing (LLM-first metadata)
        -> Contact scope pre-resolution (once, before loop)
        -> Bounded agent loop (LLM <-> tools)
        -> Finalization + metadata
      -> conversation persistence
```

## 2) Pre-Agent Behavior (App Layer)

Before the agent runs:
- Commands are intercepted (`/event`, etc.) and can bypass normal agent flow.
- `/new` is handled in session resolution (new thread / reset behavior).
- Thread/session context is established.
- Conversation history is loaded and passed to the controller.

Important nuance:
- The user can still send text like `"/new when did I last meet Gio?"`.
- Main session parsing strips `/new` before agent execution; controller also sanitizes slash-prefixes in memory-search normalization as a defensive fallback.

## 3) Controller Phases

## 3.1 Intent Routing (No Tool Narrowing)
- `IntentRouter` classifies the request.
- Allowed tool groups are still produced for state/observability.
- Controller currently exposes the **full tool set** to the model each step.

## 3.2 Contact Scope Priming (New Main Flow)

Before the loop starts, controller tries to resolve people from the top-level question once:
- Runs `resolve_contacts` logic using user email + recent conversation context.
- Stores either:
  - `active_contact_scope_ids` (success), or
  - pending clarification state (ambiguous).

If clarification is needed, controller **returns clarification immediately** and skips the loop.

## 3.3 Message Construction

Controller builds LLM messages with:
- system prompt
- schema/tag/time/self context
- bounded protocol
- skill injection
- state injection (`CURRENT_STATE`)
- conversation history
- current user question

## 3.4 Bounded Loop

For each step:
1. Check stop conditions (`max_steps`, `max_tool_calls`, `max_repairs`, no-progress detection).
2. Call LLM with full tool set.
3. If tool calls:
   - execute tool pipeline (below),
   - if clarification now required, return clarification immediately,
   - check goal completion state and continue as needed.
4. If text response:
   - guard against malformed "tool descriptions" instead of real tool calls,
   - guard against premature completion,
   - finalize answer.

## 4) Tool Execution Pipeline and Guardrails

For each tool call:

```text
parse args
-> lifecycle trace start
-> tool-specific controller guards
-> pre-validation (schema/contracts)
-> handler execution
-> post-validation (coverage/facts/failures/user-input)
-> state updates (facts, tool records, completion evidence)
```

## 4.1 Tool-Specific Guards

### `resolve_contacts`
- Blocks redundant no-progress retries on identical text.
- If the same scope is already resolved in state, returns cached success-like result.
- If same scope already needs clarification, returns cached clarification result.

### `search_memories`
- Controller normalizes args before execution:
  - query normalization and slash-prefix cleanup,
  - temporal sort inference (`newest`/`oldest`),
  - temporal limit floor (`>= 25`),
  - applies active contact scope IDs automatically,
  - blocks when pending contact clarification exists.
- Low-signal person-only query (e.g., `Gio`) with scoped contact IDs is rewritten to the full goal text to avoid weak semantic retrieval.
- Logs finalized args as `Normalized args`.
- Blocks redundant equivalent searches that do not increase retrieval window/signal.

## 4.2 Pre-Execution Validation

Contract-level checks:
- required fields
- unknown fields
- type/range/enum checks
- custom validators for tool-specific safety checks

Invalid calls return structured feedback to the model; repair attempts are bounded.

## 4.3 Post-Execution Validation

Deterministic first, optional LLM fallback for ambiguity:
- `FAILED`: explicit error/failure signals.
- `NEED_USER_INPUT`: ambiguity/clarification required.
- `NEEDS_MORE_TOOLS`: continue tooling.
- Fact extraction added into controller state.

Controller reaction:
- `FAILED`: inject guidance into result (`_validation` block).
- `NEED_USER_INPUT`: persist pending question/fact; main loop returns clarification promptly.

## 5) Goal Completion Guardrails

There are two completion checks:

1. **After tool execution**: track pending actions, continue if incomplete.
2. **Before returning text answer**: blocks premature completion and forces continuation if required.

Temporal query hardening:
- For goals like "when did I last/first...", goal is not considered complete from generic "found results" alone.
- Requires explicit temporal resolution signals (`get_events`) before finalizing.

## 6) Retrieval Layer Behavior (Relevant to Main Flow)

`search_memories` retrieval combines:
- vector + BM25 + structured filters.

Current behavior:
- If structured filters exist (people/place/time), they act as hard constraints for event candidates.
- Supports partial time filters:
  - only `time_start`: `start_date >= time_start`
  - only `time_end`: `start_date <= time_end`
  - both: `BETWEEN`
- Supports ordering modes: `relevance`, `newest`, `oldest`.

## 7) Limits and Stop Rules

Hard limits:
- `max_steps` (default 15)
- `max_tool_calls` (default 20)
- `max_repairs` (default 2)

No-progress limits:
- repeated identical calls
- empty-result streaks

On violation:
- run stops gracefully,
- returns partial answer + context.

## 8) Streaming Flow Differences

`run_stream` mirrors the same logic and guardrails, with SSE event output:
- `token`, `tool_call`, `tool_result`, `status`, `done`

Clarification handling is also immediate in streaming mode (emits clarification text and finalizes).

## 9) Flow Schematic: Contact + Memory + Temporal Query

```text
User: "when did I last meet Gio?"
  -> intent: memory_search (+resolution tools available)
  -> pre-prime contact scope:
       resolve_contacts(question) -> active_contact_scope_ids=[...]
  -> LLM asks search_memories(query="Gio", ...)
  -> controller normalizes:
       query -> full goal text (low-signal rewrite)
       contact_ids injected from scope
       sort_order="newest"
       limit>=25
  -> retrieval with structured people filter + newest ordering
  -> if model retries equivalent search without new signal -> blocked
  -> model fetches event details for temporal grounding
  -> final answer
```

## 10) Practical Guardrail Summary

- Model cannot directly execute arbitrary behavior; controller mediates everything.
- Tool visibility is currently full-set (no intent narrowing).
- Tool params are schema-validated before execution.
- Ambiguity produces clarification prompts, not silent guessing.
- Temporal "first/last/most recent" questions require explicit temporal grounding.
- Repeated equivalent searches are blocked.
- Hard budgets prevent runaway loops.
- Malformed "I will call tool..." style outputs are corrected in-loop.
