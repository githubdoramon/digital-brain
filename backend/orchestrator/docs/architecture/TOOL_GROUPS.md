# Tool Groups Reference

Tool groups are the canonical mapping between logical capabilities and tools.

Current role:
- Primary: shared taxonomy for router hints, observability, and tests.
- Secondary: optional filtering API (`ToolRegistry.get_tools_for_groups`) used by some integration/tests.
- Runtime behavior in main agent: the controller currently exposes the **full tool set** to the LLM (no narrowing).

## Canonical Source

`backend/orchestrator/tools/registry.py` is the source of truth:

```python
TOOL_GROUPS = {
    "memory": ["search_memories", "get_events", "get_document"],
    "resolution": ["resolve_query", "resolve_contacts", "lookup_contact"],
    "web": ["web_search", "fetch_web_page"],
    "home": ["home_assistant"],
    "skills": ["run_skill_script"],
    "system": ["bash"],
}
```

`backend/orchestrator/agent/router.py` imports these groups to avoid drift.

## Intent Mapping

Router intents still map to group lists for metadata/hints:

- `MEMORY_SEARCH`: `["memory", "resolution"]`
- `DATA_QUERY`: `["memory", "resolution"]`
- `CONTACT_LOOKUP`: `["resolution", "memory"]`
- `WEB_SEARCH`: `["web"]`
- `HOME_CONTROL`: `["home"]`
- `SKILL_EXECUTION`: `["skills", "memory"]`
- `SYSTEM_COMMAND`: `["system"]`
- `CONVERSATIONAL`: `[]`
- `COMPLEX` / `UNKNOWN`: all groups

Important:
- This mapping is **not** currently used to narrow the tool list passed to the LLM in `AgentController`.

## When Editing Tool Groups

1. Update `TOOL_GROUPS` in `backend/orchestrator/tools/registry.py`.
2. Ensure registered tool contracts include compatible group assignments.
3. Keep router/tests aligned (intent mappings and expectations).
4. If runtime narrowing is reintroduced later, update controller docs and tests accordingly.

## Common Pitfalls

- Assuming groups are enforced at runtime in the main loop.
- Updating router mappings without updating registry groups.
- Forgetting to keep integration tests in sync with renamed/removed tools.
