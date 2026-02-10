---
name: clarification-generation
description: Generate minimal, dynamic clarification requests and UI fields so the assistant only asks for missing information.
---

# Clarification Generation

Use this skill whenever a task is missing information and you need follow-up from the user.

## Core Rules

- Ask only for information that is still missing.
- Prefer the smallest number of follow-up fields/questions needed to proceed.
- Never ask for data that is already present in prior context.
- If ambiguity is about people/entities, ask for disambiguation directly and avoid unrelated fields.
- Use explicit labels and short placeholders.

## Structured Clarification Output

When the prompt expects structured clarification, return a `need_user_input` object:

- `need_user_input.kind`: `clarification` or `disambiguation`.
- `need_user_input.prompt`: concise user-facing prompt.
- `need_user_input.questions`: short question list aligned to fields.
- `need_user_input.fields`: map 1:1 to missing data, with each field:
  - `id`: short snake_case key
  - `kind`: one of `text`, `textarea`, `number`, `date`, `time`, `datetime`, `email`, `url`, `select`
  - `label`: concise prompt label
  - `placeholder`: optional short hint
  - `required`: boolean
  - `options`: only for `select` (list of `{id, label}`)
- `need_user_input.submission_mode`: `text` or `ui_submission` when the caller supports structured submission.

Prefer concrete field kinds:
- Missing timestamp: `datetime` (or `date`/`time` if explicitly partial)
- Missing place: `text`
- Missing free-form detail: `textarea`
- Explicit choice among known options: `select`

## Bounded Agent UI Directive Mode

When operating through bounded tools and UI directives:

- If user input is required, emit a `clarification_form` via `emit_ui_directive`.
- Include a clear `fallback_text`.
- Keep forms focused and minimal.

## Anti-patterns

- Do not ask for broad “tell me more” when specific fields can be requested.
- Do not include optional decorative fields that are not needed for continuation.
- Do not duplicate multiple fields for the same missing fact.
