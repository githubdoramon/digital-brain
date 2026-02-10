"""
Pre-execution validation for tool calls.

Validates tool calls before execution:
1. Validates against JSON Schema
2. Applies custom validators
3. Rejects unknown fields
4. Rejects missing required fields
5. Implements repair loop (max 2 retries)
"""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

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
        max_repairs: int = 2,
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
        result = self.validate(tool_name, params)

        if not result.valid:
            return result, None

        # Normalize parameters
        contract = self.registry.get_contract(tool_name)
        if contract:
            normalized = contract.normalize(params)
            return result, normalized

        return result, params

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
            action = str(params.get("action") or "").strip()
            if action == "call_tool" and not str(params.get("tool_name") or "").strip():
                return "When action='call_tool', 'tool_name' is required"

        if tool_name == "lookup_contact":
            action = str(params.get("action") or "").strip()
            has_query = bool(str(params.get("query") or "").strip())
            has_contact_id = bool(str(params.get("contact_id") or "").strip())
            if action in {"search", "find_related"} and not has_query:
                return f"When action='{action}', provide a non-empty 'query'"
            if action == "get_relationships" and not (has_contact_id or has_query):
                return "When action='get_relationships', provide 'contact_id' or 'query'"

        return None

    def _semantic_repair_hints(self, tool_name: str, params: dict[str, Any]) -> list[str]:
        """Produce targeted repair hints for common mistakes."""
        hints: list[str] = []

        if tool_name == "home_assistant":
            action = str(params.get("action") or "").strip()
            if action == "call_tool" and not str(params.get("tool_name") or "").strip():
                hints.append(
                    "Call `home_assistant` with action='list_tools' first, then reuse a returned tool_name"
                )
            if action == "call_tool" and not isinstance(params.get("arguments"), dict):
                hints.append("Use an object for 'arguments' when calling a Home Assistant MCP tool")

        if tool_name == "search_memories":
            if not str(params.get("query") or "").strip():
                hints.append("Provide a focused 'query' describing the topic to retrieve")

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
