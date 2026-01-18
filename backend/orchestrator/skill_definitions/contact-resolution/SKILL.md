---
name: contact-resolution
description: Extract person mentions from text, resolve them to existing contacts (including relationships and nested relationships), return candidates when ambiguous, and propose new contacts plus relationship suggestions.
---

# Contact Resolution

Use this skill when the user provides a sentence or note and wants to identify which contacts are mentioned, or when you need to link people in free-form text to the contacts database.

This agent performs a full pipeline:
- Extracts person mentions from text (names, relationships, nested relationships)
- Resolves each mention to an existing contact when possible
- Marks new people when no match exists and infers professions from context
- Suggests missing relationships between mention pairs

## Primary Endpoint: `POST /contacts/resolve`

Request body:

```json
{
  "text": "visited my daughter's eye doctor"
}
```

Notes:
- `user_email` is injected by the backend from auth; do not include it in the request.
- The resolver never writes to the database; it only reads and returns candidates.

## Response Format

```json
{
  "status": "success" | "needs_clarification" | "no_people" | "error",
  "text": "visited my daughter's eye doctor",
  "people_mentioned": ["my daughter's eye doctor"],
  "resolved_contacts": [
    {
      "original_text": "my daughter's eye doctor",
      "contact_id": "contact:dr-smith",
      "display_name": "Dr. Smith",
      "matched_via": "nested_relationship",
      "confidence": "medium",
      "resolution_path": ["user", "Emma", "Dr. Smith"]
    }
  ],
  "new_contacts": [
    {
      "original_text": "new therapist",
      "display_name": "new therapist",
      "inferred_profession": "therapist"
    }
  ],
  "ambiguous_contacts": [
    {
      "original_text": "John",
      "candidates": [
        { "contact_id": "contact:john-smith", "display_name": "John Smith", "match_score": 92 }
      ],
      "clarification_prompt": "Multiple contacts match 'John'. Which one did you mean: John Smith?"
    }
  ],
  "suggested_relationships": [
    {
      "from_text": "Dr. Smith",
      "to_text": "my daughter",
      "from_contact_id": "contact:dr-smith",
      "to_contact_id": "contact:emma",
      "type": "doctor",
      "other_type": "patient",
      "relationship_hint": "eye doctor"
    }
  ]
}
```

Key fields:
- `status`: `needs_clarification` when any ambiguous people are found, `no_people` when none extracted.
- `matched_via`: `direct_match`, `relationship`, `nested_relationship`, or `llm_disambiguation`.
- `resolution_path`: for nested relationships, shows the chain (user -> intermediate -> target).
- `suggested_relationships`: only included when an explicit relationship is mentioned and the pair does not already have a relationship.

## Behavior Guarantees

- Never hallucinates contacts: only returns existing contacts, candidates, or marks as new.
- Uses LLM only for extraction and disambiguation, never creation.
- Handles relationships like "my daughter" and nested forms like "my daughter's doctor".
- Uses the special token `user` internally to represent the current user when they are a participant.

## Examples

### "I met my daughter's doctor"

```json
{
  "text": "I met my daughter's doctor"
}
```

Expect:
- `people_mentioned` includes `user` and `my daughter's doctor`
- `resolved_contacts` includes the doctor with `matched_via: nested_relationship`

### "Met John yesterday"

```json
{
  "text": "Met John yesterday"
}
```

Expect:
- `resolved_contacts` if a single John matches
- `ambiguous_contacts` if multiple Johns exist (ask the clarification prompt)

## Tips

- If `status` is `needs_clarification`, ask the user the provided clarification prompt before proceeding.
- Use `people_mentioned` as the canonical list of extracted mentions for downstream logic.
- Treat `new_contacts` as candidates for later ingestion if the user confirms.
