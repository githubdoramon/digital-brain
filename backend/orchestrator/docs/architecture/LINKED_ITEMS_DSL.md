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
- `conversation` (thread ID plus matching message ID; inspected original messages)
- `message` (inspected messenger evidence with a controller-generated `source_url`)

Messenger destinations are derived from owner-checked archive identifiers, never
model URLs or message text. WhatsApp phone-number identifiers open direct chats.
Telegram channel/supergroup identifiers open specific messages with
`t.me/c/<channel>/<message>`. Telegram direct chats use a known username or phone
number and do not claim exact-message navigation. Unsupported destinations are
not emitted as external message chips. The controller validates allowed URL
patterns before emitting a chip; clients open message chips externally while
other entity chips keep their existing internal routes.

Web archive messages expose the same destination. Mobile fact/task/proposal source
evidence opens the original when supported, with a separate archived-source action
and saved-evidence fallback on failure. Deleted Telegram messages do not get exact
message links; a known conversation may still be offered. Links do not grant
access to private groups. OS/browser app-link preferences decide whether a
universal link opens the installed application or its website.

Migration `0078_messaging_source_links.sql` adds nullable conversation username and
phone metadata. Telegram refreshes it from observed chat entities, including
replayed recent messages, without duplicating canonical messages. Existing
Telegram channel/supergroup and WhatsApp phone links require no reimport; Telegram
direct-chat links appear when chat metadata is observed again. No group invite is
created, no message is sent, and no source URLs are exported as telemetry.

Validation on 2026-10-20: 1,279 backend tests passed with eight integration skips,
including link generation, unsupported/malicious destinations, owner isolation,
metadata refresh and controller reference tests. Web/mobile TypeScript and focused
ESLint passed. PostgreSQL tests used a disposable database; installed-app opening
and production deployment were not verified.

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

- Source entities: `event`, `document`, `contact`, `place`, `conversation`
- Source evidence: successful retrieval/inspection results plus controller-held
  provenance from the run
- Selection policy: role-based, query-aligned, answer-aware ranking
- Limits/dedupe: bounded count and deterministic de-duplication in controller
- User-facing text must remain human-readable and must not expose raw IDs
