#!/usr/bin/env python3
"""
Re-embed all events and documents with the new nomic-embed-text-v2-moe model.

This script regenerates embeddings for all records in the database using the
new embedding model with proper task prefixes (search_document).

Usage:
    python scripts/reembed_all.py [--batch-size N] [--events-only] [--documents-only] [--dry-run]

Options:
    --batch-size N      Number of records to process per batch (default: 100)
    --events-only       Only re-embed events
    --documents-only    Only re-embed documents
    --dry-run           Show what would be done without making changes
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Dict, List, Sequence
from dotenv import load_dotenv
load_dotenv('../.env')  # Load from backend/.env

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import get_conn
from embeddings import embed_text

# Constants - nomic-embed-text v1 has 8192 token context
MAX_EVENT_EMBED_CHARS = 6000
MAX_DOCUMENT_EMBED_CHARS = 8000


def generate_event_embed_text(event: Dict[str, Any]) -> str:
    """Generate the text to embed for an event (mirrors _generate_event_embedding logic)."""
    segments: List[str] = []

    title = event.get("title")
    if isinstance(title, str) and title.strip():
        segments.append(title.strip())

    summary = event.get("summary") or event.get("content")
    if isinstance(summary, str) and summary.strip():
        segments.append(summary.strip())

    tags = event.get("tags")
    if isinstance(tags, (list, tuple)):
        formatted = ", ".join(str(tag).strip() for tag in tags if isinstance(tag, str) and tag.strip())
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


def generate_document_embed_text(document: Dict[str, Any]) -> str:
    """Generate the text to embed for a document (mirrors _generate_document_embedding logic)."""
    segments: List[str] = []

    tags = document.get("tags")
    if isinstance(tags, (list, tuple)):
        tag_text = " ".join(str(tag).strip() for tag in tags if isinstance(tag, str) and tag.strip())
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


def count_records(table: str, id_col: str) -> int:
    """Count total records in a table."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) as cnt FROM {table}")
        row = cur.fetchone()
        return row["cnt"] if row else 0


def fetch_events_batch(offset: int, limit: int) -> List[Dict[str, Any]]:
    """Fetch a batch of events."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, title, summary, tags, types, people, place_id, raw
            FROM events
            ORDER BY id
            OFFSET %s LIMIT %s
            """,
            (offset, limit),
        )
        return list(cur.fetchall())


def fetch_documents_batch(offset: int, limit: int) -> List[Dict[str, Any]]:
    """Fetch a batch of documents."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT document_id, title, description, content, tags, file_name
            FROM documents
            ORDER BY document_id
            OFFSET %s LIMIT %s
            """,
            (offset, limit),
        )
        return list(cur.fetchall())


def update_event_embedding(event_id: str, embedding: Sequence[float]) -> None:
    """Update the embedding for a single event."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE events SET what_embed = %s::vector WHERE id = %s",
            (list(embedding), event_id),
        )
        conn.commit()


def update_document_embedding(document_id: str, embedding: Sequence[float]) -> None:
    """Update the embedding for a single document."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE documents SET content_embed = %s::vector WHERE document_id = %s",
            (list(embedding), document_id),
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
                    print(f"  [events] Processed {processed}/{total} ({100*processed/total:.1f}%)")

            except Exception as e:
                print(f"  [events] ERROR processing event {event_id}: {e}")
                failed += 1

        offset += batch_size

        # Small delay to avoid overwhelming the embedding service
        if not dry_run:
            time.sleep(0.1)

    print(f"[events] Completed: {processed} processed, {failed} failed")
    return processed


def reembed_documents(batch_size: int, dry_run: bool) -> int:
    """Re-embed all documents. Returns count of processed records."""
    total = count_records("documents", "document_id")
    print(f"\n[documents] Found {total} documents to re-embed")

    if total == 0:
        return 0

    processed = 0
    failed = 0
    offset = 0

    while offset < total:
        batch = fetch_documents_batch(offset, batch_size)
        if not batch:
            break

        for doc in batch:
            doc_id = doc["document_id"]
            try:
                embed_text_str = generate_document_embed_text(doc)

                if dry_run:
                    print(f"  [dry-run] Would re-embed document {doc_id}: {embed_text_str[:80]}...")
                else:
                    embedding = embed_text(embed_text_str)
                    update_document_embedding(doc_id, embedding)

                processed += 1

                if processed % 10 == 0:
                    print(f"  [documents] Processed {processed}/{total} ({100*processed/total:.1f}%)")

            except Exception as e:
                print(f"  [documents] ERROR processing document {doc_id}: {e}")
                failed += 1

        offset += batch_size

        # Small delay to avoid overwhelming the embedding service
        if not dry_run:
            time.sleep(0.1)

    print(f"[documents] Completed: {processed} processed, {failed} failed")
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
        "--dry-run",
        action="store_true",
        help="Show what would be done without making changes",
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

    if not args.documents_only:
        total_processed += reembed_events(args.batch_size, args.dry_run)

    if not args.events_only:
        total_processed += reembed_documents(args.batch_size, args.dry_run)

    elapsed = time.time() - start_time
    print("\n" + "=" * 60)
    print(f"Migration complete!")
    print(f"Total records processed: {total_processed}")
    print(f"Time elapsed: {elapsed:.1f} seconds")
    print("=" * 60)


if __name__ == "__main__":
    main()
