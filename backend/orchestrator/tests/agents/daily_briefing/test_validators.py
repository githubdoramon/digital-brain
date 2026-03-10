"""Tests for daily briefing three-tier validation pipeline."""

from __future__ import annotations

from unittest.mock import patch

from agents.daily_briefing.validators import (
    TIER_COHERENCE,
    TIER_LLM_JUDGE,
    TIER_STRUCTURAL,
    ValidationResult,
    _validate_coherence,
    _validate_llm_judge,
    _validate_structural,
    validate_briefing,
    validate_event_sections,
    validate_summary,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

VALID_BRIEFING = (
    "# Daily Briefing - 2026-02-15 (UTC)\n"
    "## Day Overview\n"
    "Busy day with 3 meetings and 2 pending todos.\n"
    "## Schedule\n"
    "- 09:00 - Team Standup (Office)\n"
    "- 11:00 - Product Review\n"
    "- 14:00 - Client Call\n"
    "## Event Prep\n"
    "### 09:00 - Team Standup\n"
    "- Review sprint progress\n"
    "### 11:00 - Product Review\n"
    "- Prepare demo for new feature\n"
    "### 14:00 - Client Call\n"
    "- Discuss Q1 deliverables\n"
    "## Outstanding Todos\n"
    "- Send Q1 report to finance\n"
    "- Update roadmap document\n"
)

VALID_BRIEFING_WITH_NEWS = (
    VALID_BRIEFING + "## News & Topics\n"
    "- [OpenAI releases GPT-5](https://example.com/gpt5) - Major model upgrade. (TechCrunch)\n"
)


def _ctx(events=None, news=None):
    return {
        "date": "2026-02-15",
        "timezone": "UTC",
        "events": events or [],
        "news_articles": news or [],
    }


def _event(title="Team Standup"):
    return {"title": title, "id": "evt_1"}


def _news(title="AI News", url="https://example.com/ai"):
    return {"title": title, "url": url, "summary": "Big AI news", "source": "hacker_news"}


# ---------------------------------------------------------------------------
# Tier 1: Structural
# ---------------------------------------------------------------------------


class TestStructuralValidation:
    def test_valid_briefing_passes(self):
        result = _validate_structural(VALID_BRIEFING, _ctx(events=[_event()]))
        assert result.valid

    def test_missing_header_fails(self):
        bad = "## Day Overview\nSome content.\n## Outstanding Todos\n- stuff"
        result = _validate_structural(bad, _ctx())
        assert not result.valid
        assert result.tier == TIER_STRUCTURAL
        assert any("header" in r.lower() for r in result.reasons)

    def test_missing_required_section_with_events(self):
        bad = "# Daily Briefing - 2026-02-15\n## Day Overview\nSome content\n"
        result = _validate_structural(bad, _ctx(events=[_event()]))
        assert not result.valid
        assert any(
            "## Schedule" in r or "## Event Prep" in r or "## Outstanding Todos" in r
            for r in result.reasons
        )

    def test_no_events_relaxes_section_requirements(self):
        brief = (
            "# Daily Briefing - 2026-02-15 (UTC)\n"
            "## Day Overview\n"
            "No events scheduled today.\n"
            "## Outstanding Todos\n"
            "- Review Q1 budget\n"
        )
        result = _validate_structural(brief, _ctx(events=[]))
        assert result.valid

    def test_too_short_with_events(self):
        bad = (
            "# Daily Briefing\n## Day Overview\n## Schedule\n## Event Prep\n## Outstanding Todos\n"
        )
        result = _validate_structural(bad, _ctx(events=[_event()]))
        assert not result.valid
        assert any("short" in r.lower() for r in result.reasons)

    def test_meta_commentary_banned(self):
        bad = VALID_BRIEFING + "\nIt appears the user has several meetings."
        result = _validate_structural(bad, _ctx(events=[_event()]))
        assert not result.valid
        assert any("banned" in r.lower() for r in result.reasons)

    def test_thinking_pattern_banned(self):
        bad = "# Daily Briefing - 2026-02-15\nLet me analyze the events.\n## Day Overview\nStuff.\n## Schedule\n- stuff\n## Event Prep\n- stuff\n## Outstanding Todos\n- stuff\nmore content to meet length"
        result = _validate_structural(bad, _ctx(events=[_event()]))
        assert not result.valid
        assert any("banned" in r.lower() or "thinking" in r.lower() for r in result.reasons)

    def test_thinking_tag_banned(self):
        bad = VALID_BRIEFING.replace(
            "## Day Overview", "<thinking>\nI need to process this.\n</thinking>\n## Day Overview"
        )
        result = _validate_structural(bad, _ctx(events=[_event()]))
        assert not result.valid

    def test_step_by_step_banned(self):
        bad = VALID_BRIEFING.replace(
            "## Day Overview", "Step 1: Review events\nStep 2: Create briefing\n## Day Overview"
        )
        result = _validate_structural(bad, _ctx(events=[_event()]))
        assert not result.valid

    def test_first_ill_banned(self):
        bad = VALID_BRIEFING.replace(
            "## Day Overview", "First, I'll review the schedule.\n## Day Overview"
        )
        result = _validate_structural(bad, _ctx(events=[_event()]))
        assert not result.valid

    def test_i_need_to_banned(self):
        bad = VALID_BRIEFING.replace("Busy day", "I need to create a briefing. Busy day")
        result = _validate_structural(bad, _ctx(events=[_event()]))
        assert not result.valid


# ---------------------------------------------------------------------------
# Tier 2: Coherence
# ---------------------------------------------------------------------------


class TestCoherenceValidation:
    def test_valid_briefing_passes(self):
        result = _validate_coherence(VALID_BRIEFING, _ctx(events=[_event("Team Standup")]))
        assert result.valid

    def test_missing_event_title_fails(self):
        brief = VALID_BRIEFING.replace("Team Standup", "Something Else")
        result = _validate_coherence(brief, _ctx(events=[_event("Board Meeting")]))
        assert not result.valid
        assert result.tier == TIER_COHERENCE
        assert any("event titles" in r.lower() for r in result.reasons)

    def test_partial_title_match_passes(self):
        """Partial word overlap should be accepted."""
        brief = VALID_BRIEFING.replace("Team Standup", "Weekly Team Discussion")
        result = _validate_coherence(
            brief, _ctx(events=[_event("Weekly Team Discussion and Review")])
        )
        assert result.valid

    def test_news_section_without_links_fails(self):
        brief = VALID_BRIEFING + "## News & Topics\nSome generic news about technology.\n"
        result = _validate_coherence(brief, _ctx(events=[_event()], news=[_news()]))
        assert not result.valid
        assert any("links" in r.lower() for r in result.reasons)

    def test_news_section_with_links_passes(self):
        result = _validate_coherence(
            VALID_BRIEFING_WITH_NEWS,
            _ctx(events=[_event()], news=[_news()]),
        )
        assert result.valid

    def test_news_no_notable_passes(self):
        brief = VALID_BRIEFING + "## News & Topics\nNo notable news today.\n"
        result = _validate_coherence(brief, _ctx(events=[_event()], news=[_news()]))
        assert result.valid

    def test_raw_json_artifact_fails(self):
        bad = VALID_BRIEFING + '\n{"tool_call": "search_memories"}\n'
        result = _validate_coherence(bad, _ctx(events=[_event()]))
        assert not result.valid
        assert any("artifact" in r.lower() for r in result.reasons)

    def test_tool_use_tags_fail(self):
        bad = VALID_BRIEFING + "\n<tool_use>web_search</tool_use>\n"
        result = _validate_coherence(bad, _ctx(events=[_event()]))
        assert not result.valid

    def test_no_events_no_title_check(self):
        brief = (
            "# Daily Briefing - 2026-02-15\n"
            "## Day Overview\nNo events.\n"
            "## Outstanding Todos\n- Review budget\n"
        )
        result = _validate_coherence(brief, _ctx(events=[]))
        assert result.valid


# ---------------------------------------------------------------------------
# Tier 3: LLM judge
# ---------------------------------------------------------------------------


class TestLLMJudge:
    @patch("llm_helpers.call_llm")
    def test_pass_verdict(self, mock_llm):
        mock_llm.return_value = "PASS"
        result = _validate_llm_judge(VALID_BRIEFING)
        assert result.valid

    @patch("llm_helpers.call_llm")
    def test_fail_verdict(self, mock_llm):
        mock_llm.return_value = "FAIL: Contains reasoning steps before the briefing"
        result = _validate_llm_judge("Let me think about this.\n# Daily Briefing\n...")
        assert not result.valid
        assert result.tier == TIER_LLM_JUDGE
        assert "reasoning" in result.reasons[0].lower()

    @patch("llm_helpers.call_llm", side_effect=Exception("timeout"))
    def test_graceful_failure_passes_by_default(self, mock_llm):
        result = _validate_llm_judge(VALID_BRIEFING)
        assert result.valid


# ---------------------------------------------------------------------------
# Full pipeline: validate_briefing
# ---------------------------------------------------------------------------


class TestValidateBriefing:
    def test_valid_briefing_passes_all_tiers(self):
        with patch("llm_helpers.call_llm", return_value="PASS"):
            result = validate_briefing(VALID_BRIEFING, _ctx(events=[_event()]))
        assert result.valid

    def test_structural_failure_short_circuits(self):
        """Structural failure should not call LLM judge."""
        bad = "Not a briefing at all."
        with patch("llm_helpers.call_llm") as mock_llm:
            result = validate_briefing(bad, _ctx())
        assert not result.valid
        assert result.tier == TIER_STRUCTURAL
        mock_llm.assert_not_called()

    def test_coherence_failure_short_circuits(self):
        """Coherence failure should not call LLM judge."""
        brief = VALID_BRIEFING + '\n{"tool_call": "search_memories"}\n'
        with patch("llm_helpers.call_llm") as mock_llm:
            result = validate_briefing(brief, _ctx(events=[_event()]))
        assert not result.valid
        assert result.tier == TIER_COHERENCE
        mock_llm.assert_not_called()


# ---------------------------------------------------------------------------
# validate_summary
# ---------------------------------------------------------------------------


class TestValidateSummary:
    def test_valid_summary_passes(self):
        result = validate_summary("You have 3 meetings today and 2 pending todos.")
        assert result.valid

    def test_too_long_fails(self):
        result = validate_summary("x" * 501)
        assert not result.valid
        assert any("long" in r.lower() for r in result.reasons)

    def test_markdown_header_fails(self):
        result = validate_summary("# Summary\nYou have meetings.")
        assert not result.valid
        assert any("header" in r.lower() for r in result.reasons)

    def test_thinking_pattern_fails(self):
        result = validate_summary("Let me think about your day. You have 3 meetings.")
        assert not result.valid
        assert any("thinking" in r.lower() for r in result.reasons)

    def test_short_clean_summary_passes(self):
        result = validate_summary("Quiet day with no meetings. 1 pending todo to review.")
        assert result.valid


class TestValidateEventSections:
    def test_accepts_schedule_titles_without_exact_raw_substring(self):
        content = (
            "## Day Overview\n"
            "- You have three meetings scheduled today.\n"
            "## Schedule\n"
            "- **10:30 - 10:55** - 1:1 with Sean\n"
            "- **11:30 - 11:55** - 1:1 with Seb\n"
            "- **15:00 - 16:00** - Leadership weekly\n"
            "## Event Prep\n"
            "### 10:30 - 1:1 with Sean\n"
            "- Discuss hiring blockers\n"
        )
        context = _ctx(
            events=[
                _event("1:1 with Sean"),
                _event("1:1 with Seb"),
                _event("Leadership weekly"),
            ]
        )
        result = validate_event_sections(content, context)
        assert result.valid


# ---------------------------------------------------------------------------
# ValidationResult dataclass
# ---------------------------------------------------------------------------


class TestValidationResult:
    def test_defaults(self):
        r = ValidationResult(valid=True)
        assert r.tier == ""
        assert r.reasons == []

    def test_with_failure(self):
        r = ValidationResult(valid=False, tier=TIER_STRUCTURAL, reasons=["Missing header"])
        assert not r.valid
        assert r.tier == TIER_STRUCTURAL
        assert len(r.reasons) == 1
