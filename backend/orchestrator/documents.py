from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from docx import Document as DocxDocument
from fastapi import UploadFile
from pdfminer.high_level import extract_text as extract_pdf_text

from db import get_conn
from embeddings import embed_text
from search_normalization import normalize_search_list, normalize_search_text
from tags_manager import (
    _merge_tag_lists,
    _normalize_strings,
    _suggest_additional_tags,
)


class DocumentProcessingError(RuntimeError):
    """Raised when an uploaded document cannot be processed."""


DOCUMENT_STORAGE_DIR = Path(
    os.getenv("DOCUMENT_STORAGE_DIR", "/app/storage/documents")
).expanduser()

MAX_CONTENT_CHARS = int(os.getenv("DOCUMENT_MAX_CONTENT_CHARS", "20000"))
MAX_EMBED_CHARS = int(os.getenv("DOCUMENT_EMBED_MAX_CHARS", "8000"))
MAX_TRANSLATION_SOURCE_CHARS = int(os.getenv("DOCUMENT_TRANSLATION_MAX_CHARS", "2500"))
MAX_TITLE_PROMPT_CHARS = int(os.getenv("DOCUMENT_TITLE_PROMPT_CHARS", "2000"))
MAX_DATE_PROMPT_CHARS = int(os.getenv("DOCUMENT_DATE_PROMPT_CHARS", "2000"))
MAX_DESCRIPTION_PROMPT_CHARS = int(os.getenv("DOCUMENT_DESCRIPTION_PROMPT_CHARS", "1200"))

LLM_CHAT_MODEL = os.getenv("LLM_CHAT_MODEL")
OLLAMA_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "60"))

CONTENT_TRANSLATION_TEXT_KEY = "content_english_for_embedding"
CONTENT_TRANSLATION_HASH_KEY = "content_english_source_hash"
CONTENT_TRANSLATION_GENERATED_KEY = "content_english_generated"


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
        model=LLM_CHAT_MODEL,
        timeout=timeout,
    )


def _call_llm_json_response(
    prompt: str,
    *,
    system_prompt: str,
    timeout: int,
) -> Any:
    from llm_helpers import call_llm_json

    return call_llm_json(
        prompt,
        system_prompt=system_prompt,
        model=LLM_CHAT_MODEL,
        timeout=timeout,
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


def _ensure_document_storage_dir() -> Path:
    """Create document storage directory only when file operations need it."""
    DOCUMENT_STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    return DOCUMENT_STORAGE_DIR


def ingest_document(
    *,
    title: str | None,
    tags: Sequence[str] | None,
    description: str | None,
    upload: UploadFile,
    document_date: datetime | None = None,
) -> dict[str, Any]:
    """Persist a new document, extracting text, embeddings, and tags."""
    provided_title = (title or "").strip()
    if not upload:
        raise DocumentProcessingError("File upload is required")

    document_id = f"doc:{uuid4().hex}"
    stored = _store_upload(upload, document_id)

    content_text = _extract_text(stored.path, stored.mime_type)
    if content_text:
        content_text = content_text[:MAX_CONTENT_CHARS]
    else:
        fallback = filter(None, [description, provided_title, stored.file_name, document_id])
        content_text = " ".join(fallback)

    base_raw_metadata = {
        "original_filename": upload.filename,
        "provided_mime_type": upload.content_type,
        "stored_mime_type": stored.mime_type,
        "file_size": stored.size,
    }

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

    row = _upsert_document(
        document_id=document_id,
        title=prepared.title,
        tags=prepared.tags,
        description=prepared.description,
        stored=stored,
        content=content_text,
        embedding=prepared.embedding,
        document_date=prepared.document_date,
        raw_metadata=prepared.raw_metadata,
    )
    return _row_to_document(row, include_metadata=True, include_content=True)


def list_documents(limit: int = 200, offset: int = 0) -> list[dict[str, Any]]:
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
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
            ORDER BY created_at DESC
            LIMIT %s OFFSET %s
            """,
            (limit, offset),
        )
        rows = cur.fetchall()
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
    if not row:
        return None
    return _row_to_document(row, include_metadata=True, include_content=True)


def update_document_metadata(
    document_id: str,
    *,
    title: str | None = None,
    tags: Sequence[str] | None = None,
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
        extracted = _extract_text(stored.path, stored.mime_type)
        recovered = (extracted or "").strip()
        if recovered:
            content_text = recovered[:MAX_CONTENT_CHARS]
            print(
                "[documents] recovered missing content from file "
                f"document_id={document_id} chars={len(content_text)}"
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
        description=prepared.description,
        stored=stored,
        content=content_text,
        embedding=prepared.embedding,
        document_date=prepared.document_date,
        raw_metadata=prepared.raw_metadata,
    )
    return _row_to_document(row, include_metadata=True, include_content=True)


def search_documents(
    query: str,
    *,
    tags: Sequence[str] | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    search_query = normalize_search_text(query)
    normalized_tags = normalize_search_list(tags)

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

    if not scores:
        return []

    sorted_ids = [
        doc_id
        for doc_id, _ in sorted(scores.items(), key=lambda item: item[1], reverse=True)
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
            print(f"[documents] Failed to delete file {file_path}: {exc}")
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
    suffix = path.suffix.lower()
    try:
        if suffix == ".pdf" or mime_type == "application/pdf":
            return extract_pdf_text(path)
        if suffix in {".docx"} or mime_type in {
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        }:
            doc = DocxDocument(path)
            return "\n".join(paragraph.text for paragraph in doc.paragraphs)
        if suffix in {".txt", ".md"} or (mime_type and mime_type.startswith("text/")):
            with open(path, encoding="utf-8", errors="ignore") as handle:
                return handle.read()
    except Exception as exc:
        print(f"[documents] Failed to extract text from {path}: {exc}")
    # Fallback: attempt binary read and decode
    try:
        with open(path, "rb") as handle:
            data = handle.read()
        return data.decode("utf-8", errors="ignore")
    except Exception as exc:
        print(f"[documents] Failed binary decode for {path}: {exc}")
        return ""


def _suggest_document_date(content: str, fallback: str | None) -> datetime | None:
    cleaned = (content or fallback or "").strip()
    if not cleaned or not LLM_CHAT_MODEL:
        return None
    excerpt = cleaned[:MAX_DATE_PROMPT_CHARS]
    system_prompt = (
        "You extract dates from documents. Find the primary date the document was written or refers to. "
        "Respond with JSON like {\"date\": \"YYYY-MM-DD\"} or {\"date\": null} if unsure."
    )
    user_prompt = (
        f"Document excerpt:\n{excerpt}\n\n"
        "If a clear date is present, respond with it in ISO-8601 (YYYY-MM-DD or YYYY-MM-DDTHH:MM)."
    )
    try:
        raw_content = _call_llm_text(
            user_prompt,
            system_prompt=system_prompt,
            timeout=OLLAMA_TIMEOUT,
        ).strip()
        if not raw_content:
            return None
        candidate = _parse_date_response(raw_content)
        if candidate:
            return candidate
    except Exception as exc:
        print(f"[documents] Failed to infer date: {exc}")
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
    print(f"[documents] tags={tags}")
    normalized_tags = _normalize_strings(tags)
    print(f"[documents] normalized_tags={normalized_tags}")
    english_tags = _normalize_strings(_translate_tags_to_english(normalized_tags))
    print(f"[documents] english_tags={english_tags}")
    suggested_tags = _suggest_additional_tags(content_text, english_tags)
    print(f"[documents] suggested_tags={suggested_tags}")
    merged_tags = _merge_tag_lists(english_tags, suggested_tags)
    print(f"[documents] merged_tags={merged_tags}")

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
        final_title = (
            generated_title
            or _derive_title_from_filename(file_name)
            or document_id
        )

    final_date = document_date
    inferred_date: datetime | None = None
    if not final_date:
        inferred_date = _suggest_document_date(content_text, fallback=final_description)
        final_date = inferred_date

    embedding = _generate_document_embedding(
        {
            "document_id": document_id,
            "content": content_text,
            "description": final_description,
            "title": final_title,
            "tags": merged_tags,
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
        tags=merged_tags,
        document_date=final_date,
        embedding=embedding,
        raw_metadata=metadata,
        suggested_tags=suggested_tags,
        generated_title=generated_title,
        generated_description=generated_description,
        inferred_date=inferred_date,
    )


def _translate_text_to_english(text: str, max_chars: int) -> str:
    trimmed = (text or "").strip()
    if not trimmed:
        return text
    if not LLM_CHAT_MODEL:
        print(
            "[documents] Translation skipped: no LLM_CHAT_MODEL configured"
        )
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
        print(f"[documents] Failed to translate text: {exc}")
        return text


def _translate_tags_to_english(tags: Sequence[str]) -> list[str]:
    normalized = [t for t in tags if t]
    if not normalized or not LLM_CHAT_MODEL:
        return normalized
    prompt = (
        "Translate each of the following labels into concise English (1-3 words). If a tag is already in English, just return the exact same tag. "
        "Respond with JSON like {\"tags\": [\"tag\", ...]} in the same order."
    )
    user_prompt = json.dumps({"tags": normalized}, ensure_ascii=False)
    try:
        parsed = _call_llm_json_response(
            user_prompt,
            system_prompt=prompt,
            timeout=OLLAMA_TIMEOUT,
        )
        print(f"[documents] parsed={parsed}")
        if isinstance(parsed, dict) and isinstance(parsed.get("tags"), list):
            translated = []
            for item in parsed["tags"]:
                if isinstance(item, str):
                    print(f"[documents] item={item}")
                    cleaned = item.strip()
                    print(f"[documents] cleaned={cleaned}")
                    if cleaned:
                        translated.append(cleaned)
            return translated or normalized
    except Exception as exc:
        print(f"[documents] Failed to translate tags: {exc}")
    return normalized


def _summarize_description(content: str) -> str | None:
    cleaned = (content or "").strip()
    if not cleaned or not LLM_CHAT_MODEL:
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
        print(f"[documents] Failed to summarize description: {exc}")
        return None


def _upsert_document(
    *,
    document_id: str,
    title: str,
    tags: Sequence[str],
    description: str | None,
    stored: StoredFileInfo,
    content: str,
    embedding: Sequence[float],
    document_date: datetime | None,
    raw_metadata: dict[str, Any],
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
        conn.commit()
    return row


def _vector_search_documents(query: str, k: int) -> dict[str, float]:
    cleaned_query = normalize_search_text(query)
    if not cleaned_query:
        return {}
    query_vector = embed_text(cleaned_query)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT document_id, 1 - (content_embed <=> %s::vector) AS score
            FROM documents
            ORDER BY content_embed <=> %s::vector
            LIMIT %s
            """,
            (query_vector, query_vector, k),
        )
        return {row["document_id"]: float(row["score"]) for row in cur.fetchall()}


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
        return cur.fetchall()


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


def _translation_source_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _resolve_english_content_for_embedding(
    content: str,
    raw_metadata: dict[str, Any] | None,
    document_id: str | None = None,
) -> str:
    cleaned = (content or "").strip()
    if not cleaned:
        if document_id:
            print(
                "[documents] content empty before translation "
                f"document_id={document_id}"
            )
        return ""

    translation_chars = max(1, min(MAX_EMBED_CHARS, MAX_TRANSLATION_SOURCE_CHARS))
    source_text = cleaned[:translation_chars]
    source_hash = _translation_source_hash(source_text)

    if raw_metadata is not None:
        cached_hash = raw_metadata.get(CONTENT_TRANSLATION_HASH_KEY)
        cached_text = raw_metadata.get(CONTENT_TRANSLATION_TEXT_KEY)
        if (
            isinstance(cached_hash, str)
            and cached_hash == source_hash
            and isinstance(cached_text, str)
            and cached_text.strip()
        ):
            if document_id:
                cached_preview = " ".join(cached_text.strip().split())[:100]
                print(
                    "[documents] using cached english content "
                    f"document_id={document_id} chars={len(cached_text.strip())} preview={cached_preview!r}"
                )
            return cached_text.strip()

    if document_id:
        print(
            "[documents] translating content for embedding "
            f"document_id={document_id} chars={len(source_text)}"
        )
    translated = _translate_text_to_english(source_text, translation_chars)
    english_content = (translated or "").strip() or source_text

    if raw_metadata is not None:
        raw_metadata[CONTENT_TRANSLATION_TEXT_KEY] = english_content
        raw_metadata[CONTENT_TRANSLATION_HASH_KEY] = source_hash
        raw_metadata[CONTENT_TRANSLATION_GENERATED_KEY] = english_content != source_text

    return english_content


def _generate_document_embedding(
    document: dict[str, Any],
    *,
    raw_metadata: dict[str, Any] | None = None,
) -> Sequence[float]:
    segments: list[str] = []

    tags = document.get("tags")
    if isinstance(tags, (list, tuple)):
        tag_text = " ".join(str(tag).strip() for tag in tags if isinstance(tag, str) and tag.strip())
        if tag_text:
            segments.append(tag_text)

    content = document.get("content")
    english_content = ""
    if isinstance(content, str):
        document_id = document.get("document_id")
        document_id_text = document_id if isinstance(document_id, str) else None
        english_content = _resolve_english_content_for_embedding(
            content,
            raw_metadata,
            document_id=document_id_text,
        )
        if english_content:
            segments.append(english_content)

    description = document.get("description")
    if isinstance(description, str):
        cleaned = description.strip()
        if cleaned and cleaned not in segments:
            segments.append(cleaned)

    title = document.get("title")
    if isinstance(title, str):
        cleaned = title.strip()
        if cleaned:
            segments.append(cleaned)

    file_name = document.get("file_name")
    if isinstance(file_name, str):
        cleaned = file_name.strip()
        if cleaned:
            segments.append(cleaned)

    combined = " ".join(segments).strip()
    embed_input = combined[:MAX_EMBED_CHARS] or "document"
    document_id = document.get("document_id")
    if isinstance(document_id, str):
        print(
            "[documents] embedding payload "
            f"document_id={document_id} chars={len(embed_input)} bytes={len(embed_input.encode('utf-8'))}"
        )
    return embed_text(embed_input)


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
    if not cleaned or not LLM_CHAT_MODEL:
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
        print(f"[documents] Failed to generate title: {exc}")
    return _derive_title_from_filename(fallback)
