# Tool Groups Reference

Tool groups enable **tool-set narrowing** - restricting which tools the LLM can see based on the classified intent.

## Why Tool Groups?

1. **Reduce hallucination**: LLM can't call tools it can't see
2. **Improve focus**: Fewer options = better tool selection
3. **Security**: Restrict dangerous tools to specific intents
4. **Performance**: Smaller tool list = faster LLM processing

## Current Groups

| Group | Tools | Purpose |
|-------|-------|---------|
| `memory` | search_memories, get_events, get_document | Accessing stored memories and documents |
| `resolution` | resolve_query | Entity resolution and lookup |
| `web` | web_search | External web access |
| `home` | home_assistant | Smart home control |
| `skills` | run_skill_script | Executing registered skills |
| `system` | bash | System shell commands |

## Group Definitions

Groups are defined in two places that must stay in sync:

### 1. Router Definition (`agent/router.py`)

```python
TOOL_GROUPS = {
    "memory": ["search_memories", "get_events", "get_document"],
    "resolution": ["resolve_query"],
    "web": ["web_search"],
    "home": ["home_assistant"],
    "skills": ["run_skill_script"],
    "system": ["bash"],
}
```

### 2. Registry Assignment (`tools/registry.py`)

```python
registry.register(
    contract=search_memories_contract,
    handler=search_memories_handler,
    groups=["memory"],  # Must match TOOL_GROUPS
)
```

## Intent to Group Mapping

Each intent is mapped to allowed groups:

```python
INTENT_TOOL_MAP = {
    IntentType.MEMORY_SEARCH: ["memory", "resolution"],
    IntentType.DATA_QUERY: ["memory", "resolution"],
    IntentType.CONTACT_LOOKUP: ["resolution", "memory"],
    IntentType.WEB_SEARCH: ["web"],
    IntentType.HOME_CONTROL: ["home"],
    IntentType.SKILL_EXECUTION: ["skills", "memory"],
    IntentType.SYSTEM_COMMAND: ["system"],
    IntentType.CONVERSATIONAL: [],  # No tools
    IntentType.COMPLEX: list(TOOL_GROUPS.keys()),  # All tools
    IntentType.UNKNOWN: list(TOOL_GROUPS.keys()),  # All tools (fallback)
}
```

## How Groups Are Used

```python
# 1. Intent classification
classification = router.classify("Find my meetings")
# classification.allowed_tool_groups = ["memory", "resolution"]

# 2. Get tools for allowed groups
tools = registry.get_tools_for_groups(classification.allowed_tool_groups)
# tools = [search_memories, get_events, get_document, resolve_query]

# 3. Convert to LLM format
tool_definitions = [t.to_openai_tool() for t in tools]

# 4. Call LLM with filtered tools
response = llm.chat(messages, tools=tool_definitions)
```

## Adding a Tool to an Existing Group

1. **Register with group** in `tools/registry.py`:
   ```python
   registry.register(
       contract=new_tool_contract,
       handler=new_tool_handler,
       groups=["memory"],  # Existing group
   )
   ```

2. **Update TOOL_GROUPS** in `agent/router.py`:
   ```python
   TOOL_GROUPS = {
       "memory": ["search_memories", "get_events", "get_document", "new_tool"],
       # ...
   }
   ```

3. **Add tests** to verify the tool is accessible.

## Creating a New Group

See [ADDING_INTENTS.md](./ADDING_INTENTS.md) for creating groups tied to new intents.

For standalone groups:

1. **Define in TOOL_GROUPS**:
   ```python
   TOOL_GROUPS = {
       # ... existing groups ...
       "analytics": ["generate_report", "create_chart"],
   }
   ```

2. **Register tools with group**:
   ```python
   registry.register(
       contract=generate_report_contract,
       handler=generate_report_handler,
       groups=["analytics"],
   )
   ```

3. **Map to intents**:
   ```python
   INTENT_TOOL_MAP = {
       IntentType.DATA_QUERY: ["memory", "resolution", "analytics"],
       # ...
   }
   ```

## Multi-Group Tools

Tools can belong to multiple groups:

```python
registry.register(
    contract=resolve_query_contract,
    handler=resolve_query_handler,
    groups=["resolution", "memory", "analytics"],  # Available in multiple contexts
)
```

This makes the tool available whenever any of those groups is allowed.

## Special Groups

### Empty Groups (Conversational)

```python
IntentType.CONVERSATIONAL: []  # No tools
```

For conversational intents, the LLM receives no tools and must respond directly.

### All Groups (Complex/Unknown)

```python
IntentType.COMPLEX: list(TOOL_GROUPS.keys())  # All tools
IntentType.UNKNOWN: list(TOOL_GROUPS.keys())  # All tools
```

Fallback intents get access to everything. This is a safety net.

## Caveats

### 1. Sync Required

`TOOL_GROUPS` in router and registry assignments must match:

```python
# router.py
TOOL_GROUPS = {"memory": ["search_memories", "get_events"]}

# registry.py - these must match!
registry.register(..., groups=["memory"])  # Tool must be in TOOL_GROUPS["memory"]
```

**If mismatched**: Tool may be registered but never visible to LLM.

### 2. Order Independence

Groups are sets - order doesn't matter:

```python
["memory", "resolution"]  # Same as
["resolution", "memory"]
```

### 3. Deduplication

If a tool is in multiple allowed groups, it only appears once:

```python
# resolve_query in both "resolution" and "memory" groups
# allowed_groups = ["memory", "resolution"]
# Result: resolve_query appears once, not twice
```

### 4. Empty Results

If `get_tools_for_groups([])` is called, returns empty list. This is intentional for `CONVERSATIONAL` intent.

### 5. Unknown Groups

Requesting tools for unknown group returns empty:

```python
registry.get_tools_for_groups(["nonexistent"])  # Returns []
```

No error is raised - this is silent.

### 6. Testing Groups

Always verify group membership in tests:

```python
def test_tool_in_correct_group(self):
    registry = get_registry()
    tools = registry.get_tools_for_groups(["memory"])
    tool_names = [t.name for t in tools]

    assert "search_memories" in tool_names
    assert "home_assistant" not in tool_names  # Not in memory group
```

## Quick Reference

```python
# Get all tools in a group
registry.get_tools_for_groups(["memory"])

# Get tools for multiple groups (union)
registry.get_tools_for_groups(["memory", "resolution"])

# Check if tool exists
registry.has_tool("search_memories")

# Get specific tool contract
contract = registry.get_contract("search_memories")

# List all groups
registry.list_groups()

# List tools in a group
registry.list_tools_in_group("memory")
```
