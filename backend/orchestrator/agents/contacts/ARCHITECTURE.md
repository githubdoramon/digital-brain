# Contacts Resolver Architecture

## System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        Event Handler                            │
│  (commands/handlers/event.py or other consumers)                │
└─────────────────┬───────────────────────────────────────────────┘
                  │
                  │ 1. Extract entities via LLM
                  │    (who, where, what, when)
                  │
                  ▼
┌────────────────────────────────────────────────────────────────┐
│                    Entity Resolver Module                      │
│                   (entity_resolver.py)                         │
│                                                                │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  resolve_person_entity()                                 │  │
│  │  • Detects generic terms ("my daughter")                 │  │
│  │  • Tries relationship resolution                         │  │
│  │  • Tries direct search                                   │  │
│  │  • Uses LLM for disambiguation ONLY                      │  │
│  │  • Returns: resolved | candidates | new                  │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                │
│                                                                │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  batch_resolve_entities()                                │  │
│  │  • Orchestrates multiple entity resolutions              │  │
│  │  • Aggregates clarification questions                    │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  infer_entity_attributes()                               │  │
│  │  • Extracts explicit attributes only                     │  │
│  │  • No hallucination of data                              │  │
│  └──────────────────────────────────────────────────────────┘  │
└─────┬───────────────────────────────────────────────┬──────────┘
      │                                               │
      │ 2. Uses LLM for                               │ 3. Uses search
      │    disambiguation only                        │    functions
      │                                               │
      ▼                                               ▼
┌─────────────────┐                         ┌──────────────────────┐
│  LLM Module     │                         │  Search Functions    │
│  (llm_helpers)  │                         │  (contacts.search_   │
│                 │                         │   contacts, etc.)    │
│ • call_llm_json │                         │                      │
│ • No side       │                         │ • Read-only          │
│   effects       │                         │ • Return candidates  │
└─────────────────┘                         └──────────────────────┘
```

## Data Flow

### Scenario 1: Direct Match (Simple)

```
User input: "met with John Smith"
                  ↓
        Extract: ["John Smith"]
                  ↓
        Search contacts: "John Smith"
                  ↓
        Result: 1 match (confidence: 95%)
                  ↓
        Return: {
          status: "resolved",
          resolution: {contact_id: "123", name: "John Smith"},
          confidence: "high"
        }
```

### Scenario 2: Relationship Resolution

```
User input: "took my daughter to school"
                  ↓
        Extract: ["my daughter"]
                  ↓
        Detect: generic term "daughter"
                  ↓
        Check relationships: user → child
                  ↓
        Found: Emma Smith (relationship: child)
                  ↓
        Return: {
          status: "resolved",
          resolution: {contact_id: "456", name: "Emma Smith"},
          matched_via: "relationship",
          confidence: "high"
        }
```

### Scenario 3: Disambiguation with LLM

```
User input: "had lunch with John at the hospital"
                  ↓
        Extract: ["John"]
                  ↓
        Search contacts: "John"
                  ↓
        Result: 3 matches
          - John Doe (software engineer)
          - Dr. John Smith (doctor)
          - John Williams (friend)
                  ↓
        LLM analyzes context: "at the hospital"
                  ↓
        LLM picks: candidate #2 (Dr. John Smith)
          Reasoning: "hospital context suggests medical professional"
                  ↓
        Return: {
          status: "resolved",
          resolution: {contact_id: "789", name: "Dr. John Smith"},
          matched_via: "llm_disambiguation",
          confidence: "medium"
        }
```

### Scenario 4: Ambiguous - Ask User

```
User input: "saw John"
                  ↓
        Extract: ["John"]
                  ↓
        Search contacts: "John"
                  ↓
        Result: 3 matches
          - John Doe
          - John Smith
          - John Williams
                  ↓
        LLM analyzes context: insufficient context
                  ↓
        LLM returns: cannot_decide
                  ↓
        Return: {
          status: "candidates",
          candidates: [John Doe, John Smith, John Williams],
          needs_input: true,
          clarification_prompt: "Multiple contacts match 'John'..."
        }
```

### Scenario 5: New Entity

```
User input: "met Alice Johnson for the first time"
                  ↓
        Extract: ["Alice Johnson"]
                  ↓
        Search contacts: "Alice Johnson"
                  ↓
        Result: 0 matches
                  ↓
        Infer attributes from context:
          - profession: null (not mentioned)
          - relationship: null (not specified)
                  ↓
        Return: {
          status: "new",
          suggested_new_contact: {
            display_name: "Alice Johnson",
            inferred_attributes: {}
          }
        }
```

## Component Responsibilities

### Entity Resolver Module
**Responsibility:** Pure resolution logic
- ✅ Read search results
- ✅ Analyze context
- ✅ Return decisions
- ❌ Create entities
- ❌ Update database
- ❌ Make side effects

### LLM Module (llm_helpers)
**Responsibility:** LLM infrastructure
- ✅ Call LLM API
- ✅ Parse JSON responses
- ✅ Handle errors
- ❌ Make business decisions
- ❌ Access database

### Event Handler (Consumer)
**Responsibility:** Orchestration and persistence
- ✅ Extract entities
- ✅ Call entity resolver
- ✅ Create new entities if needed
- ✅ Update database
- ✅ Ask user for clarification
- ❌ Implement resolution logic

### Search Functions (Contacts module)
**Responsibility:** Data retrieval
- ✅ Search existing entities
- ✅ Fuzzy matching
- ✅ Return candidates
- ❌ Make resolution decisions

## Key Guarantees

### 1. No Hallucination
```python
# ✅ CORRECT: Return candidates or new
if no_exact_match:
    return {"status": "candidates"} or {"status": "new"}

# ❌ WRONG: Guess or invent
if no_exact_match:
    return {"status": "resolved", "resolution": {"contact_id": "fake_id"}}
```

### 2. LLM as Advisor, Not Creator
```python
# ✅ CORRECT: LLM picks from candidates
llm_result = call_llm_json(f"Choose from: {candidates}")
if llm_result["choice"] in candidates:
    return chosen_candidate

# ❌ WRONG: LLM creates new entity
llm_result = call_llm_json("Who might this be?")
return llm_result["invented_person"]  # Hallucination!
```

### 3. Explicit Confidence
```python
# ✅ CORRECT: Clear confidence levels
return {
    "confidence": "high",  # match_score > 90
    "confidence": "medium",  # match_score 70-90
    "confidence": "low",  # match_score < 70
}

# ❌ WRONG: Unclear or missing confidence
return {"resolved": true}  # No confidence info
```

### 4. Caller Control
```python
# ✅ CORRECT: Return decision, let caller act
resolution = resolve_person_entity("John")
if resolution["status"] == "new":
    contact_id = create_contact(resolution["suggested_new_contact"])

# ❌ WRONG: Make decisions inside resolver
def resolve_person_entity(name):
    if not found:
        contact_id = create_contact(name)  # Side effect!
    return contact_id
```

## Integration Points

### 1. Event Handler
```python
from entity_resolver import batch_resolve_entities

def handle_event(message, user_email):
    extracted = extract_entities_with_llm(message)
    resolution = batch_resolve_entities(extracted, ...)

    if resolution["needs_clarification"]:
        return ask_user(resolution["clarification_questions"])

    # Process resolved and new entities
    for result in resolution["people"]:
        if result["status"] == "resolved":
            link_to_event(result["resolution"]["contact_id"])
        elif result["status"] == "new":
            new_id = create_contact(result["suggested_new_contact"])
            link_to_event(new_id)
```

### 2. Command Parser
```python
from entity_resolver import resolve_person_entity

def parse_command(command_text):
    if command.startswith("/message"):
        # Resolve recipient
        recipient_name = extract_recipient(command_text)
        resolution = resolve_person_entity(
            recipient_name,
            search_function=search_contacts
        )

        if resolution["status"] == "resolved":
            send_message_to(resolution["resolution"]["contact_id"])
        elif resolution["needs_input"]:
            return ask_user(resolution["clarification_prompt"])
```

### 3. Chat Agent
```python
from entity_resolver import resolve_person_entity

def answer_question(question):
    # Extract mentioned people
    people = extract_people_from_question(question)

    for person in people:
        resolution = resolve_person_entity(person, ...)
        if resolution["status"] == "resolved":
            # Enrich answer with actual contact info
            add_context(resolution["resolution"])
```

## Error Handling

```python
try:
    resolution = resolve_person_entity("John", ...)
except Exception as e:
    # Entity resolver failures should be graceful
    return {
        "status": "new",
        "error": str(e),
        "suggested_new_contact": {"display_name": "John"}
    }
```

The entity resolver always returns a valid result structure, even on failure. It degrades gracefully to marking entities as "new" rather than crashing.
