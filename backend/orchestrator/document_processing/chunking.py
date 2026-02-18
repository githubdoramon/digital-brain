from __future__ import annotations

from .types import NormalizedDocument, ParsedSection, StructuredChunk


def chunk_normalized_document(
    document: NormalizedDocument,
    *,
    chunk_chars: int,
    overlap_chars: int,
    max_chunks: int,
) -> list[StructuredChunk]:
    sections = document.sections
    parser_used = document.parser_used

    if sections:
        chunks = _chunk_sections(
            sections,
            parser_used=parser_used,
            chunk_chars=chunk_chars,
            overlap_chars=overlap_chars,
            max_chunks=max_chunks,
        )
        if chunks:
            return chunks

    text_chunks = _split_text_with_overlap(
        document.normalized_text,
        chunk_chars=chunk_chars,
        overlap_chars=overlap_chars,
        max_chunks=max_chunks,
    )
    return [
        StructuredChunk(
            chunk_text=text,
            section_title=None,
            page_start=None,
            page_end=None,
            content_type="paragraph",
            parser_used=parser_used,
        )
        for text in text_chunks
    ]


def _chunk_sections(
    sections: list[ParsedSection],
    *,
    parser_used: str,
    chunk_chars: int,
    overlap_chars: int,
    max_chunks: int,
) -> list[StructuredChunk]:
    chunks: list[StructuredChunk] = []
    for section in sections:
        if len(chunks) >= max_chunks:
            break
        section_chunks = _split_text_with_overlap(
            section.text,
            chunk_chars=chunk_chars,
            overlap_chars=overlap_chars,
            max_chunks=max_chunks - len(chunks),
        )
        if not section_chunks:
            continue
        for body in section_chunks:
            prefixed = body
            if section.title:
                prefixed = f"{section.title}\n\n{body}".strip()
            chunks.append(
                StructuredChunk(
                    chunk_text=prefixed,
                    section_title=section.title,
                    page_start=section.page_start,
                    page_end=section.page_end,
                    content_type=section.content_type,
                    parser_used=parser_used,
                )
            )
            if len(chunks) >= max_chunks:
                break
    return chunks


def _split_text_with_overlap(
    text: str,
    *,
    chunk_chars: int,
    overlap_chars: int,
    max_chunks: int,
) -> list[str]:
    cleaned = (text or "").strip()
    if not cleaned:
        return []

    safe_chunk_chars = max(200, chunk_chars)
    safe_overlap = max(0, min(overlap_chars, safe_chunk_chars - 1))
    step = max(1, safe_chunk_chars - safe_overlap)
    allowed_chunks = max(1, max_chunks)
    chunks: list[str] = []
    cursor = 0
    text_len = len(cleaned)

    while cursor < text_len and len(chunks) < allowed_chunks:
        end = min(text_len, cursor + safe_chunk_chars)
        if end < text_len:
            split_idx = cleaned.rfind("\n", cursor, end)
            if split_idx <= cursor:
                split_idx = cleaned.rfind(" ", cursor, end)
            if split_idx > cursor + safe_chunk_chars // 2:
                end = split_idx + 1
        chunk = cleaned[cursor:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= text_len:
            break
        cursor = max(cursor + step, end - safe_overlap)
    return chunks
