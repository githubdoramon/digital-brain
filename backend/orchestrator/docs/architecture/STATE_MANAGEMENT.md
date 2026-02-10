# Agent State Management

`AgentState` is the canonical, controller-owned runtime state for one request.  
The LLM never mutates it directly.

## Core Principle

**The controller owns state. The LLM proposes actions.**

```
LLM: "call search_memories with query='meetings'"
Controller: Validate → Execute → Update AgentState → Inject into next prompt
```

## AgentState Structure

```python
@dataclass
class AgentState:
    # Core task tracking
    goal: str
    constraints: list[str]

    # Knowledge accumulation
    known_facts: list[str]
    completed_actions: list[str]
    pending_questions: list[str]

    # Progress tracking
    tool_calls: list[ToolCallRecord]
    step_count: int
    repair_count: int

    # Intent routing metadata
    intent: str | None
    allowed_tool_groups: list[str]
    skill_hints: list[str]

    # Completion tracking
    goal_achieved: bool
    pending_actions: list[str]
    completion_evidence: list[str]

    # Runtime context
    resolution: dict[str, Any]
    activated_skills: list[dict[str, Any]]
    information_candidates: list[dict[str, Any]]
    ui_directives: dict[str, Any] | None
    request_context: dict[str, Any]
```

### Important Change

`AgentState` no longer stores `search_results` / `events_results` arrays directly.  
Those response payloads are derived from `tool_calls` at finalization time.

## ToolCallRecord Structure

```python
@dataclass
class ToolCallRecord:
    tool_name: str
    arguments: dict[str, Any]
    result: dict[str, Any]
    duration_ms: float
    success: bool
    error: str | None = None
    validation_errors: list[str] | None = None
    was_repaired: bool = False
```

## Information Candidates (Reusable Evidence)

`information_candidates` is the generic evidence memory used across steps.  
It replaces document-only candidate handling.

Each candidate stores:
- `kind` (`document`, `event`, `contact`, etc.)
- `candidate_id`
- `label`
- `best_score`
- `times_seen`
- `last_query`
- `last_source_tool`
- `inspected` and `inspected_step`

Use:

```python
state.remember_information_candidate(
    kind="document",
    candidate_id="doc:abc",
    label="Clinical Report",
    score=1.42,
    query="vitamin b12",
    source_tool="search_memories",
)

state.mark_information_candidate_inspected("document", "doc:abc")
best = state.get_best_information_candidate(inspected_only=True)
```

This supports:
- avoiding repeated broad searches
- revisiting previously inspected sources
- compact prompt context over longer loops

## State Lifecycle

### 1. Creation

```python
state = AgentState(goal="Find my meetings from last week")
```

### 2. Intent Classification

```python
classification = router.classify(state.goal)
state.intent = classification.intent.value
state.allowed_tool_groups = classification.allowed_tool_groups
state.constraints = classification.constraints

# Controller currently still passes the full tool set to the LLM.
```

### 3. Loop Iteration

```python
state.step_count += 1

record = ToolCallRecord(
    tool_name="search_memories",
    arguments={"query": "meetings last week"},
    result={"results": [...]},
    duration_ms=150.5,
    success=True,
)
state.record_tool_call(record)

state.add_fact("Found 3 meetings from last week")
state.add_action("Searched memories for meetings")
```

### 4. Finalization

Controller derives response payloads from tool history:
- `search_results` (latest broad search rows)
- `events_results` (expanded event details from `get_events`)
- `document_results` (expanded document details from `get_document`)

## Key Methods

### Facts / Actions

```python
state.add_fact("Found 3 meetings")
state.add_action("Retrieved event details")
```

`add_fact()` is deduplicated.

### Tool Call Recording

```python
state.record_tool_call(ToolCallRecord(...))
```

### Progress Helpers

```python
state.tool_calls_count
state.successful_tool_calls
state.failed_tool_calls
state.has_repeated_calls(n=3)
state.has_empty_result_streak(n=3)
```

## State Injection

`to_context_string()` injects controller state into each LLM turn:

```python
context = state.to_context_string()
```

Includes:
- `GOAL`, `STEP`, `TOOL_CALLS_USED`
- recent `KNOWN_FACTS` and `COMPLETED`
- `PENDING_ACTIONS` / `PENDING_QUESTIONS`
- top `INFORMATION_CANDIDATES`
- request context (timezone/locale/location availability)

Candidate injection is capped to avoid prompt bloat.

## Serialization

### `to_dict()`

Full debug serialization, including `tool_calls` and runtime fields.

### `to_metadata()`

Compact metadata for logs/traces (counts + summary flags).

## No-Progress Detection

State-level checks used by limits/progress guardrails:

```python
state.has_repeated_calls(n=3)
state.has_empty_result_streak(n=3)
```

`_is_empty_result()` currently treats these as empty:
- explicit errors
- empty `results`
- empty `rows`
- `count == 0`

If you add new tool result formats, update emptiness semantics accordingly.

## Caveats

### 1. State Is Per Request

`AgentState` is created fresh per request.  
Cross-request memory comes from conversation history and storage layers.

### 2. Facts Must Be Added Explicitly

Facts are not auto-extracted from every tool result.  
Add extraction in post-execution validation where needed.

### 3. ToolCallRecord Should Be Treated as Append-Only

Record results after execution; avoid mutating past records.

### 4. Counters Are Controller-Managed

`step_count`, `repair_count`, and related lifecycle counters are controller-owned.

### 5. Keep Context Compact

Avoid storing large ad-hoc blobs in state fields.  
Prefer compact facts and candidate references; full data should stay in tool results.

### 6. `duration_ms` Is Required

Always measure and record duration in each `ToolCallRecord`.

## Example Flow

```python
state = AgentState(goal="What is my latest vitamin B12 result?")

# search
state.step_count += 1
search = search_memories(...)
state.record_tool_call(ToolCallRecord(...))
state.add_fact("Found a relevant lab report")
state.remember_information_candidate(
    kind="document",
    candidate_id="doc:lab",
    label="Clinical Laboratory Test Results Report",
    score=1.3,
    query="vitamin b12",
    source_tool="search_memories",
)

# inspect top candidate
state.step_count += 1
doc = get_document(...)
state.record_tool_call(ToolCallRecord(...))
state.mark_information_candidate_inspected("document", "doc:lab")
state.add_fact("Retrieved document content for extraction")
```

## Quick Reference

```python
state = AgentState(goal="User question")

state.step_count += 1
state.repair_count += 1

state.add_fact("Fact")
state.add_action("Action")
state.record_tool_call(ToolCallRecord(...))

state.remember_information_candidate(...)
state.mark_information_candidate_inspected(...)
state.get_best_information_candidate(inspected_only=True)

state.to_dict()
state.to_context_string()

state.resolution
state.activated_skills
state.information_candidates
state.ui_directives
```
