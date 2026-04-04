"""
Pre-execution validation for tool calls.

Validates tool calls before execution:
1. Validates against JSON Schema
2. Applies custom validators
3. Rejects unknown fields
4. Rejects missing required fields
5. Implements repair loop (max 2 retries)
"""

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

from tools.action_enums import GetEventsAction, HomeAssistantAction, LookupContactAction

if TYPE_CHECKING:
    from tools.registry import ToolRegistry


@dataclass
class ValidationResult:
    """Result of pre-execution validation."""

    valid: bool
    tool_name: str
    original_params: dict[str, Any]
    errors: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    repaired_params: Optional[dict[str, Any]] = None

    def to_feedback(self) -> dict[str, Any]:
        """Convert to feedback format for the model."""
        if self.valid:
            return {"valid": True, "tool": self.tool_name}

        return {
            "valid": False,
            "tool": self.tool_name,
            "error": "; ".join(self.errors),
            "suggestions": self.suggestions,
        }

    def to_message(self) -> str:
        """Convert to a message string for model feedback."""
        if self.valid:
            return f"Tool call to {self.tool_name} validated successfully."

        lines = [
            f"Validation failed for {self.tool_name}:",
            f"Errors: {'; '.join(self.errors)}",
        ]
        if self.suggestions:
            lines.append(f"Suggestions: {'; '.join(self.suggestions)}")
        return "\n".join(lines)


class PreExecutionValidator:
    """
    Validates tool calls before execution.

    Implements the repair loop with configurable max retries.
    """

    def __init__(
        self,
        registry: "ToolRegistry",
        max_repairs: int = 5,
    ):
        """
        Initialize the validator.

        Args:
            registry: Tool registry for looking up contracts
            max_repairs: Maximum repair attempts before giving up
        """
        self.registry = registry
        self.max_repairs = max_repairs

    def validate(
        self,
        tool_name: str,
        params: dict[str, Any],
    ) -> ValidationResult:
        """
        Validate a tool call against its contract.

        Args:
            tool_name: Name of the tool being called
            params: Parameters provided by the model

        Returns:
            ValidationResult with validation status and feedback
        """
        params = self._canonicalize_params(tool_name, params)

        # Check if tool exists
        contract = self.registry.get_contract(tool_name)
        if not contract:
            return ValidationResult(
                valid=False,
                tool_name=tool_name,
                original_params=params,
                errors=[f"Unknown tool: {tool_name}"],
                suggestions=[f"Available tools: {list(self.registry._contracts.keys())}"],
            )

        # Validate parameters
        is_valid, error, suggestions = contract.validate_params(params)

        # Targeted semantic checks for action-dependent contracts.
        semantic_error = self._semantic_validate(tool_name, params)
        if semantic_error:
            is_valid = False
            if error:
                error = f"{error}; {semantic_error}"
            else:
                error = semantic_error
            suggestions = suggestions or []
            suggestions.extend(self._semantic_repair_hints(tool_name, params))

        if not is_valid:
            return ValidationResult(
                valid=False,
                tool_name=tool_name,
                original_params=params,
                errors=[error] if error else [],
                suggestions=suggestions
                or self._generate_repair_hints(contract, params, tool_name=tool_name),
            )

        # Validation passed
        return ValidationResult(
            valid=True,
            tool_name=tool_name,
            original_params=params,
        )

    def validate_and_normalize(
        self,
        tool_name: str,
        params: dict[str, Any],
    ) -> tuple[ValidationResult, Optional[dict[str, Any]]]:
        """
        Validate and normalize parameters.

        Args:
            tool_name: Name of the tool being called
            params: Parameters provided by the model

        Returns:
            Tuple of (ValidationResult, normalized_params or None)
        """
        canonical_params = self._canonicalize_params(tool_name, params)
        result = self.validate(tool_name, canonical_params)

        if not result.valid:
            return result, None

        # Normalize parameters
        contract = self.registry.get_contract(tool_name)
        if contract:
            normalized = contract.normalize(canonical_params)
            return result, normalized

        return result, canonical_params

    def _canonicalize_params(self, tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
        """Apply generic canonicalization before validation/normalization."""
        canonical = dict(params or {})

        def _strip_id_fields(obj: dict[str, Any]) -> None:
            for key, value in list(obj.items()):
                if key.endswith("_id") and isinstance(value, str):
                    obj[key] = _apply_id_prefix_for_field(key, value)
                elif key.endswith("_ids") and isinstance(value, list):
                    normalized_values: list[str] = []
                    for item in value:
                        normalized = _apply_id_prefix_for_field(key, str(item))
                        if isinstance(normalized, str) and normalized.strip():
                            normalized_values.append(normalized.strip())
                    obj[key] = normalized_values

        _strip_id_fields(canonical)

        return canonical

    def _generate_repair_hints(
        self,
        contract,
        params: dict[str, Any],
        tool_name: str = "",
    ) -> list[str]:
        """Generate helpful hints for fixing validation errors."""
        hints = []

        if contract.description:
            hints.append(f"Tool intent: {contract.description}")

        # Check for missing required params
        required = contract.get_required_params()
        missing = [p for p in required if p not in params]
        if missing:
            hints.append(f"Add required parameters: {missing}")

        # Check for unknown params
        valid_params = contract.get_all_param_names()
        unknown = [p for p in params if p not in valid_params]
        if unknown:
            hints.append(f"Remove unknown parameters: {unknown}")
            hints.append(f"Valid parameters are: {valid_params}")

        hints.extend(self._semantic_repair_hints(tool_name, params))

        return hints

    def _semantic_validate(self, tool_name: str, params: dict[str, Any]) -> str | None:
        """Run action-specific semantic validation not expressible in static JSON schema."""
        if tool_name == "home_assistant":
            action = HomeAssistantAction.from_value(params.get("action"))
            if (
                action is HomeAssistantAction.CALL_TOOL
                and not str(params.get("tool_name") or "").strip()
            ):
                return "When action='call_tool', 'tool_name' is required"

        if tool_name == "lookup_contact":
            action = LookupContactAction.from_value(
                params.get("action"),
                default=LookupContactAction.SEARCH,
            )
            action_label = (
                action.value
                if isinstance(action, LookupContactAction)
                else LookupContactAction.SEARCH.value
            )
            has_query = bool(str(params.get("query") or "").strip())
            has_contact_id = bool(str(params.get("contact_id") or "").strip())
            if (
                action in {LookupContactAction.SEARCH, LookupContactAction.FIND_RELATED}
                and not has_query
            ):
                return f"When action='{action_label}', provide a non-empty 'query'"
            if action is LookupContactAction.GET_RELATIONSHIPS and not (
                has_contact_id or has_query
            ):
                return "When action='get_relationships', provide 'contact_id' or 'query'"

        if tool_name == "get_events":
            action = GetEventsAction.from_value(params.get("action"))
            has_event_ids = bool(params.get("event_ids"))
            has_time_start = bool(str(params.get("time_start") or "").strip())
            has_time_end = bool(str(params.get("time_end") or "").strip())

            if action is None:
                if has_event_ids:
                    action = GetEventsAction.BY_IDS
                elif has_time_start or has_time_end:
                    action = GetEventsAction.BY_TIME_SPAN

            if action is GetEventsAction.BY_IDS and not has_event_ids:
                return "When action='by_ids', provide non-empty 'event_ids'"

            if action is GetEventsAction.BY_TIME_SPAN and (not has_time_start or not has_time_end):
                return "When action='by_time_span', provide both 'time_start' and 'time_end'"

            if action is None:
                return "Provide either 'event_ids' or a time span ('time_start' + 'time_end') for get_events"

        if tool_name == "summarize_memories":
            has_time_start = bool(str(params.get("time_start") or "").strip())
            has_time_end = bool(str(params.get("time_end") or "").strip())
            if not has_time_start or not has_time_end:
                return "When using summarize_memories, provide both 'time_start' and 'time_end'"

        if tool_name == "lookup_contact_places":
            has_contact_id = bool(str(params.get("contact_id") or "").strip())
            has_contact_query = bool(str(params.get("contact_query") or "").strip())
            has_group_query = bool(str(params.get("group_query") or "").strip())
            if not has_contact_id and not has_contact_query and not has_group_query:
                return (
                    "Provide 'contact_id', 'contact_query', or 'group_query' "
                    "for lookup_contact_places"
                )

        if tool_name == "lookup_place_contacts":
            has_place_id = bool(str(params.get("place_id") or "").strip())
            has_place_query = bool(str(params.get("place_query") or "").strip())
            if not has_place_id and not has_place_query:
                return "Provide 'place_id' or 'place_query' for lookup_place_contacts"

        return None

    def _semantic_repair_hints(self, tool_name: str, params: dict[str, Any]) -> list[str]:
        """Produce targeted repair hints for common mistakes."""
        hints: list[str] = []

        if tool_name == "home_assistant":
            action = HomeAssistantAction.from_value(params.get("action"))
            if (
                action is HomeAssistantAction.CALL_TOOL
                and not str(params.get("tool_name") or "").strip()
            ):
                hints.append(
                    "Call `home_assistant` with action='list_tools' first, then reuse a returned tool_name"
                )
            if action is HomeAssistantAction.CALL_TOOL and not isinstance(
                params.get("arguments"), dict
            ):
                hints.append("Use an object for 'arguments' when calling a Home Assistant MCP tool")

        if tool_name == "search_memories":
            if not str(params.get("query") or "").strip():
                hints.append("Provide a focused 'query' describing the topic to retrieve")

        if tool_name == "get_events":
            action = GetEventsAction.from_value(params.get("action"))
            has_event_ids = bool(params.get("event_ids"))
            if action is GetEventsAction.BY_IDS and not has_event_ids:
                hints.append("Use 'event_ids' from search_memories results when action='by_ids'")
            if action is GetEventsAction.BY_TIME_SPAN:
                hints.append(
                    "Provide both 'time_start' and 'time_end' in ISO 8601 for strict event windows"
                )

        if tool_name == "summarize_memories":
            hints.append(
                "Use summarize_memories for bounded recap windows with 'time_start' and 'time_end'"
            )

        if tool_name == "lookup_contact_places":
            has_contact_id = bool(str(params.get("contact_id") or "").strip())
            has_contact_query = bool(str(params.get("contact_query") or "").strip())
            has_group_query = bool(str(params.get("group_query") or "").strip())
            if not has_contact_id and not has_contact_query and not has_group_query:
                hints.append(
                    "Set 'contact_id', 'contact_query', or 'group_query' to identify the target"
                )

        if tool_name == "lookup_place_contacts":
            has_place_id = bool(str(params.get("place_id") or "").strip())
            has_place_query = bool(str(params.get("place_query") or "").strip())
            if not has_place_id and not has_place_query:
                hints.append("Set 'place_id' or 'place_query' to identify the place")

        return hints

    def create_validation_feedback(
        self,
        result: ValidationResult,
        repair_attempt: int,
    ) -> dict[str, Any]:
        """
        Create structured feedback for the model to repair invalid params.

        Args:
            result: The validation result
            repair_attempt: Current repair attempt number (1-indexed)

        Returns:
            Dict with feedback for the model
        """
        feedback = result.to_feedback()
        feedback["repair_attempt"] = repair_attempt
        feedback["max_repairs"] = self.max_repairs
        feedback["remaining_attempts"] = self.max_repairs - repair_attempt

        if repair_attempt >= self.max_repairs:
            feedback["final_attempt"] = True
            feedback["warning"] = "This is your last attempt to fix the tool call."

        return feedback

    def can_repair(self, repair_count: int) -> bool:
        """Check if more repair attempts are allowed."""
        return repair_count < self.max_repairs
_UUID_PATTERN = re.compile(
    r"^[0-9a-fA-F]{32}$|^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
)

_ID_PREFIX_BY_FIELD: dict[str, str] = {
    "document_id": "doc",
    "contact_id": "contact",
    "place_id": "place",
    "todo_id": "todo",
    "event_id": "event",
    "thread_id": "thread",
}


def _apply_id_prefix_for_field(field_name: str, raw_value: Any) -> Any:
    """Prefix UUID-only IDs when field has known entity namespace."""
    if not isinstance(raw_value, str):
        return raw_value

    trimmed = raw_value.strip()
    if not trimmed:
        return trimmed
    if ":" in trimmed:
        return trimmed
    if not _UUID_PATTERN.fullmatch(trimmed):
        return trimmed

    singular_field = field_name[:-1] if field_name.endswith("_ids") else field_name
    prefix = _ID_PREFIX_BY_FIELD.get(singular_field)
    if not prefix:
        return trimmed
    return f"{prefix}:{trimmed}"
