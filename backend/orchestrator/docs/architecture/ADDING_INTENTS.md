# Adding New Intents

This guide covers how to add new intent types for the intent router system.

## Checklist

When adding a new intent, you must update:

- [ ] `agent/router.py` - Add to `IntentType` enum
- [ ] `agent/router.py` - Add to `INTENT_TOOL_MAP`
- [ ] `agent/router.py` - Add to `INTENT_SKILL_HINTS` (optional)
- [ ] `agent/router.py` - Add to `TOOL_GROUPS` (if new group needed)
- [ ] `agent/router.py` - Add rule-based keywords in `_rule_based_classify()`
- [ ] `agent/router.py` - Update LLM classification prompt
- [ ] `tests/agent/test_router.py` - Add tests for new intent

## Step 1: Add to IntentType Enum

In `agent/router.py`:

```python
class IntentType(str, Enum):
    """Classified intent types."""

    MEMORY_SEARCH = "memory_search"
    DATA_QUERY = "data_query"
    # ... existing intents ...

    # Add your new intent
    MY_NEW_INTENT = "my_new_intent"
```

## Step 2: Map Intent to Tool Groups

Define which tool groups this intent should have access to:

```python
INTENT_TOOL_MAP = {
    IntentType.MEMORY_SEARCH: ["memory", "resolution"],
    IntentType.DATA_QUERY: ["database", "resolution"],
    # ... existing mappings ...

    # Add your mapping
    IntentType.MY_NEW_INTENT: ["my_group", "resolution"],
}
```

### Choosing Tool Groups

- **Minimal access**: Only include groups absolutely needed
- **Resolution often helps**: Include "resolution" if entity lookups might help
- **Multiple groups OK**: Intent can access multiple groups

## Step 3: Add Skill Hints (Optional)

If certain skills are relevant to this intent:

```python
INTENT_SKILL_HINTS = {
    IntentType.MEMORY_SEARCH: ["document-search", "event-analysis"],
    # ... existing hints ...

    # Add your hints
    IntentType.MY_NEW_INTENT: ["relevant-skill-1", "relevant-skill-2"],
}
```

Skill hints help the skill matcher narrow down candidate skills.

## Step 4: Create New Tool Group (If Needed)

If your intent needs tools not covered by existing groups:

```python
TOOL_GROUPS = {
    "memory": ["search_memories", "get_events", "get_document"],
    "resolution": ["resolve_query", "resolve_contacts", "lookup_contact"],
    # ... existing groups ...

    # Add your new group
    "my_group": ["my_tool_1", "my_tool_2"],
}
```

**Note**: Tools must be registered in `tools/registry.py` with this group.

## Step 5: Add Rule-Based Keywords

In `_rule_based_classify()`, add keyword detection:

```python
def _rule_based_classify(self, question: str) -> Optional[IntentClassification]:
    q_lower = question.lower()

    # ... existing checks (ORDER MATTERS!) ...

    # Add your intent detection
    # Place this in the right position based on specificity
    my_intent_keywords = [
        "specific phrase",
        "another phrase",
        "unique keyword",
    ]
    if any(kw in q_lower for kw in my_intent_keywords):
        return IntentClassification(
            intent=IntentType.MY_NEW_INTENT,
            confidence=0.85,
            allowed_tool_groups=INTENT_TOOL_MAP[IntentType.MY_NEW_INTENT],
            skill_hints=INTENT_SKILL_HINTS.get(IntentType.MY_NEW_INTENT, []),
            reasoning="My intent keywords detected",
        )

    # ... rest of checks ...
```

### Keyword Placement Order

**CRITICAL**: The order of keyword checks matters! Earlier checks take precedence.

Current order:
1. `HOME_CONTROL` - "turn on", "light", "thermostat", etc.
2. `WEB_SEARCH` - "search the web", "google", etc.
3. `CONTACT_LOOKUP` - "who is", "contact", etc.
4. `MEMORY_SEARCH` - "remember", "find", "meeting", etc.
5. `DATA_QUERY` - "how many", "count", "sql", etc.
6. `SYSTEM_COMMAND` - "run command", "bash", etc.
7. `CONVERSATIONAL` - "hello", "thanks", etc.
8. `UNKNOWN` - fallback

Place your intent based on:
- **More specific = earlier**: Unique phrases should be checked first
- **Avoid keyword overlap**: Check for conflicts with existing intents
- **Confidence affects fallthrough**: Low confidence (< 0.8) triggers LLM classification

## Step 6: Update LLM Classification Prompt

In `_build_classification_prompt()`, update the intent descriptions:

```python
def _build_classification_prompt(self, question: str, ...) -> str:
    return f"""Classify the user's intent to determine which tools are needed.

QUESTION: {question}

AVAILABLE INTENTS:
- memory_search: Searching memories, events, documents
- data_query: SQL queries, counting, aggregations
# ... existing intents ...
- my_new_intent: Description of what this intent covers

Respond with JSON:
{{"intent": "intent_name", "confidence": 0.0-1.0, "reasoning": "why"}}
"""
```

## Step 7: Add Tests

```python
# tests/agent/test_router.py

class TestIntentType:
    def test_my_new_intent_exists(self):
        """Test MY_NEW_INTENT is defined."""
        assert IntentType.MY_NEW_INTENT.value == "my_new_intent"


class TestIntentToolMap:
    def test_my_new_intent_tools(self):
        """Test MY_NEW_INTENT has correct tool groups."""
        groups = INTENT_TOOL_MAP[IntentType.MY_NEW_INTENT]
        assert "my_group" in groups


class TestRuleBasedClassification:
    def test_my_new_intent_keywords(self, router):
        """Test my_new_intent detection via rule-based."""
        questions = [
            "A question with specific phrase",
            "Another with unique keyword",
        ]

        for question in questions:
            result = router._rule_based_classify(question)
            assert result is not None
            assert result.intent == IntentType.MY_NEW_INTENT
```

## Caveats

### 1. Keyword Overlap

Keywords can match multiple intents. For example:
- "temperature" matches HOME_CONTROL
- "how many" matches DATA_QUERY
- "find" matches MEMORY_SEARCH

**Solution**: Use more specific phrases, or rely on LLM classification for ambiguous cases.

### 2. Confidence Threshold

Rule-based results with confidence < 0.8 trigger LLM classification as backup:

```python
if rule_result and rule_result.confidence >= 0.8:
    return rule_result  # Use rule-based

# Fall through to LLM
```

Set appropriate confidence based on keyword specificity.

### 3. Tool Group Dependencies

If your intent uses a new tool group, ensure:
1. The group is defined in `TOOL_GROUPS`
2. Tools are registered with that group in `tools/registry.py`
3. Tools have handlers in `tools/handlers/`

### 4. Case Sensitivity

`q_lower = question.lower()` - all keyword matching is case-insensitive.

### 5. Substring Matching

`any(kw in q_lower for kw in keywords)` - matches substrings, not whole words.

This means:
- "ac" in "space" = True (matches HOME_CONTROL)
- "hi" in "this" = True (doesn't match CONVERSATIONAL because "hi " has trailing space)

**Solution**: Add spaces or use word boundaries for short keywords.

### 6. COMPLEX and UNKNOWN Intents

- `COMPLEX`: Multi-step tasks, gets ALL tool groups
- `UNKNOWN`: Fallback when classification fails, also gets ALL tool groups

Don't remove these - they're safety nets.

### 7. Intent Router Model

The intent router can use a separate, faster model:

```bash
INTENT_ROUTER_MODEL=llama-3.1-8b  # Smaller model for speed
INTENT_ROUTER_BASE_URL=...
INTENT_ROUTER_TIMEOUT=10  # Shorter timeout
```

Ensure your prompt works with the configured model.

## Example: Adding a Calendar Intent

```python
# 1. Add enum
class IntentType(str, Enum):
    CALENDAR_MANAGEMENT = "calendar_management"

# 2. Add tool mapping
INTENT_TOOL_MAP = {
    IntentType.CALENDAR_MANAGEMENT: ["calendar", "memory"],
}

# 3. Add skill hints
INTENT_SKILL_HINTS = {
    IntentType.CALENDAR_MANAGEMENT: ["calendar-create", "calendar-query"],
}

# 4. Add tool group (if new)
TOOL_GROUPS = {
    "calendar": ["create_event", "list_events", "delete_event"],
}

# 5. Add rule-based detection
calendar_keywords = [
    "schedule", "calendar", "appointment", "book a meeting",
    "create event", "add to calendar",
]
if any(kw in q_lower for kw in calendar_keywords):
    return IntentClassification(
        intent=IntentType.CALENDAR_MANAGEMENT,
        confidence=0.9,
        allowed_tool_groups=INTENT_TOOL_MAP[IntentType.CALENDAR_MANAGEMENT],
        skill_hints=INTENT_SKILL_HINTS[IntentType.CALENDAR_MANAGEMENT],
        reasoning="Calendar keywords detected",
    )
```
