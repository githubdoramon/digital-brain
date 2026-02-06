# Validation System

The validation system ensures tool calls are safe and effective through pre-execution and post-execution checks.

## Overview

```
LLM proposes tool call
        │
        ▼
┌─────────────────────┐
│ PRE-EXECUTION       │
│ - Schema validation │
│ - Type checking     │
│ - Custom validators │
│ - Parameter repair  │
└─────────────────────┘
        │
        ▼ (if valid)
┌─────────────────────┐
│ TOOL EXECUTION      │
└─────────────────────┘
        │
        ▼
┌─────────────────────┐
│ POST-EXECUTION      │
│ - Success/error     │
│ - Empty detection   │
│ - Fact extraction   │
│ - Goal coverage     │
└─────────────────────┘
```

## Pre-Execution Validation

Located in `tools/validators/pre_execution.py` and `tools/contracts.py`.

### What It Validates

1. **Required parameters present**
2. **Parameter types correct**
3. **Values within ranges**
4. **Enum values valid**
5. **Custom validators pass**

### ToolContract Validation

```python
from tools.contracts import ToolContract, ToolParameter

contract = ToolContract(
    name="search_memories",
    description="Search stored memories",
    parameters=[
        ToolParameter(
            name="query",
            type="string",
            description="Search query",
            required=True,
        ),
        ToolParameter(
            name="limit",
            type="integer",
            description="Max results",
            required=False,
            default=10,
            minimum=1,
            maximum=100,
        ),
    ],
)

# Validate parameters
is_valid, error, suggestions = contract.validate_params({
    "query": "meetings",
    "limit": 50,
})

if not is_valid:
    print(f"Error: {error}")
    print(f"Suggestions: {suggestions}")
```

### Validation Return Value

`validate_params()` returns a tuple:

```python
(
    is_valid: bool,           # True if all validations pass
    error: Optional[str],     # Error message if invalid
    suggestions: Optional[List[str]]  # Suggestions for fixing
)
```

### Type Validation

| Type | Validation |
|------|------------|
| `string` | Must be str |
| `integer` | Must be int, optionally within min/max |
| `number` | Must be int or float, optionally within min/max |
| `boolean` | Must be bool |
| `array` | Must be list |
| `object` | Must be dict |

### Range Validation

```python
ToolParameter(
    name="limit",
    type="integer",
    minimum=1,
    maximum=100,
)

# Valid: 1, 50, 100
# Invalid: 0, 101, -5
```

### Enum Validation

```python
ToolParameter(
    name="sort_order",
    type="string",
    enum=["asc", "desc"],
)

# Valid: "asc", "desc"
# Invalid: "ascending", "ASC"
```

### Custom Validators

For complex validation beyond types:

```python
from tools.contracts import validate_path_safe

contract = ToolContract(
    name="read_file",
    parameters=[
        ToolParameter(
            name="path",
            type="string",
            required=True,
        ),
    ],
    custom_validators={
        "path": validate_path_safe,
    },
)
```

### Built-in Custom Validators

```python
# tools/contracts.py

def validate_path_safe(path: str) -> bool:
    """Block path traversal attempts."""
    dangerous = ["..", "~", "$"]
    return not any(d in path for d in dangerous)

def validate_url_safe(url: str) -> bool:
    """Validate URL format and block dangerous schemes."""
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        return parsed.scheme in ("http", "https")
    except Exception:
        return False
```

### Adding Custom Validators

```python
# 1. Define the validator
def validate_my_rule(value: str) -> bool:
    """Return True if valid, False otherwise."""
    return len(value) <= 1000  # Example: max length

# 2. Add to contract
contract = ToolContract(
    name="my_tool",
    parameters=[...],
    custom_validators={
        "my_param": validate_my_rule,
    },
)
```

## Normalization

Normalization adds default values for missing optional parameters:

```python
normalized = contract.normalize({
    "query": "meetings",
    # "limit" not provided
})

# Result: {"query": "meetings", "limit": 10}  # default added
```

## Repair Loop

When validation fails, the system can ask the LLM to fix the parameters:

```
1. LLM proposes: {"query": "", "limit": 200}
2. Validation fails: "query is required", "limit exceeds maximum"
3. Controller sends error back to LLM
4. LLM proposes: {"query": "meetings", "limit": 100}
5. Validation passes
6. Execute tool
```

**Max repairs**: Controlled by `config.max_repairs` (default: 2)

After max repairs, the tool call is abandoned.

## Post-Execution Validation

Located in `tools/validators/post_execution.py`.

### What It Checks

1. **Success/failure** - Did the tool return an error or `success: false`?
2. **Empty results** - Did the tool return no data?
3. **Fact extraction** - What facts should be added to state?
4. **Goal coverage** - Is the goal satisfied? (optional LLM check)
5. **Failure guidance** - Provides hints to the LLM on how to recover from failures

### GoalCoverage States

The post-validator returns one of these states:
- `SATISFIED` - Goal is achieved
- `NEEDS_MORE_TOOLS` - Need more tool calls to complete
- `NEED_USER_INPUT` - Need clarification from user
- `FAILED` - Tool call failed, includes recovery guidance

When `FAILED` is returned, the controller injects guidance into the result to help the LLM recover.

### Success Detection

```python
def is_success(result: Dict) -> bool:
    """Check if tool execution succeeded."""
    if result is None:
        return False
    if "error" in result:
        return False
    if result.get("success") is False:
        return False
    return True
```

### Empty Result Detection

```python
def is_empty_result(result: Dict) -> bool:
    """Check if result is effectively empty."""
    if result is None:
        return True

    # Standard result formats
    if "results" in result:
        return len(result["results"]) == 0
    if "rows" in result:
        return len(result["rows"]) == 0
    if result.get("count", -1) == 0:
        return True

    # Add checks for custom tool formats here!
    return False
```

**IMPORTANT**: If your tool uses a different result format, update this function!

### Fact Extraction

Post-execution should extract facts for state injection:

```python
def extract_facts(tool_name: str, result: Dict) -> List[str]:
    """Extract facts from tool results."""
    facts = []

    if "count" in result:
        facts.append(f"Found {result['count']} results")

    if tool_name == "search_memories" and "results" in result:
        facts.append(f"Search returned {len(result['results'])} memories")

    if tool_name == "search_memories" and "results" in result:
        facts.append(f"Search returned {len(result['results'])} results")

    # Add extraction for your tools here!

    return facts
```

### Goal Coverage (Optional)

For ambiguous cases, an LLM can evaluate if the goal is satisfied:

```python
def evaluate_goal_coverage(
    goal: str,
    state: AgentState,
    result: Dict,
) -> str:
    """
    Returns:
        "satisfied" - Goal is met
        "needs_more_tools" - Need more tool calls
        "need_user_input" - Need clarification
        "failed" - Can't achieve goal
    """
    # This would call an LLM to evaluate
    pass
```

This is optional and uses a separate LLM call.

## Validation Feedback

For debugging and repair, get detailed feedback:

```python
feedback = contract.get_validation_feedback({
    "query": "",
    "limit": 200,
})

# {
#     "valid": False,
#     "error": "Multiple validation errors",
#     "errors": [
#         "query: required field is empty",
#         "limit: value 200 exceeds maximum 100"
#     ],
#     "required_fields": ["query"],
#     "optional_fields": ["limit"],
# }
```

## Feature Flag

Validation can be disabled:

```bash
AGENT_ENABLE_VALIDATION=false
```

When disabled:
- Pre-execution validation skipped
- Tools execute with raw LLM parameters
- Post-execution still runs (for fact extraction)

**Use with caution** - disabling validation increases risk of:
- SQL injection
- Path traversal
- Invalid parameters causing crashes

## Caveats

### 1. Custom Validators Must Return Bool

```python
# Correct
def my_validator(value: str) -> bool:
    return len(value) > 0

# Wrong - don't raise exceptions
def my_validator(value: str) -> bool:
    if len(value) == 0:
        raise ValueError("Empty!")  # Don't do this
    return True
```

### 2. Validation Order

1. Required field check
2. Type check
3. Range/enum check
4. Custom validators

Validation stops at first failure.

### 3. Normalization Runs After Validation

```python
# Flow:
# 1. validate_params() - checks provided values
# 2. normalize() - adds defaults for missing optionals
```

### 4. Empty Result Format Matters

If your tool returns results in a non-standard format, update `is_empty_result()`:

```python
# Your tool returns: {"items": [...]}
# Update the check:
if "items" in result:
    return len(result["items"]) == 0
```

Otherwise, consecutive calls may not trigger no-progress detection.

### 5. Fact Extraction is Manual

Facts are not automatically extracted. You must implement extraction logic for each tool's result format.

### 6. Repair Loop Can Fail

If the LLM consistently produces invalid parameters, all repair attempts may fail. The tool call is then abandoned.

### 7. Post-Execution is Always Run

Even when pre-execution validation is disabled, post-execution runs to:
- Detect errors
- Extract facts
- Check for empty results

### 8. Validation Errors Go to LLM

Validation errors are formatted and sent back to the LLM as feedback:

```
Your tool call had validation errors:
- query: required field is empty
- limit: value 200 exceeds maximum 100

Please fix these issues and try again.
```

## Adding Validation for New Tools

### 1. Define Contract with Validators

```python
my_tool_contract = ToolContract(
    name="my_tool",
    description="Does something",
    parameters=[
        ToolParameter(
            name="input",
            type="string",
            required=True,
        ),
    ],
    custom_validators={
        "input": validate_my_input,
    },
)
```

### 2. Update Empty Result Check

```python
# tools/validators/post_execution.py or agent/state.py

def is_empty_result(result):
    # ... existing checks ...

    # Add your tool's format
    if "my_data" in result:
        return len(result["my_data"]) == 0

    return False
```

### 3. Add Fact Extraction

```python
def extract_facts(tool_name, result):
    # ... existing extraction ...

    if tool_name == "my_tool":
        if "count" in result:
            return [f"My tool found {result['count']} items"]

    return []
```

### 4. Add Tests

```python
# tests/tools/test_contracts.py

def test_my_tool_validation_valid(self):
    is_valid, error, _ = my_tool_contract.validate_params({
        "input": "valid value",
    })
    assert is_valid is True

def test_my_tool_validation_invalid(self):
    is_valid, error, _ = my_tool_contract.validate_params({
        "input": "",  # Empty, should fail
    })
    assert is_valid is False
```

## Quick Reference

```python
# Pre-execution validation
is_valid, error, suggestions = contract.validate_params(params)
normalized = contract.normalize(params)
feedback = contract.get_validation_feedback(params)

# Custom validators
def my_validator(value: str) -> bool:
    return True  # or False

# Post-execution checks
is_success(result)      # Did tool succeed?
is_empty_result(result) # Is result empty?
extract_facts(name, result)  # Get facts for state

# Built-in validators
validate_path_safe(path) # Block path traversal
validate_url_safe(url)   # Validate URL format
```
