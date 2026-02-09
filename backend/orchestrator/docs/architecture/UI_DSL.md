# UI DSL (v1)

## Goal

Allow the model to propose structured chat UI blocks while preserving the bounded
agent rule:

> The model proposes. The controller validates, executes, and decides.

The model never sends UI directly to clients. It calls a tool (`emit_ui_directive`);
the controller validates/sanitizes the payload and only then includes it in the
response bundle.

## High-Level Flow

1. Model decides a structured follow-up/display is useful.
2. Model calls `emit_ui_directive`.
3. Backend validates and sanitizes the directive.
4. Accepted directive is stored in agent state and response bundle as `ui_directives`.
5. `fallback_text` is always available for unsupported clients.
6. Client can send structured `ui_submission`, or plain `text_fallback`.

## API Contracts

### Ask input additions

`ui_submission` is optional and supports both structured and text fallback modes:

```json
{
  "block_id": "followup_1",
  "action_id": "select_range",
  "values": { "range": "7d" },
  "text_fallback": "Last 7 days please"
}
```

### Ask output additions

`ui_directives` is optional:

```json
{
  "version": "1.0",
  "fallback_text": "Please pick one option.",
  "blocks": [
    {
      "id": "followup_1",
      "type": "choice_buttons",
      "title": "Choose range",
      "options": [
        { "id": "7d", "label": "Last 7 days" },
        { "id": "30d", "label": "Last 30 days" }
      ]
    }
  ]
}
```

## Supported Block Types (v1)

- `clarification_form`
- `choice_buttons`
- `info_card`

## Guardrails

- `version` must be `"1.0"`.
- `fallback_text` is required.
- `https://` links only.
- Hard limits on block/field/option/link counts and text sizes.
- Duplicate block/field/option ids are rejected.
- Unsupported types are rejected.

## Persistence

Accepted `ui_directives` are persisted in `assistant_metadata` so chat history can
replay the same cards after reload.

## Observability

Log at least:

- validation accepted/rejected
- rejection reasons
- number of blocks emitted
- whether submission arrived as structured action or text fallback

