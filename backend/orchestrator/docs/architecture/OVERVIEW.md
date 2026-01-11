# Bounded Agent Architecture Overview

## Core Principle

**"The model proposes, the controller validates and decides."**

The LLM suggests tool calls, but the controller:
- Validates parameters before execution
- Executes tools and captures results
- Decides when to continue or stop
- Maintains canonical state across all interactions

## Component Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                        AgentController                          │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐  │
│  │ IntentRouter │  │ LimitChecker │  │ AgentState           │  │
│  │ (classify)   │  │ (stop rules) │  │ (canonical state)    │  │
│  └──────────────┘  └──────────────┘  └──────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                        Tool System                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐  │
│  │ ToolRegistry │  │ ToolContract │  │ Validators           │  │
│  │ (grouping)   │  │ (schemas)    │  │ (pre/post execution) │  │
│  └──────────────┘  └──────────────┘  └──────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                      Tool Handlers                              │
│  memory.py │ database.py │ web.py │ homeassistant.py │ etc.    │
└─────────────────────────────────────────────────────────────────┘
```

## Request Flow

```
1. User Question
       │
       ▼
2. IntentRouter.classify()
   ├─ Rule-based classification (fast)
   └─ LLM classification (if ambiguous)
       │
       ▼
3. Filter tools by intent (tool-set narrowing)
       │
       ▼
4. Agent Loop:
   ├─ Check limits (max_steps, max_tool_calls, max_repairs)
   ├─ Inject state into prompt
   ├─ Call LLM with filtered tools
   ├─ If tool_call:
   │   ├─ Pre-execution validation
   │   ├─ Execute tool handler
   │   ├─ Post-execution validation
   │   └─ Update state (facts, actions)
   ├─ If content: check if final answer
   └─ Check no-progress detection
       │
       ▼
5. Return response bundle
```

## Key Files

| File | Purpose |
|------|---------|
| `agent/controller.py` | Main orchestration logic |
| `agent/state.py` | AgentState and ToolCallRecord |
| `agent/router.py` | Intent classification |
| `agent/limits.py` | Stop rules, no-progress detection |
| `tools/registry.py` | Tool registration and grouping |
| `tools/contracts.py` | ToolContract with JSON Schema |
| `tools/validators/` | Pre/post execution validation |
| `tools/handlers/` | Actual tool implementations |

## Feature Flags

```bash
# Enable bounded agent (vs legacy implementation)
USE_BOUNDED_AGENT=true

# Enable LLM-based intent routing
AGENT_ENABLE_INTENT_ROUTING=true

# Enable pre/post validation
AGENT_ENABLE_VALIDATION=true
```

## Related Documentation

- [ADDING_TOOLS.md](./ADDING_TOOLS.md) - How to add new tools
- [ADDING_INTENTS.md](./ADDING_INTENTS.md) - How to add new intent types
- [TOOL_GROUPS.md](./TOOL_GROUPS.md) - Tool group system reference
- [AGENT_LIMITS.md](./AGENT_LIMITS.md) - Limit configuration
- [STATE_MANAGEMENT.md](./STATE_MANAGEMENT.md) - Agent state guide
- [VALIDATION.md](./VALIDATION.md) - Validation system guide

## Caveats

1. **Intent router checks order matters**: Rule-based classification checks intents in a specific order. Earlier matches take precedence.

2. **Tool groups affect visibility**: A tool not in any allowed group won't be available for that intent.

3. **State is per-request**: AgentState is created fresh for each user question. Cross-request state must be handled externally.

4. **LLM fallback**: If intent router LLM fails, falls back to rule-based classification with all tools available.
