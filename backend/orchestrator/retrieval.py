from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

from dateparser.search import search_dates
from rapidfuzz import process, fuzz

from db import fetch_events, get_conn
from embeddings import embed_text


# --------------------------- Ingestion helpers ---------------------------
def ingest_contact(contact) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO contacts (
              contact_id,
              display_name,
              aliases,
              birthday,
              emails,
              phones,
              links,
              tags,
              relationship
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (contact_id) DO UPDATE
              SET display_name = EXCLUDED.display_name,
                  aliases = EXCLUDED.aliases,
                  birthday = EXCLUDED.birthday,
                  emails = EXCLUDED.emails,
                  phones = EXCLUDED.phones,
                  links = EXCLUDED.links,
                  tags = EXCLUDED.tags,
                  relationship = EXCLUDED.relationship
            """,
            (
                contact.contact_id,
                contact.display_name,
                contact.aliases or [],
                contact.birthday,
                contact.emails or [],
                contact.phones or [],
                contact.links or [],
                contact.tags or [],
                contact.relationship,
            ),
        )
        conn.commit()


def ingest_place(place) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO places (place_id, name, city, country, lat, lon, geohash)
            VALUES (%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (place_id) DO UPDATE
              SET name=EXCLUDED.name, city=EXCLUDED.city, country=EXCLUDED.country,
                  lat=EXCLUDED.lat, lon=EXCLUDED.lon, geohash=EXCLUDED.geohash
            """,
            (place.place_id, place.name, place.city, place.country, place.lat, place.lon, place.geohash),
        )
        conn.commit()


__all__ = ["normalize_event_types"]


EVENT_TYPE_CHOICES = {
    "generic",
    "meeting",
    "communication",
    "task",
    "creation",
    "consumption",
    "travel",
    "personal",
    "system",
    "financial",
    "observation",
    "interaction",
    "education",
    "celebration",
    "purchase",
    "health",
}


def normalize_event_types(types: Optional[Sequence[str]]) -> List[str]:
    """Convert the provided event types into canonical, unique values."""
    if not types:
        return ["generic"]
    normalized: List[str] = []
    for value in types:
        if not isinstance(value, str):
            continue
        cleaned = value.strip().lower()
        if cleaned in EVENT_TYPE_CHOICES and cleaned not in normalized:
            normalized.append(cleaned)
    return normalized or ["generic"]


def ingest_event(event) -> None:
    emb = embed_text(event.what_text or "")
    types = normalize_event_types(event.types)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO events (id, ts, place_id, people, tags, types, what_text, raw, what_embed)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (id) DO UPDATE
              SET ts=EXCLUDED.ts,
                  place_id=EXCLUDED.place_id,
                  people=EXCLUDED.people,
                  tags=EXCLUDED.tags,
                  types=EXCLUDED.types,
                  what_text=EXCLUDED.what_text,
                  raw=EXCLUDED.raw,
                  what_embed=EXCLUDED.what_embed
            """,
            (
                event.id,
                event.ts,
                event.place_id,
                event.people or [],
                event.tags or [],
                types,
                event.what_text or "",
                json.dumps(event.raw or {}),
                emb,
            ),
        )
        conn.commit()


# --------------------------- Resolution helpers ---------------------------
def resolve_query(text: str, need_contacts: bool = True, need_places: bool = True) -> Dict[str, Any]:
    q = (text or "").strip()
    people = resolve_entities(q, "contacts", "contact_id", "display_name", "aliases") if need_contacts else []
    if need_contacts:
        rel_filters = infer_relationship_filters(q)
        if rel_filters:
            rel_ids = fetch_contacts_by_relationship(rel_filters)
            people = list({*people, *rel_ids})
    places = resolve_entities(q, "places", "place_id", "name") if need_places else []
    span = parse_timespan_text(q)
    return {
        "people": people,
        "places": places,
        "timespan": [span[0].isoformat(), span[1].isoformat()] if span else None,
    }


def parse_timespan_text(q: str) -> Optional[Tuple[datetime, datetime]]:
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
    alias_col: Optional[str] = None,
    limit: int = 3,
) -> List[str]:
    with get_conn() as conn, conn.cursor() as cur:
        if alias_col:
            cur.execute(f"SELECT {key_col} AS id, {label_col} AS label, {alias_col} AS aliases FROM {table}")
        else:
            cur.execute(f"SELECT {key_col} AS id, {label_col} AS label FROM {table}")
        rows = cur.fetchall()
    choices: List[Tuple[str, str]] = []
    for r in rows:
        choices.append((r["id"], r["label"]))
        if alias_col and r.get("aliases"):
            for a in r["aliases"]:
                choices.append((r["id"], a))
    if not choices:
        return []
    labels = [c[1] for c in choices]
    matches = process.extract(q, labels, scorer=fuzz.WRatio, limit=limit)
    out_ids = {choices[idx][0] for label, score, idx in matches if score >= 85}
    return list(out_ids)


FAMILY_RELATIONSHIPS = {
    "Mother",
    "Father",
    "Brother",
    "Sister",
    "Daughter",
    "Son",
    "Wife",
    "Husband",
}

RELATIONSHIP_KEYWORDS: Dict[str, List[str]] = {
    "Mother": ["mother", "mom", "mum", "mommy"],
    "Father": ["father", "dad", "daddy"],
    "Brother": ["brother", "bro"],
    "Sister": ["sister", "sis"],
    "Daughter": ["daughter"],
    "Son": ["son"],
    "Wife": ["wife", "spouse"],
    "Husband": ["husband", "spouse"],
    "Friend": ["friend"],
    "Coworker": ["coworker", "colleague", "workmate"],
}


def infer_relationship_filters(text: str) -> List[str]:
    if not text:
        return []
    lower = text.lower()
    matches: List[str] = []
    if "family" in lower:
        matches.extend(FAMILY_RELATIONSHIPS)
    for relationship, keywords in RELATIONSHIP_KEYWORDS.items():
        for kw in keywords:
            if kw in lower:
                matches.append(relationship)
                break
    return list(dict.fromkeys(matches))


def fetch_contacts_by_relationship(relationships: Sequence[str]) -> List[str]:
    if not relationships:
        return []
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT contact_id
            FROM contacts
            WHERE relationship = ANY(%s)
            """,
            (list(relationships),),
        )
        rows = cur.fetchall()
    return [row["contact_id"] for row in rows]


# --------------------------- Search helpers ---------------------------
def search_memories(
    query: str,
    people: Optional[Sequence[str]] = None,
    place_ids: Optional[Sequence[str]] = None,
    time_start: Optional[str] = None,
    time_end: Optional[str] = None,
    limit: int = 5,
) -> Dict[str, Any]:
    span = None
    if time_start and time_end:
        try:
            span = (datetime.fromisoformat(time_start), datetime.fromisoformat(time_end))
        except Exception:
            span = None

    vec = vector_search(query, 50)
    bm = bm25_search(query, 50)
    st = structured_candidates(span, list(people or []), list(place_ids or []), 200)

    cand_ids = set(vec) | set(bm) | set(st)
    scored: List[Tuple[str, float]] = []
    for i in cand_ids:
        v = vec.get(i, 0.0)
        b = bm.get(i, 0.0)
        s = st.get(i, 0.0)
        bonus = 0.05 if s > 0 else 0.0
        scored.append((i, 0.6 * v + 0.3 * b + 0.1 * s + bonus))
    scored.sort(key=lambda x: x[1], reverse=True)
    top_ids = [i for i, _ in scored[:limit]]

    rows = fetch_events(top_ids)
    results = [
        {
            "id": r["id"],
            "ts": r["ts"].isoformat(),
            "place": {
                "place_id": r["place_id"],
                "name": r["place_name"],
                "city": r["city"],
                "country": r["country"],
            },
            "people": r["people"],
            "tags": r["tags"],
            "types": r.get("types", []),
            "snippet": make_snippet(r["what_text"]),
        }
        for r in rows
    ]

    return {"results": results}


def vector_search(query: str, k: int = 50):
    qvec = embed_text(query)
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


def bm25_search(query: str, k: int = 50):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, ts_rank_cd(what_tsv, plainto_tsquery('english', %s)) AS bscore
            FROM events
            WHERE what_tsv @@ plainto_tsquery('english', %s)
            ORDER BY bscore DESC
            LIMIT %s
            """,
            (query, query, k),
        )
        return {r["id"]: float(r["bscore"]) for r in cur.fetchall()}


def structured_candidates(timespan, people_ids: List[str], place_ids: List[str], k: int = 200):
    clauses = []
    params: List[Any] = []
    if timespan:
        clauses.append("ts BETWEEN %s AND %s")
        params += [timespan[0], timespan[1]]
    if people_ids:
        clauses.append("people && %s")
        params.append(people_ids)
    if place_ids:
        clauses.append("place_id = ANY(%s)")
        params.append(place_ids)
    where = " AND ".join(clauses) if clauses else "TRUE"
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT id, 1.0 AS sscore
            FROM events
            WHERE {where}
            ORDER BY ts DESC
            LIMIT %s
            """,
            (*params, k),
        )
        return {r["id"]: float(r["sscore"]) for r in cur.fetchall()}


def make_snippet(text: Optional[str], length: int = 160) -> str:
    if not text:
        return ""
    t = " ".join(text.split())
    return (t[:length] + "…") if len(t) > length else t


# --------------------------- Fetch helpers ---------------------------
def get_events(ids: List[str]) -> List[Dict[str, Any]]:
    rows = fetch_events(ids)
    return [
        {
            "id": r["id"],
            "ts": r["ts"].isoformat(),
            "people": r["people"],
            "tags": r["tags"],
            "types": r.get("types", []),
            "what_text": r["what_text"],
            "place": {
                "place_id": r["place_id"],
                "name": r["place_name"],
                "city": r["city"],
                "country": r["country"],
                "lat": r["lat"],
                "lon": r["lon"],
            },
        }
        for r in rows
    ]


# --------------------------- Pipeline ---------------------------
def run_pipeline(question: str, search_limit: int = 3) -> Dict[str, Any]:
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
    detailed = get_events([row["id"] for row in search.get("results", [])])
    return {
        "question": question,
        "resolution": resolution,
        "search_results": search.get("results", []),
        "detailed_events": detailed,
    }
