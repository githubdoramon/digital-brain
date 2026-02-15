"""Tests for daily briefing executor – per-event analysis, research & birthdays."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from agents.daily_briefing.executor import (
    BIRTHDAY_LOOKAHEAD_DAYS,
    _build_briefing_prompt,
    _format_context_text,
    _format_event_for_analysis,
    _research_event,
    _summarize_event,
    _synthesise_event_summary,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_event_context(
    *,
    title: str = "Team Standup",
    deep_summary: str = "",
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
        "deep_summary": deep_summary,
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
# _format_context_text – deep summary integration
# ---------------------------------------------------------------------------


class TestFormatContextWithDeepSummary:
    def test_deep_summary_included_in_context(self):
        event = _make_event_context(deep_summary="KEY POINTS:\n- Past standup went long")
        ctx = _make_context(events=[event])
        text = _format_context_text(ctx)
        assert "Analysis (key points, action items, prep focus):" in text
        assert "Past standup went long" in text

    def test_fallback_when_deep_summary_empty(self):
        event = _make_event_context(
            deep_summary="",
            summary="We discussed Q1 roadmap",
            todos=[{"status": "pending", "description": "Follow up on Q1"}],
        )
        ctx = _make_context(events=[event])
        text = _format_context_text(ctx)
        assert "Context from prior notes" in text
        assert "Follow up on Q1" in text
        # deep summary header should NOT appear
        assert "Analysis (key points" not in text

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
        assert "pre-computed analysis" in prompt


# ---------------------------------------------------------------------------
# _format_event_for_analysis – event text block
# ---------------------------------------------------------------------------


class TestFormatEventForAnalysis:
    def test_includes_title_and_time(self):
        event = _make_event_context(title="Board Meeting")
        text = _format_event_for_analysis(event)
        assert "Event: Board Meeting" in text
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
        assert "Past occurrences" in text
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
    def test_returns_research_content(self, mock_build_profile, mock_tool_loop):
        """Research loop returns useful findings."""
        mock_profile = MagicMock()
        mock_profile.build_tools_and_handlers.return_value = (
            [{"type": "function", "function": {"name": "web_search"}}],
            {"web_search": lambda args: {}},
        )
        mock_profile.runtime = MagicMock()
        mock_profile.get_system_prompt.return_value = "system"
        mock_build_profile.return_value = mock_profile

        mock_tool_loop.return_value = {
            "content": "- Acme Corp raised $50M Series B\n- Source: https://example.com"
        }

        result = _research_event("Event: Acme Intro Call", "Acme Intro Call", "UTC")
        assert "Acme Corp" in result
        assert "example.com" in result
        mock_tool_loop.assert_called_once()

    @patch("agents.daily_briefing.executor.run_profiled_tool_loop")
    @patch("agents.daily_briefing.executor.build_event_research_profile")
    def test_no_research_needed_returns_empty(self, mock_build_profile, mock_tool_loop):
        """When the LLM decides no research is needed, return empty string."""
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
    def test_graceful_failure(self, mock_build_profile, mock_tool_loop):
        """Research failures should not crash the pipeline."""
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


# ---------------------------------------------------------------------------
# _synthesise_event_summary – final synthesis with research
# ---------------------------------------------------------------------------


class TestSynthesiseEventSummary:
    @patch("agents.daily_briefing.executor.call_llm")
    def test_includes_research_in_prompt(self, mock_call_llm):
        mock_call_llm.return_value = "KEY POINTS:\n- Acme raised funding"
        result = _synthesise_event_summary(
            "Event: Acme Call",
            "- Acme raised $50M\n- Source: https://example.com",
            "Acme Call",
            "UTC",
        )
        assert "Acme raised" in result
        # Verify research was injected into the prompt
        prompt = mock_call_llm.call_args[0][0]
        assert "WEB RESEARCH FINDINGS" in prompt
        assert "Acme raised $50M" in prompt

    @patch("agents.daily_briefing.executor.call_llm")
    def test_no_research_section_when_empty(self, mock_call_llm):
        mock_call_llm.return_value = "KEY POINTS:\n- Routine standup"
        _synthesise_event_summary("Event: Standup", "", "Standup", "UTC")
        prompt = mock_call_llm.call_args[0][0]
        assert "WEB RESEARCH FINDINGS" not in prompt

    @patch("agents.daily_briefing.executor.call_llm")
    def test_prompt_requests_suggested_reading(self, mock_call_llm):
        mock_call_llm.return_value = "SUGGESTED READING:\n- https://example.com"
        _synthesise_event_summary("Event: Conf", "- agenda link", "Conf", "UTC")
        prompt = mock_call_llm.call_args[0][0]
        assert "SUGGESTED READING" in prompt

    @patch("agents.daily_briefing.executor.call_llm", side_effect=Exception("LLM down"))
    def test_returns_empty_on_failure(self, mock_call_llm):
        result = _synthesise_event_summary("Event: X", "", "X", "UTC")
        assert result == ""


# ---------------------------------------------------------------------------
# _summarize_event – full two-phase pipeline
# ---------------------------------------------------------------------------


class TestSummarizeEvent:
    @patch("agents.daily_briefing.executor._synthesise_event_summary")
    @patch("agents.daily_briefing.executor._research_event")
    def test_calls_research_then_synthesis(self, mock_research, mock_synthesise):
        mock_research.return_value = "- Found company info"
        mock_synthesise.return_value = "KEY POINTS:\n- Important context"
        event = _make_event_context(title="Intro Call")
        result = _summarize_event(event, "UTC")

        mock_research.assert_called_once()
        mock_synthesise.assert_called_once()
        # Research output should be passed to synthesis
        synth_args = mock_synthesise.call_args
        assert synth_args[0][1] == "- Found company info"  # research_notes arg
        assert "KEY POINTS" in result

    @patch("agents.daily_briefing.executor._synthesise_event_summary")
    @patch("agents.daily_briefing.executor._research_event")
    def test_passes_empty_research_to_synthesis(self, mock_research, mock_synthesise):
        mock_research.return_value = ""
        mock_synthesise.return_value = "PREP FOCUS:\n- Review agenda"
        event = _make_event_context(title="Standup")
        _summarize_event(event, "UTC")

        synth_args = mock_synthesise.call_args
        assert synth_args[0][1] == ""  # empty research


# ---------------------------------------------------------------------------
# BIRTHDAY_LOOKAHEAD_DAYS constant
# ---------------------------------------------------------------------------


def test_birthday_lookahead_is_seven():
    assert BIRTHDAY_LOOKAHEAD_DAYS == 7


# ---------------------------------------------------------------------------
# _format_context_text – news articles section
# ---------------------------------------------------------------------------


class TestFormatContextNews:
    def test_topic_matched_news_included(self):
        articles = [
            _make_news_article(
                title="OpenAI releases GPT-5",
                url="https://example.com/gpt5",
                source="tavily",
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

    def test_general_headlines_capped(self):
        """General headlines should be capped to avoid context bloat."""
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
        # We cap at 15 general headlines
        assert "General Headlines (25)" in text
        assert "Headline 14" in text  # 0-indexed, the 15th item
        assert "Headline 15" not in text  # 16th should be excluded


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

    def test_prompt_instructs_markdown_links(self):
        articles = [_make_news_article(topic_matches=["AI"])]
        ctx = _make_context(news_articles=articles)
        prompt = _build_briefing_prompt(ctx)
        assert "markdown link" in prompt.lower()

    def test_prompt_emphasises_summaries(self):
        articles = [_make_news_article(topic_matches=["AI"])]
        ctx = _make_context(news_articles=articles)
        prompt = _build_briefing_prompt(ctx)
        assert "MUST include a brief summary" in prompt

    def test_prompt_omits_news_section_when_empty(self):
        ctx = _make_context(news_articles=[])
        prompt = _build_briefing_prompt(ctx)
        assert "## News & Topics" not in prompt
        assert "News & Topics section" not in prompt
