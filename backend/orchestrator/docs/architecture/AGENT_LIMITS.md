# Agent Limits and Stop Rules

The bounded agent uses hard limits plus no-progress detection.

Completion depends on the routed task and specific evidence requirements, not
generic words such as "tell me", "what", "how" or "create". Conversation and
general explanations can finish without tools, including short complete replies.
Personal retrieval and execution retain source/result validation; a conversational
classification cannot waive an explicit saved-record search or meeting evidence
requirements. Empty successful searches can end with an honest no-results answer.
Continuation repair targets actual tool promises, not incidental phrases inside
answers or stories. UI follow-up tools are optional, never a completion prerequisite.

Every model round also passes through the shared complete-request budget and
working-context compactor. See [LLM_CONTEXT.md](LLM_CONTEXT.md) for reservations,
protected context, bounded LLM summaries and source-processing behavior.

## Config Surface

```bash
AGENT_MAX_STEPS=15
AGENT_MAX_TOOL_CALLS=20
AGENT_MAX_REPAIRS=5
AGENT_ENABLE_INTENT_ROUTING=true
AGENT_ENABLE_VALIDATION=true
```

## Limit Types

- `MAX_STEPS`: loop iterations reached.
- `MAX_TOOL_CALLS`: total executed tools reached.
- `MAX_REPAIRS`: validation repair attempts reached.
- `NO_PROGRESS_REPEATED`: repeated identical call signatures.
- `NO_PROGRESS_EMPTY`: consecutive empty/failed outcomes.

## Current Runtime Behavior

```mermaid
flowchart TD
  A[Before step] --> B{Hard limit?}
  B -->|yes| STOP[Stop and return partial bundle]
  B -->|no| C{No-progress?}
  C -->|no| RUN[Run next step]
  C -->|yes + restricted tools| ESC[Escalate tool visibility to full]
  ESC --> RUN
  C -->|yes + already full| STOP
```

Important:

- In restricted mode, no-progress first attempts recovery by tool-visibility escalation.
- Escalation events are counted in state (`tool_visibility_escalations_count`).

## APIs

- `LimitChecker.check(state)`: hard limits only.
- `LimitChecker.detect_no_progress(state)`: no-progress only.
- `LimitChecker.should_stop(state)`: compatibility helper.

Controller uses hard/no-progress checks separately to allow escalation before stop.

## Violation Payload

`LimitViolation` includes:

- `limit_type`
- `message`
- `partial_answer_allowed`
- optional `suggestion`

## Operational Guidance

- Keep defaults conservative; tune only with telemetry.
- Prefer solving no-progress with better tool guidance first.
- Avoid raising limits to mask repeated-call loops.

## Test Coverage

Primary tests:

- `tests/agent/test_limits.py`
- `tests/agent/test_controller.py` (runtime behavior)
- `tests/agent/test_tool_visibility_policy.py` (escalation interaction)
