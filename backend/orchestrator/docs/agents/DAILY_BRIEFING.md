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
   - Event-critical sections first (`Day Overview`, `Schedule`, `Event Prep`) in an isolated prompt.
   - Build deterministic sections in code for birthdays and outstanding todos.
   - Build `## News & Topics` deterministically in code from the bounded news subset (topic grouping is code-owned), with per-article one-sentence LLM summaries generated after selection.
   - Assemble final markdown in code (no full-document rewrite pass).
6. Run summary generation (plain text 1-2 sentences).

## Parallelism

- The pipeline executes independent work in parallel with bounded worker pools:
  - per-event enrichment (similar history, linked todos, contacts),
  - per-event deep summary generation,
  - birthdays/news/unlinked-todos fetches,
  - final event-section and news-section generation.
- Results are merged back in deterministic event order.

## News Relevance and Selection

- News is bounded before LLM generation to avoid prompt overload.
- Selection includes:
  - per-topic cap (currently 3 per topic),
  - general-headline cap,
  - deduplication,
  - relevance scoring (topic matches, source quality, recency, overlap with event/todo terms).
- Topic matching uses confidence scoring over normalized title/summary keyword evidence (including accent-insensitive text normalization) to reduce wrong-topic clustering.
- After selection, each included article gets a one-sentence LLM rewrite focused on decision value (`what happened` + `why it matters`) before rendering.
- Goal: fewer but higher-signal articles that are more relevant to the day.

## Voice and Perspective

- The briefing is written for the calendar owner (the authenticated user).
- Prefer owner-facing language (for example, "you will review...") and avoid third-person owner references.
- Event prep should avoid phrasing like "align with <owner name>" when that name is the user.

## User Context Injection

- Daily briefing prompt calls inject both:
  - self identity context (display name, known emails, aliases, ownership guardrail), and
  - relevant user facts (`get_user_facts_context`).
- This applies across event research/synthesis, core markdown generation, news-section generation, and rewrite prompts.

## Validation Pipeline

Focused validation runs in `agents/daily_briefing/validators.py`:

1. Event section validation (`validate_event_sections`): checks event-critical sections and title coverage.
2. News section validation (`validate_news_section`): checks article presentation format (title + link + summary + source).

If event section generation fails validation, the executor falls back to deterministic event-section construction.
If news validation fails, the executor falls back to `No notable news today.`

## Key Files

- Runtime: `backend/orchestrator/agents/daily_briefing/executor.py`
- Profile/tool policy: `backend/orchestrator/agents/daily_briefing/profile.py`
- Validation: `backend/orchestrator/agents/daily_briefing/validators.py`
