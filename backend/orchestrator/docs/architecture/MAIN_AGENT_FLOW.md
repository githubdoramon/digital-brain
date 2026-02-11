# Main Agent Flow (Current)

This document describes the runtime behavior of the main bounded agent after profile/runtime consolidation.

## Runtime Ownership

- Shared runtime: `backend/orchestrator/agent/*`
- Main-agent policy: `backend/orchestrator/agents/main/*`

## End-to-End Flow

```mermaid
flowchart TD
  Q[User Question] --> APP[app.py]
  APP --> CTRL[AgentController.run]
  CTRL --> ROUTE[IntentRouter classify]
  ROUTE --> VIS[Tool visibility policy]
  VIS --> PRE[Optional contact pre-resolution]
  PRE --> LOOP[Bounded loop]
  LOOP --> FIN[Finalize response bundle]
```

## Detailed Step Flow

```mermaid
flowchart TD
  A[Start step] --> B{Hard limits hit?}
  B -->|yes| Z[Return limit bundle]
  B -->|no| C{No-progress detected?}
  C -->|yes + restricted| D[Escalate to full tool visibility]
  D --> A
  C -->|yes + already full| Z
  C -->|no| E[Call LLM with current visible tools]
  E --> F{tool_calls?}
  F -->|yes| G[Execute tool pipeline]
  G --> H{Clarification needed?}
  H -->|yes| I[Return clarification]
  H -->|no| A
  F -->|no| J{Malformed/tool-describing output?}
  J -->|yes| K[Inject repair prompt and continue]
  K --> A
  J -->|no| L{Goal complete?}
  L -->|no| M[Inject force-completion prompt]
  M --> A
  L -->|yes| N[Return final answer]
```

## Routing and Visibility Rules

Main controller now applies confidence-tier visibility policy:

- `high` confidence: restrict to routed groups.
- `medium` confidence: routed groups + `resolution`.
- `low` confidence: full tool set.

Escalation policy:

- If restricted mode hits no-progress (repeated/empty), controller escalates to full tools within the same run.
- Escalation is always enabled in runtime policy.

## Main Policy Modules

- `agents/main/message_builder.py`
  - system/context/state message assembly
  - skill injection
  - cached static prompt blocks (system/protocol/tag/clarification-skill)
- `agents/main/runtime_policy.py`
  - malformed-output classification
  - follow-up prompt selection
  - force-completion prompt generation
  - tool status normalization (`need_user_input` aware)

## Contact-Aware Memory Flow

```mermaid
sequenceDiagram
  participant Ctrl as Controller
  participant Res as resolve_contacts
  participant Mem as search_memories

  Ctrl->>Res: pre-resolve people from top-level question
  alt ambiguous
    Res-->>Ctrl: need_user_input(disambiguation)
    Ctrl-->>Ctrl: store pending clarification + return
  else resolved
    Res-->>Ctrl: contact_ids
    Ctrl->>Mem: search_memories with contact_ids + normalized query
  end
```

## Clarification Behavior

- Clarification is returned immediately when pending contact disambiguation or UI form follow-up is required.
- Clarification payloads are normalized to `need_user_input` standards and can produce `clarification_form` UI directives.

## Streaming Path

`run_stream` mirrors the same decision policy and emits:

- `status`
- `token`
- `tool_call`
- `tool_result`
- `done`

Streaming uses the same limit checks, escalation policy, clarification checks, and malformed-output recovery logic.

## Observability Fields

Main run metadata includes:

- `profile`
- `route_source`
- `route_confidence`
- `route_confidence_tier`
- `tool_visibility_mode`
- `tool_visibility_escalated`
- `tool_visibility_escalations_count`
- `clarification_requests_count`
