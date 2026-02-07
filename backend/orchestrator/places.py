from __future__ import annotations

from db import get_conn

__all__ = ["get_place", "ingest_place"]


def get_place(place_id: str) -> dict | None:
    if not place_id:
        return None
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT place_id, name, city, country, lat, lon, geohash
            FROM places
            WHERE place_id = %s
            """,
            (place_id,),
        )
        row = cur.fetchone()
        return dict(row) if row else None


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
            (
                place.place_id,
                place.name,
                place.city,
                place.country,
                place.lat,
                place.lon,
                place.geohash,
            ),
        )
        conn.commit()
