# Agent Limits and Stop Rules

The agent uses hard limits and no-progress detection to prevent runaway execution.

## Configuration

### Environment Variables

```bash
# Hard limits
AGENT_MAX_STEPS=15        # Maximum loop iterations
AGENT_MAX_TOOL_CALLS=20   # Maximum total tool calls
AGENT_MAX_REPAIRS=2       # Maximum validation repair attempts

# Feature flags
AGENT_ENABLE_INTENT_ROUTING=true
AGENT_ENABLE_VALIDATION=true
```

### AgentConfig Class

```python
from agent.limits import AgentConfig

# Default values
config = AgentConfig()
# max_steps=15, max_tool_calls=20, max_repairs=2

# Custom values
config = AgentConfig(
    max_steps=10,
    max_tool_calls=15,
    max_repairs=3,
    enable_intent_routing=True,
    enable_validation=True,
)

# From environment
config = AgentConfig.from_env()
```

## Limit Types

### 1. MAX_STEPS

**What it limits**: Number of agent loop iterations.

**Default**: 15

**When it triggers**: `state.step_count >= config.max_steps`

**Why it matters**: Prevents infinite loops when LLM keeps requesting tools.

```python
# Each loop iteration increments step_count
state.step_count += 1

# Check before each iteration
if state.step_count >= config.max_steps:
    # Stop with partial answer
```

### 2. MAX_TOOL_CALLS

**What it limits**: Total number of tool executions.

**Default**: 20

**When it triggers**: `state.tool_calls_count >= config.max_tool_calls`

**Why it matters**: Prevents excessive API/database calls.

```python
# Each tool execution is recorded
state.record_tool_call(record)

# Check before executing
if state.tool_calls_count >= config.max_tool_calls:
    # Stop, explain what's missing
```

### 3. MAX_REPAIRS

**What it limits**: Validation repair attempts per request.

**Default**: 2

**When it triggers**: `state.repair_count >= config.max_repairs`

**Why it matters**: Prevents loops of invalid → repair → invalid.

```python
# When validation fails and we ask LLM to fix
state.repair_count += 1

# Check before repair attempt
if state.repair_count >= config.max_repairs:
    # Give up on this tool call
```

## No-Progress Detection

Beyond hard limits, the system detects when the agent isn't making progress.

### NO_PROGRESS_REPEATED

**Triggers when**: Same tool + same arguments called 3+ times consecutively.

**Detection logic**:
```python
def has_repeated_calls(self, threshold: int = 3) -> bool:
    if len(self.tool_calls) < threshold:
        return False

    recent = self.tool_calls[-threshold:]
    first = (recent[0].tool_name, recent[0].arguments)

    return all(
        (tc.tool_name, tc.arguments) == first
        for tc in recent
    )
```

**Why it matters**: LLM stuck in a loop calling the same thing.

### NO_PROGRESS_EMPTY

**Triggers when**: 3+ consecutive tool calls return empty results.

**Detection logic**:
```python
def has_empty_result_streak(self, threshold: int = 3) -> bool:
    if len(self.tool_calls) < threshold:
        return False

    recent = self.tool_calls[-threshold:]
    return all(self._is_empty_result(tc.result) for tc in recent)

def _is_empty_result(self, result: Dict) -> bool:
    if result is None:
        return True
    if "results" in result and len(result["results"]) == 0:
        return True
    if "rows" in result and len(result["rows"]) == 0:
        return True
    if result.get("count", -1) == 0:
        return True
    return False
```

**Why it matters**: Queries aren't finding data, need different approach.

## LimitChecker API

```python
from agent.limits import LimitChecker, AgentConfig

config = AgentConfig(max_steps=5, max_tool_calls=10)
checker = LimitChecker(config)

# Check hard limits
violation = checker.check(state)
if violation:
    print(f"Limit hit: {violation.limit_type}")
    print(f"Message: {violation.message}")
    print(f"Suggestion: {violation.suggestion}")

# Check no-progress
violation = checker.detect_no_progress(state)
if violation:
    print(f"No progress: {violation.limit_type}")

# Combined check
should_stop, violation = checker.should_stop(state)
if should_stop:
    # Return partial answer
```

## LimitViolation Structure

```python
@dataclass
class LimitViolation:
    limit_type: LimitType  # Enum value
    message: str           # Human-readable message
    suggestion: str        # What to do next

# Example
violation = LimitViolation(
    limit_type=LimitType.MAX_STEPS,
    message="Reached maximum steps (15)",
    suggestion="Try a more specific query to get results faster",
)

# Serialization
data = violation.to_dict()
# {
#     "limit_type": "max_steps",
#     "message": "Reached maximum steps (15)",
#     "suggestion": "Try a more specific query..."
# }
```

## Handling Limit Violations

When a limit is hit, the agent should:

1. **Stop gracefully**: Don't crash or error out
2. **Return partial results**: Whatever was gathered so far
3. **Explain what happened**: Tell user why it stopped
4. **Suggest next steps**: How to get better results

```python
if should_stop:
    return {
        "content": f"I found partial results but had to stop. {violation.message}",
        "partial_results": state.known_facts,
        "suggestion": violation.suggestion,
        "state": state.to_dict(),
    }
```

## Adding New Limit Types

### 1. Add to LimitType Enum

```python
# agent/limits.py
class LimitType(str, Enum):
    MAX_STEPS = "max_steps"
    MAX_TOOL_CALLS = "max_tool_calls"
    MAX_REPAIRS = "max_repairs"
    NO_PROGRESS_REPEATED = "no_progress_repeated"
    NO_PROGRESS_EMPTY = "no_progress_empty"

    # Add new type
    MAX_DURATION = "max_duration"
```

### 2. Add Config Field

```python
@dataclass
class AgentConfig:
    max_steps: int = 15
    max_tool_calls: int = 20
    max_repairs: int = 2
    max_duration_seconds: int = 60  # New field

    @classmethod
    def from_env(cls) -> "AgentConfig":
        return cls(
            # ... existing ...
            max_duration_seconds=int(os.getenv("AGENT_MAX_DURATION", "60")),
        )
```

### 3. Add Check in LimitChecker

```python
def check(self, state: AgentState) -> Optional[LimitViolation]:
    # ... existing checks ...

    # New check
    if state.duration_seconds >= self.config.max_duration_seconds:
        return LimitViolation(
            limit_type=LimitType.MAX_DURATION,
            message=f"Exceeded time limit ({self.config.max_duration_seconds}s)",
            suggestion="Try breaking this into smaller questions",
        )

    return None
```

### 4. Add Tests

```python
def test_max_duration_violation(self, agent_config):
    checker = LimitChecker(agent_config)
    state = AgentState(goal="Test", duration_seconds=61)

    violation = checker.check(state)

    assert violation is not None
    assert violation.limit_type == LimitType.MAX_DURATION
```

## Caveats

### 1. Step vs Tool Call Counts

- **Step count**: Incremented each loop iteration (even without tool calls)
- **Tool call count**: Only incremented when tools execute

A step can have 0, 1, or multiple tool calls.

### 2. Repair Count is Global

Repair count is per-request, not per-tool. Three failed validations across different tools = 3 repairs.

### 3. No-Progress Threshold

The default threshold of 3 is hardcoded in `AgentState` methods. To change:

```python
# Currently hardcoded
state.has_repeated_calls(threshold=3)
state.has_empty_result_streak(threshold=3)

# To make configurable, add to AgentConfig
```

### 4. Empty Result Detection

The `_is_empty_result()` check only knows about standard formats:
- `{"results": []}`
- `{"rows": []}`
- `{"count": 0}`

**If you add tools with different formats**, update the empty check in `agent/state.py` or `tools/validators/post_execution.py`.

### 5. Limits Don't Prevent Tool Execution

Limits are checked **before** each step. A tool call in progress will complete even if it pushes over the limit.

### 6. Suggestions Are Static

Limit violation suggestions are generic. Consider making them context-aware based on:
- Which tools were used
- What results were found
- The original goal

### 7. Testing Limits

Set low limits for testing to trigger violations quickly:

```python
@pytest.fixture
def agent_config():
    return AgentConfig(
        max_steps=5,
        max_tool_calls=10,
        max_repairs=2,
    )
```

## Quick Reference

```python
# Create config
config = AgentConfig.from_env()

# Create checker
checker = LimitChecker(config)

# Check all limits
should_stop, violation = checker.should_stop(state)

# Check only hard limits
violation = checker.check(state)

# Check only no-progress
violation = checker.detect_no_progress(state)

# Get violation details
if violation:
    print(violation.limit_type.value)  # "max_steps"
    print(violation.message)           # "Reached maximum steps (15)"
    print(violation.suggestion)        # "Try a more specific query..."
```
