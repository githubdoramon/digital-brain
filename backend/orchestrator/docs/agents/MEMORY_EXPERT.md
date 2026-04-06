# Memory Expert Agent

This document captures memory-expert profile behavior around retrieval, contact scoping, and disambiguation.

## Responsibilities

- Answer personal-memory questions using bounded tools.
- Prefer precise retrieval over broad unscoped search.
- Clarify ambiguity instead of guessing the wrong person/entity.

## Contact-Aware Retrieval

When a query is person-referential and memory search lacks `contact_ids`:

1. Resolve contacts first.
2. If resolution is unambiguous, inject resolved `contact_ids` into memory search.
3. If ambiguous, return clarification-needed outcome instead of unfiltered retrieval.
4. Prevent repetitive identical `resolve_contacts` calls after clarification/no-person outcomes.

## Collective Selectors

- Selector phrases can resolve to groups even without explicit names (for example domain/company/team selectors).
- Deterministic selectors can be auto-persisted as confirmed reusable contact groups.
- Non-deterministic selectors require explicit user confirmation (event preview UX path).
- Contact groups are user-scoped by owner self-contact identity.

## Ranking / Discovery Queries

- For strict interaction-ranking windows (for example "who did I meet most this week"), prefer `get_events(action=by_time_span)` and then rank counterparts.
- Router pre-resolution policy should skip pre-resolve for non-person-referential ranking/discovery prompts.

## Current-Status Queries

- For evolving status questions, prefer the newest aligned event evidence over older semantic hits.
- When newer evidence gives a more current reference point than an older relative description, use the newer evidence instead of repeating stale relative phrasing verbatim.

## Domain-Scoped Recaps

- For bounded recap/report questions with explicit windows (for example "summarize my work last week"), prefer structured retrieval first.
- Use domain tags like `Work` as precision hints when the user clearly names a domain, but do not make tags mandatory for broader semantic discovery.
- Treat documents as first-class evidence for outcomes, topics, and decisions; events remain primary for chronology.
- Prefer `summarize_memories` for combined event+document recap synthesis when available.

## Validation Semantics

- Clarification-required resolution/search outcomes are treated as `need_user_input` (not generic empty retries).

## Key Files

- Profile and prompts: `backend/orchestrator/agents/memory_expert/`
- Controller/runtime orchestration: `backend/orchestrator/agent/controller.py`
- Resolution handlers: `backend/orchestrator/tools/handlers/resolution.py`
