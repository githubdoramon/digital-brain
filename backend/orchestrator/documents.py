from __future__ import annotations

import json
import mimetypes
import os
import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import UploadFile

from db import get_conn
from document_processing import (
    NormalizedDocument,
    ParsedSection,
    StructuredChunk,
    chunk_normalized_document,
    normalize_document,
    parse_document,
)
from embeddings import embed_text
from llm_config import get_smart_model
from observability.logger import get_runtime_logger
from search_normalization import normalize_search_list, normalize_search_text
from tags_manager import _merge_tag_lists, _normalize_strings, _suggest_additional_tags

logger = get_runtime_logger(__name__)


class DocumentProcessingError(RuntimeError):
    """Raised when an uploaded document cannot be processed."""


DOCUMENT_STORAGE_DIR = Path(
    os.getenv("DOCUMENT_STORAGE_DIR", "/app/storage/documents")
).expanduser()

MAX_CONTENT_CHARS = int(os.getenv("DOCUMENT_MAX_CONTENT_CHARS", "80000"))
MAX_EMBED_CHARS = int(os.getenv("DOCUMENT_EMBED_MAX_CHARS", "8000"))
EMBED_CHUNK_CHARS = int(os.getenv("DOCUMENT_EMBED_CHUNK_CHARS", "1200"))
EMBED_CHUNK_OVERLAP_CHARS = int(os.getenv("DOCUMENT_EMBED_CHUNK_OVERLAP_CHARS", "200"))
EMBED_MAX_CHUNKS = int(os.getenv("DOCUMENT_EMBED_MAX_CHUNKS", "64"))
CHUNK_SEARCH_CANDIDATE_MULTIPLIER = int(
    os.getenv("DOCUMENT_CHUNK_SEARCH_CANDIDATE_MULTIPLIER", "8")
)
CHUNK_SCORE_BEST_WEIGHT = float(os.getenv("DOCUMENT_CHUNK_SCORE_BEST_WEIGHT", "0.75"))
CHUNK_SCORE_MEAN_WEIGHT = float(os.getenv("DOCUMENT_CHUNK_SCORE_MEAN_WEIGHT", "0.25"))
COMBINED_SCORE_CHUNK_WEIGHT = float(os.getenv("DOCUMENT_COMBINED_SCORE_CHUNK_WEIGHT", "0.85"))
COMBINED_SCORE_DOC_WEIGHT = float(os.getenv("DOCUMENT_COMBINED_SCORE_DOC_WEIGHT", "0.15"))
EMBED_CHUNK_METADATA_MAX_CHARS = int(os.getenv("DOCUMENT_EMBED_CHUNK_METADATA_MAX_CHARS", "240"))
EMBED_CHUNK_METADATA_TAG_LIMIT = int(os.getenv("DOCUMENT_EMBED_CHUNK_METADATA_TAG_LIMIT", "4"))
MAX_TRANSLATION_CHUNK_CHARS = int(os.getenv("DOCUMENT_TRANSLATION_CHUNK_CHARS", "10000"))
MAX_TITLE_PROMPT_CHARS = int(os.getenv("DOCUMENT_TITLE_PROMPT_CHARS", "2000"))
MAX_DATE_PROMPT_CHARS = int(os.getenv("DOCUMENT_DATE_PROMPT_CHARS", "2000"))
MAX_DESCRIPTION_PROMPT_CHARS = int(os.getenv("DOCUMENT_DESCRIPTION_PROMPT_CHARS", "1200"))

OLLAMA_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "60"))


def _call_llm_text(
    prompt: str,
    *,
    system_prompt: str,
    timeout: int,
) -> str:
    from llm_helpers import call_llm

    return call_llm(
        prompt,
        system_prompt=system_prompt,
        model=get_smart_model(),
        use_fast_model=False,
        timeout=timeout,
    )


def _call_llm_json_response(
    prompt: str,
    *,
    system_prompt: str,
    timeout: int,
    response_format: dict[str, Any] | None = None,
) -> Any:
    from llm_helpers import call_llm_json

    return call_llm_json(
        prompt,
        system_prompt=system_prompt,
        model=get_smart_model(),
        use_fast_model=False,
        timeout=timeout,
        response_format=response_format,
    )


@dataclass
class StoredFileInfo:
    document_id: str
    path: Path
    file_name: str
    mime_type: str | None
    size: int


@dataclass
class DocumentPrepared:
    title: str
    description: str
    tags: list[str]
    document_date: datetime | None
    embedding: Sequence[float]
    raw_metadata: dict[str, Any]
    suggested_tags: list[str]
    generated_title: str | None
    generated_description: str | None
    inferred_date: datetime | None
    chunk_embeddings: list[DocumentChunkEmbedding]


@dataclass
class DocumentChunkEmbedding:
    chunk_index: int
    chunk_text: str
    embedding: Sequence[float]
    section_title: str | None = None
    page_start: int | None = None
    page_end: int | None = None
    content_type: str | None = None
    parser_used: str | None = None


def _ensure_document_storage_dir() -> Path:
    """Create document storage directory only when file operations need it."""
    DOCUMENT_STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    return DOCUMENT_STORAGE_DIR


def ingest_document(
    *,
    title: str | None,
    tags: Sequence[str] | None,
    contact_ids: Sequence[str] | None,
    description: str | None,
    upload: UploadFile,
    document_date: datetime | None = None,
    user_email: str | None = None,
) -> dict[str, Any]:
    """Persist a new document, extracting text, embeddings, and tags."""
    provided_title = (title or "").strip()
    if not upload:
        raise DocumentProcessingError("File upload is required")

    document_id = f"doc:{uuid4().hex}"
    stored = _store_upload(upload, document_id)

    extraction = _extract_and_normalize_document(stored.path, stored.mime_type)
    content_text = extraction["normalized_content"]
    if not content_text:
        fallback = filter(None, [description, provided_title, stored.file_name, document_id])
        content_text = " ".join(fallback)

    base_raw_metadata = {
        "original_filename": upload.filename,
        "provided_mime_type": upload.content_type,
        "stored_mime_type": stored.mime_type,
        "file_size": stored.size,
    }
    if extraction.get("raw_extracted_text"):
        base_raw_metadata["raw_extracted_text"] = extraction["raw_extracted_text"]
    if extraction.get("parser_used"):
        base_raw_metadata["parser_used"] = extraction["parser_used"]
    if extraction.get("parser_warnings"):
        base_raw_metadata["parser_warnings"] = extraction["parser_warnings"]
    if extraction.get("normalized_sections"):
        base_raw_metadata["normalized_sections"] = extraction["normalized_sections"]
    if extraction.get("normalization_metadata"):
        base_raw_metadata["normalization_metadata"] = extraction["normalization_metadata"]
    content_text, base_raw_metadata = prepare_document_content_for_storage(
        content_text,
        document_id=document_id,
        raw_metadata=base_raw_metadata,
    )

    tags_input = tags if tags is not None else []

    prepared = _build_document_fields(
        document_id=document_id,
        content_text=content_text,
        tags=tags_input,
        provided_title=provided_title,
        provided_description=(description or "").strip(),
        document_date=document_date,
        file_name=stored.file_name,
        raw_metadata=base_raw_metadata,
    )

    merged_contact_ids = _merge_document_contact_ids(
        contact_ids,
        _infer_document_contact_ids(
            user_email=user_email,
            title=prepared.title,
            description=prepared.description,
            file_name=stored.file_name,
            content=content_text,
        ),
    )

    row = _upsert_document(
        document_id=document_id,
        title=prepared.title,
        tags=prepared.tags,
        contact_ids=merged_contact_ids,
        description=prepared.description,
        stored=stored,
        content=content_text,
        embedding=prepared.embedding,
        chunk_embeddings=prepared.chunk_embeddings,
        document_date=prepared.document_date,
        raw_metadata=prepared.raw_metadata,
        replace_contact_links=True,
    )
    _enqueue_document_tag_enrichment(document_id)
    return _row_to_document(row, include_metadata=True, include_content=True)


def list_documents(
    limit: int = 200,
    offset: int = 0,
    contact_ids: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    normalized_contact_ids = _normalize_contact_ids(contact_ids)
    with get_conn() as conn, conn.cursor() as cur:
        filters: list[str] = []
        params: list[Any] = []
        if normalized_contact_ids:
            filters.append(
                "EXISTS (SELECT 1 FROM document_contacts dc WHERE dc.document_id = documents.document_id AND dc.contact_id = ANY(%s))"
            )
            params.append(normalized_contact_ids)
        where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""
        cur.execute(
            f"""
            SELECT
                document_id,
                title,
                tags,
                description,
                file_name,
                file_mime,
                file_size,
                document_date,
                created_at,
                updated_at,
                content
            FROM documents
            {where_clause}
            ORDER BY created_at DESC
            LIMIT %s OFFSET %s
            """,
            (*params, limit, offset),
        )
        rows = cur.fetchall()
        _attach_linked_contacts(cur, rows)
    return [_row_to_document(row) for row in rows]


def get_document(document_id: str) -> dict[str, Any] | None:
    if not document_id:
        return None
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                document_id,
                title,
                tags,
                description,
                file_name,
                file_mime,
                file_size,
                document_date,
                created_at,
                updated_at,
                content,
                raw_metadata
            FROM documents
            WHERE document_id = %s
            """,
            (document_id,),
        )
        row = cur.fetchone()
        if row:
            _attach_linked_contacts(cur, [row])
    if not row:
        return None
    return _row_to_document(row, include_metadata=True, include_content=True)


def update_document_metadata(
    document_id: str,
    *,
    title: str | None = None,
    tags: Sequence[str] | None = None,
    contact_ids: Sequence[str] | None = None,
    description: str | None = None,
    document_date: datetime | None = None,
) -> dict[str, Any] | None:
    if not document_id:
        return None

    row = _load_document_row(document_id)

    if not row:
        return None

    file_path = row.get("file_path")
    if not file_path:
        raise DocumentProcessingError("Stored file reference is missing for the document")

    path_obj = Path(file_path)
    derived_name = path_obj.name if path_obj.name not in {"", "."} else None
    file_name = row.get("file_name") or derived_name or document_id
    file_mime = row.get("file_mime")
    size_candidate = row.get("file_size")
    if size_candidate is None:
        size_candidate = path_obj.stat().st_size if path_obj.exists() else 0
    try:
        size_int = int(size_candidate)
    except (TypeError, ValueError):
        size_int = 0

    stored = StoredFileInfo(
        document_id=document_id,
        path=path_obj,
        file_name=file_name,
        mime_type=file_mime,
        size=size_int,
    )

    existing_raw = row.get("raw_metadata")
    if isinstance(existing_raw, str):
        try:
            existing_raw = json.loads(existing_raw)
        except json.JSONDecodeError:
            existing_raw = {"raw": existing_raw}
    if not isinstance(existing_raw, dict):
        existing_raw = {}
    raw_metadata = dict(existing_raw)
    if file_name and "original_filename" not in raw_metadata:
        raw_metadata["original_filename"] = file_name
    if file_mime:
        raw_metadata["stored_mime_type"] = file_mime
    raw_metadata["file_size"] = size_int

    title_input = title if title is not None else row.get("title")
    provided_title = (title_input or "").strip()
    description_input = description if description is not None else row.get("description")
    provided_description = (description_input or "").strip()

    tags_input = tags if tags is not None else row.get("tags") or []

    content_text = (row.get("content") or "")[:MAX_CONTENT_CHARS]
    if not content_text and stored.path.exists():
        extracted_payload = _extract_and_normalize_document(stored.path, stored.mime_type)
        recovered = (extracted_payload.get("normalized_content") or "").strip()
        if recovered:
            content_text = recovered[:MAX_CONTENT_CHARS]
            if extracted_payload.get("raw_extracted_text"):
                raw_metadata["raw_extracted_text"] = extracted_payload["raw_extracted_text"]
            if extracted_payload.get("parser_used"):
                raw_metadata["parser_used"] = extracted_payload["parser_used"]
            if extracted_payload.get("parser_warnings"):
                raw_metadata["parser_warnings"] = extracted_payload["parser_warnings"]
            if extracted_payload.get("normalized_sections"):
                raw_metadata["normalized_sections"] = extracted_payload["normalized_sections"]
            if extracted_payload.get("normalization_metadata"):
                raw_metadata["normalization_metadata"] = extracted_payload["normalization_metadata"]
            logger.info(
                "[documents] recovered missing content from file document_id=%s chars=%s",
                document_id,
                len(content_text),
            )
    if not content_text:
        fallback_parts: list[str] = []
        for candidate in (
            provided_description,
            row.get("description"),
            provided_title,
            row.get("title"),
            file_name,
            document_id,
        ):
            if not candidate:
                continue
            text = candidate.strip() if isinstance(candidate, str) else str(candidate).strip()
            if text:
                fallback_parts.append(text)
        fallback_joined = " ".join(fallback_parts)
        content_text = fallback_joined[:MAX_CONTENT_CHARS]

    prepared = _build_document_fields(
        document_id=document_id,
        content_text=content_text,
        tags=tags_input,
        provided_title=provided_title,
        provided_description=provided_description,
        document_date=document_date if document_date is not None else row.get("document_date"),
        file_name=file_name,
        raw_metadata=raw_metadata,
    )

    row = _upsert_document(
        document_id=document_id,
        title=prepared.title,
        tags=prepared.tags,
        contact_ids=contact_ids,
        description=prepared.description,
        stored=stored,
        content=content_text,
        embedding=prepared.embedding,
        chunk_embeddings=prepared.chunk_embeddings,
        document_date=prepared.document_date,
        raw_metadata=prepared.raw_metadata,
        replace_contact_links=contact_ids is not None,
    )
    _enqueue_document_tag_enrichment(document_id)
    return _row_to_document(row, include_metadata=True, include_content=True)


def search_documents(
    query: str,
    *,
    tags: Sequence[str] | None = None,
    contact_ids: Sequence[str] | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    search_query = normalize_search_text(query)
    normalized_tags = normalize_search_list(tags)
    normalized_contact_ids = _normalize_contact_ids(contact_ids)

    scores: dict[str, float] = {}
    if search_query:
        vec_scores = _vector_search_documents(search_query, 50)
        bm_scores = _bm25_search_documents(search_query, 50)
        for doc_id, score in vec_scores.items():
            scores[doc_id] = scores.get(doc_id, 0.0) + 0.6 * score
        for doc_id, score in bm_scores.items():
            scores[doc_id] = scores.get(doc_id, 0.0) + 0.4 * score

    if normalized_tags:
        tag_scores = _tag_search_documents(normalized_tags)
        if scores:
            for doc_id in list(scores.keys()):
                if doc_id not in tag_scores:
                    # Enforce tag filter when both query and tags provided
                    scores.pop(doc_id, None)
                else:
                    scores[doc_id] += tag_scores[doc_id]
        else:
            scores = tag_scores

    if normalized_contact_ids:
        contact_scoped_ids = _search_document_ids_by_contacts(normalized_contact_ids)
        if scores:
            scores = {
                doc_id: score for doc_id, score in scores.items() if doc_id in contact_scoped_ids
            }
        else:
            scores = dict.fromkeys(contact_scoped_ids, 1.0)

    if not scores:
        return []

    sorted_ids = [
        doc_id for doc_id, _ in sorted(scores.items(), key=lambda item: item[1], reverse=True)
    ][: max(1, limit)]

    rows = _fetch_documents(sorted_ids)
    order = {doc_id: idx for idx, doc_id in enumerate(sorted_ids)}
    rows.sort(key=lambda r: order.get(r["document_id"], len(sorted_ids)))
    return [_row_to_document(row) for row in rows]


def delete_document(document_id: str) -> bool:
    if not document_id:
        return False
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT file_path
            FROM documents
            WHERE document_id = %s
            """,
            (document_id,),
        )
        row = cur.fetchone()
        if not row:
            return False
        file_path = Path(row["file_path"]) if row.get("file_path") else None
        cur.execute("DELETE FROM documents WHERE document_id = %s", (document_id,))
        conn.commit()

    if file_path and file_path.exists():
        try:
            file_path.unlink()
        except OSError as exc:
            logger.warning("[documents] Failed to delete file %s: %s", file_path, exc, exc_info=exc)
    return True


def get_document_file(document_id: str) -> dict[str, Any] | None:
    if not document_id:
        return None
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT file_path, file_name, file_mime
            FROM documents
            WHERE document_id = %s
            """,
            (document_id,),
        )
        row = cur.fetchone()
    if not row:
        return None
    return {
        "file_path": row.get("file_path"),
        "file_name": row.get("file_name"),
        "file_mime": row.get("file_mime"),
    }


def _normalize_contact_ids(contact_ids: Sequence[str] | None) -> list[str]:
    return [str(contact_id).strip() for contact_id in (contact_ids or []) if str(contact_id).strip()]


def _search_document_ids_by_contacts(contact_ids: Sequence[str] | None) -> set[str]:
    normalized_contact_ids = _normalize_contact_ids(contact_ids)
    if not normalized_contact_ids:
        return set()
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT document_id
            FROM document_contacts
            WHERE contact_id = ANY(%s)
            """,
            (normalized_contact_ids,),
        )
        return {str(row["document_id"]) for row in cur.fetchall()}


def _merge_document_contact_ids(*contact_groups: Sequence[str] | None) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for group in contact_groups:
        for contact_id in _normalize_contact_ids(group):
            if contact_id in seen:
                continue
            seen.add(contact_id)
            merged.append(contact_id)
    return merged


def _infer_document_contact_ids(
    *,
    user_email: str | None,
    title: str,
    description: str | None,
    file_name: str | None,
    content: str,
) -> list[str]:
    runtime_email = str(user_email or "").strip()
    if not runtime_email:
        return []

    inference_parts = [
        str(title or "").strip(),
        str(description or "").strip(),
        str(file_name or "").strip(),
        str(content or "").strip()[:2500],
    ]
    inference_text = "\n".join(part for part in inference_parts if part).strip()
    if not inference_text:
        return []

    try:
        from contact_resolution_service import resolve_contacts_request

        resolution = resolve_contacts_request(
            {
                "text": inference_text,
                "user_email": runtime_email,
                "mode": "minimal",
            }
        )
    except Exception as exc:
        logger.warning("[documents] contact inference failed: %s", exc, exc_info=exc)
        return []

    resolved = resolution.get("resolved_contacts") or []
    inferred_ids = [
        str(item.get("contact_id") or "").strip()
        for item in resolved
        if isinstance(item, dict) and str(item.get("contact_id") or "").strip()
    ]
    if not inferred_ids:
        return []

    logger.info(
        "[documents] inferred contact links count=%s title=%r",
        len(inferred_ids),
        title,
    )
    return list(dict.fromkeys(inferred_ids))


def _set_document_contacts(
    cur,
    *,
    document_id: str,
    contact_ids: Sequence[str] | None,
) -> None:
    normalized_contact_ids = list(dict.fromkeys(_normalize_contact_ids(contact_ids)))
    cur.execute("DELETE FROM document_contacts WHERE document_id = %s", (document_id,))
    if not normalized_contact_ids:
        return
    cur.executemany(
        """
        INSERT INTO document_contacts (document_id, contact_id)
        VALUES (%s, %s)
        ON CONFLICT (document_id, contact_id) DO UPDATE SET updated_at = NOW()
        """,
        [(document_id, contact_id) for contact_id in normalized_contact_ids],
    )


def _fetch_document_contacts_map(cur, document_ids: Sequence[str]) -> dict[str, list[dict[str, Any]]]:
    if not document_ids:
        return {}
    cur.execute(
        """
        SELECT
            dc.document_id,
            dc.contact_id,
            dc.role,
            dc.source,
            dc.confidence,
            c.display_name
        FROM document_contacts dc
        LEFT JOIN contacts c ON c.contact_id = dc.contact_id
        WHERE dc.document_id = ANY(%s)
        ORDER BY dc.document_id ASC, c.display_name ASC NULLS LAST, dc.contact_id ASC
        """,
        (list(document_ids),),
    )
    contacts_map: dict[str, list[dict[str, Any]]] = {}
    for row in cur.fetchall():
        contacts_map.setdefault(str(row["document_id"]), []).append(
            {
                "contact_id": str(row["contact_id"]),
                "display_name": str(row.get("display_name") or row["contact_id"]),
                "role": row.get("role"),
                "source": row.get("source"),
                "confidence": row.get("confidence"),
            }
        )
    return contacts_map


def _attach_linked_contacts(cur, rows: Sequence[dict[str, Any]]) -> None:
    if not rows:
        return
    contacts_map = _fetch_document_contacts_map(
        cur,
        [str(row["document_id"]) for row in rows if row.get("document_id")],
    )
    for row in rows:
        row["linked_contacts"] = contacts_map.get(str(row["document_id"]), [])


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _store_upload(upload: UploadFile, document_id: str) -> StoredFileInfo:
    original_name = upload.filename or document_id
    mime_type = upload.content_type or mimetypes.guess_type(original_name)[0]
    extension = _preferred_extension(original_name, mime_type)
    safe_name = _sanitize_filename(original_name) or f"{document_id}{extension or ''}"
    final_name = f"{document_id}{extension}" if extension else f"{document_id}_{safe_name}"

    target_path = _ensure_document_storage_dir() / final_name
    with open(target_path, "wb") as destination:
        upload.file.seek(0)
        shutil.copyfileobj(upload.file, destination)
    upload.file.close()

    size = target_path.stat().st_size if target_path.exists() else 0
    return StoredFileInfo(
        document_id=document_id,
        path=target_path,
        file_name=original_name,
        mime_type=mime_type,
        size=size,
    )


def _extract_text(path: Path, mime_type: str | None) -> str:
    extracted = _extract_and_normalize_document(path, mime_type)
    return extracted["normalized_content"]


def reprocess_document_content(
    *,
    path: Path,
    mime_type: str | None,
) -> dict[str, Any]:
    """Re-extract and normalize a document file for full re-embedding workflows."""
    return _extract_and_normalize_document(path, mime_type)


def prepare_document_content_for_storage(
    content: str,
    *,
    document_id: str | None = None,
    raw_metadata: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Apply storage-time normalization policies shared by ingest and re-embed workflows."""
    metadata = dict(raw_metadata or {})
    original_content = (content or "").strip()
    translated_content, translated_for_storage = _translate_content_for_storage(
        original_content,
        document_id=document_id,
    )
    stored_content = translated_content[:MAX_CONTENT_CHARS]
    _update_content_storage_metadata(
        metadata,
        original_content=original_content,
        stored_content=stored_content,
        translated=translated_for_storage,
    )
    return stored_content, metadata


def _extract_and_normalize_document(path: Path, mime_type: str | None) -> dict[str, Any]:
    parsed = parse_document(path, mime_type)
    normalized = normalize_document(parsed, max_chars=MAX_CONTENT_CHARS)
    section_payload = _serialize_normalized_sections(normalized.sections)
    return {
        "normalized_content": normalized.normalized_text,
        "raw_extracted_text": normalized.raw_extracted_text,
        "parser_used": normalized.parser_used,
        "parser_warnings": normalized.warnings,
        "normalized_sections": section_payload,
        "normalization_metadata": normalized.metadata,
    }


def _serialize_normalized_sections(sections: Sequence[ParsedSection]) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for section in sections:
        text = (section.text or "").strip()
        if not text:
            continue
        item: dict[str, Any] = {
            "title": section.title,
            "text": text,
            "page_start": section.page_start,
            "page_end": section.page_end,
            "content_type": section.content_type,
        }
        payload.append(item)
    return payload


def _deserialize_sections(raw_sections: Any) -> list[ParsedSection]:
    if not isinstance(raw_sections, list):
        return []
    sections: list[ParsedSection] = []
    for item in raw_sections:
        if not isinstance(item, dict):
            continue
        text = _safe_str(item.get("text"))
        if not text:
            continue
        title = _safe_str(item.get("title"))
        sections.append(
            ParsedSection(
                title=title,
                text=text,
                page_start=_safe_int(item.get("page_start")),
                page_end=_safe_int(item.get("page_end")),
                content_type=_safe_str(item.get("content_type")) or "paragraph",
            )
        )
    return sections


def _safe_str(value: Any) -> str | None:
    if isinstance(value, str):
        cleaned = value.strip()
        return cleaned or None
    return None


def _safe_int(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _suggest_document_date(content: str, fallback: str | None) -> datetime | None:
    cleaned = (content or fallback or "").strip()
    if not cleaned:
        return None
    excerpt = cleaned[:MAX_DATE_PROMPT_CHARS]
    system_prompt = (
        "You extract dates from documents. Find the primary date the document was written or refers to. "
        "Respond with a JSON object matching the supplied response schema."
    )
    user_prompt = (
        f"Document excerpt:\n{excerpt}\n\n"
        "If a clear date is present, respond with it in ISO-8601 (YYYY-MM-DD or YYYY-MM-DDTHH:MM)."
    )
    try:
        from llm_helpers import build_json_schema_response_format
        from llm_json_schemas import DOCUMENT_DATE_RESPONSE_SCHEMA

        parsed = _call_llm_json_response(
            user_prompt,
            system_prompt=system_prompt,
            timeout=OLLAMA_TIMEOUT,
            response_format=build_json_schema_response_format(
                name="document_date",
                schema=DOCUMENT_DATE_RESPONSE_SCHEMA,
            ),
        )
        candidate = _parse_date_response(json.dumps(parsed, ensure_ascii=False))
        if candidate:
            return candidate
    except Exception as exc:
        from llm_helpers import LLMUnavailableError

        if isinstance(exc, LLMUnavailableError):
            logger.warning("[documents] LLM unavailable while inferring date; using no inferred date")
            return None
        logger.warning("[documents] Failed to infer date: %s", exc, exc_info=exc)
    return None


def _parse_date_response(raw_content: str) -> datetime | None:
    try:
        loaded = json.loads(raw_content)
        if isinstance(loaded, dict):
            value = loaded.get("date")
        elif isinstance(loaded, list) and loaded:
            value = loaded[0]
        else:
            value = loaded
    except json.JSONDecodeError:
        value = raw_content.splitlines()[0].strip()

    if not value or value in {"null", "None"}:
        return None
    candidate = str(value).strip().strip('"')
    if not candidate:
        return None
    for formatter in (_parse_iso_datetime, _parse_flexible_date):
        dt = formatter(candidate)
        if dt:
            return dt
    return None


def _parse_iso_datetime(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


def _parse_flexible_date(value: str) -> datetime | None:
    try:
        return datetime.strptime(value, "%Y-%m-%d")
    except Exception:
        return None


def _build_document_fields(
    *,
    document_id: str,
    content_text: str,
    tags: Sequence[str],
    provided_title: str,
    provided_description: str,
    document_date: datetime | None,
    file_name: str | None,
    raw_metadata: dict[str, Any],
) -> DocumentPrepared:
    metadata = dict(raw_metadata or {})
    logger.debug("[documents] tags=%s", tags)
    normalized_tags = _normalize_strings(tags)
    logger.debug("[documents] normalized_tags=%s", normalized_tags)
    suggested_tags: list[str] = []

    final_description = provided_description
    generated_description: str | None = None
    if not final_description:
        generated_description = _summarize_description(content_text)
        final_description = generated_description or _default_description(
            content_text,
            provided_title or file_name or document_id,
        )

    final_title = provided_title
    generated_title: str | None = None
    if not final_title:
        generated_title = _suggest_title(content_text, fallback=file_name or document_id)
        final_title = generated_title or _derive_title_from_filename(file_name) or document_id

    final_date = document_date
    inferred_date: datetime | None = None
    if not final_date:
        inferred_date = _suggest_document_date(content_text, fallback=final_description)
        final_date = inferred_date

    embedding, chunk_embeddings = _generate_document_embeddings(
        {
            "document_id": document_id,
            "content": content_text,
            "description": final_description,
            "title": final_title,
            "tags": normalized_tags,
            "file_name": file_name,
        },
        raw_metadata=metadata,
    )

    metadata["suggested_tags"] = suggested_tags
    metadata["title_generated"] = bool(generated_title)
    metadata["date_generated"] = bool(inferred_date)
    metadata["description_generated"] = bool(generated_description)
    if generated_title:
        metadata["generated_title"] = generated_title
    else:
        metadata.pop("generated_title", None)
    if inferred_date:
        metadata["generated_date_iso"] = inferred_date.isoformat()
    else:
        metadata.pop("generated_date_iso", None)
    if generated_description:
        metadata["generated_description"] = generated_description
    else:
        metadata.pop("generated_description", None)

    return DocumentPrepared(
        title=final_title,
        description=final_description,
        tags=normalized_tags,
        document_date=final_date,
        embedding=embedding,
        raw_metadata=metadata,
        suggested_tags=suggested_tags,
        generated_title=generated_title,
        generated_description=generated_description,
        inferred_date=inferred_date,
        chunk_embeddings=chunk_embeddings,
    )


def _enqueue_document_tag_enrichment(document_id: str) -> None:
    try:
        import document_tag_jobs

        document_tag_jobs.enqueue_document_tag_enrichment(document_id)
    except Exception as exc:
        logger.warning(
            "[documents] Failed to queue tag enrichment for %s: %s",
            document_id,
            exc,
        )


def generate_and_persist_document_tags(document_id: str) -> dict[str, Any]:
    cleaned_document_id = str(document_id or "").strip()
    if not cleaned_document_id:
        raise ValueError("document_id is required")

    document = get_document(cleaned_document_id)
    if not document:
        return {
            "document_id": cleaned_document_id,
            "updated": False,
            "reason": "document_not_found",
        }

    raw_tags = list(document.get("tags") or [])
    normalized_tags = _normalize_strings(raw_tags)
    english_tags = _normalize_strings(_translate_tags_to_english(normalized_tags))
    content_text = str(document.get("content") or "").strip()
    suggested_tags = _suggest_additional_tags(content_text, english_tags)
    merged_tags = _merge_tag_lists(english_tags, suggested_tags)
    if merged_tags == normalized_tags and raw_tags == normalized_tags:
        return {
            "document_id": cleaned_document_id,
            "updated": False,
            "reason": "no_new_tags",
            "tags": normalized_tags,
        }

    raw_metadata = document.get("raw_metadata")
    if not isinstance(raw_metadata, dict):
        raw_metadata = {}
    metadata = dict(raw_metadata)
    metadata["suggested_tags"] = suggested_tags
    metadata["tags_enriched"] = True

    embedding, chunk_embeddings = _generate_document_embeddings(
        {
            "document_id": cleaned_document_id,
            "content": content_text,
            "description": document.get("description"),
            "title": document.get("title"),
            "tags": merged_tags,
            "file_name": document.get("file_name"),
        },
        raw_metadata=metadata,
    )

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE documents
            SET tags = %s,
                content_embed = %s,
                raw_metadata = %s::jsonb,
                updated_at = NOW()
            WHERE document_id = %s
            """,
            (merged_tags, embedding, json.dumps(metadata), cleaned_document_id),
        )
        updated = cur.rowcount > 0
        _replace_document_chunks(
            cur,
            document_id=cleaned_document_id,
            chunk_embeddings=chunk_embeddings,
        )
        conn.commit()

    return {
        "document_id": cleaned_document_id,
        "updated": updated,
        "tags": merged_tags,
        "suggested_tags": suggested_tags,
    }


def _translate_text_to_english(
    text: str,
    max_chars: int,
    *,
    document_id: str | None = None,
    chunk_index: int | None = None,
    total_chunks: int | None = None,
) -> str:
    trimmed = (text or "").strip()
    if not trimmed:
        return text
    excerpt = trimmed[:max_chars]
    system_prompt = (
        "Translate the user's text into fluent English. Respond with the translation only. "
        "If already in english, just return the same text."
    )

    try:
        candidate = _call_llm_text(
            excerpt,
            system_prompt=system_prompt,
            timeout=OLLAMA_TIMEOUT,
        ).strip()
        return candidate or text
    except Exception as exc:
        from llm_helpers import LLMUnavailableError

        if isinstance(exc, LLMUnavailableError):
            logger.warning(
                "[documents] LLM unavailable during translation; keeping original text document_id=%s chunk=%s/%s",
                document_id or "unknown",
                "?" if chunk_index is None else chunk_index + 1,
                "?" if total_chunks is None else total_chunks,
            )
            return text
        logger.warning(
            "[documents] translation failed document_id=%s chunk=%s/%s error=%s",
            document_id or "unknown",
            "?" if chunk_index is None else chunk_index + 1,
            "?" if total_chunks is None else total_chunks,
            exc,
            exc_info=exc,
        )
        return text


def _chunk_text_for_translation(text: str, max_chars: int) -> list[str]:
    cleaned = (text or "").strip()
    if not cleaned:
        return []
    if max_chars <= 0 or len(cleaned) <= max_chars:
        return [cleaned]

    chunks: list[str] = []
    cursor = 0
    text_len = len(cleaned)

    while cursor < text_len:
        end = min(text_len, cursor + max_chars)
        if end < text_len:
            split_idx = cleaned.rfind("\n", cursor, end)
            if split_idx <= cursor:
                split_idx = cleaned.rfind(" ", cursor, end)
            if split_idx > cursor + max_chars // 2:
                end = split_idx + 1
        part = cleaned[cursor:end]
        if part:
            chunks.append(part)
        cursor = end

    return chunks


def _translate_content_for_storage(
    content: str,
    *,
    document_id: str | None = None,
) -> tuple[str, bool]:
    cleaned = (content or "").strip()
    if not cleaned:
        return cleaned, False

    chunk_chars = max(200, min(MAX_TRANSLATION_CHUNK_CHARS, MAX_CONTENT_CHARS))
    chunks = _chunk_text_for_translation(cleaned, chunk_chars)
    translated_parts: list[str] = []
    for idx, chunk in enumerate(chunks):
        translated_chunk = _translate_text_to_english(
            chunk,
            len(chunk),
            document_id=document_id,
            chunk_index=idx,
            total_chunks=len(chunks),
        ).strip()
        translated_parts.append(translated_chunk or chunk)

    translated = "".join(translated_parts).strip() or cleaned
    translated_for_storage = translated != cleaned
    return translated, translated_for_storage


def _update_content_storage_metadata(
    raw_metadata: dict[str, Any],
    *,
    original_content: str,
    stored_content: str,
    translated: bool,
) -> None:
    _ = stored_content
    raw_metadata["original_content"] = (original_content or "")[:MAX_CONTENT_CHARS]
    raw_metadata["content_translated_for_storage"] = bool(translated)


def _translate_tags_to_english(tags: Sequence[str]) -> list[str]:
    normalized = [t for t in tags if t]
    if not normalized:
        return normalized
    prompt = (
        "Translate each of the following labels into concise English (1-3 words). If a tag is already in English, just return the exact same tag. "
        "Respond with a JSON object matching the supplied response schema, preserving the same order."
    )
    user_prompt = json.dumps({"tags": normalized}, ensure_ascii=False)
    try:
        from llm_helpers import build_json_schema_response_format
        from llm_json_schemas import TAG_SUGGESTION_RESPONSE_SCHEMA

        parsed = _call_llm_json_response(
            user_prompt,
            system_prompt=prompt,
            timeout=OLLAMA_TIMEOUT,
            response_format=build_json_schema_response_format(
                name="document_tag_translation",
                schema=TAG_SUGGESTION_RESPONSE_SCHEMA,
            ),
        )
        logger.debug("[documents] parsed=%s", parsed)
        if isinstance(parsed, dict) and isinstance(parsed.get("tags"), list):
            translated = []
            for item in parsed["tags"]:
                if isinstance(item, str):
                    logger.debug("[documents] item=%s", item)
                    cleaned = item.strip()
                    logger.debug("[documents] cleaned=%s", cleaned)
                    if cleaned:
                        translated.append(cleaned)
            return translated or normalized
    except Exception as exc:
        from llm_helpers import LLMUnavailableError

        if isinstance(exc, LLMUnavailableError):
            logger.warning("[documents] LLM unavailable while translating tags; keeping original tags")
            return normalized
        logger.warning("[documents] Failed to translate tags: %s", exc, exc_info=exc)
    return normalized


def _summarize_description(content: str) -> str | None:
    cleaned = (content or "").strip()
    if not cleaned:
        return None
    excerpt = cleaned[:MAX_DESCRIPTION_PROMPT_CHARS]
    system_prompt = (
        "Provide a concise English description (<= 400 words) of the user's document excerpt. "
        "No need to output the amount of words used and no need to use any kind of text formatting."
        "Highlight the main topic, purpose and main findings of the document."
    )
    try:
        candidate = _call_llm_text(
            excerpt,
            system_prompt=system_prompt,
            timeout=OLLAMA_TIMEOUT,
        ).strip()
        return candidate or None
    except Exception as exc:
        from llm_helpers import LLMUnavailableError

        if isinstance(exc, LLMUnavailableError):
            logger.warning(
                "[documents] LLM unavailable while summarizing description; using deterministic fallback"
            )
            return None
        logger.warning("[documents] Failed to summarize description: %s", exc, exc_info=exc)
        return None


def _upsert_document(
    *,
    document_id: str,
    title: str,
    tags: Sequence[str],
    contact_ids: Sequence[str] | None,
    description: str | None,
    stored: StoredFileInfo,
    content: str,
    embedding: Sequence[float],
    chunk_embeddings: Sequence[DocumentChunkEmbedding],
    document_date: datetime | None,
    raw_metadata: dict[str, Any],
    replace_contact_links: bool,
) -> dict[str, Any]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO documents (
                document_id,
                title,
                tags,
                description,
                file_path,
                file_name,
                file_mime,
                file_size,
                document_date,
                content,
                content_embed,
                raw_metadata,
                created_at,
                updated_at
            )
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW(),NOW())
            ON CONFLICT (document_id) DO UPDATE
              SET title = EXCLUDED.title,
                  tags = EXCLUDED.tags,
                  description = EXCLUDED.description,
                  file_path = EXCLUDED.file_path,
                  file_name = EXCLUDED.file_name,
                  file_mime = EXCLUDED.file_mime,
                  file_size = EXCLUDED.file_size,
                  content = EXCLUDED.content,
                  content_embed = EXCLUDED.content_embed,
                  document_date = EXCLUDED.document_date,
                  raw_metadata = EXCLUDED.raw_metadata,
                  updated_at = NOW()
            RETURNING
                document_id,
                title,
                tags,
                description,
                file_name,
                file_mime,
                file_size,
                document_date,
                created_at,
                updated_at,
                content,
                raw_metadata
            """,
            (
                document_id,
                title,
                list(tags),
                description,
                str(stored.path),
                stored.file_name,
                stored.mime_type,
                stored.size,
                document_date,
                content,
                embedding,
                json.dumps(raw_metadata),
            ),
        )
        row = cur.fetchone()
        _replace_document_chunks(cur, document_id=document_id, chunk_embeddings=chunk_embeddings)
        if replace_contact_links:
            _set_document_contacts(cur, document_id=document_id, contact_ids=contact_ids)
        if row:
            _attach_linked_contacts(cur, [row])
        conn.commit()
    return row


def _vector_search_documents(query: str, k: int) -> dict[str, float]:
    cleaned_query = normalize_search_text(query)
    if not cleaned_query:
        return {}
    query_vector = embed_text(cleaned_query)
    with get_conn() as conn, conn.cursor() as cur:
        chunk_columns = _get_document_chunk_columns(cur)
        has_section_title = "section_title" in chunk_columns
        has_content_type = "content_type" in chunk_columns

        quality_boost_sql = "0.0"
        quality_boost_params: list[Any] = []
        if has_section_title:
            quality_boost_sql = f"{quality_boost_sql} + (CASE WHEN COALESCE(NULLIF(TRIM(section_title), ''), '') <> '' THEN 0.03 ELSE 0 END)"
        if has_content_type:
            quality_boost_sql = f"{quality_boost_sql} + (CASE WHEN content_type = 'table' AND %s ~ '[0-9]' THEN 0.04 ELSE 0 END)"
            quality_boost_params.append(cleaned_query)

        chunk_candidate_limit = max(k, k * max(1, CHUNK_SEARCH_CANDIDATE_MULTIPLIER))
        query_sql = f"""
            WITH ranked_chunks AS (
                SELECT
                    document_id,
                    (1 - (chunk_embed <=> %s::vector)) + ({quality_boost_sql}) AS score
                FROM document_chunks
                ORDER BY chunk_embed <=> %s::vector
                LIMIT %s
            ),
            aggregated_chunks AS (
                SELECT
                    document_id,
                    MAX(score) AS best_score,
                    AVG(score) AS mean_score
                FROM ranked_chunks
                GROUP BY document_id
            )
            SELECT
                a.document_id,
                a.best_score,
                a.mean_score,
                CASE
                    WHEN d.content_embed IS NOT NULL
                        THEN 1 - (d.content_embed <=> %s::vector)
                    ELSE NULL
                END AS doc_score
            FROM aggregated_chunks a
            LEFT JOIN documents d ON d.document_id = a.document_id
            ORDER BY a.best_score DESC, a.mean_score DESC
            LIMIT %s
        """
        query_params: list[Any] = [query_vector]
        query_params.extend(quality_boost_params)
        query_params.extend([query_vector, chunk_candidate_limit, query_vector, k])
        cur.execute(
            query_sql,
            tuple(query_params),
        )
        rows = cur.fetchall()
        document_scores = {
            row["document_id"]: _score_document_match(
                best_score=row.get("best_score"),
                mean_score=row.get("mean_score"),
                doc_score=row.get("doc_score"),
            )
            for row in rows
        }
        if len(document_scores) >= k:
            return document_scores

        exclude_ids = list(document_scores.keys())
        fallback_limit = max(0, k - len(document_scores))
        if fallback_limit <= 0:
            return document_scores

        if exclude_ids:
            cur.execute(
                """
                SELECT document_id, 1 - (content_embed <=> %s::vector) AS score
                FROM documents
                WHERE content_embed IS NOT NULL
                  AND NOT (document_id = ANY(%s))
                ORDER BY content_embed <=> %s::vector
                LIMIT %s
                """,
                (query_vector, exclude_ids, query_vector, fallback_limit),
            )
        else:
            cur.execute(
                """
                SELECT document_id, 1 - (content_embed <=> %s::vector) AS score
                FROM documents
                WHERE content_embed IS NOT NULL
                ORDER BY content_embed <=> %s::vector
                LIMIT %s
                """,
                (query_vector, query_vector, fallback_limit),
            )
        fallback_rows = cur.fetchall()
        for row in fallback_rows:
            document_scores[row["document_id"]] = _score_document_match(
                best_score=None,
                mean_score=None,
                doc_score=row["score"],
            )
        return document_scores


def _score_chunk_match(*, best_score: Any, mean_score: Any) -> float | None:
    if best_score is None and mean_score is None:
        return None
    try:
        best = float(best_score)
    except (TypeError, ValueError):
        best = 0.0
    try:
        mean = float(mean_score)
    except (TypeError, ValueError):
        mean = best
    return (CHUNK_SCORE_BEST_WEIGHT * best) + (CHUNK_SCORE_MEAN_WEIGHT * mean)


def _score_document_match(
    *,
    best_score: Any,
    mean_score: Any,
    doc_score: Any,
) -> float:
    chunk_component = _safe_float(_score_chunk_match(best_score=best_score, mean_score=mean_score))
    doc_component = _safe_float(doc_score)
    if chunk_component is not None and doc_component is not None:
        return (COMBINED_SCORE_CHUNK_WEIGHT * chunk_component) + (
            COMBINED_SCORE_DOC_WEIGHT * doc_component
        )
    if chunk_component is not None:
        return chunk_component
    if doc_component is not None:
        return doc_component
    return 0.0


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _replace_document_chunks(
    cur: Any,
    *,
    document_id: str,
    chunk_embeddings: Sequence[DocumentChunkEmbedding],
) -> None:
    cur.execute("DELETE FROM document_chunks WHERE document_id = %s", (document_id,))
    if not chunk_embeddings:
        return

    columns = _get_document_chunk_columns(cur)
    has_section_title = "section_title" in columns
    has_page_start = "page_start" in columns
    has_page_end = "page_end" in columns
    has_content_type = "content_type" in columns
    has_parser_used = "parser_used" in columns

    insert_columns = ["document_id", "chunk_index", "chunk_text", "chunk_embed"]
    if has_section_title:
        insert_columns.append("section_title")
    if has_page_start:
        insert_columns.append("page_start")
    if has_page_end:
        insert_columns.append("page_end")
    if has_content_type:
        insert_columns.append("content_type")
    if has_parser_used:
        insert_columns.append("parser_used")

    placeholders = ", ".join(["%s"] * len(insert_columns))
    query = f"INSERT INTO document_chunks ({', '.join(insert_columns)}) VALUES ({placeholders})"
    rows: list[tuple[Any, ...]] = []
    for chunk in chunk_embeddings:
        values: list[Any] = [
            document_id,
            chunk.chunk_index,
            chunk.chunk_text,
            list(chunk.embedding),
        ]
        if has_section_title:
            values.append(chunk.section_title)
        if has_page_start:
            values.append(chunk.page_start)
        if has_page_end:
            values.append(chunk.page_end)
        if has_content_type:
            values.append(chunk.content_type)
        if has_parser_used:
            values.append(chunk.parser_used)
        rows.append(tuple(values))
    cur.executemany(query, rows)


@lru_cache(maxsize=1)
def _cached_document_chunk_columns() -> set[str]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'document_chunks'
            """
        )
        return {str(row["column_name"]) for row in cur.fetchall()}


def _get_document_chunk_columns(cur: Any | None = None) -> set[str]:
    if cur is None:
        return _cached_document_chunk_columns()
    try:
        return _cached_document_chunk_columns()
    except Exception:
        cur.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'document_chunks'
            """
        )
        return {str(row["column_name"]) for row in cur.fetchall()}


def _bm25_search_documents(query: str, k: int) -> dict[str, float]:
    cleaned_query = normalize_search_text(query)
    if not cleaned_query:
        return {}
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT document_id, ts_rank_cd(content_tsv, plainto_tsquery('english', unaccent(%s))) AS score
            FROM documents
            WHERE content_tsv @@ plainto_tsquery('english', unaccent(%s))
            ORDER BY score DESC
            LIMIT %s
            """,
            (cleaned_query, cleaned_query, k),
        )
        return {row["document_id"]: float(row["score"]) for row in cur.fetchall()}


def _tag_search_documents(tags: Sequence[str]) -> dict[str, float]:
    normalized_tags = normalize_search_list(tags)
    if not normalized_tags:
        return {}
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT document_id, tags
            FROM documents
            WHERE EXISTS (
                SELECT 1
                FROM unnest(tags) AS t(tag)
                WHERE unaccent(lower(t.tag)) = ANY(%s)
            )
            """,
            (normalized_tags,),
        )
        rows = cur.fetchall()

    scores: dict[str, float] = {}
    for row in rows:
        doc_tags = row.get("tags") or []
        doc_normalized = set(normalize_search_list(doc_tags))
        overlap = sum(1 for tag in doc_normalized if tag in normalized_tags)
        if overlap:
            scores[row["document_id"]] = 0.3 + 0.2 * overlap
        else:
            scores[row["document_id"]] = 0.2
    return scores


def _load_document_row(document_id: str) -> dict[str, Any] | None:
    if not document_id:
        return None
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                document_id,
                title,
                tags,
                description,
                file_path,
                file_name,
                file_mime,
                file_size,
                document_date,
                content,
                raw_metadata
            FROM documents
            WHERE document_id = %s
            """,
            (document_id,),
        )
        return cur.fetchone()


def _fetch_documents(document_ids: Sequence[str]) -> list[dict[str, Any]]:
    if not document_ids:
        return []
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                document_id,
                title,
                tags,
                description,
                file_name,
                file_mime,
                file_size,
                document_date,
                created_at,
                updated_at,
                content
            FROM documents
            WHERE document_id = ANY(%s)
            """,
            (list(document_ids),),
        )
        rows = cur.fetchall()
        _attach_linked_contacts(cur, rows)
        return rows


def _row_to_document(
    row: dict[str, Any],
    *,
    include_metadata: bool = False,
    include_content: bool = False,
) -> dict[str, Any]:
    snippet_source = row.get("description") or row.get("content") or ""
    document: dict[str, Any] = {
        "document_id": row["document_id"],
        "title": row["title"],
        "tags": row.get("tags") or [],
        "description": row.get("description"),
        "document_date": row.get("document_date"),
        "file_name": row.get("file_name"),
        "file_mime": row.get("file_mime"),
        "file_size": row.get("file_size"),
        "download_url": f"/documents/{row['document_id']}/download",
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "snippet": _make_snippet(snippet_source),
        "linked_contacts": row.get("linked_contacts") or [],
    }
    if include_metadata:
        raw_metadata = row.get("raw_metadata")
        if isinstance(raw_metadata, str):
            try:
                raw_metadata = json.loads(raw_metadata)
            except json.JSONDecodeError:
                raw_metadata = {"raw": raw_metadata}
        document["raw_metadata"] = raw_metadata or {}
    if include_content:
        content = row.get("content") or ""
        document["content_preview"] = content[:1000]
        document["content"] = content
    return document


def _make_snippet(text: str, length: int = 160) -> str:
    if not text:
        return ""
    cleaned = " ".join(text.split())
    if len(cleaned) <= length:
        return cleaned
    return cleaned[: length - 1] + "…"


def _default_description(content: str, fallback: str | None) -> str:
    snippet = _make_snippet(content, 200)
    if snippet:
        return snippet
    return fallback or "Document description unavailable"


def _preferred_extension(filename: str, mime_type: str | None) -> str | None:
    if mime_type == "application/pdf":
        return ".pdf"
    if mime_type in {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }:
        return ".docx"
    suffix = Path(filename).suffix
    if suffix:
        return suffix
    guessed = mimetypes.guess_extension(mime_type) if mime_type else None
    return guessed


def _sanitize_filename(name: str) -> str:
    return "".join(ch for ch in name if ch.isalnum() or ch in {"-", "_", "."}).strip()


def _build_chunk_embedding_source(document: dict[str, Any]) -> str:
    content = document.get("content")
    if isinstance(content, str):
        cleaned = content.strip()
        if cleaned:
            return cleaned

    description = document.get("description")
    if isinstance(description, str):
        cleaned = description.strip()
        if cleaned:
            return cleaned

    title = document.get("title")
    if isinstance(title, str):
        cleaned = title.strip()
        if cleaned:
            return cleaned

    tags = document.get("tags")
    if isinstance(tags, (list, tuple)):
        cleaned_tags = [str(tag).strip() for tag in tags if isinstance(tag, str) and tag.strip()]
        if cleaned_tags:
            return " ".join(cleaned_tags)

    file_name = document.get("file_name")
    if isinstance(file_name, str):
        cleaned = file_name.strip()
        if cleaned:
            return cleaned
    return ""


def _build_chunk_metadata_prefix(document: dict[str, Any]) -> str:
    parts: list[str] = []

    title = document.get("title")
    if isinstance(title, str):
        cleaned = title.strip()
        if cleaned:
            parts.append(f"title: {cleaned}")

    tags = document.get("tags")
    if isinstance(tags, (list, tuple)):
        cleaned_tags = [str(tag).strip() for tag in tags if isinstance(tag, str) and tag.strip()]
        if cleaned_tags:
            limit = max(1, EMBED_CHUNK_METADATA_TAG_LIMIT)
            parts.append(f"tags: {', '.join(cleaned_tags[:limit])}")

    file_name = document.get("file_name")
    if isinstance(file_name, str):
        cleaned = file_name.strip()
        if cleaned:
            parts.append(f"file: {cleaned}")

    description = document.get("description")
    if isinstance(description, str):
        cleaned = description.strip()
        if cleaned:
            parts.append(f"summary: {cleaned[:120]}")

    if not parts:
        return ""
    max_chars = max(80, EMBED_CHUNK_METADATA_MAX_CHARS)
    return " | ".join(parts)[:max_chars].strip()


def _compose_chunk_embedding_payload(metadata_prefix: str, chunk_text: str) -> str:
    cleaned_chunk = (chunk_text or "").strip()
    cleaned_prefix = (metadata_prefix or "").strip()
    if cleaned_prefix and cleaned_chunk:
        return f"{cleaned_prefix}\n\n{cleaned_chunk}"
    if cleaned_prefix:
        return cleaned_prefix
    if cleaned_chunk:
        return cleaned_chunk
    return "document"


def _chunk_text_for_embedding(
    text: str,
    *,
    chunk_chars: int = EMBED_CHUNK_CHARS,
    overlap_chars: int = EMBED_CHUNK_OVERLAP_CHARS,
    max_chunks: int = EMBED_MAX_CHUNKS,
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


def _average_embeddings(vectors: Sequence[Sequence[float]]) -> list[float] | None:
    valid = [list(vector) for vector in vectors if vector]
    if not valid:
        return None
    dim = len(valid[0])
    if dim == 0:
        return None
    sums = [0.0] * dim
    used = 0
    for vector in valid:
        if len(vector) != dim:
            logger.warning(
                "[documents] skipped embedding vector due to mismatched dimension expected=%s got=%s",
                dim,
                len(vector),
            )
            continue
        for idx, value in enumerate(vector):
            sums[idx] += float(value)
        used += 1
    if used == 0:
        return None
    return [value / used for value in sums]


def _generate_document_embeddings(
    document: dict[str, Any],
    *,
    raw_metadata: dict[str, Any] | None = None,
) -> tuple[Sequence[float], list[DocumentChunkEmbedding]]:
    metadata = dict(raw_metadata or {})
    content_source = _build_chunk_embedding_source(document)
    metadata_prefix = _build_chunk_metadata_prefix(document)

    section_payload = metadata.get("normalized_sections")
    structured_sections = _deserialize_sections(section_payload)
    if metadata.get("content_translated_for_storage"):
        structured_sections = []
    parser_used = _safe_str(metadata.get("parser_used")) or "unknown_parser"

    structured_chunks: list[StructuredChunk]
    if structured_sections:
        normalized_doc = NormalizedDocument(
            normalized_text=content_source,
            raw_extracted_text="",
            parser_used=parser_used,
            sections=structured_sections,
        )
        structured_chunks = chunk_normalized_document(
            normalized_doc,
            chunk_chars=EMBED_CHUNK_CHARS,
            overlap_chars=EMBED_CHUNK_OVERLAP_CHARS,
            max_chunks=EMBED_MAX_CHUNKS,
        )
    else:
        structured_chunks = []

    if not structured_chunks:
        chunk_inputs = _chunk_text_for_embedding(content_source)
        if not chunk_inputs:
            chunk_inputs = [""]
        structured_chunks = [
            StructuredChunk(
                chunk_text=chunk_text,
                section_title=None,
                page_start=None,
                page_end=None,
                content_type="paragraph",
                parser_used=parser_used,
            )
            for chunk_text in chunk_inputs
        ]

    chunk_embeddings: list[DocumentChunkEmbedding] = []
    for idx, structured_chunk in enumerate(structured_chunks):
        chunk_payload = _compose_chunk_embedding_payload(
            metadata_prefix, structured_chunk.chunk_text
        )
        chunk_embeddings.append(
            DocumentChunkEmbedding(
                chunk_index=idx,
                chunk_text=chunk_payload,
                embedding=embed_text(chunk_payload),
                section_title=structured_chunk.section_title,
                page_start=structured_chunk.page_start,
                page_end=structured_chunk.page_end,
                content_type=structured_chunk.content_type,
                parser_used=structured_chunk.parser_used,
            )
        )

    averaged = _average_embeddings([chunk.embedding for chunk in chunk_embeddings])
    if averaged:
        embedding: Sequence[float] = averaged
    else:
        embed_input = _compose_chunk_embedding_payload(metadata_prefix, content_source)
        embed_input = embed_input[:MAX_EMBED_CHARS] or "document"
        embedding = embed_text(embed_input)

    document_id = document.get("document_id")
    if isinstance(document_id, str):
        logger.debug(
            "[documents] embedding payload document_id=%s chunk_count=%s content_chars=%s metadata_chars=%s",
            document_id,
            len(chunk_embeddings),
            len(content_source),
            len(metadata_prefix),
        )
    return embedding, chunk_embeddings


def _derive_title_from_filename(filename: str | None) -> str | None:
    if not filename:
        return None
    stem = Path(filename).stem
    cleaned = stem.replace("_", " ").replace("-", " ").strip()
    if not cleaned:
        return None
    parts = [part for part in cleaned.split() if part]
    if not parts:
        return None
    return " ".join(word.capitalize() for word in parts)


def _suggest_title(content: str, fallback: str | None) -> str | None:
    cleaned = (content or "").strip()
    if not cleaned:
        return _derive_title_from_filename(fallback)
    excerpt = cleaned[:MAX_TITLE_PROMPT_CHARS]
    system_prompt = (
        "You generate concise, descriptive document titles (maximum 8 words). "
        "If documents are not in english, you still suggest a title in english. "
        "Do not use any kind of text formatting. Respond with text only, no JSON."
    )
    user_prompt = f"Suggest a short title for the following document excerpt:\n\n{excerpt}"
    try:
        candidate = _call_llm_text(
            user_prompt,
            system_prompt=system_prompt,
            timeout=OLLAMA_TIMEOUT,
        ).strip()
        if candidate:
            return candidate.splitlines()[0].strip()
    except Exception as exc:
        from llm_helpers import LLMUnavailableError

        if isinstance(exc, LLMUnavailableError):
            logger.warning(
                "[documents] LLM unavailable while generating title; falling back to filename-derived title"
            )
            return _derive_title_from_filename(fallback)
        logger.warning("[documents] Failed to generate title: %s", exc, exc_info=exc)
    return _derive_title_from_filename(fallback)
