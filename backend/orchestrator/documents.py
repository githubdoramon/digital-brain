from __future__ import annotations

import json
import mimetypes
import os
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence
from uuid import uuid4

import requests
from docx import Document as DocxDocument
from fastapi import UploadFile
from pdfminer.high_level import extract_text as extract_pdf_text

from db import get_conn
from embeddings import embed_text


class DocumentProcessingError(RuntimeError):
    """Raised when an uploaded document cannot be processed."""


DOCUMENT_STORAGE_DIR = Path(
    os.getenv(
        "DOCUMENT_STORAGE_DIR",
        Path(__file__).resolve().parent / "storage" / "documents",
    )
)
DOCUMENT_STORAGE_DIR.mkdir(parents=True, exist_ok=True)

MAX_CONTENT_CHARS = int(os.getenv("DOCUMENT_MAX_CONTENT_CHARS", "20000"))
MAX_EMBED_CHARS = int(os.getenv("DOCUMENT_EMBED_MAX_CHARS", "10000"))
MAX_LABEL_PROMPT_CHARS = int(os.getenv("DOCUMENT_LABEL_PROMPT_CHARS", "4000"))
MAX_SUGGESTED_TAGS = int(os.getenv("DOCUMENT_LABEL_MAX_COUNT", "3"))
MAX_TITLE_PROMPT_CHARS = int(os.getenv("DOCUMENT_TITLE_PROMPT_CHARS", "2000"))
MAX_DATE_PROMPT_CHARS = int(os.getenv("DOCUMENT_DATE_PROMPT_CHARS", "2000"))
MAX_DESCRIPTION_PROMPT_CHARS = int(os.getenv("DOCUMENT_DESCRIPTION_PROMPT_CHARS", "1200"))

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_CHAT_MODEL = os.getenv("OLLAMA_CHAT_MODEL")
OLLAMA_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "60"))
DOCUMENT_LLM_TIMEOUT = int(os.getenv("DOCUMENT_LLM_TIMEOUT", str(max(60, int(os.getenv("OLLAMA_TIMEOUT", "60"))))))


@dataclass
class StoredFileInfo:
    document_id: str
    path: Path
    file_name: str
    mime_type: Optional[str]
    size: int


def ingest_document(
    *,
    title: Optional[str],
    tags: Sequence[str] | None,
    description: Optional[str],
    upload: UploadFile,
    document_date: Optional[datetime] = None,
) -> Dict[str, Any]:
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

    embed_source = (content_text or "")[:MAX_EMBED_CHARS]
    embed_input = _translate_text_to_english(embed_source, MAX_EMBED_CHARS)
    embedding = embed_text(embed_input)

    normalized_tags = _normalize_strings(tags)
    english_tags = _normalize_strings(_translate_tags_to_english(normalized_tags))
    suggested_tags = _suggest_additional_tags(content_text, english_tags)
    merged_tags = _merge_tag_lists(english_tags, suggested_tags)

    provided_description = (description or "").strip()
    generated_description: Optional[str] = None
    final_description = provided_description
    if not final_description:
        generated_description = _summarize_description(content_text)
        final_description = generated_description or _default_description(content_text, provided_title or stored.file_name or document_id)

    generated_title: Optional[str] = None
    final_title = provided_title
    if not final_title:
        generated_title = _suggest_title(
            content_text,
            fallback=stored.file_name or document_id,
        )
        final_title = generated_title or _derive_title_from_filename(stored.file_name) or document_id

    inferred_date: Optional[datetime] = None
    final_date = document_date
    if not final_date:
        inferred_date = _suggest_document_date(content_text, fallback=final_description)
        final_date = inferred_date

    raw_metadata = {
        "original_filename": upload.filename,
        "provided_mime_type": upload.content_type,
        "stored_mime_type": stored.mime_type,
        "file_size": stored.size,
        "suggested_tags": suggested_tags,
        "title_generated": bool(generated_title),
        "date_generated": bool(inferred_date),
        "description_generated": bool(generated_description),
    }
    if generated_title:
        raw_metadata["generated_title"] = generated_title
    if inferred_date:
        raw_metadata["generated_date_iso"] = inferred_date.isoformat()
    if generated_description:
        raw_metadata["generated_description"] = generated_description

    row = _upsert_document(
        document_id=document_id,
        title=final_title,
        tags=merged_tags,
        description=final_description,
        stored=stored,
        content=content_text,
        embedding=embedding,
        document_date=final_date,
        raw_metadata=raw_metadata,
    )
    return _row_to_document(row, include_metadata=True, include_content=True)


def list_documents(limit: int = 200, offset: int = 0) -> List[Dict[str, Any]]:
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


def get_document(document_id: str) -> Optional[Dict[str, Any]]:
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


def search_documents(
    query: str,
    *,
    tags: Sequence[str] | None = None,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    q = (query or "").strip()
    normalized_tags = _normalize_strings(tags)

    scores: Dict[str, float] = {}
    if q:
        vec_scores = _vector_search_documents(q, 50)
        bm_scores = _bm25_search_documents(q, 50)
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


def get_document_file(document_id: str) -> Optional[Dict[str, Any]]:
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

    target_path = DOCUMENT_STORAGE_DIR / final_name
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


def _extract_text(path: Path, mime_type: Optional[str]) -> str:
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
            with open(path, "r", encoding="utf-8", errors="ignore") as handle:
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


def _suggest_additional_tags(content: str, tags: Sequence[str]) -> List[str]:
    cleaned = (content or "").strip()
    if not cleaned or not OLLAMA_CHAT_MODEL:
        return []
    prompt_content = cleaned[:MAX_LABEL_PROMPT_CHARS]
    existing = ", ".join(tags) if tags else "none"
    payload = {
        "model": OLLAMA_CHAT_MODEL,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a document librarian. Propose concise topical tags in English for reference. "
                    "Respond with JSON in the shape {\"tags\": [\"tag\", ...]} using 1-3 word phrases."
                ),
            },
            {
                "role": "user",
                "content": (
                    "Existing tags: {existing_tags}\n"
                    "Document excerpt:\n"
                    "{excerpt}\n\n"
                    "Return up to {max_tags} new tags relevant to the content."
                ).format(
                    existing_tags=existing,
                    excerpt=prompt_content,
                    max_tags=MAX_SUGGESTED_TAGS,
                ),
            },
        ],
        "stream": False,
    }

    try:
        response = requests.post(
            f"{OLLAMA_HOST}/api/chat", json=payload, timeout=DOCUMENT_LLM_TIMEOUT
        )
        response.raise_for_status()
        data = response.json()
        message = data.get("message") or {}
        raw_content = (message.get("content") or "").strip()
        if not raw_content:
            return []
        parsed = _parse_suggested_tags_response(raw_content)
        return parsed[:MAX_SUGGESTED_TAGS]
    except Exception as exc:
        print(f"[documents] Failed to generate tags: {exc}")
        return []


def _parse_suggested_tags_response(raw_content: str) -> List[str]:
    try:
        loaded = json.loads(raw_content)
        if isinstance(loaded, dict):
            if "tags" in loaded:
                candidate = loaded["tags"]
            elif "labels" in loaded:
                candidate = loaded["labels"]
            else:
                candidate = loaded
        else:
            candidate = loaded
    except json.JSONDecodeError:
        lines = [line.strip("-• ").strip() for line in raw_content.splitlines()]
        candidate = [line for line in lines if line]

    parsed_tags: List[str] = []
    if isinstance(candidate, dict):
        candidate = list(candidate.values())
    if isinstance(candidate, list):
        for item in candidate:
            if isinstance(item, str):
                label = item.strip()
                if label:
                    parsed_tags.append(label)
    return parsed_tags


def _suggest_document_date(content: str, fallback: Optional[str]) -> Optional[datetime]:
    cleaned = (content or fallback or "").strip()
    if not cleaned or not OLLAMA_CHAT_MODEL:
        return None
    excerpt = cleaned[:MAX_DATE_PROMPT_CHARS]
    payload = {
        "model": OLLAMA_CHAT_MODEL,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You extract dates from documents. Find the primary date the document was written or refers to. "
                    "Respond with JSON like {\"date\": \"YYYY-MM-DD\"} or {\"date\": null} if unsure."
                ),
            },
            {
                "role": "user",
                "content": (
                    "Document excerpt:\n{excerpt}\n\n"
                    "If a clear date is present, respond with it in ISO-8601 (YYYY-MM-DD or YYYY-MM-DDTHH:MM)."
                ).format(excerpt=excerpt),
            },
        ],
        "stream": False,
    }
    try:
        response = requests.post(
            f"{OLLAMA_HOST}/api/chat", json=payload, timeout=DOCUMENT_LLM_TIMEOUT
        )
        response.raise_for_status()
        data = response.json()
        message = data.get("message") or {}
        raw_content = (message.get("content") or "").strip()
        if not raw_content:
            return None
        candidate = _parse_date_response(raw_content)
        if candidate:
            return candidate
    except Exception as exc:
        print(f"[documents] Failed to infer date: {exc}")
    return None


def _parse_date_response(raw_content: str) -> Optional[datetime]:
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


def _parse_iso_datetime(value: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


def _parse_flexible_date(value: str) -> Optional[datetime]:
    try:
        return datetime.strptime(value, "%Y-%m-%d")
    except Exception:
        return None


def _translate_text_to_english(text: str, max_chars: int) -> str:
    trimmed = (text or "").strip()
    if not trimmed or not OLLAMA_CHAT_MODEL:
        return text
    excerpt = trimmed[:max_chars]
    payload = {
        "model": OLLAMA_CHAT_MODEL,
        "messages": [
            {
                "role": "system",
                "content": "Translate the user's text into fluent English. Respond with the translation only.",
            },
            {
                "role": "user",
                "content": excerpt[:100],
            },
        ],
        "stream": False,
    }

    print(f"Payload: {payload}")
    try:
        response = requests.post(f"{OLLAMA_HOST}/api/chat", json=payload, timeout=DOCUMENT_LLM_TIMEOUT)
        response.raise_for_status()
        data = response.json()
        message = data.get("message") or {}
        candidate = (message.get("content") or "").strip()
        return candidate or text
    except Exception as exc:
        print(f"[documents] Failed to translate text: {exc}")
        return text


def _translate_tags_to_english(tags: Sequence[str]) -> List[str]:
    normalized = [t for t in tags if t]
    if not normalized or not OLLAMA_CHAT_MODEL:
        return normalized
    prompt = (
        "Translate each of the following labels into concise English (1-3 words). "
        "Respond with JSON like {\"tags\": [\"tag\", ...]} in the same order."
    )
    payload = {
        "model": OLLAMA_CHAT_MODEL,
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": json.dumps({"tags": normalized}, ensure_ascii=False)},
        ],
        "stream": False,
    }
    try:
        response = requests.post(f"{OLLAMA_HOST}/api/chat", json=payload, timeout=DOCUMENT_LLM_TIMEOUT)
        response.raise_for_status()
        data = response.json()
        message = data.get("message") or {}
        raw = (message.get("content") or "").strip()
        if not raw:
            return normalized
        parsed = json.loads(raw)
        if isinstance(parsed, dict) and isinstance(parsed.get("tags"), list):
            translated = []
            for item in parsed["tags"]:
                if isinstance(item, str):
                    cleaned = item.strip()
                    if cleaned:
                        translated.append(cleaned)
            return translated or normalized
    except Exception as exc:
        print(f"[documents] Failed to translate tags: {exc}")
    return normalized


def _summarize_description(content: str) -> Optional[str]:
    cleaned = (content or "").strip()
    if not cleaned or not OLLAMA_CHAT_MODEL:
        return None
    excerpt = cleaned[:MAX_DESCRIPTION_PROMPT_CHARS]
    payload = {
        "model": OLLAMA_CHAT_MODEL,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Provide a concise English description (<= 400 words) of the user's document excerpt. "
                    "Highlight the main topic and purpose."
                ),
            },
            {
                "role": "user",
                "content": excerpt,
            },
        ],
        "stream": False,
    }
    try:
        response = requests.post(f"{OLLAMA_HOST}/api/chat", json=payload, timeout=DOCUMENT_LLM_TIMEOUT)
        response.raise_for_status()
        data = response.json()
        message = data.get("message") or {}
        candidate = (message.get("content") or "").strip()
        return candidate or None
    except Exception as exc:
        print(f"[documents] Failed to summarize description: {exc}")
        return None


def _upsert_document(
    *,
    document_id: str,
    title: str,
    tags: Sequence[str],
    description: Optional[str],
    stored: StoredFileInfo,
    content: str,
    embedding: Sequence[float],
    document_date: Optional[datetime],
    raw_metadata: Dict[str, Any],
) -> Dict[str, Any]:
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


def _vector_search_documents(query: str, k: int) -> Dict[str, float]:
    query_vector = embed_text(query)
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


def _bm25_search_documents(query: str, k: int) -> Dict[str, float]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT document_id, ts_rank_cd(content_tsv, plainto_tsquery('english', %s)) AS score
            FROM documents
            WHERE content_tsv @@ plainto_tsquery('english', %s)
            ORDER BY score DESC
            LIMIT %s
            """,
            (query, query, k),
        )
        return {row["document_id"]: float(row["score"]) for row in cur.fetchall()}


def _tag_search_documents(tags: Sequence[str]) -> Dict[str, float]:
    if not tags:
        return {}
    lowered_tags = {tag.lower() for tag in tags if tag}
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT document_id, tags
            FROM documents
            WHERE tags && %s::text[]
            """,
            (list(tags),),
        )
        rows = cur.fetchall()

    scores: Dict[str, float] = {}
    for row in rows:
        doc_tags = row.get("tags") or []
        overlap = 0
        for tag in doc_tags:
            if isinstance(tag, str) and tag.lower() in lowered_tags:
                overlap += 1
        if overlap:
            scores[row["document_id"]] = 0.3 + 0.2 * overlap
        else:
            scores[row["document_id"]] = 0.2
    return scores


def _fetch_documents(document_ids: Sequence[str]) -> List[Dict[str, Any]]:
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
    row: Dict[str, Any],
    *,
    include_metadata: bool = False,
    include_content: bool = False,
) -> Dict[str, Any]:
    snippet_source = row.get("description") or row.get("content") or ""
    document: Dict[str, Any] = {
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


def _default_description(content: str, fallback: Optional[str]) -> str:
    snippet = _make_snippet(content, 200)
    if snippet:
        return snippet
    return fallback or "Document description unavailable"


def _preferred_extension(filename: str, mime_type: Optional[str]) -> Optional[str]:
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


def _normalize_strings(values: Iterable[str] | None) -> List[str]:
    if not values:
        return []
    seen = set()
    normalized: List[str] = []
    for item in values:
        if item is None:
            continue
        candidate = str(item).strip()
        if not candidate:
            continue
        lower = candidate.lower()
        if lower in seen:
            continue
        seen.add(lower)
        normalized.append(candidate)
    return normalized


def _merge_tag_lists(primary: Sequence[str], secondary: Sequence[str]) -> List[str]:
    merged: List[str] = list(primary or [])
    seen = {tag.lower() for tag in merged if isinstance(tag, str)}
    for tag in secondary:
        if not isinstance(tag, str):
            continue
        candidate = tag.strip()
        if not candidate:
            continue
        lowered = candidate.lower()
        if lowered in seen:
            continue
        merged.append(candidate)
        seen.add(lowered)
    return merged


def _derive_title_from_filename(filename: Optional[str]) -> Optional[str]:
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


def _suggest_title(content: str, fallback: Optional[str]) -> Optional[str]:
    cleaned = (content or "").strip()
    if not cleaned or not OLLAMA_CHAT_MODEL:
        return _derive_title_from_filename(fallback)
    excerpt = cleaned[:MAX_TITLE_PROMPT_CHARS]
    payload = {
        "model": OLLAMA_CHAT_MODEL,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You generate concise, descriptive document titles (maximum 8 words). "
                    "Respond with text only, no JSON."
                ),
            },
            {
                "role": "user",
                "content": (
                    "Suggest a short title for the following document excerpt:\n\n"
                    "{excerpt}"
                ).format(excerpt=excerpt),
            },
        ],
        "stream": False,
    }
    try:
        response = requests.post(
            f"{OLLAMA_HOST}/api/chat", json=payload, timeout=DOCUMENT_LLM_TIMEOUT
        )
        response.raise_for_status()
        data = response.json()
        message = data.get("message") or {}
        candidate = (message.get("content") or "").strip()
        if candidate:
            return candidate.splitlines()[0].strip()
    except Exception as exc:
        print(f"[documents] Failed to generate title: {exc}")
    return _derive_title_from_filename(fallback)

