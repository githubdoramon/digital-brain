from __future__ import annotations

from typing import Any

STRING_ARRAY_SCHEMA: dict[str, Any] = {
    "type": "array",
    "items": {"type": "string"},
}

NEED_USER_INPUT_SCHEMA: dict[str, Any] = {
    "type": ["object", "null"],
    "additionalProperties": True,
}

EVENT_EXTRACTION_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "need_user_input": NEED_USER_INPUT_SCHEMA,
        "title": {"type": ["string", "null"]},
        "summary": {"type": ["string", "null"]},
        "when": {"type": ["string", "null"]},
        "end_when": {"type": ["string", "null"]},
        "where": {"type": ["string", "null"]},
        "documents": STRING_ARRAY_SCHEMA,
        "tags": STRING_ARRAY_SCHEMA,
        "types": STRING_ARRAY_SCHEMA,
    },
    "required": [
        "need_user_input",
        "title",
        "summary",
        "when",
        "end_when",
        "where",
        "documents",
        "tags",
        "types",
    ],
    "additionalProperties": False,
}

CONTACT_UPDATE_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "need_user_input": NEED_USER_INPUT_SCHEMA,
        "contacts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "contact_name": {"type": "string"},
                    "birthday": {"type": ["string", "null"]},
                    "comments": {"type": ["string", "null"]},
                    "profession": {"type": ["string", "null"]},
                    "aliases": STRING_ARRAY_SCHEMA,
                    "emails": STRING_ARRAY_SCHEMA,
                    "phones": STRING_ARRAY_SCHEMA,
                    "links": STRING_ARRAY_SCHEMA,
                    "tags": STRING_ARRAY_SCHEMA,
                },
                "required": [
                    "contact_name",
                    "birthday",
                    "comments",
                    "profession",
                    "aliases",
                    "emails",
                    "phones",
                    "links",
                    "tags",
                ],
                "additionalProperties": False,
            },
        },
        "relationships": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "from_contact_name": {"type": "string"},
                    "to_contact_name": {"type": "string"},
                    "relationship_type": {"type": "string"},
                    "reciprocal_type": {"type": "string"},
                },
                "required": [
                    "from_contact_name",
                    "to_contact_name",
                    "relationship_type",
                    "reciprocal_type",
                ],
                "additionalProperties": False,
            },
        },
        "contact_place_links": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "contact_name": {"type": "string"},
                    "place_text": {"type": "string"},
                    "place_role": {"type": ["string", "null"]},
                },
                "required": ["contact_name", "place_text", "place_role"],
                "additionalProperties": False,
            },
        },
        "summary": {"type": "string"},
    },
    "required": [
        "need_user_input",
        "contacts",
        "relationships",
        "contact_place_links",
        "summary",
    ],
    "additionalProperties": False,
}

TAG_SUGGESTION_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"tags": STRING_ARRAY_SCHEMA},
    "required": ["tags"],
    "additionalProperties": False,
}

MEETING_TRANSCRIPT_SUMMARY_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "action_items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "task": {"type": "string"},
                    "assignee_name": {"type": ["string", "null"]},
                    "assignee_email": {"type": ["string", "null"]},
                    "due_date": {"type": ["string", "null"]},
                    "evidence": {"type": ["string", "null"]},
                },
                "required": [
                    "task",
                    "assignee_name",
                    "assignee_email",
                    "due_date",
                    "evidence",
                ],
                "additionalProperties": False,
            },
        },
    },
    "required": ["summary", "action_items"],
    "additionalProperties": False,
}

PEOPLE_EXTRACTION_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"people": STRING_ARRAY_SCHEMA},
    "required": ["people"],
    "additionalProperties": False,
}

COLLECTIVE_SELECTOR_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "selectors": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string"},
                    "value": {"type": "string"},
                    "raw": {"type": "string"},
                    "deterministic": {"type": "boolean"},
                },
                "required": ["kind", "value", "raw", "deterministic"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["selectors"],
    "additionalProperties": False,
}

EVENT_PARTICIPANT_FILTER_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "participants": STRING_ARRAY_SCHEMA,
        "excluded": STRING_ARRAY_SCHEMA,
    },
    "required": ["participants", "excluded"],
    "additionalProperties": False,
}

CONTACT_DISAMBIGUATION_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "decision": {"type": "string"},
        "candidate_number": {"type": ["integer", "null"]},
        "new_contact": {"type": "boolean"},
        "confidence": {"type": "string"},
        "reasoning": {"type": "string"},
    },
    "required": ["decision", "candidate_number", "new_contact", "confidence", "reasoning"],
    "additionalProperties": False,
}

PROFESSION_INFERENCE_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"profession": {"type": ["string", "null"]}},
    "required": ["profession"],
    "additionalProperties": False,
}

PROPOSED_EVENT_ENRICHMENT_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "suggested_title": {"type": "string"},
        "suggested_summary": {"type": "string"},
        "suggested_contact_ids": STRING_ARRAY_SCHEMA,
        "confidence": {"type": "string", "enum": ["medium", "high"]},
        "reason": {"type": "string"},
        "recurrence_hint": {"type": ["string", "null"]},
    },
    "required": [
        "suggested_title",
        "suggested_summary",
        "suggested_contact_ids",
        "confidence",
        "reason",
        "recurrence_hint",
    ],
    "additionalProperties": False,
}

PROPOSED_EVENT_OVERLAP_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "blocks_proposal": {"type": "boolean"},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        "reason": {"type": "string"},
    },
    "required": ["blocks_proposal", "confidence", "reason"],
    "additionalProperties": False,
}

RELATIONSHIP_PAIRS_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "relationships": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "person_text": {"type": "string"},
                    "anchor_text": {"type": "string"},
                    "relationship_hint": {"type": "string"},
                },
                "required": ["person_text", "anchor_text", "relationship_hint"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["relationships"],
    "additionalProperties": False,
}

RELATIONSHIP_TYPES_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "type": {"type": ["string", "null"]},
        "other_type": {"type": ["string", "null"]},
    },
    "required": ["type", "other_type"],
    "additionalProperties": False,
}

NESTED_RELATIONSHIP_SELECTION_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "candidate_numbers": {
            "type": "array",
            "items": {"type": "integer"},
        },
        "collective_reference": {"type": "boolean"},
        "confidence": {"type": "string"},
        "reasoning": {"type": "string"},
    },
    "required": ["candidate_numbers", "collective_reference", "confidence", "reasoning"],
    "additionalProperties": False,
}

EVENT_MATCH_INTENT_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "intent": {"type": "string"},
        "confidence": {"type": "string"},
    },
    "required": ["intent", "confidence"],
    "additionalProperties": False,
}

EVENT_FOLLOWUP_STRATEGY_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {"type": "string"},
        "fields": STRING_ARRAY_SCHEMA,
        "confidence": {"type": "string"},
    },
    "required": ["action", "fields", "confidence"],
    "additionalProperties": False,
}

EVENT_FIELD_INFERENCE_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "fields": STRING_ARRAY_SCHEMA,
        "confidence": {"type": "string"},
    },
    "required": ["fields", "confidence"],
    "additionalProperties": False,
}

EVENT_RELATIONSHIP_SUGGESTION_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "relationships": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "from_person": {"type": "string"},
                    "to_person": {"type": "string"},
                    "relationship_type": {"type": "string"},
                    "reciprocal_type": {"type": "string"},
                    "confidence": {"type": "string"},
                    "reasoning": {"type": "string"},
                },
                "required": [
                    "from_person",
                    "to_person",
                    "relationship_type",
                    "reciprocal_type",
                    "confidence",
                    "reasoning",
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": ["relationships"],
    "additionalProperties": False,
}

DAILY_BRIEFING_RESEARCH_PLAN_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "should_research": {"type": "boolean"},
        "reason": {"type": "string"},
        "targets": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "why": {"type": "string"},
                },
                "required": ["query", "why"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["should_research", "reason", "targets"],
    "additionalProperties": False,
}

FACT_EXTRACTION_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "facts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "content": {"type": "string"},
                    "category": {"type": "string"},
                    "importance": {"type": "integer"},
                    "fact_mode": {"type": "string"},
                    "rule_type": {"type": ["string", "null"]},
                    "rule_scope": STRING_ARRAY_SCHEMA,
                    "rule_payload": {
                        "type": "object",
                        "additionalProperties": True,
                    },
                    "action": {"type": "string"},
                    "target_fact_id": {"type": ["string", "null"]},
                },
                "required": [
                    "content",
                    "category",
                    "importance",
                    "fact_mode",
                    "rule_type",
                    "rule_scope",
                    "rule_payload",
                    "action",
                    "target_fact_id",
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": ["facts"],
    "additionalProperties": False,
}

DOCUMENT_DATE_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "date": {"type": ["string", "null"]},
    },
    "required": ["date"],
    "additionalProperties": False,
}
