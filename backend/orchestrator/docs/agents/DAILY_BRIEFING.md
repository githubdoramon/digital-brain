# Daily Briefing Agent

This document captures behavior and quality rules for the daily briefing profile.

## Responsibilities

- Build a prep-oriented daily briefing for a specific local day/timezone.
- Focus on upcoming events and actionable preparation.
- Include birthdays, outstanding todos, and relevant news when available.

## Generation Flow

1. Gather event context for the day, similar past events, linked todos, contacts.
2. Run per-event deep analysis (`_summarize_event`) using dedicated calls:
   - Similar history retrieval uses exact title and recurrence keys first.
   - If those miss, fallback to attendee-overlap history (exact attendee set first, then >=80% overlap), ranked by overlap and recency.
   - Attendee-overlap comparison excludes the current user from both sides and ignores zero-attendee matches after that filtering.
   - Prep synthesis reuses the `summarize_memories` summary flow/prompt over the last 4 matching prior occurrences for that event, instead of broad memory search.
   - If there are no prior occurrences, fallback matching uses the same attendees. If history is still empty, a final freeform prep synthesis may use current context plus targeted research.
   - Per-event prep has a single responsibility: produce one structured prep payload (`key_points`, `action_items`, `suggested_reading`, `prep_focus`). Research runs only in the no-history fallback path.
3. Gather birthdays and unlinked pending todos.
4. Aggregate news via `news_feeds.fetch_news()` (Tavily + NewsData + RSS), then story-cluster and persist mention history. Partition the collected set into one bounded editorial bucket per tracked topic plus a general-news bucket. Each bucket sends at most 28 candidates to the shared structured-output model, processed synchronously and sequentially with a 300-second per-request timeout. Multi-topic articles may be reviewed in more than one bucket but are merged by stable article identity before ranking, and only controller-approved configured labels survive. Same-story coverage is deduplicated within each bucket and the existing URL/cluster dedupe remains authoritative afterward. A failed tracked-topic bucket fails closed so keyword collisions cannot enter a topic; a failed general bucket retains only its bounded candidates because it has no topic-label collision, with normal deterministic ranking still applied.
5. Generate final markdown in focused passes:
   - Build event-critical sections (`Day Overview`, `Schedule`, `Event Prep`) deterministically from the structured per-event prep payloads (no second event-summary rewrite pass).
   - Build deterministic sections in code for birthdays and outstanding todos.
   - Build `## News & Topics` deterministically in code from the bounded news subset (topic grouping is code-owned), with per-article one-sentence LLM summaries generated after selection.
   - Assemble final markdown in code (no full-document rewrite pass).
6. Build a deterministic plain-text summary that includes only meeting/todo counts and (when location is available) a weather outlook for the day.

## Parallelism

- The pipeline executes independent work in parallel with bounded worker pools:
  - per-event enrichment (similar history, linked todos, contacts),
  - per-event deep summary generation,
  - birthdays/news/unlinked-todos fetches,
  - final event-section and news-section generation.
- Results are merged back in deterministic event order.

## News Relevance and Selection

- News selection is bounded before per-article summary generation to avoid prompt overload; editorial curation is also bounded per topic/general bucket (28 candidates per request, processed sequentially with a 300-second timeout per bucket).
- Each structured editorial bucket compares article content within that topic/general set and keeps one representative of the same underlying story when outlets, URLs, titles, and wording differ, while preserving genuinely distinct developments or reporting angles.
- Tracked-topic buckets validate claimed labels by semantic subject matter. Keywords are treated only as collection hints, so ambiguous namesakes and incidental mentions are removed from topic sections instead of being accepted as matches. Model-authored topic labels are never trusted outside the controller's configured bucket label.
- Curation failures are isolated: malformed, incomplete, timed-out, or failed tracked-topic buckets fail closed; general-bucket failures retain only the bounded request candidates because general articles have no topic label to validate. URL/cluster deduplication and deterministic ranking still run afterward.
- Selection includes:
  - dynamic score-threshold selection (instead of strict fixed per-topic/source limits),
  - per-topic hard cap (currently max 10 selected articles per topic label),
  - minimum general-headline floor (currently at least 3 selected general headlines when available),
  - deduplication,
  - relevance scoring (topic matches, source quality, recency, overlap with event/todo terms),
  - trend/novelty signals from persisted story mention history,
  - user-preference weighting from explicit article interactions (open, thumbs up/down).
- Topic matching uses confidence scoring over normalized title/summary keyword evidence (including accent-insensitive text normalization) to reduce wrong-topic clustering.
- After selection, each included article gets a one-sentence LLM rewrite focused on decision value (`what happened` + `why it matters`) before rendering.
- Goal: fewer but higher-signal articles that are more relevant to the day.

## Delivery Semantics

- Mobile briefing fetch is non-blocking: when a daily briefing is missing, the API enqueues generation and returns `pending` immediately (HTTP 202).
- Clients poll the same daily endpoint until status becomes `ready`.
- Service endpoint `/agents/daily-briefing/run` also queues work and returns immediately.
- After a successful generation, a notification is dispatched using the `daily-briefing` notification type and the stored per-channel user subscription preferences. Push payloads use `kind=daily_briefing_ready` so mobile opens `/home` with the full briefing component already expanded.

## Feedback Loop

- Mobile article opens and thumbs feedback are recorded via `/mobile/news/interactions`.
- Signals update `news_user_profiles` and feed personalization weights used in the next daily run.

## Voice and Perspective

- The briefing is written for the calendar owner (the authenticated user).
- Prefer owner-facing language (for example, "you will review...") and avoid third-person owner references.
- Event prep should avoid phrasing like "align with <owner name>" when that name is the user.
- Event prep output should suppress low-value generic advice (for example "review notes", "confirm agenda", "prepare talking points") and only keep context-grounded, non-obvious items.
- Event prep should favor the last 4 matching prior occurrences as the primary source of historical topics, outcomes/decisions, and follow-ups, with current notes/todos only filling gaps.
- Event synthesis should explicitly separate current upcoming-event context from historical similar-event references.
- Do not summarize event prep twice; once the structured per-event prep payload is built, final briefing rendering should be deterministic.
- Keep `Day Overview` strategic and concise, while `Schedule` carries the concrete per-event timeline.

## Event Research Policy

- Research should not run on the main history-synthesis path for events that already have useful prior-occurrence evidence.
- Research must pass a value gate (e.g., external attendees, high-signal event framing, or context gaps) before tool usage.
- A planning pass proposes up to 3 targeted queries with explicit "why this matters now" rationale.
- Research output is accepted only when each finding contains both:
  - meeting-specific impact ("why it matters"), and
  - source URL.
- Generic company overviews, meeting hygiene reminders, and unsupported suggestions are treated as low-value and removed.
- If no grounded high-value finding is available, research is skipped and event prep remains sparse.

## Summary Rules

- The briefing summary is deterministic (no LLM rewrite pass).
- Include meeting and pending-todo counts only; do not describe specific meetings/todos in the summary.
- Do not include preference-driven idea suggestions in the summary.
- Do not include a news digest paragraph in the summary.
- If a latest user location exists in history, append a weather outlook sentence for the briefing day.

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
- News curation: `backend/orchestrator/agents/daily_briefing/news_curation.py`
- Profile/tool policy: `backend/orchestrator/agents/daily_briefing/profile.py`
- Validation: `backend/orchestrator/agents/daily_briefing/validators.py`
