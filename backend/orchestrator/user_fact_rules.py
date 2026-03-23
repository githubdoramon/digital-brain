"""Enums and helpers for user fact hard-rule semantics."""

from __future__ import annotations

from enum import StrEnum


class FactMode(StrEnum):
    SOFT = "soft"
    HARD_RULE = "hard_rule"


class RuleType(StrEnum):
    ENTITY_ALIAS = "entity_alias"


class RuleScope(StrEnum):
    AGENT_GLOBAL = "agent_global"
    CONTACT_RESOLUTION = "contact_resolution"
    EVENT_COMMAND = "event_command"
    DAILY_BRIEFING = "daily_briefing"


VALID_FACT_MODE_VALUES = frozenset(mode.value for mode in FactMode)
VALID_RULE_TYPE_VALUES = frozenset(rule_type.value for rule_type in RuleType)
VALID_RULE_SCOPE_VALUES = frozenset(scope.value for scope in RuleScope)


def normalize_rule_scope(scope: RuleScope | str | None) -> str | None:
    if scope is None:
        return None
    normalized = str(scope).strip().lower()
    if normalized in VALID_RULE_SCOPE_VALUES:
        return normalized
    return None


def normalize_rule_type(rule_type: RuleType | str | None) -> str | None:
    if rule_type is None:
        return None
    normalized = str(rule_type).strip().lower()
    if normalized in VALID_RULE_TYPE_VALUES:
        return normalized
    return None
