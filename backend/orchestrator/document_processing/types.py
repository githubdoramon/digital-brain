from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ParsedSection:
    title: str | None
    text: str
    page_start: int | None = None
    page_end: int | None = None
    content_type: str = "paragraph"


@dataclass
class DocumentParseResult:
    text: str
    raw_text: str
    parser_used: str
    warnings: list[str] = field(default_factory=list)
    pages: list[str] = field(default_factory=list)
    sections: list[ParsedSection] = field(default_factory=list)
    tables: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    ocr_applied: bool = False


@dataclass
class NormalizedDocument:
    normalized_text: str
    raw_extracted_text: str
    parser_used: str
    sections: list[ParsedSection] = field(default_factory=list)
    pages: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class StructuredChunk:
    chunk_text: str
    section_title: str | None
    page_start: int | None
    page_end: int | None
    content_type: str
    parser_used: str
