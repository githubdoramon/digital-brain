#!/usr/bin/env python3
"""
Re-embed all events, documents, and contacts with the new nomic-embed-text-v2-moe model.

This script regenerates embeddings for all records in the database using the
new embedding model with proper task prefixes (search_document).

Usage:
    python scripts/reembed_all.py [--batch-size N] [--events-only] [--documents-only] [--contacts-only] [--dry-run]

Options:
    --batch-size N      Number of records to process per batch (default: 100)
    --events-only       Only re-embed events
    --documents-only    Only re-embed documents
    --dry-run           Show what would be done without making changes
    --contacts-only     Only re-embed contacts
    --debug-documents   Emit detailed per-document reprocessing logs
    --debug-document-id Emit detailed logs only for one document_id
"""

from __future__ import annotations

import argparse
import builtins
import json
import logging
import os
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

# Add paret directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import get_conn  # noqa: E402
from documents import (
    MAX_CONTENT_CHARS,  # noqa: E402
    prepare_document_content_for_storage,  # noqa: E402
    reprocess_document_content,  # noqa: E402
)
from documents import _generate_document_embeddings as generate_document_embeddings  # noqa: E402
from documents import _replace_document_chunks as replace_document_chunks  # noqa: E402
from embeddings import embed_text  # noqa: E402

logger = logging.getLogger(__name__)


def _log_print(*args: object, **kwargs: object) -> None:
    sep_raw = kwargs.get("sep", " ")
    end_raw = kwargs.get("end", "")
    sep = sep_raw if isinstance(sep_raw, str) else str(sep_raw)
    end = end_raw if isinstance(end_raw, str) else str(end_raw)
    message = f"{sep.join(str(arg) for arg in args)}{end}".rstrip("\n")
    if not message:
        return
    lowered = message.lower()
    is_error = "error" in lowered or "failed" in lowered or "✗" in message
    if is_error:
        logger.warning(message)
    else:
        logger.info(message)
    builtins.print(message, file=sys.stderr if is_error else sys.stdout, flush=True)


print = _log_print

# Constants for payload assembly before centralized embedding truncation
MAX_EVENT_EMBED_CHARS = 6000
MAX_DOCUMENT_EMBED_CHARS = 8000
MAX_CONTACT_EMBED_CHARS = 4000


def generate_event_embed_text(event: dict[str, Any]) -> str:
    """Generate the text to embed for an event (mirrors _generate_event_embedding logic)."""
    segments: list[str] = []

    title = event.get("title")
    if isinstance(title, str) and title.strip():
        segments.append(title.strip())

    summary = event.get("summary") or event.get("content")
    if isinstance(summary, str) and summary.strip():
        segments.append(summary.strip())

    tags = event.get("tags")
    if isinstance(tags, (list, tuple)):
        formatted = ", ".join(
            str(tag).strip() for tag in tags if isinstance(tag, str) and tag.strip()
        )
        if formatted:
            segments.append(f"tags: {formatted}")

    types = event.get("types")
    if isinstance(types, (list, tuple)):
        formatted = ", ".join(str(t).strip() for t in types if isinstance(t, str) and t.strip())
        if formatted:
            segments.append(f"types: {formatted}")

    people = event.get("people")
    if isinstance(people, (list, tuple)):
        formatted = ", ".join(str(person).strip() for person in people if person)
        if formatted:
            segments.append(f"people: {formatted}")

    place_id = event.get("place_id")
    if place_id:
        segments.append(f"place: {place_id}")

    raw = event.get("raw")
    if isinstance(raw, (dict, list)):
        try:
            raw_text = json.dumps(raw, ensure_ascii=False)
        except TypeError:
            raw_text = str(raw)
        if raw_text:
            segments.append(raw_text)
    elif isinstance(raw, str) and raw.strip():
        segments.append(raw.strip())

    if not segments:
        fallback = event.get("id") or ""
        segments.append(str(fallback or "event"))

    combined = " ".join(segments).strip()
    if not combined:
        combined = str(event.get("id") or "event")

    return combined[:MAX_EVENT_EMBED_CHARS] or "event"


def generate_document_embed_text(document: dict[str, Any]) -> str:
    """Generate the text to embed for a document (mirrors _generate_document_embedding logic)."""
    segments: list[str] = []

    tags = document.get("tags")
    if isinstance(tags, (list, tuple)):
        tag_text = " ".join(
            str(tag).strip() for tag in tags if isinstance(tag, str) and tag.strip()
        )
        if tag_text:
            segments.append(tag_text)

    content = document.get("content")
    if isinstance(content, str) and content.strip():
        segments.append(content.strip())

    description = document.get("description")
    if isinstance(description, str) and description.strip() and description.strip() not in segments:
        segments.append(description.strip())

    title = document.get("title")
    if isinstance(title, str) and title.strip():
        segments.append(title.strip())

    file_name = document.get("file_name")
    if isinstance(file_name, str) and file_name.strip():
        segments.append(file_name.strip())

    combined = " ".join(segments).strip()
    return combined[:MAX_DOCUMENT_EMBED_CHARS] or "document"


def generate_contact_embed_text(contact: dict[str, Any]) -> str:
    """Generate the text to embed for a contact (mirrors contact embedding logic)."""
    segments: list[str] = []

    display_name = contact.get("display_name")
    if isinstance(display_name, str) and display_name.strip():
        segments.append(display_name.strip())

    aliases = contact.get("aliases")
    if isinstance(aliases, (list, tuple)):
        alias_text = ", ".join(
            str(alias).strip() for alias in aliases if isinstance(alias, str) and alias.strip()
        )
        if alias_text:
            segments.append(f"aliases: {alias_text}")

    tags = contact.get("tags")
    if isinstance(tags, (list, tuple)):
        tag_text = ", ".join(
            str(tag).strip() for tag in tags if isinstance(tag, str) and tag.strip()
        )
        if tag_text:
            segments.append(f"tags: {tag_text}")

    comments = contact.get("comments")
    if isinstance(comments, str) and comments.strip():
        segments.append(comments.strip())

    if not segments:
        fallback = contact.get("contact_id") or "contact"
        segments.append(str(fallback))

    combined = " ".join(segments).strip()
    return combined[:MAX_CONTACT_EMBED_CHARS] or "contact"


def count_records(table: str, id_col: str) -> int:
    """Count total records in a table."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(cast(Any, f"SELECT COUNT(*) as cnt FROM {table}"))
        row = cur.fetchone()
        if not row:
            return 0
        row_dict = cast(dict[str, Any], row)
        return int(row_dict.get("cnt", 0))


def fetch_events_batch(offset: int, limit: int) -> list[dict[str, Any]]:
    """Fetch a batch of events with people from the event_contacts junction table."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, title, summary, tags, types, place_id, raw
            FROM events
            ORDER BY id
            OFFSET %s LIMIT %s
            """,
            (offset, limit),
        )
        rows = [dict(row) for row in cur.fetchall()]
        if rows:
            event_ids = [r["id"] for r in rows]
            cur.execute(
                "SELECT event_id, contact_id FROM event_contacts WHERE event_id = ANY(%s)",
                (event_ids,),
            )
            people_map: dict[str, list[str]] = {}
            for r in cur.fetchall():
                people_map.setdefault(r["event_id"], []).append(r["contact_id"])
            for r in rows:
                r["people"] = people_map.get(r["id"], [])
        return rows


def fetch_documents_batch(offset: int, limit: int) -> list[dict[str, Any]]:
    """Fetch a batch of documents."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT
              document_id,
              title,
              description,
              content,
              tags,
              file_name,
              file_path,
              file_mime,
              raw_metadata
            FROM documents
            ORDER BY document_id
            OFFSET %s LIMIT %s
            """,
            (offset, limit),
        )
        return [dict(row) for row in cur.fetchall()]


def document_exists(document_id: str) -> bool:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM documents WHERE document_id = %s LIMIT 1",
            (document_id,),
        )
        return cur.fetchone() is not None


def fetch_contacts_batch(offset: int, limit: int) -> list[dict[str, Any]]:
    """Fetch a batch of contacts."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT contact_id, display_name, aliases, tags, comments
            FROM contacts
            ORDER BY contact_id
            OFFSET %s LIMIT %s
            """,
            (offset, limit),
        )
        return [dict(row) for row in cur.fetchall()]


def update_event_embedding(event_id: str, embedding: Sequence[float]) -> None:
    """Update the embedding for a single event."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE events SET what_embed = %s::vector WHERE id = %s",
            (list(embedding), event_id),
        )
        conn.commit()


def update_document_embedding(
    document_id: str,
    embedding: Sequence[float],
    chunk_embeddings: Sequence[Any],
    raw_metadata: dict[str, Any],
    content: str | None = None,
) -> None:
    """Update the embedding for a single document."""
    with get_conn() as conn, conn.cursor() as cur:
        if content is None:
            cur.execute(
                """
                UPDATE documents
                SET content_embed = %s::vector,
                    raw_metadata = %s::jsonb
                WHERE document_id = %s
                """,
                (list(embedding), json.dumps(raw_metadata or {}), document_id),
            )
        else:
            cur.execute(
                """
                UPDATE documents
                SET content_embed = %s::vector,
                    raw_metadata = %s::jsonb,
                    content = %s
                WHERE document_id = %s
                """,
                (list(embedding), json.dumps(raw_metadata or {}), content, document_id),
            )
        replace_document_chunks(
            cur,
            document_id=document_id,
            chunk_embeddings=chunk_embeddings,
        )
        conn.commit()


def normalize_raw_metadata(raw_metadata: Any) -> dict[str, Any]:
    if isinstance(raw_metadata, dict):
        return dict(raw_metadata)
    if isinstance(raw_metadata, str):
        try:
            loaded = json.loads(raw_metadata)
            if isinstance(loaded, dict):
                return loaded
        except json.JSONDecodeError:
            pass
        return {"raw": raw_metadata}
    return {}


def recover_document_content(doc: dict[str, Any]) -> str:
    current_content = doc.get("content")
    if isinstance(current_content, str) and current_content.strip():
        return current_content[:MAX_CONTENT_CHARS]

    file_path = doc.get("file_path")
    if not isinstance(file_path, str) or not file_path.strip():
        return ""

    path = Path(file_path)
    if not path.exists():
        print(
            "[documents] content missing and file path not found "
            f"document_id={doc.get('document_id')} file_path={file_path}"
        )
        return ""

    result = reprocess_document_content(path=path, mime_type=doc.get("file_mime"))
    recovered = (result.get("normalized_content") or "").strip()
    if not recovered:
        print(
            "[documents] failed to recover content from file "
            f"document_id={doc.get('document_id')} file_path={file_path}"
        )
        return ""

    clipped = recovered[:MAX_CONTENT_CHARS]
    print(
        "[documents] recovered content from file for re-embed "
        f"document_id={doc.get('document_id')} chars={len(clipped)}"
    )
    return clipped


def reprocess_document(
    doc: dict[str, Any], raw_metadata: dict[str, Any]
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    document_id = str(doc.get("document_id") or "")
    debug: dict[str, Any] = {
        "used_file_reprocess": False,
        "used_fallback_content": False,
        "fallback_reason": None,
        "file_exists": False,
        "file_path": doc.get("file_path"),
        "parser_used": None,
        "parser_warnings": [],
        "normalized_sections": 0,
        "normalized_chars": 0,
        "stored_chars": 0,
        "raw_chars": 0,
        "translated_for_storage": False,
    }
    current_content = doc.get("content")
    fallback_content = (
        current_content[:MAX_CONTENT_CHARS]
        if isinstance(current_content, str) and current_content.strip()
        else ""
    )

    file_path = doc.get("file_path")
    if not isinstance(file_path, str) or not file_path.strip():
        if fallback_content:
            debug["used_fallback_content"] = True
            debug["fallback_reason"] = "missing_file_path"
            stored_content, stored_metadata = prepare_document_content_for_storage(
                fallback_content,
                document_id=document_id,
                raw_metadata=raw_metadata,
            )
            debug["stored_chars"] = len(stored_content)
            debug["translated_for_storage"] = bool(
                stored_metadata.get("content_translated_for_storage")
            )
            return stored_content, stored_metadata, debug
        debug["fallback_reason"] = "missing_file_path"
        return "", raw_metadata, debug

    path = Path(file_path)
    debug["file_exists"] = path.exists()
    if not path.exists():
        if fallback_content:
            debug["used_fallback_content"] = True
            debug["fallback_reason"] = "file_not_found"
            stored_content, stored_metadata = prepare_document_content_for_storage(
                fallback_content,
                document_id=document_id,
                raw_metadata=raw_metadata,
            )
            debug["stored_chars"] = len(stored_content)
            debug["translated_for_storage"] = bool(
                stored_metadata.get("content_translated_for_storage")
            )
            return stored_content, stored_metadata, debug
        print(
            "[documents] content missing and file path not found "
            f"document_id={doc.get('document_id')} file_path={file_path}"
        )
        debug["fallback_reason"] = "file_not_found"
        return "", raw_metadata, debug

    result = reprocess_document_content(path=path, mime_type=doc.get("file_mime"))
    debug["used_file_reprocess"] = True
    normalized_content = (result.get("normalized_content") or "").strip()[:MAX_CONTENT_CHARS]
    debug["normalized_chars"] = len(normalized_content)
    debug["raw_chars"] = len((result.get("raw_extracted_text") or "").strip())
    debug["parser_used"] = result.get("parser_used")
    debug["parser_warnings"] = result.get("parser_warnings") or []
    debug["normalized_sections"] = len(result.get("normalized_sections") or [])
    if not normalized_content:
        debug["fallback_reason"] = "empty_normalized_content"
        if fallback_content:
            debug["used_fallback_content"] = True
            stored_content, stored_metadata = prepare_document_content_for_storage(
                fallback_content,
                document_id=document_id,
                raw_metadata=raw_metadata,
            )
            debug["stored_chars"] = len(stored_content)
            debug["translated_for_storage"] = bool(
                stored_metadata.get("content_translated_for_storage")
            )
            return stored_content, stored_metadata, debug
        return "", raw_metadata, debug

    if result.get("raw_extracted_text"):
        raw_metadata["raw_extracted_text"] = result["raw_extracted_text"]
    if result.get("parser_used"):
        raw_metadata["parser_used"] = result["parser_used"]
    if result.get("parser_warnings"):
        raw_metadata["parser_warnings"] = result["parser_warnings"]
    if result.get("normalized_sections"):
        raw_metadata["normalized_sections"] = result["normalized_sections"]
    if result.get("normalization_metadata"):
        raw_metadata["normalization_metadata"] = result["normalization_metadata"]
    stored_content, stored_metadata = prepare_document_content_for_storage(
        normalized_content,
        document_id=document_id,
        raw_metadata=raw_metadata,
    )
    debug["stored_chars"] = len(stored_content)
    debug["translated_for_storage"] = bool(stored_metadata.get("content_translated_for_storage"))
    return stored_content, stored_metadata, debug


def _should_debug_document(
    document_id: str,
    *,
    debug_documents: bool,
    debug_document_id: str | None,
) -> bool:
    if debug_document_id:
        return document_id == debug_document_id
    return debug_documents


def _log_document_reprocess_debug(
    document_id: str,
    *,
    debug_info: dict[str, Any],
    chunk_count: int | None = None,
    persisted_content: bool | None = None,
) -> None:
    print(
        "[documents][debug] "
        f"document_id={document_id} "
        f"file_path={debug_info.get('file_path')} "
        f"file_exists={debug_info.get('file_exists')} "
        f"used_file_reprocess={debug_info.get('used_file_reprocess')} "
        f"used_fallback_content={debug_info.get('used_fallback_content')} "
        f"fallback_reason={debug_info.get('fallback_reason')} "
        f"parser_used={debug_info.get('parser_used')} "
        f"parser_warnings={debug_info.get('parser_warnings')} "
        f"raw_chars={debug_info.get('raw_chars')} "
        f"normalized_chars={debug_info.get('normalized_chars')} "
        f"stored_chars={debug_info.get('stored_chars')} "
        f"normalized_sections={debug_info.get('normalized_sections')} "
        f"translated_for_storage={debug_info.get('translated_for_storage')} "
        f"chunk_count={chunk_count if chunk_count is not None else 'n/a'} "
        f"persisted_content={persisted_content if persisted_content is not None else 'n/a'}"
    )


def update_contact_embedding(contact_id: str, embedding: Sequence[float]) -> None:
    """Update the embedding for a single contact."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE contacts SET comments_embed = %s::vector WHERE contact_id = %s",
            (list(embedding), contact_id),
        )
        conn.commit()


def reembed_events(batch_size: int, dry_run: bool) -> int:
    """Re-embed all events. Returns count of processed records."""
    total = count_records("events", "id")
    print(f"\n[events] Found {total} events to re-embed")

    if total == 0:
        return 0

    processed = 0
    failed = 0
    offset = 0

    while offset < total:
        batch = fetch_events_batch(offset, batch_size)
        if not batch:
            break

        for event in batch:
            event_id = event["id"]
            try:
                embed_text_str = generate_event_embed_text(event)

                if dry_run:
                    print(f"  [dry-run] Would re-embed event {event_id}: {embed_text_str[:80]}...")
                else:
                    embedding = embed_text(embed_text_str)
                    update_event_embedding(event_id, embedding)

                processed += 1

                if processed % 10 == 0:
                    print(
                        f"  [events] Processed {processed}/{total} ({100 * processed / total:.1f}%)"
                    )

            except Exception as e:
                print(f"  [events] ERROR processing event {event_id}: {e}")
                failed += 1

        offset += batch_size

        # Small delay to avoid overwhelming the embedding service
        if not dry_run:
            time.sleep(0.1)

    print(f"[events] Completed: {processed} processed, {failed} failed")
    return processed


def reembed_documents(
    batch_size: int,
    dry_run: bool,
    *,
    debug_documents: bool = False,
    debug_document_id: str | None = None,
) -> int:
    """Re-embed all documents. Returns count of processed records."""
    total = count_records("documents", "document_id")
    print(f"\n[documents] Found {total} documents to re-embed")

    if total == 0:
        return 0

    processed = 0
    failed = 0
    debug_matches = 0
    offset = 0

    while offset < total:
        batch = fetch_documents_batch(offset, batch_size)
        if not batch:
            break

        for doc in batch:
            doc_id = doc["document_id"]
            try:
                raw_metadata = normalize_raw_metadata(doc.get("raw_metadata"))
                rebuilt_content, raw_metadata, debug_info = reprocess_document(doc, raw_metadata)
                should_persist_content = False
                if rebuilt_content:
                    existing_content = doc.get("content")
                    had_content = bool(
                        isinstance(existing_content, str) and existing_content.strip()
                    )
                    doc["content"] = rebuilt_content
                    should_persist_content = (not had_content) or (
                        isinstance(existing_content, str)
                        and existing_content[:MAX_CONTENT_CHARS] != rebuilt_content
                    )

                if dry_run:
                    embed_text_str = generate_document_embed_text(doc)
                    print(f"  [dry-run] Would re-embed document {doc_id}: {embed_text_str[:80]}...")
                    should_debug = _should_debug_document(
                        doc_id,
                        debug_documents=debug_documents,
                        debug_document_id=debug_document_id,
                    )
                    if should_debug:
                        debug_matches += 1
                        _log_document_reprocess_debug(
                            doc_id,
                            debug_info=debug_info,
                            chunk_count=None,
                            persisted_content=should_persist_content,
                        )
                else:
                    embedding, chunk_embeddings = generate_document_embeddings(
                        doc, raw_metadata=raw_metadata
                    )
                    update_document_embedding(
                        doc_id,
                        embedding,
                        chunk_embeddings,
                        raw_metadata,
                        content=rebuilt_content if should_persist_content else None,
                    )
                    should_debug = _should_debug_document(
                        doc_id,
                        debug_documents=debug_documents,
                        debug_document_id=debug_document_id,
                    )
                    if should_debug:
                        debug_matches += 1
                        _log_document_reprocess_debug(
                            doc_id,
                            debug_info=debug_info,
                            chunk_count=len(chunk_embeddings),
                            persisted_content=should_persist_content,
                        )

                processed += 1

                if processed % 10 == 0:
                    print(
                        f"  [documents] Processed {processed}/{total} ({100 * processed / total:.1f}%)"
                    )

            except Exception as e:
                print(f"  [documents] ERROR processing document {doc_id}: {e}")
                failed += 1

        offset += batch_size

        # Small delay to avoid overwhelming the embedding service
        if not dry_run:
            time.sleep(0.1)

    print(f"[documents] Completed: {processed} processed, {failed} failed")
    if debug_document_id and debug_matches == 0:
        exists = document_exists(debug_document_id)
        print(
            "[documents][debug] no matching document was debugged "
            f"document_id={debug_document_id} exists_in_db={exists}"
        )
    return processed


def reembed_contacts(batch_size: int, dry_run: bool) -> int:
    """Re-embed all contacts. Returns count of processed records."""
    total = count_records("contacts", "contact_id")
    print(f"\n[contacts] Found {total} contacts to re-embed")

    if total == 0:
        return 0

    processed = 0
    failed = 0
    offset = 0

    while offset < total:
        batch = fetch_contacts_batch(offset, batch_size)
        if not batch:
            break

        for contact in batch:
            contact_id = contact["contact_id"]
            try:
                embed_text_str = generate_contact_embed_text(contact)

                if dry_run:
                    print(
                        f"  [dry-run] Would re-embed contact {contact_id}: {embed_text_str[:80]}..."
                    )
                else:
                    embedding = embed_text(embed_text_str)
                    update_contact_embedding(contact_id, embedding)

                processed += 1

                if processed % 10 == 0:
                    print(
                        f"  [contacts] Processed {processed}/{total} ({100 * processed / total:.1f}%)"
                    )

            except Exception as e:
                print(f"  [contacts] ERROR processing contact {contact_id}: {e}")
                failed += 1

        offset += batch_size

        # Small delay to avoid overwhelming the embedding service
        if not dry_run:
            time.sleep(0.1)

    print(f"[contacts] Completed: {processed} processed, {failed} failed")
    return processed


def main():
    parser = argparse.ArgumentParser(
        description="Re-embed all events and documents with the new embedding model"
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=100,
        help="Number of records to process per batch (default: 100)",
    )
    parser.add_argument(
        "--events-only",
        action="store_true",
        help="Only re-embed events",
    )
    parser.add_argument(
        "--documents-only",
        action="store_true",
        help="Only re-embed documents",
    )
    parser.add_argument(
        "--contacts-only",
        action="store_true",
        help="Only re-embed contacts",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without making changes",
    )
    parser.add_argument(
        "--debug-documents",
        action="store_true",
        help="Emit detailed per-document reprocessing logs",
    )
    parser.add_argument(
        "--debug-document-id",
        type=str,
        default=None,
        help="Emit debug logs only for one document_id",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("Re-embedding Migration Script")
    print("=" * 60)
    print(f"Embedding model: {os.getenv('OLLAMA_EMBED_MODEL', 'nomic-embed-text')}")
    print(f"Batch size: {args.batch_size}")
    print(f"Dry run: {args.dry_run}")
    print("=" * 60)

    if args.dry_run:
        print("\n*** DRY RUN MODE - No changes will be made ***\n")

    start_time = time.time()
    total_processed = 0

    if args.contacts_only:
        total_processed += reembed_contacts(args.batch_size, args.dry_run)
    else:
        if not args.documents_only:
            total_processed += reembed_events(args.batch_size, args.dry_run)

        if not args.events_only:
            total_processed += reembed_documents(
                args.batch_size,
                args.dry_run,
                debug_documents=args.debug_documents,
                debug_document_id=args.debug_document_id,
            )

        if not args.events_only and not args.documents_only:
            total_processed += reembed_contacts(args.batch_size, args.dry_run)

    elapsed = time.time() - start_time
    print("\n" + "=" * 60)
    print("Migration complete!")
    print(f"Total records processed: {total_processed}")
    print(f"Time elapsed: {elapsed:.1f} seconds")
    print("=" * 60)


if __name__ == "__main__":
    main()
