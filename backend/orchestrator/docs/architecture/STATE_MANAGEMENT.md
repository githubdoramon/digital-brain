# Agent State Management

The `AgentState` is the canonical state object maintained by the controller. The LLM never directly modifies it - the controller updates state based on LLM outputs.

## Core Principle

**The controller owns state. The LLM proposes actions.**

```
LLM: "I want to call search_memories with query='meetings'"
Controller: Validates → Executes → Updates AgentState → Injects into next prompt
```

## AgentState Structure

```python
@dataclass
class AgentState:
    # Core fields
    goal: str                           # The user's question/objective
    constraints: List[str]              # Any constraints from intent routing
    known_facts: List[str]              # Accumulated facts from tool results
    completed_actions: List[str]        # Summary of completed steps
    pending_questions: List[str]        # Questions for clarification
    tool_calls: List[ToolCallRecord]    # Full tool call history

    # Counters
    step_count: int = 0                 # Loop iterations
    repair_count: int = 0               # Validation repair attempts

    # Intent routing metadata
    intent: Optional[str] = None        # Classified intent (metadata only)
    allowed_tool_groups: List[str]      # Router groups (metadata only; not enforced for visibility)

    # Legacy compatibility fields
    resolution: Dict[str, Any]          # From resolve_query results
    search_results: List[Dict]          # From search_memories results
    detailed_events: List[Dict]         # From get_events results
    activated_skills: List[Dict]        # From skill execution results
```

## ToolCallRecord Structure

```python
@dataclass
class ToolCallRecord:
    tool_name: str                      # Name of the tool called
    arguments: Dict[str, Any]           # Arguments passed to tool
    result: Optional[Dict[str, Any]]    # Tool result (None if failed)
    duration_ms: float                  # Execution time in milliseconds
    success: bool                       # Whether execution succeeded

    # Optional fields
    error: Optional[str] = None         # Error message if failed
    validation_errors: Optional[List[str]] = None  # Pre-exec validation errors
    was_repaired: bool = False          # Whether args were repaired
```

## State Lifecycle

### 1. Creation

```python
# At the start of each request
state = AgentState(goal="Find my meetings from last week")
```

### 2. Intent Classification

```python
classification = router.classify(state.goal)
state.intent = classification.intent.value
state.allowed_tool_groups = classification.allowed_tool_groups
state.constraints = classification.constraints

# Note: the main controller currently still passes the full tool set to the LLM.
```

### 3. Loop Iteration

```python
# Each iteration
state.step_count += 1

# If tool call succeeds
record = ToolCallRecord(
    tool_name="search_memories",
    arguments={"query": "meetings last week"},
    result={"results": [...]},
    duration_ms=150.5,
    success=True,
)
state.record_tool_call(record)

# Extract and add facts
state.add_fact("Found 3 meetings from last week")
state.add_action("Searched memories for 'meetings last week'")
```

### 4. Completion

```python
# At the end
response_data = state.to_dict()
```

## Key Methods

### Adding Facts

```python
state.add_fact("User has 3 meetings scheduled")
state.add_fact("Earliest meeting is at 9am")

# Duplicates are ignored
state.add_fact("User has 3 meetings scheduled")  # No-op
```

### Adding Actions

```python
state.add_action("Searched memories for meetings")
state.add_action("Retrieved event details")
```

### Recording Tool Calls

```python
record = ToolCallRecord(
    tool_name="search_memories",
    arguments={"query": "events", "limit": 5},
    result={"results": [...]},
    duration_ms=50.0,
    success=True,
)
state.record_tool_call(record)
```

### Properties

```python
state.tool_calls_count          # Total number of tool calls
state.successful_tool_calls     # Count of successful calls
state.has_repeated_calls()      # Check for repeated identical calls
state.has_empty_result_streak() # Check for consecutive empty results
```

## State Injection

State is injected into every LLM prompt via `to_context_string()`:

```python
context = state.to_context_string()
```

Output format:
```
CURRENT_STATE:
GOAL: Find my meetings from last week
STEP: 3
TOOL_CALLS_USED: 2
CONSTRAINTS: read_only
KNOWN_FACTS:
- Found 3 meetings
- Earliest is Team Standup at 9am
COMPLETED:
- Searched memories for meetings
- Retrieved event details
```

This helps the LLM understand:
- What it's trying to accomplish
- How many resources it has used
- What it already knows
- What actions it has taken

## Serialization

### to_dict()

For API responses and logging:

```python
data = state.to_dict()
# {
#     "goal": "Find my meetings",
#     "step_count": 3,
#     "known_facts": ["Found 3 meetings", ...],
#     "completed_actions": ["Searched memories", ...],
#     "tool_calls": [
#         {
#             "tool_name": "search_memories",
#             "arguments": {"query": "meetings"},
#             "result": {...},
#             "duration_ms": 150.5,
#             "success": True
#         }
#     ],
#     # ... other fields
# }
```

### to_context_string()

For LLM prompt injection (see above).

## Legacy Compatibility

These fields exist for backward compatibility with existing code:

```python
# Resolution results
state.resolution["entity_type"] = "person"
state.resolution["entity_id"] = "123"

# Search results
state.search_results.append({"id": "doc_1", "content": "..."})

# Detailed events
state.detailed_events.append({"id": "evt_1", "title": "Meeting"})

# Activated skills
state.activated_skills.append({"skill": "calendar", "result": {...}})
```

**When adding new tools**: Consider whether results should populate these legacy fields for compatibility.

## No-Progress Detection

State tracks patterns that indicate lack of progress:

### Repeated Calls

```python
# Same tool + same args 3+ times
state.has_repeated_calls(threshold=3)
```

### Empty Result Streak

```python
# 3+ consecutive empty results
state.has_empty_result_streak(threshold=3)
```

**Important**: The `_is_empty_result()` method must know your tool's result format:

```python
def _is_empty_result(self, result: Dict) -> bool:
    if result is None:
        return True
    if "results" in result and len(result["results"]) == 0:
        return True
    if "rows" in result and len(result["rows"]) == 0:
        return True
    if result.get("count", -1) == 0:
        return True
    # Add your tool's format here!
    return False
```

## Caveats

### 1. State is Per-Request

AgentState is created fresh for each user question. It does not persist across requests.

For cross-request context, use:
- Conversation history (passed to LLM)
- External storage (database, cache)

### 2. Facts Must Be Explicit

The controller must explicitly call `add_fact()`. Facts are not automatically extracted from tool results.

**Best practice**: Add fact extraction in post-execution validation:

```python
# tools/validators/post_execution.py
if "count" in result:
    state.add_fact(f"Found {result['count']} results")
```

### 3. Tool Call Records Are Immutable

Once recorded, a ToolCallRecord should not be modified. Create a new record if needed.

### 4. Counters Are Controller-Managed

Don't let the LLM modify `step_count`, `repair_count`, etc. These are controller-managed.

### 5. Legacy Fields Need Updates

If your tool should populate legacy fields (resolution, search_results, etc.), update the tool handler or post-execution logic.

### 6. Context String Length

`to_context_string()` shows only recent facts/actions to avoid prompt bloat:

```python
# Shows last 5 facts, last 3 actions
facts = self.known_facts[-5:]
actions = self.completed_actions[-3:]
```

Adjust if you need more/less context.

### 7. duration_ms is Required

`ToolCallRecord` requires `duration_ms`. Always measure and record execution time:

```python
import time

start = time.time()
result = await tool_handler(arguments)
duration_ms = (time.time() - start) * 1000

record = ToolCallRecord(
    tool_name="my_tool",
    arguments=arguments,
    result=result,
    duration_ms=duration_ms,
    success=True,
)
```

## Example: Complete State Flow

```python
# 1. Create state
state = AgentState(goal="Find meetings with John last week")

# 2. Classify intent
classification = router.classify(state.goal)
state.intent = classification.intent.value
state.allowed_tool_groups = classification.allowed_tool_groups

# 3. First tool call
state.step_count += 1
result1 = await search_memories({"query": "meetings John"})
state.record_tool_call(ToolCallRecord(
    tool_name="search_memories",
    arguments={"query": "meetings John"},
    result=result1,
    duration_ms=120.0,
    success=True,
))
state.add_fact(f"Found {len(result1['results'])} potential meetings")
state.add_action("Searched memories for meetings with John")

# 4. Second tool call
state.step_count += 1
result2 = await get_events({"ids": ["evt_1", "evt_2"]})
state.record_tool_call(ToolCallRecord(
    tool_name="get_events",
    arguments={"ids": ["evt_1", "evt_2"]},
    result=result2,
    duration_ms=80.0,
    success=True,
))
state.add_fact("Meeting 1: Team Sync on Monday 9am")
state.add_fact("Meeting 2: Project Review on Wednesday 2pm")
state.add_action("Retrieved event details")

# 5. Serialize for response
response = {
    "content": "I found 2 meetings with John last week...",
    "state": state.to_dict(),
}
```

## Quick Reference

```python
# Create state
state = AgentState(goal="User question")

# Update counters
state.step_count += 1
state.repair_count += 1

# Add information
state.add_fact("Some discovered fact")
state.add_action("Some completed action")

# Record tool calls
state.record_tool_call(ToolCallRecord(...))

# Check properties
state.tool_calls_count
state.successful_tool_calls
state.has_repeated_calls()
state.has_empty_result_streak()

# Serialize
state.to_dict()           # Full serialization
state.to_context_string() # For LLM prompt

# Legacy fields
state.resolution
state.search_results
state.detailed_events
state.activated_skills
```
