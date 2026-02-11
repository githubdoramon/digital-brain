"""Shared tool visibility and escalation policy for bounded agents."""

from __future__ import annotations

from typing import Any

from .enums import ConfidenceTier, ToolVisibilityMode
from .limits import LimitType


def confidence_tier(
    confidence: float,
    *,
    high_threshold: float,
    medium_threshold: float,
) -> ConfidenceTier:
    """Map routing confidence to high/medium/low tier."""
    if confidence >= high_threshold:
        return ConfidenceTier.HIGH
    if confidence >= medium_threshold:
        return ConfidenceTier.MEDIUM
    return ConfidenceTier.LOW


def resolve_tool_visibility(
    *,
    tool_registry: Any,
    classification: Any | None,
    restriction_mode: str,
    high_threshold: float,
    medium_threshold: float,
) -> tuple[list[dict[str, Any]], ToolVisibilityMode, list[str]]:
    """Choose visible tools based on routing confidence and restriction policy."""
    all_tools = tool_registry.get_tool_definitions()
    all_groups = list(tool_registry.list_groups())
    if classification is None:
        return all_tools, ToolVisibilityMode.FULL, all_groups

    tier = confidence_tier(
        float(classification.confidence),
        high_threshold=high_threshold,
        medium_threshold=medium_threshold,
    )
    state_groups = list(classification.allowed_tool_groups or [])

    if restriction_mode != "conservative":
        selected_groups = list(dict.fromkeys(state_groups))
        tool_defs = tool_registry.get_tool_definitions_for_groups(selected_groups)
        if selected_groups and tool_defs:
            return tool_defs, ToolVisibilityMode.RESTRICTED, selected_groups
        return all_tools, ToolVisibilityMode.FULL, all_groups

    if tier is ConfidenceTier.HIGH:
        selected_groups = list(dict.fromkeys(state_groups))
        tool_defs = tool_registry.get_tool_definitions_for_groups(selected_groups)
        if selected_groups and tool_defs:
            return tool_defs, ToolVisibilityMode.RESTRICTED, selected_groups
        if not selected_groups:
            return [], ToolVisibilityMode.NONE, []
        return all_tools, ToolVisibilityMode.FULL, all_groups

    if tier is ConfidenceTier.MEDIUM:
        selected_groups = list(dict.fromkeys([*state_groups, "resolution"]))
        tool_defs = tool_registry.get_tool_definitions_for_groups(selected_groups)
        if tool_defs:
            return tool_defs, ToolVisibilityMode.RESTRICTED_WITH_RESOLUTION, selected_groups
        return all_tools, ToolVisibilityMode.FULL, all_groups

    return all_tools, ToolVisibilityMode.FULL, all_groups


def should_escalate_tool_visibility(
    *,
    state: Any,
    violation: Any | None,
) -> bool:
    """Decide when restricted tool visibility should widen to full tools."""
    if state.tool_visibility_mode == ToolVisibilityMode.FULL.value:
        return False
    if state.tool_visibility_escalated:
        return False

    if violation is not None and getattr(violation, "limit_type", None) in {
        LimitType.NO_PROGRESS_EMPTY,
        LimitType.NO_PROGRESS_REPEATED,
    }:
        return True

    recent_calls = state.get_recent_tool_calls(2)
    if len(recent_calls) < 2:
        return False

    def _is_failed_or_empty(call: Any) -> bool:
        if not getattr(call, "success", False):
            return True
        result = getattr(call, "result", {}) or {}
        return state._is_empty_result(result)

    return all(_is_failed_or_empty(call) for call in recent_calls)
