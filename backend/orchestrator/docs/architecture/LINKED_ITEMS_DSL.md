# Linked Items Protocol (v2)

## Goal

Provide deterministic deep-link metadata that points users to the entities that
best answer the original question.

This is not a model-authored payload contract. The model does not emit
`linked_items` directly.

## Core Principle

The runtime keeps ownership of deep-link metadata:

1. The model retrieves and inspects evidence with existing tools.
2. The controller tracks candidate entities plus provenance across the run.
3. The controller ranks entities against the original question and final answer.
4. The response bundle includes a bounded set of `linked_items` metadata for
   clients.

## Entity Coverage

Linked items may now include:

- `event`
- `document`
- `contact`
- `place`

## Selection Model

Linked items are selected by answer role, not by a rigid entity-type preference.
Each candidate may contribute to one of these controller-owned roles:

- `primary_answer_anchor` - the entity that most directly answers the question
- `subject_entity` - the person/place/thing the question is about
- `context_anchor` - a supporting disambiguation entity
- `evidence_anchor` - an inspected supporting artifact

The controller combines deterministic signals such as:

- retrieval provenance (source tool, rank, direct lookup vs broad search)
- inspection state
- overlap with the original question
- overlap with the final answer text
- role hints accumulated during retrieval

## Prompt-Level DSL Signal

Conversational profile prompts should keep a short rule:

- If retrieved entities materially support the answer, reference those
  findings clearly in text.
- Relevant entities may be surfaced by the controller as `linked_items`
  metadata.

This keeps behavior discoverable to the model without introducing a new tool or
skill.

## Why No New Skill/Tool

- `linked_items` remain controller-derived and deterministic from tool outputs.
- Skill definitions are reserved for guidance not already covered by tool
  contracts/profile protocol.
- A new model-facing tool is unnecessary unless we need model-authored
  ordering/inclusion decisions.

## Runtime Contract

- Source entities: `event`, `document`, `contact`, `place`
- Source evidence: successful retrieval/inspection results plus controller-held
  provenance from the run
- Selection policy: role-based, query-aligned, answer-aware ranking
- Limits/dedupe: bounded count and deterministic de-duplication in controller
- User-facing text must remain human-readable and must not expose raw IDs
