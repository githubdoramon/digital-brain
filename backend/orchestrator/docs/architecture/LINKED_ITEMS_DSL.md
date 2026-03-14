# Linked Items Protocol (v1)

## Goal

Provide a lightweight, prompt-level protocol that encourages the model to inspect
the most relevant events/documents so clients can receive deterministic deep-link
metadata in chat responses.

This is not a model-authored payload contract. The model does not emit
`linked_items` directly.

## Core Principle

The runtime keeps ownership of deep-link metadata:

1. The model retrieves and inspects evidence with existing tools (for example
   `get_events`, `get_document`).
2. The controller derives a bounded set of deep-link candidates from successful
   inspected results.
3. The response bundle includes `linked_items` metadata for clients.

## Prompt-Level DSL Signal

Conversational profile prompts should include a short rule:

- If inspected events/documents are central to the answer, reference those
  findings clearly in text.
- Relevant inspected items may be surfaced by the controller as
  `linked_items` metadata.

This keeps behavior discoverable to the model without introducing a new tool or
skill.

## Why No New Skill/Tool

- `linked_items` are controller-derived and deterministic from tool outputs.
- Skill definitions are reserved for guidance not already covered by tool
  contracts/profile protocol.
- A new model-facing tool is unnecessary unless we need model-chosen ordering or
  inclusion rules beyond deterministic runtime extraction.

## Runtime Contract

- Source entities: currently `event` and `document`.
- Source evidence: successful inspected tool results (`get_events`,
  `get_document`).
- Limits/dedupe: bounded count and deterministic de-duplication in controller.
- User-facing text must remain human-readable and must not expose raw IDs.
