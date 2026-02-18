"""Contact group persistence and lookup helpers."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import contacts
from db import get_conn
from embeddings import embed_text
from search_normalization import normalize_search_text

GROUP_EMBED_MAX_CHARS = 3000


def _row_to_dict(row: Any) -> dict[str, Any]:
    if isinstance(row, dict):
        return row
    try:
        return dict(row)
    except Exception:
        return {}


def _build_group_embedding_text(name: str, description: str | None, aliases: list[str]) -> str:
    parts: list[str] = []
    group_name = str(name or "").strip()
    if group_name:
        parts.append(group_name)

    group_description = str(description or "").strip()
    if group_description:
        parts.append(group_description)

    cleaned_aliases = [str(alias).strip() for alias in aliases if str(alias).strip()]
    if cleaned_aliases:
        parts.append("Aliases: " + ", ".join(cleaned_aliases))

    payload = "\n".join(parts).strip()
    if len(payload) > GROUP_EMBED_MAX_CHARS:
        return payload[:GROUP_EMBED_MAX_CHARS]
    return payload


def _embed_group(name: str, description: str | None, aliases: list[str]) -> list[float] | None:
    text = _build_group_embedding_text(name, description, aliases)
    if not text:
        return None
    try:
        return embed_text(text)
    except Exception:
        return None


def _list_group_aliases(group_id: str) -> list[str]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT alias
            FROM group_aliases
            WHERE group_id = %s
            ORDER BY alias ASC
            """,
            (group_id,),
        )
        rows = cur.fetchall() or []

    aliases: list[str] = []
    for row in rows:
        row_dict = _row_to_dict(row)
        alias = str(row_dict.get("alias") or "").strip()
        if alias:
            aliases.append(alias)
    return aliases


def _find_existing_group(
    owner_contact_id: str, name: str, aliases: list[str]
) -> dict[str, Any] | None:
    normalized_names = {
        normalize_search_text(name),
        *{normalize_search_text(alias) for alias in aliases},
    }
    normalized_names.discard("")
    if not normalized_names:
        return None

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT cg.group_id, cg.name, cg.description, cg.status, cg.confirmed, cg.source
            FROM contact_groups cg
            WHERE cg.owner_contact_id = %s
              AND (
                unaccent(LOWER(cg.name)) = ANY(%s)
                OR EXISTS (
                  SELECT 1
                  FROM group_aliases ga
                  WHERE ga.group_id = cg.group_id
                    AND unaccent(LOWER(ga.alias)) = ANY(%s)
                )
              )
            ORDER BY cg.updated_at DESC
            LIMIT 1
            """,
            (owner_contact_id, list(normalized_names), list(normalized_names)),
        )
        row = cur.fetchone()
    if not row:
        return None
    return _row_to_dict(row)


def _serialize_group_for_owner(owner_contact_id: str, group_row: dict[str, Any]) -> dict[str, Any]:
    group_id = str(group_row.get("group_id") or "").strip()
    if not group_id:
        return {}

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT c.contact_id, c.display_name
            FROM contact_group_members gm
            JOIN contacts c ON c.contact_id = gm.contact_id
            JOIN contact_groups cg ON cg.group_id = gm.group_id
            WHERE gm.group_id = %s
              AND cg.owner_contact_id = %s
            ORDER BY c.display_name ASC
            """,
            (group_id, owner_contact_id),
        )
        member_rows = cur.fetchall() or []

    members: list[dict[str, str]] = []
    for member_row in member_rows:
        member = _row_to_dict(member_row)
        contact_id = str(member.get("contact_id") or "").strip()
        display_name = str(member.get("display_name") or "").strip()
        if not contact_id:
            continue
        members.append(
            {
                "contact_id": contact_id,
                "display_name": display_name,
            }
        )

    return {
        "group_id": group_id,
        "owner_contact_id": owner_contact_id,
        "name": str(group_row.get("name") or "").strip(),
        "description": str(group_row.get("description") or "").strip() or None,
        "status": str(group_row.get("status") or "active").strip() or "active",
        "source": str(group_row.get("source") or "manual").strip() or "manual",
        "confirmed": bool(group_row.get("confirmed")),
        "aliases": _list_group_aliases(group_id),
        "members": members,
        "member_count": len(members),
    }


def upsert_group_from_selector(
    *,
    user_email: str,
    name: str,
    member_contact_ids: list[str],
    aliases: list[str] | None = None,
    description: str | None = None,
    source: str = "deterministic",
    confirmed: bool = True,
    replace_members: bool = False,
    added_via: str = "selector",
    confidence: float = 0.9,
) -> dict[str, Any] | None:
    owner_contact_id = contacts.get_self_contact_id(user_email)
    if not owner_contact_id:
        return None

    group_name = str(name or "").strip()
    if not group_name:
        return None

    cleaned_aliases = []
    for alias in aliases or []:
        value = str(alias or "").strip()
        if value:
            cleaned_aliases.append(value)

    existing = _find_existing_group(owner_contact_id, group_name, cleaned_aliases)
    group_id = (
        str(existing.get("group_id") or "").strip() if existing else f"group:{uuid4().hex[:12]}"
    )
    embedding = _embed_group(group_name, description, cleaned_aliases)

    unique_member_ids: list[str] = []
    seen_ids: set[str] = set()
    for contact_id in member_contact_ids:
        candidate = str(contact_id or "").strip()
        if not candidate or candidate in seen_ids:
            continue
        seen_ids.add(candidate)
        unique_member_ids.append(candidate)

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO contact_groups (
                group_id,
                owner_contact_id,
                name,
                description,
                status,
                source,
                confirmed,
                group_embed
            )
            VALUES (%s, %s, %s, %s, 'active', %s, %s, %s)
            ON CONFLICT (group_id) DO UPDATE
              SET name = EXCLUDED.name,
                  description = EXCLUDED.description,
                  status = 'active',
                  source = EXCLUDED.source,
                  confirmed = EXCLUDED.confirmed,
                  group_embed = EXCLUDED.group_embed,
                  updated_at = NOW()
            """,
            (
                group_id,
                owner_contact_id,
                group_name,
                description,
                source,
                confirmed,
                embedding,
            ),
        )

        cur.execute("DELETE FROM group_aliases WHERE group_id = %s", (group_id,))
        for alias in cleaned_aliases:
            cur.execute(
                """
                INSERT INTO group_aliases (group_id, alias)
                VALUES (%s, %s)
                ON CONFLICT DO NOTHING
                """,
                (group_id, alias),
            )

        if replace_members:
            cur.execute("DELETE FROM contact_group_members WHERE group_id = %s", (group_id,))

        for contact_id in unique_member_ids:
            cur.execute(
                """
                INSERT INTO contact_group_members (
                    group_id,
                    contact_id,
                    added_via,
                    confidence
                )
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (group_id, contact_id) DO UPDATE
                  SET added_via = EXCLUDED.added_via,
                      confidence = EXCLUDED.confidence,
                      updated_at = NOW()
                """,
                (group_id, contact_id, added_via, confidence),
            )
        conn.commit()

    return {
        "group_id": group_id,
        "owner_contact_id": owner_contact_id,
        "name": group_name,
        "description": description,
        "aliases": cleaned_aliases,
        "member_count": len(unique_member_ids),
    }


def resolve_group_members(user_email: str, query: str, *, limit: int = 200) -> dict[str, Any]:
    owner_contact_id = contacts.get_self_contact_id(user_email)
    if not owner_contact_id:
        return {"found": False, "group": None, "contacts": []}

    normalized_query = normalize_search_text(query)
    if not normalized_query:
        return {"found": False, "group": None, "contacts": []}

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT cg.group_id, cg.name, cg.description, cg.source, cg.confirmed
            FROM contact_groups cg
            WHERE cg.owner_contact_id = %s
              AND cg.status = 'active'
              AND (
                unaccent(LOWER(cg.name)) = %s
                OR EXISTS (
                  SELECT 1
                  FROM group_aliases ga
                  WHERE ga.group_id = cg.group_id
                    AND unaccent(LOWER(ga.alias)) = %s
                )
              )
            ORDER BY cg.updated_at DESC
            LIMIT 1
            """,
            (owner_contact_id, normalized_query, normalized_query),
        )
        group_row_raw = cur.fetchone()
        group_row = _row_to_dict(group_row_raw)
        if not group_row:
            return {"found": False, "group": None, "contacts": []}

        cur.execute(
            """
            SELECT c.contact_id, c.display_name, c.aliases, c.emails, c.phones, c.tags, c.comments
            FROM contact_group_members gm
            JOIN contacts c ON c.contact_id = gm.contact_id
            WHERE gm.group_id = %s
            ORDER BY c.display_name ASC
            LIMIT %s
            """,
            (str(group_row.get("group_id") or "").strip(), limit),
        )
        rows = cur.fetchall() or []

    contacts_payload: list[dict[str, Any]] = []
    for row_raw in rows:
        row = _row_to_dict(row_raw)
        contacts_payload.append(
            {
                "contact_id": str(row.get("contact_id") or "").strip(),
                "display_name": row.get("display_name"),
                "aliases": row.get("aliases") or [],
                "emails": row.get("emails") or [],
                "phones": row.get("phones") or [],
                "tags": row.get("tags") or [],
                "comments": row.get("comments"),
            }
        )

    return {
        "found": bool(contacts_payload),
        "group": {
            "group_id": str(group_row.get("group_id") or "").strip(),
            "name": group_row.get("name"),
            "description": group_row.get("description"),
            "source": group_row.get("source"),
            "confirmed": bool(group_row.get("confirmed")),
            "aliases": _list_group_aliases(str(group_row.get("group_id") or "").strip()),
        },
        "contacts": contacts_payload,
    }


def list_contact_groups(user_email: str, *, include_archived: bool = False) -> list[dict[str, Any]]:
    owner_contact_id = contacts.get_self_contact_id(user_email)
    if not owner_contact_id:
        return []

    with get_conn() as conn, conn.cursor() as cur:
        if include_archived:
            cur.execute(
                """
                SELECT group_id, name, description, status, source, confirmed
                FROM contact_groups
                WHERE owner_contact_id = %s
                ORDER BY updated_at DESC
                """,
                (owner_contact_id,),
            )
        else:
            cur.execute(
                """
                SELECT group_id, name, description, status, source, confirmed
                FROM contact_groups
                WHERE owner_contact_id = %s
                  AND status = 'active'
                ORDER BY updated_at DESC
                """,
                (owner_contact_id,),
            )
        rows = cur.fetchall() or []

    groups: list[dict[str, Any]] = []
    for row in rows:
        serialized = _serialize_group_for_owner(owner_contact_id, _row_to_dict(row))
        if serialized:
            groups.append(serialized)
    return groups


def get_contact_group(user_email: str, group_id: str) -> dict[str, Any] | None:
    owner_contact_id = contacts.get_self_contact_id(user_email)
    if not owner_contact_id:
        return None

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT group_id, name, description, status, source, confirmed
            FROM contact_groups
            WHERE owner_contact_id = %s
              AND group_id = %s
            LIMIT 1
            """,
            (owner_contact_id, group_id),
        )
        row = cur.fetchone()

    if not row:
        return None
    serialized = _serialize_group_for_owner(owner_contact_id, _row_to_dict(row))
    return serialized or None


def archive_contact_group(user_email: str, group_id: str) -> bool:
    owner_contact_id = contacts.get_self_contact_id(user_email)
    if not owner_contact_id:
        return False

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE contact_groups
            SET status = 'archived', updated_at = NOW()
            WHERE owner_contact_id = %s
              AND group_id = %s
            """,
            (owner_contact_id, group_id),
        )
        updated = cur.rowcount > 0
        conn.commit()
    return updated


def create_contact_group(
    *,
    user_email: str,
    name: str,
    member_contact_ids: list[str],
    aliases: list[str] | None = None,
    description: str | None = None,
) -> dict[str, Any] | None:
    return upsert_group_from_selector(
        user_email=user_email,
        name=name,
        member_contact_ids=member_contact_ids,
        aliases=aliases,
        description=description,
        source="manual",
        confirmed=True,
        replace_members=True,
        added_via="manual",
        confidence=1.0,
    )
