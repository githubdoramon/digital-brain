"""Shared lexical hints for personal memory-graph routing and retrieval."""

import re

PERSONAL_DOCUMENT_TERMS = frozenset(
    {
        "doc",
        "docs",
        "document",
        "documents",
        "file",
        "files",
        "paperwork",
        "record",
        "records",
        "report",
        "reports",
        "result",
        "results",
        "prescription",
        "prescriptions",
        "rx",
        "spec",
        "specs",
        "specification",
        "specifications",
        "lab",
        "labs",
        "medical",
        "glasses",
        "eyeglasses",
        "lens",
        "lenses",
        "id",
        "identity",
    }
)


def extract_normalized_tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", (text or "").lower()))


def contains_personal_document_term(text: str) -> bool:
    return any(term in extract_normalized_tokens(text) for term in PERSONAL_DOCUMENT_TERMS)
