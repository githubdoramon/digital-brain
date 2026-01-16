"""
Contact Resolution Agent

Resolves person mentions in text to contacts in the database.

Supports:
- Direct name matching (fuzzy search)
- Relationship resolution ("my daughter" → Emma)
- Nested relationships ("my daughter's doctor" → Dr. Smith via Emma)
- LLM disambiguation when multiple matches exist

CRITICAL: This module NEVER hallucinates. It only returns:
- Resolved contacts that exist in the database
- Candidates when multiple matches exist
- "new" status when no match is found

Main exports:
- extract_people_from_text: Extract person mentions from text
- resolve_contact: Resolve single person to contact
- resolve_contacts_from_text: Complete pipeline (extract + resolve)
"""

from agents.contacts.resolver import (
    extract_people_from_text,
    resolve_contact,
    resolve_contacts_from_text,
)

__all__ = [
    "extract_people_from_text",
    "resolve_contact",
    "resolve_contacts_from_text",
]
