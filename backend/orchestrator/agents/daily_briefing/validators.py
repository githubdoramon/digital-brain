"""Daily briefing post-generation validation.

Three-tier validation pipeline:
  Tier 1 – Structural: header, required sections, length, banned phrases
  Tier 2 – Coherence: event titles present, news links present, no raw artifacts
  Tier 3 – LLM judge: fast-model call to catch subtle quality issues
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from search_normalization import normalize_search_text

logger = logging.getLogger(__name__)

_TITLE_TOKEN_STOPWORDS = {
    "a",
    "an",
    "and",
    "the",
    "with",
    "for",
    "to",
    "of",
    "in",
    "on",
    "at",
    "by",
}

# ---------------------------------------------------------------------------
# Validation result
# ---------------------------------------------------------------------------

TIER_STRUCTURAL = "structural"
TIER_COHERENCE = "coherence"
TIER_LLM_JUDGE = "llm_judge"


@dataclass
class ValidationResult:
    """Outcome of a briefing validation run."""

    valid: bool
    tier: str = ""  # tier that triggered failure (empty if valid)
    reasons: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Banned phrases
# ---------------------------------------------------------------------------

# Original meta-commentary phrases
_BANNED_META = [
    "it appears",
    "you provided",
    "the text",
    "to help you",
    "let me know",
    "clarify",
    "if you have",
    "if you'd like",
    "if you would like",
    "there are several",
    "none mentioned explicitly",
    "please let me know",
    "the text includes",
    "various topics",
    "including ai,",
    "e.g.,",
    "and more.",
    "extract specific information",
]

# Thinking / reasoning leak phrases
_BANNED_THINKING = [
    "<thinking>",
    "</thinking>",
    "let me think",
    "let me analyze",
    "let me consider",
    "let me review",
    "let me process",
    "let me examine",
    "let me look at",
    "let me break",
    "let me start by",
    "step 1:",
    "step 2:",
    "step 3:",
    "first, i'll",
    "first, i will",
    "next, i'll",
    "next, i will",
    "here's my plan",
    "here is my plan",
    "my approach",
    "i need to",
    "i should",
    "i'll start",
    "i will start",
    "i'll now",
    "i will now",
    "i'll analyze",
    "i'll review",
    "i'll examine",
    "let's start",
    "let's begin",
    "let's analyze",
    "now let me",
    "now i'll",
    "now i need",
    "looking at the",
    "based on the data provided",
    "based on the information provided",
    "based on what was provided",
    "the user has",
    "the user's",
    "i can see that",
    "i notice that",
    "i can help",
]

_ALL_BANNED = _BANNED_META + _BANNED_THINKING

# Artifact patterns (raw tool call / JSON leaks)
_ARTIFACT_PATTERNS = [
    r'\{"tool_call"',
    r'"function_call"',
    r"<tool_use>",
    r"</tool_use>",
    r"<tool_result>",
    r"<result>",
    r'\{"name"\s*:\s*"(search_memories|get_document|web_search|fetch_web_page)"',
]

# Required sections when events exist
_REQUIRED_SECTIONS_WITH_EVENTS = [
    "## Day Overview",
    "## Schedule",
    "## Event Prep",
    "## Outstanding Todos",
]

# Required sections when no events
_REQUIRED_SECTIONS_NO_EVENTS = [
    "## Day Overview",
    "## Outstanding Todos",
]

# Minimum content length (chars) when events exist
_MIN_LENGTH_WITH_EVENTS = 200
_MIN_LENGTH_NO_EVENTS = 100


# ---------------------------------------------------------------------------
# Tier 1: Structural checks
# ---------------------------------------------------------------------------


def _validate_structural(content: str, context: dict[str, Any]) -> ValidationResult:
    """Fast deterministic checks: header, sections, length, banned phrases."""
    reasons: list[str] = []
    stripped = content.strip()

    # Header check
    if not stripped.startswith("# Daily Briefing"):
        reasons.append("Missing required header: must start with '# Daily Briefing'")

    # Required sections
    has_events = bool(context.get("events"))
    required = _REQUIRED_SECTIONS_WITH_EVENTS if has_events else _REQUIRED_SECTIONS_NO_EVENTS
    for section in required:
        if section not in content:
            reasons.append(f"Missing required section: {section}")

    # Length check
    min_len = _MIN_LENGTH_WITH_EVENTS if has_events else _MIN_LENGTH_NO_EVENTS
    if len(stripped) < min_len:
        reasons.append(f"Content too short ({len(stripped)} chars, minimum {min_len})")

    # Banned phrases
    lower = content.lower()
    for phrase in _ALL_BANNED:
        if phrase in lower:
            reasons.append(f"Banned phrase found: '{phrase}'")
            break  # one is enough to trigger rewrite

    if reasons:
        return ValidationResult(valid=False, tier=TIER_STRUCTURAL, reasons=reasons)
    return ValidationResult(valid=True)


# ---------------------------------------------------------------------------
# Tier 2: Coherence checks
# ---------------------------------------------------------------------------


def _validate_coherence(content: str, context: dict[str, Any]) -> ValidationResult:
    """Check that output actually reflects the input context."""
    reasons: list[str] = []
    lower = content.lower()

    # If events exist, at least one event title should appear
    events = context.get("events") or []
    if events:
        titles = [e.get("title", "") for e in events if e.get("title")]
        if titles and not any(t.lower() in lower for t in titles):
            # Check partial matches (first 3 significant words)
            partial_match = False
            for t in titles:
                words = [w for w in t.lower().split() if len(w) > 2][:3]
                if words and all(w in lower for w in words):
                    partial_match = True
                    break
            if not partial_match:
                reasons.append("No event titles from the context appear in the output")

    # If news articles exist and News section is expected, check for links
    news = context.get("news_articles") or []
    if news and "## News" in content:
        # Find the news section content
        news_section_match = re.search(r"## News.*?\n(.*?)(?=\n## |\Z)", content, re.DOTALL)
        if news_section_match:
            news_text = news_section_match.group(1)
            if "](http" not in news_text and "no notable news" not in news_text.lower():
                reasons.append("News section exists but contains no article links")

    # Check for raw artifacts / tool call leaks
    for pattern in _ARTIFACT_PATTERNS:
        if re.search(pattern, content, re.IGNORECASE):
            reasons.append(f"Raw artifact detected: {pattern}")
            break

    if reasons:
        return ValidationResult(valid=False, tier=TIER_COHERENCE, reasons=reasons)
    return ValidationResult(valid=True)


# ---------------------------------------------------------------------------
# Tier 3: LLM judge
# ---------------------------------------------------------------------------


def _validate_llm_judge(content: str) -> ValidationResult:
    """Use a fast LLM call to catch subtle quality issues."""
    from llm_helpers import call_llm

    judge_prompt = (
        "You are a quality checker for a daily briefing document.\n"
        "Evaluate whether the following text is a properly formatted daily briefing.\n"
        "\n"
        "A VALID briefing:\n"
        "- Is written in direct, practical tone about upcoming events\n"
        "- Contains markdown sections (Day Overview, Schedule, Event Prep, etc.)\n"
        "- Provides actionable information about the user's day\n"
        "\n"
        "An INVALID briefing contains ANY of these:\n"
        "- Internal reasoning, thinking steps, or chain-of-thought (e.g. 'Let me analyze...', 'Step 1:', 'First I'll...')\n"
        "- Meta-commentary about input data (e.g. 'The text includes...', 'Based on what was provided...')\n"
        "- Conversational filler or offers to help (e.g. 'Let me know if...', 'I can help...')\n"
        "- Raw JSON, tool calls, or system artifacts\n"
        "- Generic placeholder content instead of specific information\n"
        "- Preamble or explanation before the actual briefing content\n"
        "\n"
        "Respond with EXACTLY one line:\n"
        "PASS\n"
        "or\n"
        "FAIL: <brief reason>\n"
        "\n"
        "---\n"
        f"{content[:3000]}"  # cap to avoid excessive token use
    )
    try:
        result = call_llm(
            judge_prompt,
            system_prompt="You are a strict document quality judge. Respond with PASS or FAIL: <reason>. Nothing else.",
            temperature=0,
            max_tokens=80,
            use_fast_model=True,
        )
        result = result.strip()
        if result.upper().startswith("PASS"):
            return ValidationResult(valid=True)
        # Extract reason from "FAIL: <reason>"
        reason = result
        if ":" in result:
            reason = result.split(":", 1)[1].strip()
        return ValidationResult(
            valid=False,
            tier=TIER_LLM_JUDGE,
            reasons=[f"LLM judge: {reason}"],
        )
    except Exception:
        logger.warning(
            "[briefing-validator] LLM judge call failed, passing by default", exc_info=True
        )
        return ValidationResult(valid=True)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def validate_briefing(content: str, context: dict[str, Any]) -> ValidationResult:
    """Run the full three-tier validation pipeline.

    Tiers run in order; first failure short-circuits.
    """
    # Tier 1: Structural
    result = _validate_structural(content, context)
    if not result.valid:
        logger.info("[briefing-validator] Structural failure: %s", result.reasons)
        return result

    # Tier 2: Coherence
    result = _validate_coherence(content, context)
    if not result.valid:
        logger.info("[briefing-validator] Coherence failure: %s", result.reasons)
        return result

    # Tier 3: LLM judge
    result = _validate_llm_judge(content)
    if not result.valid:
        logger.info("[briefing-validator] LLM judge failure: %s", result.reasons)
        return result

    return ValidationResult(valid=True)


def validate_summary(summary: str) -> ValidationResult:
    """Quick validation for the 1-2 sentence summary."""
    reasons: list[str] = []

    if len(summary) > 500:
        reasons.append(f"Summary too long ({len(summary)} chars, max 500)")

    if summary.strip().startswith("#"):
        reasons.append("Summary contains markdown headers")

    lower = summary.lower()
    for phrase in _BANNED_THINKING[:15]:  # check key thinking phrases
        if phrase in lower:
            reasons.append(f"Summary contains thinking pattern: '{phrase}'")
            break

    if reasons:
        return ValidationResult(valid=False, tier=TIER_STRUCTURAL, reasons=reasons)
    return ValidationResult(valid=True)


def validate_event_sections(content: str, context: dict[str, Any]) -> ValidationResult:
    """Validate only the event-critical sections.

    This check intentionally ignores global document structure and focuses on:
    - Day Overview presence
    - Schedule/Event Prep presence when events exist
    - Event titles represented in Event Prep
    """
    reasons: list[str] = []
    has_events = bool(context.get("events"))

    if "## Day Overview" not in content:
        reasons.append("Missing section: ## Day Overview")

    if has_events:
        if "## Schedule" not in content:
            reasons.append("Missing section: ## Schedule")
        if "## Event Prep" not in content:
            reasons.append("Missing section: ## Event Prep")

        normalized_content = normalize_search_text(content)
        for event in context.get("events") or []:
            title = str(event.get("title") or "").strip()
            if not title:
                continue
            if not _event_title_present_in_output(title, normalized_content):
                reasons.append(f"Event title missing from output: {title}")
                break

    if reasons:
        return ValidationResult(valid=False, tier=TIER_COHERENCE, reasons=reasons)
    return ValidationResult(valid=True)


def _event_title_present_in_output(title: str, normalized_content: str) -> bool:
    normalized_title = normalize_search_text(title)
    if not normalized_title:
        return True

    if normalized_title in normalized_content:
        return True

    title_tokens = [
        token
        for token in re.split(r"[^a-z0-9]+", normalized_title)
        if token and token not in _TITLE_TOKEN_STOPWORDS
    ]
    if not title_tokens:
        return True

    matched = sum(1 for token in title_tokens if token in normalized_content)
    if len(title_tokens) == 1:
        return matched == 1
    required = max(1, int(len(title_tokens) * 0.6))
    return matched >= required


def validate_news_section(section_markdown: str, has_news_input: bool) -> ValidationResult:
    """Validate the news section format for title/link/summary quality."""
    text = (section_markdown or "").strip()
    if not has_news_input:
        return ValidationResult(valid=True)
    if not text:
        return ValidationResult(
            valid=False,
            tier=TIER_COHERENCE,
            reasons=["News input exists but news section is empty"],
        )
    if "## News & Topics" not in text:
        return ValidationResult(
            valid=False,
            tier=TIER_COHERENCE,
            reasons=["Missing section header: ## News & Topics"],
        )

    lowered = text.lower()
    if "no notable news today." in lowered:
        return ValidationResult(valid=True)

    article_pattern = re.compile(r"\[[^\]]+\]\(https?://[^)]+\)\s*-\s*.+\s*\([^)]+\)")
    article_lines = [
        line.strip() for line in text.splitlines() if "[" in line and "](" in line and " - " in line
    ]
    if not article_lines:
        return ValidationResult(
            valid=False,
            tier=TIER_COHERENCE,
            reasons=["No article lines found in news section"],
        )
    for line in article_lines:
        if not article_pattern.search(line):
            return ValidationResult(
                valid=False,
                tier=TIER_COHERENCE,
                reasons=[f"Invalid news line format: {line[:120]}. Corrent format is [Title](URL) - Summary (Source), in markdown format."],
            )
    return ValidationResult(valid=True)
