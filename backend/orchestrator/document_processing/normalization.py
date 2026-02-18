from __future__ import annotations

import re

from .types import DocumentParseResult, NormalizedDocument, ParsedSection

HEADER_FOOTER_REPEAT_THRESHOLD = 0.6


def normalize_document(parse_result: DocumentParseResult, *, max_chars: int) -> NormalizedDocument:
    pages = [page for page in parse_result.pages if page and page.strip()]
    cleaned_pages = _strip_repeated_headers_footers(pages)

    working_text = "\n\n".join(cleaned_pages).strip() if cleaned_pages else parse_result.text
    if not working_text.strip():
        working_text = parse_result.text

    normalized_text = _normalize_text(working_text)
    normalized_sections = _normalize_sections(parse_result.sections)

    if normalized_sections:
        joined = _build_section_markdown(normalized_sections)
        if joined:
            normalized_text = joined

    clipped = normalized_text[:max_chars].strip()
    if not clipped:
        clipped = _normalize_text(parse_result.text)[:max_chars].strip()

    metadata = dict(parse_result.metadata or {})
    metadata["parser_used"] = parse_result.parser_used
    metadata["normalization"] = {
        "source_pages": len(pages),
        "clean_pages": len(cleaned_pages),
    }
    return NormalizedDocument(
        normalized_text=clipped,
        raw_extracted_text=(parse_result.raw_text or "")[:max_chars],
        parser_used=parse_result.parser_used,
        sections=normalized_sections,
        pages=cleaned_pages,
        warnings=list(parse_result.warnings or []),
        metadata=metadata,
    )


def _normalize_sections(sections: list[ParsedSection]) -> list[ParsedSection]:
    normalized: list[ParsedSection] = []
    for section in sections:
        text = _normalize_text(section.text)
        if not text:
            continue
        title = (
            section.title.strip()
            if isinstance(section.title, str) and section.title.strip()
            else None
        )
        normalized.append(
            ParsedSection(
                title=title,
                text=text,
                page_start=section.page_start,
                page_end=section.page_end,
                content_type=section.content_type,
            )
        )
    return normalized


def _build_section_markdown(sections: list[ParsedSection]) -> str:
    parts: list[str] = []
    for section in sections:
        if section.title:
            parts.append(f"## {section.title}")
        parts.append(section.text)
    return "\n\n".join(part for part in parts if part.strip()).strip()


def _normalize_text(text: str) -> str:
    if not text:
        return ""
    cleaned = text.replace("\r\n", "\n").replace("\r", "\n")
    cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", cleaned)

    cleaned = re.sub(r"(\w)-\n(\w)", r"\1\2", cleaned)
    cleaned = _merge_wrapped_lines(cleaned)

    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    return cleaned.strip()


def _merge_wrapped_lines(text: str) -> str:
    lines = text.split("\n")
    merged: list[str] = []
    idx = 0
    while idx < len(lines):
        line = lines[idx].strip()
        if not line:
            merged.append("")
            idx += 1
            continue

        if idx + 1 >= len(lines):
            merged.append(line)
            idx += 1
            continue

        next_line = lines[idx + 1].strip()
        if _should_merge_lines(line, next_line):
            merged.append(f"{line} {next_line}".strip())
            idx += 2
            continue

        merged.append(line)
        idx += 1

    return "\n".join(merged)


def _should_merge_lines(current: str, nxt: str) -> bool:
    if not current or not nxt:
        return False
    if current.endswith((".", ";", ":", "?", "!")):
        return False
    if current.startswith("#") or nxt.startswith("#"):
        return False
    if re.match(r"^[-*]\s", current):
        return False
    if re.match(r"^\d+[.)]\s", nxt):
        return False
    if len(current) < 20:
        return False
    return nxt[0].islower() or nxt[0].isdigit() or nxt[0] in {"(", "["}


def _strip_repeated_headers_footers(pages: list[str]) -> list[str]:
    if len(pages) <= 1:
        return pages

    first_lines: dict[str, int] = {}
    last_lines: dict[str, int] = {}
    for page in pages:
        lines = [line.strip() for line in page.splitlines() if line.strip()]
        if not lines:
            continue
        first_lines[lines[0]] = first_lines.get(lines[0], 0) + 1
        last_lines[lines[-1]] = last_lines.get(lines[-1], 0) + 1

    min_repeats = max(2, int(len(pages) * HEADER_FOOTER_REPEAT_THRESHOLD))
    repeated_headers = {line for line, count in first_lines.items() if count >= min_repeats}
    repeated_footers = {line for line, count in last_lines.items() if count >= min_repeats}

    cleaned_pages: list[str] = []
    for page in pages:
        lines = [line.strip() for line in page.splitlines() if line.strip()]
        if lines and lines[0] in repeated_headers:
            lines = lines[1:]
        if lines and lines[-1] in repeated_footers:
            lines = lines[:-1]
        cleaned_pages.append("\n".join(lines).strip())
    return cleaned_pages
