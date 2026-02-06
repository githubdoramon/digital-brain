"""
Tool contracts with JSON Schema validation and runtime guards.

Each tool has a non-negotiable contract that defines:
- JSON Schema for parameters
- Required and optional fields
- Value validators (runtime guards)
- Normalization rules

The model NEVER writes raw executable commands.
The controller validates, normalizes, and executes.
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Union


@dataclass
class ToolParameter:
    """Definition of a single tool parameter."""

    name: str
    type: str  # "string", "integer", "number", "boolean", "array", "object"
    description: str
    required: bool = False
    default: Any = None
    enum: Optional[list[Any]] = None
    minimum: Optional[Union[int, float]] = None
    maximum: Optional[Union[int, float]] = None
    min_length: Optional[int] = None
    max_length: Optional[int] = None
    items_type: Optional[str] = None  # For arrays
    validator: Optional[Callable[[Any], bool]] = None  # Custom runtime validator

    def to_json_schema(self) -> dict[str, Any]:
        """Convert to JSON Schema format."""
        schema: dict[str, Any] = {
            "type": self.type,
            "description": self.description,
        }

        if self.enum:
            schema["enum"] = self.enum
        if self.minimum is not None:
            schema["minimum"] = self.minimum
        if self.maximum is not None:
            schema["maximum"] = self.maximum
        if self.min_length is not None:
            schema["minLength"] = self.min_length
        if self.max_length is not None:
            schema["maxLength"] = self.max_length
        if self.type == "array" and self.items_type:
            schema["items"] = {"type": self.items_type}

        return schema


@dataclass
class ToolContract:
    """
    Non-negotiable contract for a tool.

    Defines the schema, validation rules, and execution constraints
    for a tool. The controller uses this to validate and normalize
    tool calls before execution.
    """

    name: str
    description: str
    parameters: list[ToolParameter] = field(default_factory=list)
    handler: Optional[Callable] = None

    # Guards
    block_unknown_params: bool = True
    value_validators: dict[str, Callable[[Any], bool]] = field(default_factory=dict)

    # Normalization
    normalizer: Optional[Callable[[dict[str, Any]], dict[str, Any]]] = None

    # Constraints
    constraints: list[str] = field(default_factory=list)  # e.g., ["read_only"]

    def get_required_params(self) -> list[str]:
        """Get list of required parameter names."""
        return [p.name for p in self.parameters if p.required]

    def get_optional_params(self) -> list[str]:
        """Get list of optional parameter names."""
        return [p.name for p in self.parameters if not p.required]

    def get_all_param_names(self) -> list[str]:
        """Get list of all valid parameter names."""
        return [p.name for p in self.parameters]

    def to_json_schema(self) -> dict[str, Any]:
        """Convert to JSON Schema format for validation."""
        properties = {}
        required = []

        for param in self.parameters:
            properties[param.name] = param.to_json_schema()
            if param.required:
                required.append(param.name)

        schema = {
            "type": "object",
            "properties": properties,
            "additionalProperties": not self.block_unknown_params,
        }

        if required:
            schema["required"] = required

        return schema

    def to_openai_tool(self) -> dict[str, Any]:
        """Convert to OpenAI function calling format."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.to_json_schema(),
            },
        }

    def validate_params(
        self, params: dict[str, Any]
    ) -> tuple[bool, Optional[str], Optional[list[str]]]:
        """
        Validate parameters against schema and guards.

        Returns:
            Tuple of (is_valid, error_message, repair_suggestions)
        """
        errors = []
        suggestions = []

        # Check for unknown parameters
        if self.block_unknown_params:
            valid_names = set(self.get_all_param_names())
            unknown = set(params.keys()) - valid_names
            if unknown:
                errors.append(f"Unknown parameters: {unknown}")
                suggestions.append(f"Valid parameters are: {list(valid_names)}")

        # Check required parameters
        for param_name in self.get_required_params():
            if param_name not in params:
                errors.append(f"Missing required parameter: {param_name}")
                param = next(p for p in self.parameters if p.name == param_name)
                suggestions.append(f"Add '{param_name}': {param.description}")

        # Validate each parameter
        for param in self.parameters:
            if param.name not in params:
                continue

            value = params[param.name]
            param_errors = self._validate_param_value(param, value)
            errors.extend(param_errors)

        # Run custom validators
        for param_name, validator in self.value_validators.items():
            if param_name in params:
                try:
                    if not validator(params[param_name]):
                        errors.append(f"Invalid value for {param_name}")
                except Exception as e:
                    errors.append(f"Validation error for {param_name}: {e}")

        if errors:
            return False, "; ".join(errors), suggestions

        return True, None, None

    def _validate_param_value(
        self, param: ToolParameter, value: Any
    ) -> list[str]:
        """Validate a single parameter value against its schema."""
        errors = []

        # Type checking
        expected_type = param.type
        type_map = {
            "string": str,
            "integer": int,
            "number": (int, float),
            "boolean": bool,
            "array": list,
            "object": dict,
        }

        if expected_type in type_map:
            expected = type_map[expected_type]
            if not isinstance(value, expected):
                errors.append(
                    f"Parameter '{param.name}' should be {expected_type}, "
                    f"got {type(value).__name__}"
                )
                return errors  # Skip other validations if type is wrong

        # String validations
        if param.type == "string" and isinstance(value, str):
            if param.min_length is not None and len(value) < param.min_length:
                errors.append(
                    f"Parameter '{param.name}' too short "
                    f"(min {param.min_length} chars)"
                )
            if param.max_length is not None and len(value) > param.max_length:
                errors.append(
                    f"Parameter '{param.name}' too long "
                    f"(max {param.max_length} chars)"
                )

        # Numeric validations
        if param.type in ("integer", "number") and isinstance(value, (int, float)):
            if param.minimum is not None and value < param.minimum:
                errors.append(
                    f"Parameter '{param.name}' below minimum ({param.minimum})"
                )
            if param.maximum is not None and value > param.maximum:
                errors.append(
                    f"Parameter '{param.name}' above maximum ({param.maximum})"
                )

        # Enum validation
        if param.enum is not None and value not in param.enum:
            errors.append(
                f"Parameter '{param.name}' must be one of: {param.enum}"
            )

        # Custom validator
        if param.validator:
            try:
                if not param.validator(value):
                    errors.append(f"Parameter '{param.name}' failed validation")
            except Exception as e:
                errors.append(f"Validation error for '{param.name}': {e}")

        return errors

    def normalize(self, params: dict[str, Any]) -> dict[str, Any]:
        """
        Apply normalization to parameters.

        This can include:
        - Adding default values
        - Type coercion
        - Path sanitization
        - Custom transformations
        """
        # Apply defaults for missing optional params
        result = dict(params)
        for param in self.parameters:
            if param.name not in result and param.default is not None:
                result[param.name] = param.default

        # Apply custom normalizer
        if self.normalizer:
            result = self.normalizer(result)

        return result

    def get_validation_feedback(
        self, params: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Generate structured feedback for the model to repair invalid params.

        Returns a dict suitable for including in the next prompt.
        """
        is_valid, error, suggestions = self.validate_params(params)

        if is_valid:
            return {"valid": True}

        return {
            "valid": False,
            "error": error,
            "allowed_fields": self.get_all_param_names(),
            "required_fields": self.get_required_params(),
            "suggestions": suggestions or [],
        }

    def __repr__(self) -> str:
        required = self.get_required_params()
        optional = self.get_optional_params()
        return (
            f"ToolContract({self.name}, "
            f"required={required}, optional={optional})"
        )


# Common validators for reuse
def validate_path_safe(path: str) -> bool:
    """Validate path doesn't contain traversal attacks."""
    dangerous = ["..", "~", "$", "`", "|", ";", "&"]
    return not any(char in path for char in dangerous)


def validate_url_safe(url: str) -> bool:
    """Validate URL is safe (no file://, etc.)."""
    dangerous_schemes = ["file://", "data:", "javascript:"]
    url_lower = url.lower()
    return not any(url_lower.startswith(scheme) for scheme in dangerous_schemes)


def validate_positive_int(value: int) -> bool:
    """Validate integer is positive."""
    return isinstance(value, int) and value > 0


def validate_limit(value: int) -> bool:
    """Validate limit is within reasonable range."""
    return isinstance(value, int) and 1 <= value <= 100
