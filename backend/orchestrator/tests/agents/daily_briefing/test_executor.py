"""Tests for daily briefing executor – per-event analysis, research & birthdays."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from agents.daily_briefing.executor import (
    BIRTHDAY_LOOKAHEAD_DAYS,
    _build_briefing_prompt,
    _build_event_research_value_signals,
    _build_event_summary_debug_bundle,
    _enrich_selected_news_summaries,
    _fetch_similar_events,
    _format_context_text,
    _format_event_for_analysis,
    _generate_news_section_markdown,
    _generate_summary,
    _normalize_similarity_attendees,
    _research_event,
    _research_event_debug,
    _sanitize_research_findings,
    _select_news_for_generation,
    _summarize_event,
    _synthesise_event_summary,
    _synthesise_event_summary_from_current_context,
)
from agents.daily_briefing.news_curation import (
    NEWS_CURATION_BUCKET_MAX_CANDIDATES,
    NEWS_CURATION_TIMEOUT_SECONDS,
)
from agents.daily_briefing.news_curation import (
    curate_collected_news as _curate_collected_news,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_event_context(
    *,
    title: str = "Team Standup",
    event_prep: dict | None = None,
    summary: str = "",
    todos: list | None = None,
    contacts: list | None = None,
    similar_events: list | None = None,
    related_todos: list | None = None,
) -> dict:
    return {
        "id": "evt_1",
        "title": title,
        "local_start": "2026-02-15T09:00:00+00:00",
        "local_end": "2026-02-15T10:00:00+00:00",
        "types": ["meeting"],
        "tags": ["recurring"],
        "place": {"name": "Office", "city": "Aurora", "country": "WT"},
        "people": [],
        "contacts": contacts or [],
        "event_prep": event_prep or {},
        "summary": summary,
        "todos": todos or [],
        "related_todos": related_todos or [],
        "similar_events": similar_events or [],
    }


def _make_birthday(name: str, birthday: str, days_away: int, is_today: bool = False) -> dict:
    return {
        "contact_id": f"contact:{name.lower()}",
        "display_name": name,
        "birthday": birthday,
        "days_away": days_away,
        "is_today": is_today,
    }


def _make_news_article(
    *,
    title: str = "AI Breakthrough",
    url: str = "https://example.com/news/1",
    summary: str = "Big AI news today",
    source: str = "hacker_news",
    topic_matches: list | None = None,
) -> dict:
    return {
        "title": title,
        "url": url,
        "summary": summary,
        "source": source,
        "published_at": "2026-02-15T10:00:00+00:00",
        "topic_matches": topic_matches or [],
    }


def _make_context(
    *,
    events: list | None = None,
    all_todos: list | None = None,
    upcoming_birthdays: list | None = None,
    news_articles: list | None = None,
) -> dict:
    return {
        "date": "2026-02-15",
        "timezone": "UTC",
        "day_start": "2026-02-15T00:00:00+00:00",
        "day_end": "2026-02-16T00:00:00+00:00",
        "events": events or [],
        "all_todos": all_todos or [],
        "upcoming_birthdays": upcoming_birthdays or [],
        "news_articles": news_articles or [],
    }


# ---------------------------------------------------------------------------
# _format_context_text – structured prep integration
# ---------------------------------------------------------------------------


class TestFormatContextWithDeepSummary:
    def test_event_prep_included_in_context(self):
        event = _make_event_context(
            event_prep={"key_points": ["Past standup went long"], "action_items": [], "prep_focus": ""}
        )
        ctx = _make_context(events=[event])
        text = _format_context_text(ctx)
        assert "Structured prep:" in text
        assert "Past standup went long" in text

    def test_fallback_when_event_prep_empty(self):
        event = _make_event_context(
            summary="We discussed Q1 roadmap",
            todos=[{"status": "pending", "description": "Follow up on Q1"}],
        )
        ctx = _make_context(events=[event])
        text = _format_context_text(ctx)
        assert "Context from prior notes" in text
        assert "Follow up on Q1" in text
        assert "Structured prep:" not in text

    def test_no_events_still_works(self):
        ctx = _make_context(events=[])
        text = _format_context_text(ctx)
        assert "Events for Today (0)" in text
        assert "- None" in text


# ---------------------------------------------------------------------------
# _format_context_text – birthdays section
# ---------------------------------------------------------------------------


class TestFormatContextBirthdays:
    def test_birthdays_included(self):
        bdays = [
            _make_birthday("Alice", "1990-02-15", 0, is_today=True),
            _make_birthday("Bob", "1985-02-18", 3),
        ]
        ctx = _make_context(upcoming_birthdays=bdays)
        text = _format_context_text(ctx)
        assert "Upcoming Birthdays (2)" in text
        assert "Alice - TODAY!" in text
        assert "Bob - in 3 day(s)" in text

    def test_no_birthdays_omitted(self):
        ctx = _make_context(upcoming_birthdays=[])
        text = _format_context_text(ctx)
        assert "Upcoming Birthdays" not in text


# ---------------------------------------------------------------------------
# _build_briefing_prompt – birthdays section in required structure
# ---------------------------------------------------------------------------


class TestBriefingPromptBirthdays:
    def test_prompt_includes_birthday_section_when_present(self):
        bdays = [_make_birthday("Alice", "1990-02-15", 0, is_today=True)]
        ctx = _make_context(upcoming_birthdays=bdays)
        prompt = _build_briefing_prompt(ctx)
        assert "## Upcoming Birthdays" in prompt
        assert "include the Upcoming Birthdays section" in prompt

    def test_prompt_omits_birthday_section_when_empty(self):
        ctx = _make_context(upcoming_birthdays=[])
        prompt = _build_briefing_prompt(ctx)
        assert "## Upcoming Birthdays" not in prompt

    def test_prompt_mentions_pre_computed_analysis(self):
        ctx = _make_context()
        prompt = _build_briefing_prompt(ctx)
        assert "structured prep" in prompt


# ---------------------------------------------------------------------------
# _build_briefing_prompt – output rules ban meta-commentary
# ---------------------------------------------------------------------------


class TestBriefingPromptOutputRules:
    def test_bans_meta_commentary_patterns(self):
        ctx = _make_context()
        prompt = _build_briefing_prompt(ctx)
        assert "NEVER use meta-commentary" in prompt
        assert "the text includes" in prompt  # listed as banned example

    def test_bans_generic_category_lists(self):
        ctx = _make_context()
        prompt = _build_briefing_prompt(ctx)
        assert "NEVER produce generic category lists" in prompt

    def test_bans_asking_questions(self):
        ctx = _make_context()
        prompt = _build_briefing_prompt(ctx)
        assert "NEVER ask questions or offer to do more" in prompt


# ---------------------------------------------------------------------------
# _format_event_for_analysis – event text block
# ---------------------------------------------------------------------------


class TestFormatEventForAnalysis:
    def test_includes_title_and_time(self):
        event = _make_event_context(title="Board Meeting")
        text = _format_event_for_analysis(event)
        assert "CURRENT UPCOMING EVENT: Board Meeting" in text
        assert "Time:" in text

    def test_includes_similar_events(self):
        similar = [
            {
                "title": "Past Board",
                "local_start": "2026-02-01T09:00:00+00:00",
                "start_date": "2026-02-01",
                "summary": "Discussed budget",
            }
        ]
        event = _make_event_context(title="Board", similar_events=similar)
        text = _format_event_for_analysis(event)
        assert "Historical similar occurrences" in text
        assert "Past Board" in text
        assert "Discussed budget" in text

    def test_includes_linked_todos(self):
        todos = [{"status": "pending", "description": "Send deck", "updated_at": "2026-02-14"}]
        event = _make_event_context(todos=todos)
        text = _format_event_for_analysis(event)
        assert "Send deck" in text
        assert "Linked todos" in text

    def test_includes_location(self):
        event = _make_event_context()
        text = _format_event_for_analysis(event)
        assert "Office, Aurora, WT" in text


# ---------------------------------------------------------------------------
# _research_event – web research tool loop
# ---------------------------------------------------------------------------


class TestResearchEvent:
    @patch("agents.daily_briefing.executor.run_profiled_tool_loop")
    @patch("agents.daily_briefing.executor.build_event_research_profile")
    @patch("agents.daily_briefing.executor._plan_event_research")
    @patch("agents.daily_briefing.executor._build_event_research_value_signals")
    def test_returns_research_content(
        self,
        mock_value_signals,
        mock_plan_research,
        mock_build_profile,
        mock_tool_loop,
    ):
        """Research loop returns useful findings."""
        mock_value_signals.return_value = {
            "score": 3,
            "reasons": ["high_signal_title"],
            "should_research": True,
            "external_contact_count": 1,
        }
        mock_plan_research.return_value = {
            "should_research": True,
            "reason": "high_value",
            "targets": [{"query": "Acme Corp", "why": "Partner context"}],
        }
        mock_profile = MagicMock()
        mock_profile.build_tools_and_handlers.return_value = (
            [{"type": "function", "function": {"name": "web_search"}}],
            {"web_search": lambda args: {}},
        )
        mock_profile.runtime = MagicMock()
        mock_profile.get_system_prompt.return_value = "system"
        mock_build_profile.return_value = mock_profile

        mock_tool_loop.return_value = {
            "content": "- Acme Corp raised $50M Series B. Why it matters: gives leverage context before pricing discussion. Source: https://example.com"
        }

        result = _research_event("Event: Acme Intro Call", "Acme Intro Call", "UTC")
        assert "Acme Corp" in result
        assert "example.com" in result
        mock_tool_loop.assert_called_once()

    @patch("agents.daily_briefing.executor.run_profiled_tool_loop")
    @patch("agents.daily_briefing.executor.build_event_research_profile")
    @patch("agents.daily_briefing.executor._plan_event_research")
    @patch("agents.daily_briefing.executor._build_event_research_value_signals")
    def test_no_research_needed_returns_empty(
        self,
        mock_value_signals,
        mock_plan_research,
        mock_build_profile,
        mock_tool_loop,
    ):
        """When the LLM decides no research is needed, return empty string."""
        mock_value_signals.return_value = {
            "score": 3,
            "reasons": ["specific_title"],
            "should_research": True,
            "external_contact_count": 0,
        }
        mock_plan_research.return_value = {
            "should_research": True,
            "reason": "high_value",
            "targets": [{"query": "Team standup", "why": "none"}],
        }
        mock_profile = MagicMock()
        mock_profile.build_tools_and_handlers.return_value = (
            [{"type": "function", "function": {"name": "web_search"}}],
            {"web_search": lambda args: {}},
        )
        mock_profile.runtime = MagicMock()
        mock_profile.get_system_prompt.return_value = "system"
        mock_build_profile.return_value = mock_profile

        mock_tool_loop.return_value = {"content": "NO_RESEARCH_NEEDED"}

        result = _research_event("Event: Team Standup", "Team Standup", "UTC")
        assert result == ""

    @patch(
        "agents.daily_briefing.executor.run_profiled_tool_loop", side_effect=Exception("timeout")
    )
    @patch("agents.daily_briefing.executor.build_event_research_profile")
    @patch("agents.daily_briefing.executor._plan_event_research")
    @patch("agents.daily_briefing.executor._build_event_research_value_signals")
    def test_graceful_failure(
        self,
        mock_value_signals,
        mock_plan_research,
        mock_build_profile,
        mock_tool_loop,
    ):
        """Research failures should not crash the pipeline."""
        mock_value_signals.return_value = {
            "score": 3,
            "reasons": ["high_signal_title"],
            "should_research": True,
            "external_contact_count": 1,
        }
        mock_plan_research.return_value = {
            "should_research": True,
            "reason": "high_value",
            "targets": [{"query": "Something", "why": "unknown org"}],
        }
        mock_profile = MagicMock()
        mock_profile.build_tools_and_handlers.return_value = (
            [{"type": "function", "function": {"name": "web_search"}}],
            {"web_search": lambda args: {}},
        )
        mock_profile.runtime = MagicMock()
        mock_profile.get_system_prompt.return_value = "system"
        mock_build_profile.return_value = mock_profile

        result = _research_event("Event: Something", "Something", "UTC")
        assert result == ""

    @patch("agents.daily_briefing.executor._build_event_research_value_signals")
    def test_value_gate_can_skip_without_calling_tools(self, mock_value_signals):
        mock_value_signals.return_value = {
            "score": 0,
            "reasons": ["history_already_rich"],
            "should_research": False,
            "external_contact_count": 0,
        }

        result = _research_event("Event: Weekly Standup", "Weekly Standup", "UTC")
        assert result == ""


class TestResearchEventDebug:
    @patch("agents.daily_briefing.executor._build_event_research_value_signals")
    def test_exposes_skip_reason_when_value_gate_blocks_research(self, mock_value_signals):
        mock_value_signals.return_value = {
            "score": 0,
            "reasons": ["history_already_rich"],
            "should_research": False,
            "external_contact_count": 0,
        }

        result = _research_event_debug("Event: Weekly Standup", "Weekly Standup", "UTC")

        assert result["status"] == "skipped_value_gate"
        assert result["notes"] == ""
        assert result["plan"]["reason"] == "value_gate"


class TestResearchFindingSanitization:
    def test_keeps_only_findings_with_why_and_source(self):
        raw = (
            "- Funding round announced. Why it matters: changes negotiation leverage. Source: https://example.com/news\n"
            "- Generic background paragraph without source\n"
            "- Product release. Source: https://example.com/release\n"
        )
        cleaned = _sanitize_research_findings(raw)
        assert "Funding round announced" in cleaned
        assert "Generic background" not in cleaned
        assert "Product release" not in cleaned


class TestResearchValueSignals:
    def test_scores_external_high_signal_event_for_research(self):
        signals = _build_event_research_value_signals(
            title="Customer integration kickoff",
            event_text="CURRENT UPCOMING EVENT: Customer integration kickoff",
            event_context={
                "similar_events": [],
                "contacts": [
                    {
                        "display_name": "Partner Lead",
                        "emails": ["lead@partner.com"],
                        "tags": [],
                        "comments": "",
                    }
                ],
            },
            user_email="owner@mycompany.com",
        )
        assert signals["should_research"] is True
        assert signals["score"] >= 2


class TestSimilarEventsFallback:
    @patch("agents.daily_briefing.executor._fetch_similar_by_attendee_overlap")
    @patch("agents.daily_briefing.executor._fetch_similar_by_recurrence")
    @patch("agents.daily_briefing.executor._fetch_similar_by_title")
    def test_uses_attendee_overlap_when_title_and_recurrence_empty(
        self,
        mock_by_title,
        mock_by_recurrence,
        mock_by_attendee,
    ):
        mock_by_title.return_value = []
        mock_by_recurrence.return_value = []
        mock_by_attendee.return_value = [
            {
                "id": "evt_old",
                "title": "Different name but same crew",
                "start_date": "2026-01-01T10:00:00+00:00",
                "end_date": "2026-01-01T11:00:00+00:00",
                "summary": "Past discussion",
                "people": ["contact:a", "contact:b"],
                "attendee_overlap_ratio": 1.0,
                "similarity_match_type": "attendee_exact",
            }
        ]

        event = {
            "id": "evt_new",
            "title": "New naming",
            "people": ["contact:a", "contact:b"],
            "raw": {},
        }
        result = _fetch_similar_events(
            event,
            day_start=datetime(2026, 2, 1, tzinfo=timezone.utc),
            limit=3,
        )

        assert len(result) == 1
        assert result[0]["similarity_match_type"] == "attendee_exact"

    @patch("agents.daily_briefing.executor._fetch_similar_by_attendee_overlap")
    @patch("agents.daily_briefing.executor._fetch_similar_by_recurrence")
    @patch("agents.daily_briefing.executor._fetch_similar_by_title")
    def test_skips_attendee_overlap_when_only_self_attendee(
        self,
        mock_by_title,
        mock_by_recurrence,
        mock_by_attendee,
    ):
        mock_by_title.return_value = []
        mock_by_recurrence.return_value = []
        mock_by_attendee.return_value = [
            {
                "id": "evt_old",
                "title": "Should not be used",
                "start_date": "2026-01-01T10:00:00+00:00",
                "end_date": "2026-01-01T11:00:00+00:00",
                "summary": "Past discussion",
                "people": ["contact:user"],
            }
        ]

        event = {
            "id": "evt_new",
            "title": "Solo sync",
            "people": ["contact:user"],
            "raw": {},
        }
        result = _fetch_similar_events(
            event,
            day_start=datetime(2026, 2, 1, tzinfo=timezone.utc),
            limit=3,
            self_contact_id="contact:user",
        )

        assert result == []
        mock_by_attendee.assert_not_called()


class TestSimilarityAttendeeNormalization:
    def test_excludes_self_and_blanks(self):
        normalized = _normalize_similarity_attendees(
            ["contact:user", "contact:alice", "", "contact:alice", "  "],
            "contact:user",
        )
        assert normalized == {"contact:alice"}

    def test_can_return_empty_when_only_self_present(self):
        normalized = _normalize_similarity_attendees(["contact:user"], "contact:user")
        assert normalized == set()


# ---------------------------------------------------------------------------
# _synthesise_event_summary – matching-history synthesis
# ---------------------------------------------------------------------------


class TestSynthesiseEventSummary:
    @patch("agents.daily_briefing.executor._synthesize_memory_summary")
    def test_uses_last_four_similar_occurrences(self, mock_synthesize_memory_summary):
        mock_synthesize_memory_summary.return_value = (
            "Overview:\n"
            "- Budget alignment is the main open thread.\n"
            "Key topics:\n"
            "- Procurement needs updated pricing assumptions.\n"
            "Outcomes/decisions:\n"
            "- Finance asked for final budget sign-off before review.\n"
            "Follow-ups:\n"
            "- Send finance risk memo before the meeting."
        )
        event = _make_event_context(
            title="Finance Review",
            todos=[{"status": "pending", "description": "Bring revised cost model"}],
            similar_events=[
                {
                    "title": "Finance Review",
                    "start_date": f"2026-02-0{i}T09:00:00+00:00",
                    "summary": f"notes {i}",
                }
                for i in range(1, 6)
            ],
        )
        result = _synthesise_event_summary(
            event,
            "UTC",
        )

        synth_kwargs = mock_synthesize_memory_summary.call_args.kwargs
        assert len(synth_kwargs["events"]) == 4
        assert [item["start_date"] for item in synth_kwargs["events"]] == [
            "2026-02-01T09:00:00+00:00",
            "2026-02-02T09:00:00+00:00",
            "2026-02-03T09:00:00+00:00",
            "2026-02-04T09:00:00+00:00",
        ]
        assert any("Procurement needs updated pricing assumptions" in item for item in result["key_points"])
        assert any("Finance asked for final budget sign-off before review" in item for item in result["key_points"])
        assert any("Send finance risk memo before the meeting" in item for item in result["action_items"])
        assert any("Bring revised cost model" in item for item in result["action_items"])
        assert result["prep_focus"]
        assert result["history_source"] == "history"

    @patch("agents.daily_briefing.executor._synthesize_memory_summary")
    def test_falls_back_to_current_notes_when_history_summary_empty(self, mock_synthesize_memory_summary):
        mock_synthesize_memory_summary.return_value = ""
        event = _make_event_context(summary="Discuss Q1 roadmap and hiring plan")
        event["similar_events"] = [{"title": "Past standup", "start_date": "2026-02-01T09:00:00+00:00"}]

        result = _synthesise_event_summary(event, "UTC")

        assert any("Discuss Q1 roadmap and hiring plan" in item for item in result["key_points"])

    @patch("agents.daily_briefing.executor._synthesise_event_summary_from_current_context")
    @patch("agents.daily_briefing.executor._research_event_debug")
    def test_uses_current_context_fallback_when_no_history(self, mock_research, mock_fallback):
        mock_research.return_value = {
            "status": "skipped_value_gate",
            "notes": "",
            "value_signals": {"should_research": False},
            "plan": {"should_research": False},
            "tool_calls": 0,
        }
        mock_fallback.return_value = {"history_source": "current_context", "prep_focus": "Review external context"}
        event = _make_event_context(title="New Partner Intro", similar_events=[])

        result = _synthesise_event_summary(event, "UTC")

        mock_research.assert_called_once()
        mock_fallback.assert_called_once()
        assert result == {
            "history_source": "current_context",
            "key_points": [],
            "action_items": [],
            "suggested_reading": [],
            "prep_focus": "Review external context",
        }


class TestEventSummaryDebugBundle:
    @patch("agents.daily_briefing.executor._synthesize_memory_summary")
    def test_surfaces_history_path_details(self, mock_synthesize_memory_summary):
        mock_synthesize_memory_summary.return_value = (
            "Overview:\n"
            "- Budget alignment is the main open thread.\n"
            "Key topics:\n"
            "- Procurement needs updated pricing assumptions.\n"
            "Outcomes/decisions:\n"
            "- Finance asked for final budget sign-off before review.\n"
            "Follow-ups:\n"
            "- Send finance risk memo before the meeting."
        )
        event = _make_event_context(
            title="Finance Review",
            todos=[{"status": "pending", "description": "Bring revised cost model"}],
            similar_events=[
                {
                    "title": "Finance Review",
                    "start_date": "2026-02-01T09:00:00+00:00",
                    "summary": "Previous notes",
                }
            ],
        )

        result = _build_event_summary_debug_bundle(event, "UTC")

        assert result["path"] == "history"
        assert "Budget alignment" in result["memory_summary"]
        assert result["research"]["status"] == "skipped_history"
        assert any("Bring revised cost model" in item for item in result["event_prep"]["action_items"])


class TestSynthesiseEventSummaryFromCurrentContext:
    @patch("agents.daily_briefing.executor.call_llm")
    def test_includes_research_in_prompt(self, mock_call_llm):
        mock_call_llm.return_value = "KEY POINTS:\n- Acme raised funding"
        event = _make_event_context(title="Acme Call")
        result = _synthesise_event_summary_from_current_context(
            event,
            "Event: Acme Call",
            "- Acme raised $50M\n- Source: https://example.com",
            "Acme Call",
            "UTC",
        )
        assert "Acme raised" in result["key_points"][0]
        assert result["suggested_reading"] == ["https://example.com"]
        prompt = mock_call_llm.call_args[0][0]
        assert "WEB RESEARCH FINDINGS" in prompt
        assert "Acme raised $50M" in prompt


# ---------------------------------------------------------------------------
# _summarize_event – full two-phase pipeline
# ---------------------------------------------------------------------------


class TestSummarizeEvent:
    @patch("agents.daily_briefing.executor._synthesise_event_summary")
    def test_calls_synthesis_once(self, mock_synthesise):
        mock_synthesise.return_value = {"key_points": ["Important context"], "action_items": []}
        event = _make_event_context(title="Intro Call")
        result = _summarize_event(event, "UTC")

        mock_synthesise.assert_called_once()
        synth_args = mock_synthesise.call_args
        assert synth_args[0][0] == event
        assert synth_args[0][1] == "UTC"
        assert result["key_points"] == ["Important context"]


# ---------------------------------------------------------------------------
# BIRTHDAY_LOOKAHEAD_DAYS constant
# ---------------------------------------------------------------------------


def test_birthday_lookahead_is_seven():
    assert BIRTHDAY_LOOKAHEAD_DAYS == 7


class TestCurateCollectedNews:
    def test_partitions_topic_and_general_buckets_and_merges_multi_topic_article(self):
        topics = [
            {"label": "AI", "keywords": ["AI"]},
            {"label": "Climate", "keywords": ["climate"]},
        ]
        articles = [
            _make_news_article(
                title="AI climate model announced",
                url="https://example.com/multi",
                topic_matches=["AI", "Climate"],
            ),
            _make_news_article(
                title="New reasoning model",
                url="https://example.com/ai",
                topic_matches=["AI"],
            ),
            _make_news_article(
                title="Storm causes flooding",
                url="https://example.com/general",
                topic_matches=[],
            ),
        ]

        def curate(prompt, **_kwargs):
            ids = re.findall(r'"article_id": "(article_\d+)"', prompt)
            label = "AI" if "bucket for 'AI'" in prompt else "Climate" if "bucket for 'Climate'" in prompt else None
            return {
                "decisions": [
                    {
                        "article_id": article_id,
                        "keep": True,
                        "topic_matches": [label] if label else [],
                        "duplicate_of": None,
                        "reason": "Relevant development.",
                    }
                    for article_id in ids
                ]
            }

        with (
            patch("agents.daily_briefing.news_curation.news_feeds.list_topics", return_value=topics),
            patch("agents.daily_briefing.news_curation.call_llm_json_agentic", side_effect=curate) as mock_call,
        ):
            result = _curate_collected_news(articles)

        assert mock_call.call_count == 3
        assert [article["url"] for article in result] == [
            "https://example.com/multi",
            "https://example.com/ai",
            "https://example.com/general",
        ]
        assert result[0]["topic_matches"] == ["AI", "Climate"]
        assert result[-1]["topic_matches"] == []

    def test_caps_each_bucket_before_model_call(self):
        articles = [
            _make_news_article(
                title=f"AI report {index}",
                url=f"https://example.com/ai/{index}",
                topic_matches=["AI"],
            )
            for index in range(NEWS_CURATION_BUCKET_MAX_CANDIDATES + 5)
        ]

        def curate(prompt, **_kwargs):
            ids = re.findall(r'"article_id": "(article_\d+)"', prompt)
            assert len(ids) == NEWS_CURATION_BUCKET_MAX_CANDIDATES
            return {
                "decisions": [
                    {
                        "article_id": article_id,
                        "keep": True,
                        "topic_matches": ["AI"],
                        "duplicate_of": None,
                        "reason": "Relevant.",
                    }
                    for article_id in ids
                ]
            }

        with (
            patch(
                "agents.daily_briefing.news_curation.news_feeds.list_topics",
                return_value=[{"label": "AI", "keywords": ["AI"]}],
            ),
            patch("agents.daily_briefing.news_curation.call_llm_json_agentic", side_effect=curate),
        ):
            result = _curate_collected_news(articles)

        assert len(result) == NEWS_CURATION_BUCKET_MAX_CANDIDATES

    def test_curates_buckets_sequentially_with_five_minute_timeout(self):
        topics = [
            {"label": f"Topic {index}", "keywords": [f"keyword-{index}"]}
            for index in range(7)
        ]
        articles = [
            _make_news_article(
                title=f"Report {index}",
                url=f"https://example.com/report/{index}",
                topic_matches=[topic["label"]],
            )
            for index, topic in enumerate(topics)
        ]
        call_order = []

        def curate(prompt, **kwargs):
            article_id = re.search(r'"article_id": "(article_\d+)"', prompt).group(1)
            topic_label = re.search(r"bucket for '([^']+)'", prompt).group(1)
            call_order.append(topic_label)
            assert kwargs["timeout"] == NEWS_CURATION_TIMEOUT_SECONDS
            return {
                "decisions": [
                    {
                        "article_id": article_id,
                        "keep": True,
                        "topic_matches": [topic_label],
                        "duplicate_of": None,
                        "reason": "Relevant.",
                    }
                ]
            }

        with (
            patch("agents.daily_briefing.news_curation.news_feeds.list_topics", return_value=topics),
            patch("agents.daily_briefing.news_curation.call_llm_json_agentic", side_effect=curate),
        ):
            result = _curate_collected_news(articles)

        assert call_order == [topic["label"] for topic in topics]
        assert len(result) == len(articles)

    def test_failed_topic_bucket_isolated_from_general_bucket(self):
        articles = [
            _make_news_article(title="Gemini horoscope", url="https://example.com/horoscope", topic_matches=["AI"]),
            _make_news_article(title="Election result", url="https://example.com/general", topic_matches=[]),
        ]

        def curate(prompt, **_kwargs):
            ids = re.findall(r'"article_id": "(article_\d+)"', prompt)
            if "bucket for 'AI'" in prompt:
                raise TimeoutError("topic model timeout")
            return {
                "decisions": [
                    {
                        "article_id": article_id,
                        "keep": True,
                        "topic_matches": [],
                        "duplicate_of": None,
                        "reason": "General headline.",
                    }
                    for article_id in ids
                ]
            }

        with (
            patch(
                "agents.daily_briefing.news_curation.news_feeds.list_topics",
                return_value=[{"label": "AI", "keywords": ["Gemini"]}],
            ),
            patch("agents.daily_briefing.news_curation.call_llm_json_agentic", side_effect=curate),
        ):
            result = _curate_collected_news(articles)

        assert [article["url"] for article in result] == ["https://example.com/general"]

    def test_model_cannot_assign_a_different_topic_label(self):
        article = _make_news_article(title="Climate report", topic_matches=["AI"])
        with (
            patch(
                "agents.daily_briefing.news_curation.news_feeds.list_topics",
                return_value=[
                    {"label": "AI", "keywords": ["AI"]},
                    {"label": "Climate", "keywords": ["climate"]},
                ],
            ),
            patch(
                "agents.daily_briefing.news_curation.call_llm_json_agentic",
                return_value={
                    "decisions": [
                        {
                            "article_id": "article_1",
                            "keep": True,
                            "topic_matches": ["Climate"],
                            "duplicate_of": None,
                            "reason": "Wrong label.",
                        }
                    ]
                },
            ),
        ):
            assert _curate_collected_news([article]) == []

    def test_failed_general_bucket_does_not_bypass_candidate_cap(self):
        articles = [
            _make_news_article(
                title=f"Headline {index}",
                url=f"https://example.com/general/{index}",
                topic_matches=[],
            )
            for index in range(NEWS_CURATION_BUCKET_MAX_CANDIDATES + 3)
        ]
        with (
            patch("agents.daily_briefing.news_curation.news_feeds.list_topics", return_value=[]),
            patch(
                "agents.daily_briefing.news_curation.call_llm_json_agentic",
                side_effect=TimeoutError("general model timeout"),
            ),
        ):
            result = _curate_collected_news(articles)

        assert len(result) == NEWS_CURATION_BUCKET_MAX_CANDIDATES

    @patch("agents.daily_briefing.news_curation.call_llm_json_agentic")
    @patch("agents.daily_briefing.news_curation.news_feeds.list_topics")
    def test_removes_content_duplicate_and_semantic_topic_mismatch(
        self,
        mock_list_topics,
        mock_call_llm_json_agentic,
    ):
        mock_list_topics.return_value = [
            {"label": "AI", "keywords": ["AI", "Gemini", "artificial intelligence"]}
        ]
        articles = [
            _make_news_article(
                title="Lab releases a new reasoning model",
                url="https://example.com/model-release",
                summary="The lab launched a reasoning model for software tasks.",
                source="Source One",
                topic_matches=["AI"],
            ),
            _make_news_article(
                title="New coding model arrives",
                url="https://another.example/reasoning-launch",
                summary="A new reasoning model from the same lab targets software work.",
                source="Source Two",
                topic_matches=["AI"],
            ),
            _make_news_article(
                title="Gemini horoscope for Saturday",
                url="https://example.com/horoscope",
                summary="An astrology forecast for people born under Gemini.",
                source="Daily Example",
                topic_matches=["AI"],
            ),
        ]
        mock_call_llm_json_agentic.return_value = {
            "decisions": [
                {
                    "article_id": "article_1",
                    "keep": True,
                    "topic_matches": ["AI"],
                    "duplicate_of": None,
                    "reason": "Clearest report of the model launch.",
                },
                {
                    "article_id": "article_2",
                    "keep": False,
                    "topic_matches": ["AI"],
                    "duplicate_of": "article_1",
                    "reason": "Same underlying model launch.",
                },
                {
                    "article_id": "article_3",
                    "keep": False,
                    "topic_matches": [],
                    "duplicate_of": None,
                    "reason": "Astrology, not artificial intelligence.",
                },
            ]
        }

        result = _curate_collected_news(articles)

        assert [article["url"] for article in result] == ["https://example.com/model-release"]
        prompt = mock_call_llm_json_agentic.call_args.args[0]
        assert "same underlying real-world story" in prompt
        assert "keyword collision" in prompt
        kwargs = mock_call_llm_json_agentic.call_args.kwargs
        assert kwargs["response_format"]["json_schema"]["name"] == "daily_briefing_news_curation"
        assert kwargs["use_fast_model"] is False
        assert kwargs["reasoning_effort"] == "x-high"
        assert kwargs["timeout"] == NEWS_CURATION_TIMEOUT_SECONDS
        assert kwargs["max_turns"] == 2
        assert callable(kwargs["result_validator"])

    @patch("agents.daily_briefing.news_curation.call_llm_json_agentic")
    @patch("agents.daily_briefing.news_curation.news_feeds.list_topics", return_value=[])
    def test_incomplete_model_decisions_fall_back_without_dropping_articles(
        self,
        _mock_list_topics,
        mock_call_llm_json_agentic,
    ):
        articles = [
            _make_news_article(title="First", url="https://example.com/first"),
            _make_news_article(title="Second", url="https://example.com/second"),
        ]
        mock_call_llm_json_agentic.return_value = {
            "decisions": [
                {
                    "article_id": "article_1",
                    "keep": True,
                    "topic_matches": [],
                    "duplicate_of": None,
                    "reason": "Keep.",
                }
            ]
        }

        assert _curate_collected_news(articles) == articles


# ---------------------------------------------------------------------------
# _format_context_text – news articles section
# ---------------------------------------------------------------------------


class TestFormatContextNews:
    def test_topic_matched_news_included(self):
        articles = [
            _make_news_article(
                title="OpenAI releases GPT-5",
                url="https://example.com/gpt5",
                source="langsearch",
                topic_matches=["AI"],
            ),
        ]
        ctx = _make_context(news_articles=articles)
        text = _format_context_text(ctx)
        assert "News Matching Your Topics (1)" in text
        assert "[AI]" in text
        assert "OpenAI releases GPT-5" in text
        assert "URL: https://example.com/gpt5" in text

    def test_general_headlines_included(self):
        articles = [
            _make_news_article(
                title="Stock Market Rally",
                url="https://bbc.com/rally",
                summary="Markets surged on positive data",
                source="bbc_world",
                topic_matches=[],
            ),
        ]
        ctx = _make_context(news_articles=articles)
        text = _format_context_text(ctx)
        assert "General Headlines (1)" in text
        assert "Stock Market Rally" in text
        assert "URL: https://bbc.com/rally" in text
        assert "Markets surged on positive data" in text

    def test_no_news_omits_section(self):
        ctx = _make_context(news_articles=[])
        text = _format_context_text(ctx)
        assert "News Matching" not in text
        assert "General Headlines" not in text

    def test_mixed_topic_and_general(self):
        articles = [
            _make_news_article(title="AI News", topic_matches=["AI"]),
            _make_news_article(title="Sports Update", url="https://x.com/2", topic_matches=[]),
        ]
        ctx = _make_context(news_articles=articles)
        text = _format_context_text(ctx)
        assert "News Matching Your Topics (1)" in text
        assert "General Headlines (1)" in text

    def test_general_headlines_capped_at_five(self):
        """General headlines should be capped to top 5 worldwide items."""
        articles = [
            _make_news_article(
                title=f"Headline {i}",
                url=f"https://x.com/{i}",
                topic_matches=[],
            )
            for i in range(25)
        ]
        ctx = _make_context(news_articles=articles)
        text = _format_context_text(ctx)
        # We cap at 5 general headlines
        assert "General Headlines (25)" in text
        assert "Headline 4" in text  # 0-indexed, the 5th item
        assert "Headline 5" not in text  # 6th should be excluded


# ---------------------------------------------------------------------------
# _build_briefing_prompt – news section in required structure
# ---------------------------------------------------------------------------


class TestBriefingPromptNews:
    def test_prompt_includes_news_section_when_present(self):
        articles = [_make_news_article(topic_matches=["AI"])]
        ctx = _make_context(news_articles=articles)
        prompt = _build_briefing_prompt(ctx)
        assert "## News & Topics" in prompt
        assert "include the News & Topics section" in prompt

    def test_prompt_instructs_article_format_with_url(self):
        articles = [_make_news_article(topic_matches=["AI"])]
        ctx = _make_context(news_articles=articles)
        prompt = _build_briefing_prompt(ctx)
        assert "[Article Title](url)" in prompt

    def test_prompt_emphasises_summaries(self):
        articles = [_make_news_article(topic_matches=["AI"])]
        ctx = _make_context(news_articles=articles)
        prompt = _build_briefing_prompt(ctx)
        assert "1-sentence summary" in prompt

    def test_prompt_bans_generic_category_lists(self):
        articles = [_make_news_article(topic_matches=["AI"])]
        ctx = _make_context(news_articles=articles)
        prompt = _build_briefing_prompt(ctx)
        assert "NEVER produce generic category lists" in prompt

    def test_prompt_bans_meta_commentary(self):
        articles = [_make_news_article(topic_matches=["AI"])]
        ctx = _make_context(news_articles=articles)
        prompt = _build_briefing_prompt(ctx)
        assert "there are several" in prompt.lower()  # mentioned as banned example

    def test_prompt_requires_concrete_articles_only(self):
        articles = [_make_news_article(topic_matches=["AI"])]
        ctx = _make_context(news_articles=articles)
        prompt = _build_briefing_prompt(ctx)
        assert "ONLY concrete articles" in prompt

    def test_prompt_caps_general_headlines_at_five(self):
        articles = [_make_news_article(topic_matches=["AI"])]
        ctx = _make_context(news_articles=articles)
        prompt = _build_briefing_prompt(ctx)
        assert "up to 5" in prompt

    def test_prompt_omits_news_section_when_empty(self):
        ctx = _make_context(news_articles=[])
        prompt = _build_briefing_prompt(ctx)
        assert "## News & Topics" not in prompt
        assert "News & Topics section" not in prompt


class TestGenerateNewsSectionMarkdown:
    def test_keeps_topic_assignment_deterministic(self):
        selected_news = {
            "topic_articles": [
                _make_news_article(
                    title="Studio announces franchise sequel",
                    url="https://example.com/ent-1",
                    summary="A major studio confirmed a new release timeline.",
                    source="reuters",
                    topic_matches=["Entertainment"],
                ),
                _make_news_article(
                    title="Satellite startup secures launch slot",
                    url="https://example.com/space-1",
                    summary="The company locked a key launch window for Q3.",
                    source="bbc_world",
                    topic_matches=["Space"],
                ),
            ],
            "general_articles": [],
        }

        section = _generate_news_section_markdown(selected_news)

        entertainment_block = section.split("### Entertainment", 1)[1].split("### Space", 1)[0]
        space_block = section.split("### Space", 1)[1]

        assert "Studio announces franchise sequel" in entertainment_block
        assert "Satellite startup secures launch slot" not in entertainment_block
        assert "Satellite startup secures launch slot" in space_block

    def test_includes_general_headlines_block(self):
        selected_news = {
            "topic_articles": [],
            "general_articles": [
                _make_news_article(
                    title="Central bank signals rate pause",
                    url="https://example.com/econ-1",
                    summary="Markets are repricing growth expectations after the guidance update.",
                    source="bloomberg",
                    topic_matches=[],
                )
            ],
        }

        section = _generate_news_section_markdown(selected_news)
        assert "### General Headlines" in section
        assert "[Central bank signals rate pause](https://example.com/econ-1)" in section

    def test_dedupes_same_link_across_topic_and_general_sections(self):
        selected_news = {
            "topic_articles": [
                _make_news_article(
                    title="How to use ChatGPT app integrations",
                    url="https://techcrunch.com/2026/03/15/chatgpt-app-integrations/",
                    summary="OpenAI added app integrations to ChatGPT.",
                    source="techcrunch",
                    topic_matches=["AI"],
                )
            ],
            "general_articles": [
                _make_news_article(
                    title="How to use ChatGPT app integrations - TechCrunch",
                    url="https://techcrunch.com/2026/03/15/chatgpt-app-integrations/?guccounter=1",
                    summary="ChatGPT can now connect to DoorDash, Spotify, and Uber.",
                    source="techcrunch.com",
                    topic_matches=[],
                )
            ],
        }

        section = _generate_news_section_markdown(selected_news)

        assert section.count("chatgpt-app-integrations") == 1


class TestSelectNewsForGeneration:
    @patch("agents.daily_briefing.executor.news_feeds.get_cluster_signal_map", return_value={})
    @patch(
        "agents.daily_briefing.executor.news_personalization.get_user_preference_weights",
        return_value=({}, {}),
    )
    def test_dedupes_same_url_even_when_cluster_differs(self, _mock_weights, _mock_signals):
        context = _make_context(
            news_articles=[
                {
                    **_make_news_article(
                        title="Same story variant A",
                        url="https://example.com/story-1",
                        topic_matches=["AI"],
                    ),
                    "cluster_id": "story:alpha",
                    "source_domain": "example.com",
                },
                {
                    **_make_news_article(
                        title="Same story variant B",
                        url="https://example.com/story-1",
                        topic_matches=["AI"],
                    ),
                    "cluster_id": "story:beta",
                    "source_domain": "example.com",
                },
            ]
        )

        selected = _select_news_for_generation(context)

        selected_urls = [a["url"] for a in selected["topic_articles"]]
        assert selected_urls.count("https://example.com/story-1") == 1

    @patch("agents.daily_briefing.executor.news_feeds.get_cluster_signal_map", return_value={})
    @patch(
        "agents.daily_briefing.executor.news_personalization.get_user_preference_weights",
        return_value=({}, {}),
    )
    def test_dedupes_urls_that_only_differ_by_tracking_params(self, _mock_weights, _mock_signals):
        context = _make_context(
            news_articles=[
                {
                    **_make_news_article(
                        title="Story canonical",
                        url="https://techcrunch.com/2026/03/15/story",
                        topic_matches=["AI"],
                    ),
                    "cluster_id": "story:one",
                    "source_domain": "techcrunch.com",
                },
                {
                    **_make_news_article(
                        title="Story tracked",
                        url="https://techcrunch.com/2026/03/15/story?guccounter=1&outputType=amp",
                        topic_matches=["AI"],
                    ),
                    "cluster_id": "story:two",
                    "source_domain": "techcrunch.com",
                },
            ]
        )

        selected = _select_news_for_generation(context)
        selected_urls = [a["url"] for a in selected["topic_articles"]]

        assert len(selected_urls) == 1

    @patch("agents.daily_briefing.executor.news_feeds.get_cluster_signal_map", return_value={})
    @patch(
        "agents.daily_briefing.executor.news_personalization.get_user_preference_weights",
        return_value=({}, {}),
    )
    def test_enforces_per_topic_hard_cap(self, _mock_weights, _mock_signals):
        relevance_blob = "alpha beta gamma delta epsilon zeta theta lambda"
        context = _make_context(
            events=[_make_event_context(title=relevance_blob)],
            news_articles=[
                {
                    **_make_news_article(
                        title=f"AI story {i}",
                        url=f"https://example.com/ai-{i}",
                        summary=f"{relevance_blob} update {i}",
                        source="reuters",
                        topic_matches=["AI"],
                    ),
                    "cluster_id": f"story:ai-{i}",
                    "source_domain": "example.com",
                }
                for i in range(12)
            ]
        )

        selected = _select_news_for_generation(context)

        assert len(selected["topic_articles"]) == 10
        assert all(article.get("topic_label") == "AI" for article in selected["topic_articles"])

    @patch("agents.daily_briefing.executor.news_feeds.get_cluster_signal_map", return_value={})
    @patch(
        "agents.daily_briefing.executor.news_personalization.get_user_preference_weights",
        return_value=({}, {}),
    )
    def test_guarantees_minimum_general_headlines(self, _mock_weights, _mock_signals):
        topic_articles = [
            {
                **_make_news_article(
                    title=f"AI top story {i}",
                    url=f"https://example.com/ai-{i}",
                    source="reuters",
                    topic_matches=["AI"],
                ),
                "cluster_id": f"story:ai-{i}",
                "source_domain": "example.com",
            }
            for i in range(10)
        ] + [
            {
                **_make_news_article(
                    title=f"Markets top story {i}",
                    url=f"https://example.com/markets-{i}",
                    source="reuters",
                    topic_matches=["Markets"],
                ),
                "cluster_id": f"story:markets-{i}",
                "source_domain": "example.com",
            }
            for i in range(10)
        ] + [
            {
                **_make_news_article(
                    title=f"Sports top story {i}",
                    url=f"https://example.com/sports-{i}",
                    source="reuters",
                    topic_matches=["Sports"],
                ),
                "cluster_id": f"story:sports-{i}",
                "source_domain": "example.com",
            }
            for i in range(10)
        ]

        general_articles = [
            {
                **_make_news_article(
                    title=f"General low-signal {i}",
                    url=f"https://example.com/general-{i}",
                    summary="Brief update.",
                    source="indie_blog",
                    topic_matches=[],
                ),
                "cluster_id": f"story:general-{i}",
                "source_domain": "example.com",
            }
            for i in range(3)
        ]

        context = _make_context(news_articles=[*topic_articles, *general_articles])

        selected = _select_news_for_generation(context)

        assert len(selected["general_articles"]) == 3


class TestNewsSummaryEnrichment:
    @patch("agents.daily_briefing.executor.call_llm")
    def test_uses_llm_summary_when_available(self, mock_llm):
        mock_llm.return_value = "Regulators approved a key ETF filing, which could accelerate institutional blockchain adoption."
        selected_news = {
            "topic_articles": [
                _make_news_article(
                    title="Regulator approves crypto ETF filing",
                    summary="A regulator advanced a major filing process.",
                    topic_matches=["Blockchain"],
                )
            ],
            "general_articles": [],
        }

        enriched = _enrich_selected_news_summaries(selected_news)

        assert enriched["topic_articles"][0]["brief_summary"].startswith("Regulators approved a key ETF")

    @patch("agents.daily_briefing.executor.call_llm", side_effect=Exception("timeout"))
    def test_falls_back_to_source_summary_on_failure(self, mock_llm):
        selected_news = {
            "topic_articles": [],
            "general_articles": [
                _make_news_article(
                    title="Studio reveals sequel timeline",
                    summary="The film franchise now has a release window and production schedule.",
                    topic_matches=[],
                )
            ],
        }

        enriched = _enrich_selected_news_summaries(selected_news)

        assert "release window" in enriched["general_articles"][0]["brief_summary"]


class TestOverallSummary:
    def test_summary_is_counts_only_without_weather(self):
        context = _make_context(
            events=[_make_event_context()],
            all_todos=[{"description": "Review report"}],
        )

        summary = _generate_summary(context, "# Daily Briefing")

        assert summary == "Today: 1 meeting and 1 pending todo."

    def test_summary_appends_weather_when_available(self):
        context = _make_context(
            events=[_make_event_context(), _make_event_context(title="Board Sync")],
            all_todos=[{"description": "Review report"}, {"description": "Send recap"}],
        )
        context["weather_summary"] = "Weather in Aurora: partly cloudy, 14C to 21C, rain chance up to 20%."

        summary = _generate_summary(context, "# Daily Briefing")

        assert summary == (
            "Today: 2 meetings and 2 pending todos. "
            "Weather in Aurora: partly cloudy, 14C to 21C, rain chance up to 20%."
        )

    def test_summary_does_not_include_news_content(self):
        context = _make_context(events=[_make_event_context()])
        context["selected_news"] = {
            "topic_articles": [_make_news_article(title="Topic A")],
            "general_articles": [_make_news_article(title="General C", topic_matches=[])],
        }

        summary = _generate_summary(context, "# Daily Briefing")

        assert "Topic A" not in summary
        assert "General C" not in summary


# ---------------------------------------------------------------------------
# Banned-phrase validation (via validators module)
# ---------------------------------------------------------------------------


class TestBannedPhraseValidation:
    """These tests previously covered _is_invalid_briefing; now they test the
    structural tier of the validators module directly."""

    def test_rejects_generic_category_language(self):
        from agents.daily_briefing.validators import validate_briefing

        bad = "# Daily Briefing - 2026-02-15\n## News\nThere are several articles on various topics"
        assert not validate_briefing(bad, {}).valid

    def test_rejects_if_youd_like(self):
        from agents.daily_briefing.validators import validate_briefing

        bad = "# Daily Briefing - 2026-02-15\nIf you'd like me to extract specific information"
        assert not validate_briefing(bad, {}).valid

    def test_rejects_none_mentioned_explicitly(self):
        from agents.daily_briefing.validators import validate_briefing

        bad = "# Daily Briefing - 2026-02-15\nNone mentioned explicitly, but there are news"
        assert not validate_briefing(bad, {}).valid

    def test_rejects_eg_pattern(self):
        from agents.daily_briefing.validators import validate_briefing

        bad = "# Daily Briefing - 2026-02-15\n- AI (e.g., machine learning)"
        assert not validate_briefing(bad, {}).valid

    def test_accepts_valid_briefing(self):
        from unittest.mock import patch

        from agents.daily_briefing.validators import validate_briefing

        good = (
            "# Daily Briefing - 2026-02-15 (UTC)\n"
            "## Day Overview\nQuiet day with one meeting.\n"
            "## Outstanding Todos\n- Review budget proposal\n"
            "## News & Topics\n"
            "- [OpenAI releases GPT-5](https://example.com) - Major model upgrade. (TechCrunch)\n"
        )
        with patch("llm_helpers.call_llm", return_value="PASS"):
            assert validate_briefing(good, {}).valid
