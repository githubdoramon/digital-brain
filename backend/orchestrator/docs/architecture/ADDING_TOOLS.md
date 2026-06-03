# Adding New Tools

This guide covers all the steps needed to add a new tool to the bounded agent system.

## Checklist

When adding a new tool, you must update:

- [ ] `tools/contracts.py` - Define ToolContract with parameters
- [ ] `tools/registry.py` - Register tool and assign to group(s)
- [ ] `tools/handlers/<category>.py` - Implement the handler function
- [ ] `tools/validators/pre_execution.py` - Add custom validators (if needed)
- [ ] `tools/validators/post_execution.py` - Add result extraction logic
- [ ] `tools/registry.py` - Update TOOL_GROUPS if creating new group
- [ ] `tests/tools/test_registry.py` - Add registration tests
- [ ] `tests/tools/test_contracts.py` - Add validation tests (if custom validators)

## Step 1: Define the Tool Contract

Create a `ToolContract` in `tools/contracts.py` or in your handler file:

```python
from tools.contracts import ToolContract, ToolParameter

my_tool_contract = ToolContract(
    name="my_new_tool",
    description="Clear description of what the tool does",
    parameters=[
        ToolParameter(
            name="required_param",
            type="string",
            description="What this parameter does",
            required=True,
        ),
        ToolParameter(
            name="optional_param",
            type="integer",
            description="Optional with default",
            required=False,
            default=10,
            minimum=1,
            maximum=100,
        ),
        ToolParameter(
            name="enum_param",
            type="string",
            description="Constrained to specific values",
            required=False,
            enum=["option_a", "option_b", "option_c"],
        ),
    ],
    # Optional: custom validators
    custom_validators={
        "required_param": validate_my_custom_rule,
    },
)
```

### Parameter Types

- `string` - Text values
- `integer` - Whole numbers (supports `minimum`, `maximum`)
- `number` - Floating point (supports `minimum`, `maximum`)
- `boolean` - True/false
- `array` - Lists (specify `items` type)
- `object` - Nested objects

## Step 2: Register the Tool

In `tools/registry.py`, register your tool with its group:

```python
# In the _register_default_tools() function or similar

registry.register(
    contract=my_tool_contract,
    handler=my_tool_handler,
    groups=["my_group"],  # Assign to one or more groups
)
```

### Choosing a Group

Existing groups (canonical source: `tools/registry.py`):

| Group | Purpose | Tools |
|-------|---------|-------|
| `memory` | Memory/document search | search_memories, get_events, get_document |
| `resolution` | Entity resolution | resolve_contacts, lookup_contact, select_contacts, lookup_places, lookup_contact_places, lookup_place_contacts |
| `web` | External web access | web_search, fetch_web_page |
| `home` | Home automation | home_assistant |
| `skills` | Skill scripts | run_skill_script |
| `pdf` | Generated PDF artifacts and document ingestion | create_pdf, ingest_generated_pdf |
| `ui` | UI directives | emit_ui_directive |
| `system` | System commands | bash |

**If you need a new group**, see [ADDING_INTENTS.md](./ADDING_INTENTS.md).

## Step 3: Implement the Handler

Create or update a handler file in `tools/handlers/`:

```python
# tools/handlers/my_category.py

from typing import Any, Dict

async def my_tool_handler(
    arguments: Dict[str, Any],
    context: Dict[str, Any],  # Contains conversation context
) -> Dict[str, Any]:
    """
    Execute the tool and return results.

    Args:
        arguments: Validated parameters from the contract
        context: Additional context (user_id, conversation_id, etc.)

    Returns:
        Dict with results or error
    """
    param = arguments["required_param"]
    optional = arguments.get("optional_param", 10)

    try:
        # Your implementation here
        result = do_something(param, optional)

        return {
            "success": True,
            "data": result,
            "count": len(result),  # Include counts for fact extraction
        }
    except Exception as e:
        return {
            "error": str(e),
            "success": False,
        }
```

### Handler Return Format

**For successful results:**
```python
{
    "results": [...],      # For search-like tools
    "rows": [...],         # For SQL-like tools
    "data": {...},         # For single-item returns
    "count": 5,            # Include for fact extraction
    "success": True,
}
```

**For errors:**
```python
{
    "error": "Description of what went wrong",
    "success": False,
}
```

## Step 4: Add Custom Validators (If Needed)

If your tool needs special validation beyond type checking:

```python
# tools/validators/pre_execution.py

def validate_my_custom_rule(value: str) -> bool:
    """
    Validate that value meets custom requirements.

    Returns:
        True if valid, False otherwise
    """
    # Example: check for dangerous patterns
    dangerous = ["DROP", "DELETE", "TRUNCATE"]
    return not any(d in value.upper() for d in dangerous)
```

Then reference in your contract:
```python
custom_validators={
    "required_param": validate_my_custom_rule,
}
```

## Step 5: Update Post-Execution Logic

**IMPORTANT**: If your tool returns results in a non-standard format, update `tools/validators/post_execution.py`:

```python
# In PostExecutionValidator or result checking logic

def _is_empty_result(self, result: Dict[str, Any]) -> bool:
    """Check if result is effectively empty."""
    # Add your tool's result format
    if "my_data_key" in result:
        return len(result["my_data_key"]) == 0

    # Existing checks...
    if "results" in result:
        return len(result["results"]) == 0
    if "rows" in result:
        return len(result["rows"]) == 0

    return False

def _extract_facts(self, tool_name: str, result: Dict[str, Any]) -> List[str]:
    """Extract facts from tool results."""
    facts = []

    # Add fact extraction for your tool
    if tool_name == "my_new_tool":
        if "count" in result:
            facts.append(f"Found {result['count']} items")
        if "my_data_key" in result:
            facts.append(f"Retrieved data: {result['my_data_key'][:100]}...")

    return facts
```

### Why This Matters

The post-execution validator:
1. Detects empty results for no-progress detection
2. Extracts facts to inject into agent state
3. Determines if the tool succeeded or failed

**If you don't update this**, your tool's results may:
- Trigger false "no progress" stops
- Not contribute facts to the agent's knowledge
- Be misclassified as failures

## Step 6: Add Tests

### Registry Test

```python
# tests/tools/test_registry.py

def test_my_new_tool_registered(self):
    """Test my_new_tool is registered correctly."""
    registry = get_registry()

    assert registry.has_tool("my_new_tool")

    contract = registry.get_contract("my_new_tool")
    assert contract is not None
    assert contract.name == "my_new_tool"

def test_my_new_tool_in_group(self):
    """Test my_new_tool is in correct group."""
    registry = get_registry()

    tools = registry.get_tools_for_groups(["my_group"])
    tool_names = [t.name for t in tools]

    assert "my_new_tool" in tool_names
```

### Validation Test (if custom validators)

```python
# tests/tools/test_contracts.py

def test_my_custom_validator_valid(self):
    """Test custom validator accepts valid input."""
    assert validate_my_custom_rule("safe_value") is True

def test_my_custom_validator_invalid(self):
    """Test custom validator rejects invalid input."""
    assert validate_my_custom_rule("DROP TABLE") is False
```

## Caveats

1. **Tool names must be unique**: The registry will overwrite if you register the same name twice.

2. **Group assignment affects runtime visibility**: In conservative mode, routed confidence tiers can narrow tools by group. Ensure group assignment is correct or the model may not see your tool in high/medium confidence runs.

3. **Handler exceptions**: Always catch exceptions in handlers and return error dicts. Unhandled exceptions may crash the agent loop.

4. **Async handlers**: Handlers should be async functions. Synchronous operations will block the event loop.

5. **Result format matters**: The post-execution validator and fact extraction depend on consistent result formats. Follow existing patterns.

6. **Parameter defaults**: If a parameter has a default, it will be injected during normalization even if not provided by the LLM.

## Example: Complete Tool Addition

See `tools/handlers/memory.py` for `search_memories` as a reference implementation that includes:
- Full contract definition
- Async handler with error handling
- Proper result format with counts
- Registration with group assignment

## Advanced Pattern: Multi-Step Tools (Home Assistant Example)

Some tools require a discovery step before the main action. The `home_assistant` tool demonstrates this pattern:

### 1. Two-Step Process

```python
# First call: discover available actions
home_assistant(action="list_tools")
# Returns: {"tools": [...], "action_mapping": {...}, "usage_guide": "..."}

# Second call: perform the action
home_assistant(action="call_tool", tool_name="HassTurnOff", arguments={"name": "office lights"})
```

### 2. Include Action Mapping in Discovery Response

When listing available sub-tools, include explicit guidance on which tool to use for common actions:

```python
return {
    "tools": tools,
    "count": len(tools),
    "action_mapping": {
        "turn_off": "HassTurnOff - use 'name' parameter",
        "turn_on": "HassTurnOn - use 'name' parameter",
        "set_brightness": "HassLightSet - use 'name' and 'brightness'",
    },
    "usage_guide": "CRITICAL: To TURN OFF, use HassTurnOff, NOT HassLightSet",
}
```

### 3. Detect Common Mistakes Before Execution

Add pre-execution checks that catch likely misuse based on the user's goal:

```python
def _detect_common_mistakes(tool_name: str, tool_args: dict, state: AgentState) -> Optional[str]:
    """Return hint if a common mistake is detected."""
    if state and tool_name == "HassLightSet":
        goal_lower = state.goal.lower()
        if "turn off" in goal_lower:
            return "User wants to TURN OFF but you used HassLightSet. Use HassTurnOff instead."
    return None
```

### 4. Update Tool Description with Clear Guidance

The tool description in the registry should include:

- The two-step process requirement
- A quick reference for common actions
- Common mistakes to avoid

```python
ToolContract(
    name="home_assistant",
    description=(
        "Control Home Assistant devices. "
        "TWO-STEP PROCESS REQUIRED:\n"
        "1. FIRST call action='list_tools'\n"
        "2. THEN call action='call_tool' with correct tool\n\n"
        "QUICK GUIDE:\n"
        "- TURN OFF: Use 'HassTurnOff' with name\n"
        "- TURN ON: Use 'HassTurnOn' with name\n"
        "NEVER use entity_id - use friendly 'name' instead."
    ),
    ...
)
```

This pattern helps LLMs avoid hallucinating tool names and guides them to the correct action.
