from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from dateparser.search import search_dates
from rapidfuzz import fuzz, process

import events as events_service
from db import fetch_events, get_conn
from documents import _vector_search_documents as vector_search_documents
from embeddings import embed_text
from observability.logger import get_runtime_logger
from search_normalization import normalize_search_text

logger = get_runtime_logger(__name__)


# --------------------------- Resolution helpers ---------------------------
def resolve_query(
    text: str, need_contacts: bool = True, need_places: bool = True
) -> dict[str, Any]:
    q = (text or "").strip()
    people = (
        resolve_entities(
            q, "contacts", "contact_id", "display_name", "aliases", extra_cols=["comments"]
        )
        if need_contacts
        else []
    )
    if need_contacts:
        vector_people = resolve_contact_embeddings(q)
        if vector_people:
            people = list(dict.fromkeys([*people, *vector_people]))
    places = resolve_entities(q, "places", "place_id", "name") if need_places else []
    span = parse_timespan_text(q)
    return {
        "people": people,
        "places": places,
        "timespan": [span[0].isoformat(), span[1].isoformat()] if span else None,
    }


def parse_timespan_text(q: str) -> tuple[datetime, datetime] | None:
    found = search_dates(q, settings={"RETURN_AS_TIMEZONE_AWARE": True})
    if not found:
        return None
    dates = [d[1] for d in found]
    if len(dates) == 1:
        dt = dates[0]
        return (dt - timedelta(days=7), dt + timedelta(days=7))
    start, end = min(dates), max(dates)
    return (start, end)


def resolve_entities(
    q: str,
    table: str,
    key_col: str,
    label_col: str,
    alias_col: str | None = None,
    extra_cols: Sequence[str] | None = None,
    limit: int = 3,
) -> list[str]:
    query_text = normalize_search_text(q)
    if not query_text:
        return []
    with get_conn() as conn, conn.cursor() as cur:
        extra_cols = list(extra_cols or [])
        if alias_col:
            columns = [key_col, label_col, alias_col, *extra_cols]
        else:
            columns = [key_col, label_col, *extra_cols]
        select_cols = ", ".join(columns)
        cur.execute(f"SELECT {select_cols} FROM {table}")
        rows = cur.fetchall()
    choices: list[tuple[str, str]] = []
    for r in rows:
        base_label = normalize_search_text(r[label_col])
        if base_label:
            choices.append((r[key_col], base_label))
        if alias_col and r.get(alias_col):
            for a in r[alias_col]:
                alias_label = normalize_search_text(a)
                if alias_label:
                    choices.append((r[key_col], alias_label))
        for col in extra_cols:
            value = r.get(col)
            if not value:
                continue
            if isinstance(value, (list, tuple, set)):
                for item in value:
                    extra_label = normalize_search_text(str(item))
                    if extra_label:
                        choices.append((r[key_col], extra_label))
            else:
                extra_label = normalize_search_text(str(value))
                if extra_label:
                    choices.append((r[key_col], extra_label))
    if not choices:
        return []
    labels = [c[1] for c in choices]
    matches = process.extract(query_text, labels, scorer=fuzz.WRatio, limit=limit)
    out_ids = {choices[idx][0] for label, score, idx in matches if score >= 85}
    return list(out_ids)


def resolve_contact_embeddings(
    query: str,
    *,
    limit: int = 3,
    score_threshold: float = 0.72,
) -> list[str]:
    if not query:
        return []
    matches = vector_search_contacts(query, limit)
    return [contact_id for contact_id, score in matches if score >= score_threshold]


def _is_sentence_like_query(query: str) -> bool:
    normalized = (query or "").strip()
    if not normalized:
        return False
    tokens = normalized.split()
    return len(tokens) >= 6 or len(normalized) >= 45


# --------------------------- Search helpers ---------------------------
def search_memories(
    query: str,
    people: Sequence[str] | None = None,
    place_ids: Sequence[str] | None = None,
    tags: Sequence[str] | None = None,
    time_start: str | None = None,
    time_end: str | None = None,
    limit: int = 10,
    sort_order: str = "relevance",
) -> dict[str, Any]:
    span_start = None
    span_end = None
    if time_start:
        try:
            span_start = datetime.fromisoformat(time_start)
        except Exception:
            span_start = None
    if time_end:
        try:
            span_end = datetime.fromisoformat(time_end)
        except Exception:
            span_end = None
    span = (span_start, span_end) if (span_start or span_end) else None
    normalized_query = normalize_search_text(query)
    sentence_like_query = _is_sentence_like_query(query)
    ordering = (sort_order or "relevance").lower()
    if ordering not in {"relevance", "newest", "oldest"}:
        ordering = "relevance"
    normalized_tags = [str(tag).strip().lower() for tag in (tags or []) if str(tag).strip()]

    has_structured_filters = bool(span or people or place_ids or normalized_tags)
    is_temporal_query = bool(span)
    use_temporal_ordering = ordering in {"newest", "oldest"}
    temporal_ordering = ordering if ordering in {"newest", "oldest"} else "newest"
    logger.info(
        "[retrieval] search_memories filters people=%s places=%s tags=%s span=%s sort_order=%s limit=%s sentence_like=%s temporal_query=%s",
        list(people or []),
        list(place_ids or []),
        normalized_tags,
        span,
        ordering,
        limit,
        sentence_like_query,
        is_temporal_query,
    )

    vec_events = vector_search(normalized_query, 50) if normalized_query else {}
    bm_events = bm25_search(normalized_query, 50) if normalized_query else {}
    st_events = structured_candidates(
        span,
        list(people or []),
        list(place_ids or []),
        normalized_tags,
        200,
    )

    vec_docs = vector_search_documents(normalized_query, 50) if normalized_query else {}
    bm_docs = bm25_search_documents(normalized_query, 50) if normalized_query else {}
    st_docs = (
        structured_document_candidates(span, normalized_tags, 200)
        if (span or normalized_tags)
        else {}
    )

    if sentence_like_query:
        event_semantic_weight = 0.45
        event_keyword_weight = 0.45
    else:
        event_semantic_weight = 0.6
        event_keyword_weight = 0.3
    event_structured_weight = 0.1

    if sentence_like_query:
        doc_semantic_weight = 0.3
        doc_keyword_weight = 0.7
    else:
        doc_semantic_weight = 0.45
        doc_keyword_weight = 0.55
    doc_structured_weight = 0.0
    if span:
        # Reserve some score mass for temporal fit and scale semantic/keyword accordingly.
        doc_structured_weight = 0.15
        doc_semantic_weight *= 0.85
        doc_keyword_weight *= 0.85

    if has_structured_filters:
        # When filters are provided (people/place/time), treat them as hard constraints.
        # This avoids returning unrelated events that only match text similarity.
        event_ids = set(st_events)
    else:
        event_ids = set(vec_events) | set(bm_events) | set(st_events)
    event_scores: dict[str, float] = {}
    for event_id in event_ids:
        v = vec_events.get(event_id, 0.0)
        b = bm_events.get(event_id, 0.0)
        s = st_events.get(event_id, 0.0)
        score = (
            (event_semantic_weight * v) + (event_keyword_weight * b) + (event_structured_weight * s)
        )
        event_scores[event_id] = score

    doc_ids = set(vec_docs) | set(bm_docs)
    if span or normalized_tags:
        if normalized_query:
            doc_ids = doc_ids & set(st_docs)
        else:
            doc_ids = set(st_docs)
    doc_scores: dict[str, float] = {}
    for doc_id in doc_ids:
        v = vec_docs.get(doc_id, 0.0)
        b = bm_docs.get(doc_id, 0.0)
        s = st_docs.get(doc_id, 0.0)
        score = (doc_semantic_weight * v) + (doc_keyword_weight * b) + (doc_structured_weight * s)
        doc_scores[doc_id] = score

    event_rows_all = fetch_events(list(event_ids)) if event_ids else []
    event_lookup_all = {row["id"]: row for row in event_rows_all}
    doc_lookup_all = (
        fetch_document_summaries(list(doc_ids)) if (doc_ids and use_temporal_ordering) else {}
    )

    combined: list[tuple[str, str, float]] = []

    if use_temporal_ordering:
        with_dates: list[tuple[str, str, float, datetime]] = []
        without_dates: list[tuple[str, str, float]] = []

        for event_id in event_scores:
            row = event_lookup_all.get(event_id)
            event_date = _to_temporal_sort_value(
                (row.get("start_date") or row.get("end_date")) if row else None
            )
            item = (event_id, "event", event_scores.get(event_id, 0.0))
            if event_date:
                with_dates.append((event_id, "event", event_scores.get(event_id, 0.0), event_date))
            else:
                without_dates.append(item)

        for doc_id in doc_scores:
            doc = doc_lookup_all.get(doc_id)
            doc_date = _to_temporal_sort_value(
                (doc.get("document_date") or doc.get("created_at")) if doc else None
            )
            item = (doc_id, "document", doc_scores.get(doc_id, 0.0))
            if doc_date:
                with_dates.append((doc_id, "document", doc_scores.get(doc_id, 0.0), doc_date))
            else:
                without_dates.append(item)

        with_dates.sort(
            key=lambda item: (item[3], item[0], item[1]),
            reverse=(temporal_ordering == "newest"),
        )
        without_dates.sort(key=lambda item: (item[0], item[1]))
        combined = [
            (item_id, kind, score) for item_id, kind, score, _ in with_dates
        ] + without_dates
    else:
        combined.extend((event_id, "event", event_scores[event_id]) for event_id in event_scores)
        combined.extend((doc_id, "document", doc_scores[doc_id]) for doc_id in doc_scores)
        combined.sort(key=lambda item: (-item[2], item[0], item[1]))

    if not combined:
        return {"results": []}

    final_limit = max(1, int(limit))
    top_combined = combined[:final_limit]

    event_ids_top = [item_id for item_id, kind, _ in top_combined if kind == "event"]
    doc_ids_top = [item_id for item_id, kind, _ in top_combined if kind == "document"]

    event_lookup = (
        {
            event_id: event_lookup_all[event_id]
            for event_id in event_ids_top
            if event_id in event_lookup_all
        }
        if event_lookup_all
        else {}
    )
    if event_ids_top and not event_lookup:
        event_rows = fetch_events(event_ids_top)
        event_lookup = {row["id"]: row for row in event_rows}

    if doc_ids_top:
        if doc_lookup_all:
            doc_lookup = {
                doc_id: doc_lookup_all[doc_id] for doc_id in doc_ids_top if doc_id in doc_lookup_all
            }
            missing_doc_ids = [doc_id for doc_id in doc_ids_top if doc_id not in doc_lookup]
            if missing_doc_ids:
                doc_lookup.update(fetch_document_summaries(missing_doc_ids))
        else:
            doc_lookup = fetch_document_summaries(doc_ids_top)
    else:
        doc_lookup = {}

    results: list[dict[str, Any]] = []
    for item_id, kind, _ in top_combined:
        if kind == "event":
            row = event_lookup.get(item_id)
            if not row:
                continue
            vector_score = float(vec_events.get(item_id, 0.0))
            keyword_score = float(bm_events.get(item_id, 0.0))
            structured_score = float(st_events.get(item_id, 0.0))
            match_sources = []
            if vector_score > 0:
                match_sources.append("semantic")
            if keyword_score > 0:
                match_sources.append("keyword")
            if structured_score > 0:
                match_sources.append("structured")
            results.append(
                {
                    "id": row["id"],
                    "kind": "event",
                    "start_date": row["start_date"].isoformat() if row.get("start_date") else None,
                    "end_date": row["end_date"].isoformat() if row.get("end_date") else None,
                    "title": row.get("title"),
                    "summary": row.get("summary"),
                    "score": float(event_scores.get(item_id, 0.0)),
                    "score_breakdown": {
                        "semantic": vector_score,
                        "keyword": keyword_score,
                        "structured": structured_score,
                    },
                    "match_sources": match_sources,
                    "place": (
                        {
                            "place_id": row["place_id"],
                            "name": row["place_name"],
                            "city": row["city"],
                            "country": row["country"],
                        }
                        if row.get("place_id")
                        else None
                    ),
                    "people": row["people"],
                    "tags": row["tags"],
                    "types": row.get("types", []),
                    "snippet": make_snippet(row.get("summary") or row.get("title")),
                }
            )
        elif kind == "document":
            doc = doc_lookup.get(item_id)
            if not doc:
                continue
            vector_score = float(vec_docs.get(item_id, 0.0))
            keyword_score = float(bm_docs.get(item_id, 0.0))
            structured_score = float(st_docs.get(item_id, 0.0))
            match_sources = []
            if vector_score > 0:
                match_sources.append("semantic")
            if keyword_score > 0:
                match_sources.append("keyword")
            if structured_score > 0:
                match_sources.append("structured")
            results.append(
                {
                    "id": doc["document_id"],
                    "kind": "document",
                    "title": doc.get("title"),
                    "description": doc.get("description"),
                    "score": float(doc_scores.get(item_id, 0.0)),
                    "score_breakdown": {
                        "semantic": vector_score,
                        "keyword": keyword_score,
                        "structured": structured_score,
                    },
                    "match_sources": match_sources,
                    "tags": doc.get("tags", []),
                    "document_date": _isoformat(doc.get("document_date")),
                    "created_at": _isoformat(doc.get("created_at")),
                    "updated_at": _isoformat(doc.get("updated_at")),
                    "download_url": doc.get("download_url"),
                    "file_name": doc.get("file_name"),
                    "file_mime": doc.get("file_mime"),
                    "file_size": doc.get("file_size"),
                    "snippet": doc.get("snippet", ""),
                }
            )
    event_titles = [row.get("title") for row in results if row.get("kind") == "event"]
    document_titles = [row.get("title") for row in results if row.get("kind") == "document"]
    logger.info(
        "[retrieval] search_memories returning count=%s event_titles=%s document_titles=%s",
        len(results),
        event_titles,
        document_titles,
    )
    return {"results": results}


def vector_search(query: str, k: int = 50):
    logger.debug("[retrieval] vector_search memories (query=%r, k=%s)", query, k)
    cleaned_query = normalize_search_text(query)
    if not cleaned_query:
        return {}
    qvec = embed_text(cleaned_query)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, 1 - (what_embed <=> %s::vector) AS vscore
            FROM events
            ORDER BY what_embed <=> %s::vector
            LIMIT %s
            """,
            (qvec, qvec, k),
        )
        return {r["id"]: float(r["vscore"]) for r in cur.fetchall()}


def vector_search_contacts(query: str, k: int = 20) -> list[tuple[str, float]]:
    logger.debug("[retrieval] vector_search contacts (query=%r, k=%s)", query, k)
    cleaned_query = normalize_search_text(query)
    if not cleaned_query:
        return []
    qvec = embed_text(cleaned_query)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT contact_id, 1 - (comments_embed <=> %s::vector) AS vscore
            FROM contacts
            WHERE comments_embed IS NOT NULL
            ORDER BY comments_embed <=> %s::vector
            LIMIT %s
            """,
            (qvec, qvec, k),
        )
        return [(row["contact_id"], float(row["vscore"])) for row in cur.fetchall()]


def bm25_search(query: str, k: int = 50):
    cleaned_query = normalize_search_text(query)
    if not cleaned_query:
        return {}
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, ts_rank_cd(what_tsv, plainto_tsquery('english', unaccent(%s))) AS bscore
            FROM events
            WHERE what_tsv @@ plainto_tsquery('english', unaccent(%s))
            ORDER BY bscore DESC
            LIMIT %s
            """,
            (cleaned_query, cleaned_query, k),
        )
        return {r["id"]: float(r["bscore"]) for r in cur.fetchall()}


def bm25_search_documents(query: str, k: int = 50) -> dict[str, float]:
    cleaned_query = normalize_search_text(query)
    if not cleaned_query:
        return {}
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                document_id,
                (
                    0.55 * ts_rank_cd(content_tsv, plainto_tsquery('english', unaccent(%s)))
                    + 1.25 * ts_rank_cd(content_tsv, phraseto_tsquery('english', unaccent(%s)))
                    + CASE
                        WHEN position(
                            unaccent(%s) in unaccent(lower(coalesce(content, '') || ' ' || coalesce(description, '')))
                        ) > 0
                        THEN 2.0
                        ELSE 0.0
                    END
                ) AS bscore
            FROM documents
            WHERE content_tsv @@ plainto_tsquery('english', unaccent(%s))
               OR content_tsv @@ phraseto_tsquery('english', unaccent(%s))
               OR position(
                    unaccent(%s) in unaccent(lower(coalesce(content, '') || ' ' || coalesce(description, '')))
                  ) > 0
            ORDER BY bscore DESC
            LIMIT %s
            """,
            (
                cleaned_query,
                cleaned_query,
                cleaned_query,
                cleaned_query,
                cleaned_query,
                cleaned_query,
                k,
            ),
        )
        return {r["document_id"]: float(r["bscore"]) for r in cur.fetchall()}


def structured_candidates(
    timespan,
    people_ids: list[str],
    place_ids: list[str],
    tags: Sequence[str] | None = None,
    k: int = 200,
):
    if not timespan and not people_ids and not place_ids and not tags:
        return {}
    clauses = []
    params: list[Any] = []
    if timespan:
        start, end = timespan
        if start and end:
            clauses.append("start_date BETWEEN %s AND %s")
            params += [start, end]
        elif start:
            clauses.append("start_date >= %s")
            params.append(start)
        elif end:
            clauses.append("start_date <= %s")
            params.append(end)
    if people_ids:
        clauses.append("people && %s")
        params.append(people_ids)
    if place_ids:
        clauses.append("place_id = ANY(%s)")
        params.append(place_ids)
    if tags:
        clauses.append(
            "EXISTS (SELECT 1 FROM unnest(COALESCE(tags, ARRAY[]::text[])) AS tag WHERE lower(tag) = ANY(%s))"
        )
        params.append([tag.lower() for tag in tags])
    where = " AND ".join(clauses) if clauses else "TRUE"
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT id, 1.0 AS sscore
            FROM events
            WHERE {where}
            ORDER BY start_date DESC
            LIMIT %s
            """,
            (*params, k),
        )
        return {r["id"]: float(r["sscore"]) for r in cur.fetchall()}


def structured_document_candidates(
    timespan,
    tags: Sequence[str] | None = None,
    k: int = 200,
) -> dict[str, float]:
    if not timespan and not tags:
        return {}
    start = end = None
    if timespan:
        start, end = timespan
    clauses = []
    params: list[Any] = []
    if start and end:
        clauses.append("COALESCE(document_date, created_at) BETWEEN %s AND %s")
        params += [start, end]
    elif start:
        clauses.append("COALESCE(document_date, created_at) >= %s")
        params.append(start)
    elif end:
        clauses.append("COALESCE(document_date, created_at) <= %s")
        params.append(end)
    if tags:
        clauses.append(
            "EXISTS (SELECT 1 FROM unnest(COALESCE(tags, ARRAY[]::text[])) AS tag WHERE lower(tag) = ANY(%s))"
        )
        params.append([tag.lower() for tag in tags])
    where = " AND ".join(clauses) if clauses else "TRUE"
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT document_id, 1.0 AS sscore
            FROM documents
            WHERE {where}
            ORDER BY COALESCE(document_date, created_at) DESC
            LIMIT %s
            """,
            (*params, k),
        )
        return {r["document_id"]: float(r["sscore"]) for r in cur.fetchall()}


def make_snippet(text: str | None, length: int = 160) -> str:
    if not text:
        return ""
    t = " ".join(text.split())
    return (t[:length] + "…") if len(t) > length else t


def fetch_document_summaries(document_ids: Sequence[str]) -> dict[str, dict[str, Any]]:
    if not document_ids:
        return {}
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

    summaries: dict[str, dict[str, Any]] = {}
    for row in rows:
        snippet_source = row.get("description") or row.get("content") or ""
        summaries[row["document_id"]] = {
            "document_id": row["document_id"],
            "title": row.get("title"),
            "tags": row.get("tags") or [],
            "description": row.get("description"),
            "document_date": row.get("document_date"),
            "file_name": row.get("file_name"),
            "file_mime": row.get("file_mime"),
            "file_size": row.get("file_size"),
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
            "snippet": make_snippet(snippet_source, length=200),
            "download_url": f"/documents/{row['document_id']}/download",
        }
    return summaries


def fetch_contact_summaries(contact_ids: Sequence[str]) -> dict[str, dict[str, Any]]:
    if not contact_ids:
        return {}
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT contact_id, display_name, aliases, tags, comments
            FROM contacts
            WHERE contact_id = ANY(%s)
            """,
            (list(contact_ids),),
        )
        rows = cur.fetchall()

    summaries: dict[str, dict[str, Any]] = {}
    for row in rows:
        snippet_source = row.get("comments") or ""
        summaries[row["contact_id"]] = {
            "contact_id": row["contact_id"],
            "display_name": row.get("display_name"),
            "aliases": row.get("aliases") or [],
            "tags": row.get("tags") or [],
            "comments": row.get("comments") or "",
            "snippet": make_snippet(snippet_source, length=200),
        }
    return summaries


def _isoformat(value: Any | None) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return None


def _to_temporal_sort_value(value: Any | None) -> datetime | None:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):
        dt = datetime.combine(value, time.min)
    else:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


# --------------------------- Pipeline ---------------------------
def run_pipeline(question: str, search_limit: int = 3) -> dict[str, Any]:
    resolution = resolve_query(question)
    timespan = resolution.get("timespan") or [None, None]
    search = search_memories(
        query=question,
        people=resolution.get("people"),
        place_ids=resolution.get("places"),
        time_start=timespan[0],
        time_end=timespan[1],
        limit=search_limit,
    )
    results = search.get("results", []) if isinstance(search, dict) else []
    event_ids = [
        row.get("id")
        for row in results
        if isinstance(row, dict) and row.get("id") and row.get("kind", "event") == "event"
    ]
    detailed = events_service.get_events(event_ids)
    document_results = [
        row for row in results if isinstance(row, dict) and row.get("kind") == "document"
    ]
    return {
        "question": question,
        "resolution": resolution,
        "search_results": results,
        "events_results": detailed,
        "document_results": document_results,
    }
