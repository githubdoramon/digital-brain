from __future__ import annotations

import re
from typing import Any

from rapidfuzz import fuzz

from db import get_conn
from geo_utils import haversine_meters
from search_normalization import normalize_search_text

__all__ = [
    "add_place_alias",
    "find_best_place_match",
    "get_place",
    "ingest_place",
    "list_places",
    "list_contact_places",
    "resolve_contact_place",
    "search_places",
    "unlink_contact_place",
    "upsert_contact_place",
]

_PLACE_SYNONYMS = {
    "house": "home",
    "apt": "apartment",
    "apto": "apartment",
    "office": "work",
    "workplace": "work",
}

_PLACE_STOPWORDS = {"my", "the", "a", "an", "our", "at", "in"}


def get_place(place_id: str) -> dict[str, Any] | None:
    if not place_id:
        return None
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT place_id, name, aliases, address, city, country, lat, lon, geohash
            FROM places
            WHERE place_id = %s
            """,
            (place_id,),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def ingest_place(place: Any) -> None:
    aliases = _normalize_aliases(getattr(place, "aliases", None))
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO places (place_id, name, aliases, address, city, country, lat, lon, geohash)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (place_id) DO UPDATE
              SET name=EXCLUDED.name,
                  aliases=EXCLUDED.aliases,
                  address=EXCLUDED.address,
                  city=EXCLUDED.city,
                  country=EXCLUDED.country,
                  lat=EXCLUDED.lat,
                  lon=EXCLUDED.lon,
                  geohash=EXCLUDED.geohash
            """,
            (
                place.place_id,
                place.name,
                aliases,
                getattr(place, "address", None),
                place.city,
                place.country,
                place.lat,
                place.lon,
                place.geohash,
            ),
        )
        conn.commit()


def find_best_place_match(
    query: str,
    *,
    client_location: dict[str, Any] | None = None,
    fuzzy_threshold: int = 80,
) -> dict[str, Any] | None:
    normalized_query = _canonical_place_text(query)
    if not normalized_query:
        return None

    ranked = search_places(
        query,
        client_location=client_location,
        fuzzy_threshold=fuzzy_threshold,
        limit=1,
    )
    if not ranked:
        return None
    return ranked[0]


def search_places(
    query: str,
    *,
    client_location: dict[str, Any] | None = None,
    fuzzy_threshold: int = 80,
    limit: int = 5,
) -> list[dict[str, Any]]:
    normalized_query = _canonical_place_text(query)
    if not normalized_query:
        return []

    candidates = _list_places()
    if not candidates:
        return []

    query_lat = (
        _safe_float(client_location.get("lat")) if isinstance(client_location, dict) else None
    )
    query_lon = (
        _safe_float(client_location.get("lon")) if isinstance(client_location, dict) else None
    )

    scored_results: list[dict[str, Any]] = []
    threshold_value = float(fuzzy_threshold)
    for place in candidates:
        name = str(place.get("name") or "").strip()
        aliases = [
            str(alias or "").strip()
            for alias in (place.get("aliases") or [])
            if str(alias or "").strip()
        ]
        candidate_texts = [name, *aliases]
        candidate_texts = [text for text in candidate_texts if text]
        if not candidate_texts:
            continue

        score, matched_via, matched_text = _score_place_match(normalized_query, candidate_texts)
        if score <= 0:
            continue

        proximity_bonus = _proximity_bonus(
            query_lat=query_lat,
            query_lon=query_lon,
            place_lat=_safe_float(place.get("lat")),
            place_lon=_safe_float(place.get("lon")),
        )
        total_score = min(100.0, score + proximity_bonus)
        if total_score < threshold_value:
            continue

        scored_results.append(
            {
                "place_id": place.get("place_id"),
                "name": name,
                "city": place.get("city"),
                "country": place.get("country"),
                "lat": place.get("lat"),
                "lon": place.get("lon"),
                "match_score": round(total_score, 1),
                "match_confidence": _confidence_from_score(total_score),
                "matched_via": matched_via,
                "matched_text": matched_text,
            }
        )

    scored_results.sort(
        key=lambda row: (
            -float(row.get("match_score") or 0.0),
            str(row.get("name") or ""),
        )
    )
    return scored_results[: max(1, int(limit))]


def add_place_alias(place_id: str, alias: str) -> bool:
    """Persist an alias if it does not already exist (accent-insensitive)."""
    clean_alias = str(alias or "").strip()
    if not clean_alias:
        return False

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT name, aliases
            FROM places
            WHERE place_id = %s
            """,
            (place_id,),
        )
        row = cur.fetchone()
        if not row:
            return False

        record = dict(row)
        name = str(record.get("name") or "").strip()
        existing_aliases = [
            str(item or "").strip()
            for item in (record.get("aliases") or [])
            if str(item or "").strip()
        ]

        candidate_norm = _canonical_place_text(clean_alias)
        if not candidate_norm:
            return False
        if candidate_norm == _canonical_place_text(name):
            return False

        existing_norms = {
            _canonical_place_text(name),
            *[_canonical_place_text(item) for item in existing_aliases],
        }
        if candidate_norm in existing_norms:
            return False

        updated_aliases = [*existing_aliases, clean_alias]
        cur.execute(
            """
            UPDATE places
            SET aliases = %s
            WHERE place_id = %s
            """,
            (updated_aliases, place_id),
        )
        conn.commit()
        return True


def upsert_contact_place(
    *,
    contact_id: str,
    place_id: str,
    role: str | None = None,
    source: str | None = None,
    confidence: str | None = None,
) -> None:
    clean_contact_id = str(contact_id or "").strip()
    clean_place_id = str(place_id or "").strip()
    if not clean_contact_id or not clean_place_id:
        return

    clean_role = str(role or "").strip() or None
    clean_source = str(source or "").strip() or None
    clean_confidence = str(confidence or "").strip() or None

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO contact_places (contact_id, place_id, role, source, confidence)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (contact_id, place_id) DO UPDATE
            SET role = COALESCE(EXCLUDED.role, contact_places.role),
                source = COALESCE(EXCLUDED.source, contact_places.source),
                confidence = COALESCE(EXCLUDED.confidence, contact_places.confidence),
                updated_at = NOW()
            """,
            (clean_contact_id, clean_place_id, clean_role, clean_source, clean_confidence),
        )
        conn.commit()


def unlink_contact_place(*, contact_id: str, place_id: str) -> bool:
    clean_contact_id = str(contact_id or "").strip()
    clean_place_id = str(place_id or "").strip()
    if not clean_contact_id or not clean_place_id:
        return False

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            DELETE FROM contact_places
            WHERE contact_id = %s
              AND place_id = %s
            """,
            (clean_contact_id, clean_place_id),
        )
        deleted = cur.rowcount > 0
        conn.commit()
        return deleted


def list_contact_places(contact_id: str, role_hint: str | None = None) -> list[dict[str, Any]]:
    clean_contact_id = str(contact_id or "").strip()
    if not clean_contact_id:
        return []

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                cp.contact_id,
                cp.place_id,
                cp.role,
                cp.source,
                cp.confidence,
                p.name,
                p.aliases,
                p.address,
                p.city,
                p.country,
                p.lat,
                p.lon
            FROM contact_places cp
            JOIN places p ON p.place_id = cp.place_id
            WHERE cp.contact_id = %s
            """,
            (clean_contact_id,),
        )
        rows = [dict(row) for row in cur.fetchall()]

    if not role_hint:
        return rows

    hint = _canonical_place_text(role_hint)
    if not hint:
        return rows

    scored: list[tuple[float, dict[str, Any]]] = []
    for row in rows:
        score = _role_similarity_score(hint, str(row.get("role") or ""))
        scored.append((score, row))

    scored.sort(key=lambda item: item[0], reverse=True)
    return [row for _score, row in scored]


def resolve_contact_place(
    *,
    contact_id: str,
    role_hint: str | None = None,
    where_text: str | None = None,
) -> dict[str, Any] | None:
    candidates = list_contact_places(contact_id, role_hint=role_hint)
    if not candidates:
        return None

    best: dict[str, Any] | None = None
    best_score = -1.0
    normalized_where = _canonical_place_text(where_text or "")
    normalized_role_hint = _canonical_place_text(role_hint or "")
    for candidate in candidates:
        score = 0.0
        candidate_role = str(candidate.get("role") or "")
        if normalized_role_hint:
            score += _role_similarity_score(normalized_role_hint, candidate_role)

        candidate_texts = [str(candidate.get("name") or "")] + [
            str(alias or "") for alias in (candidate.get("aliases") or [])
        ]
        candidate_texts = [text for text in candidate_texts if text]
        if normalized_where and candidate_texts:
            text_score, _via, _text = _score_place_match(normalized_where, candidate_texts)
            score += text_score * 0.35

        if score > best_score:
            best_score = score
            best = candidate

    if best is None:
        return None

    return {
        "place_id": best.get("place_id"),
        "name": best.get("name"),
        "city": best.get("city"),
        "country": best.get("country"),
        "lat": best.get("lat"),
        "lon": best.get("lon"),
        "confidence": best.get("confidence") or "medium",
        "matched_via": "contact_place_relation",
        "role": best.get("role"),
    }


def _list_places() -> list[dict[str, Any]]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT place_id, name, aliases, address, city, country, lat, lon
            FROM places
            """
        )
        rows = cur.fetchall()
    return [dict(row) for row in rows]


def list_places(query: str | None = None, *, limit: int = 200) -> list[dict[str, Any]]:
    clean_limit = max(1, min(int(limit), 500))
    clean_query = str(query or "").strip()

    with get_conn() as conn, conn.cursor() as cur:
        if clean_query:
            like = f"%{clean_query}%"
            cur.execute(
                """
                SELECT place_id, name, aliases, address, city, country, lat, lon, geohash
                FROM places
                WHERE (
                    unaccent(coalesce(name, '')) ILIKE unaccent(%s)
                    OR unaccent(coalesce(address, '')) ILIKE unaccent(%s)
                    OR EXISTS (
                        SELECT 1
                        FROM unnest(coalesce(aliases, ARRAY[]::TEXT[])) AS alias
                        WHERE unaccent(alias) ILIKE unaccent(%s)
                    )
                )
                ORDER BY name NULLS LAST, place_id
                LIMIT %s
                """,
                (like, like, like, clean_limit),
            )
        else:
            cur.execute(
                """
                SELECT place_id, name, aliases, address, city, country, lat, lon, geohash
                FROM places
                ORDER BY name NULLS LAST, place_id
                LIMIT %s
                """,
                (clean_limit,),
            )
        return [dict(row) for row in cur.fetchall()]


def _score_place_match(
    normalized_query: str,
    candidate_texts: list[str],
) -> tuple[float, str, str]:
    best_score = 0.0
    best_via = "none"
    best_text = ""
    for text in candidate_texts:
        candidate = _canonical_place_text(text)
        if not candidate:
            continue

        if candidate == normalized_query:
            if text == candidate_texts[0]:
                return 100.0, "name_exact", text
            return 98.0, "alias_exact", text

        if normalized_query in candidate or candidate in normalized_query:
            if 92.0 > best_score:
                best_score = 92.0
                best_via = "substring"
                best_text = text

        fuzzy_score = float(fuzz.token_sort_ratio(normalized_query, candidate))
        if fuzzy_score > best_score:
            best_score = fuzzy_score
            best_via = "fuzzy"
            best_text = text

    return best_score, best_via, best_text


def _canonical_place_text(text: str) -> str:
    normalized = normalize_search_text(text)
    normalized = re.sub(r"[^a-z0-9\s]", " ", normalized)
    tokens: list[str] = []
    for token in normalized.split():
        if token in _PLACE_STOPWORDS:
            continue
        tokens.append(_PLACE_SYNONYMS.get(token, token))
    return " ".join(tokens)


def _normalize_aliases(aliases: list[str] | None) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for alias in aliases or []:
        text = str(alias or "").strip()
        normalized = _canonical_place_text(text)
        if not text or not normalized or normalized in seen:
            continue
        seen.add(normalized)
        output.append(text)
    return output


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _proximity_bonus(
    *,
    query_lat: float | None,
    query_lon: float | None,
    place_lat: float | None,
    place_lon: float | None,
) -> float:
    if query_lat is None or query_lon is None or place_lat is None or place_lon is None:
        return 0.0
    distance_m = haversine_meters(query_lat, query_lon, place_lat, place_lon)
    if distance_m <= 100:
        return 6.0
    if distance_m <= 300:
        return 4.0
    if distance_m <= 1000:
        return 2.0
    return 0.0


def _confidence_from_score(score: float) -> str:
    if score >= 92:
        return "high"
    if score >= 85:
        return "medium"
    return "low"


def _role_similarity_score(hint: str, role_value: str) -> float:
    normalized_role = _canonical_place_text(role_value)
    if not hint or not normalized_role:
        return 0.0
    if hint == normalized_role:
        return 100.0
    if hint in normalized_role or normalized_role in hint:
        return 88.0
    return float(fuzz.token_sort_ratio(hint, normalized_role))
