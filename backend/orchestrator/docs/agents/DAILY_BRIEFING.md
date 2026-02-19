# Daily Briefing Agent

This document captures behavior and quality rules for the daily briefing profile.

## Responsibilities

- Build a prep-oriented daily briefing for a specific local day/timezone.
- Focus on upcoming events and actionable preparation.
- Include birthdays, outstanding todos, and relevant news when available.

## Generation Flow

1. Gather event context for the day, similar past events, linked todos, contacts.
2. Run per-event deep analysis (`_summarize_event`) using dedicated calls.
3. Gather birthdays and unlinked pending todos.
4. Aggregate news via `news_feeds.fetch_news()`.
5. Generate final markdown in focused passes:
   - Core sections first: Day Overview, Schedule, Event Prep, Birthdays, Outstanding Todos.
   - `## News & Topics` in a dedicated call from a bounded news subset.
   - Merge sections into final markdown.
6. Run summary generation (plain text 1-2 sentences).

## News Relevance and Selection

- News is bounded before LLM generation to avoid prompt overload.
- Selection includes:
  - per-topic caps,
  - general-headline cap,
  - deduplication,
  - relevance scoring (topic matches, source quality, recency, overlap with event/todo terms).
- Goal: fewer but higher-signal articles that are more relevant to the day.

## Validation Pipeline

Final markdown is validated in `agents/daily_briefing/validators.py`:

1. Structural: required header/sections, minimum length, banned meta/thinking phrases.
2. Coherence: event-title presence, news links when expected, no tool/JSON artifacts.
3. LLM judge: lightweight quality gate for subtle failures.

On failure, targeted rewrites are attempted with explicit failure reasons.

## Key Files

- Runtime: `backend/orchestrator/agents/daily_briefing/executor.py`
- Profile/tool policy: `backend/orchestrator/agents/daily_briefing/profile.py`
- Validation: `backend/orchestrator/agents/daily_briefing/validators.py`
