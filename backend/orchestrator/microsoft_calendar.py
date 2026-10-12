from __future__ import annotations

import html
import os
from datetime import datetime
from html.parser import HTMLParser
from typing import Any
from urllib.parse import quote

import requests
from cryptography.fernet import Fernet, InvalidToken

from db import get_conn

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
GRAPH_TIMEOUT_SECONDS = 20


class MicrosoftCalendarError(RuntimeError):
    pass


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.parts.append(data.strip())


def _plain_text(value: Any) -> str:
    raw = str(value or "")
    parser = _TextExtractor()
    try:
        parser.feed(raw)
        return " ".join(parser.parts).strip() or html.unescape(raw).strip()
    except Exception:
        return html.unescape(raw).strip()


def _fernet() -> Fernet:
    key = os.getenv("MICROSOFT_CALENDAR_TOKEN_ENCRYPTION_KEY", "").strip()
    if not key:
        raise MicrosoftCalendarError("Microsoft Calendar token encryption is not configured")
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise MicrosoftCalendarError("Microsoft Calendar token encryption key is invalid") from exc


def _oauth_config() -> tuple[str, str, str]:
    client_id = os.getenv("MICROSOFT_CALENDAR_CLIENT_ID", "").strip()
    client_secret = os.getenv("MICROSOFT_CALENDAR_CLIENT_SECRET", "").strip()
    tenant = os.getenv("MICROSOFT_CALENDAR_TENANT_ID", "organizations").strip() or "organizations"
    if not client_id or not client_secret:
        raise MicrosoftCalendarError("Microsoft Calendar OAuth client is not configured")
    return client_id, client_secret, tenant


def _token_url(tenant: str) -> str:
    return f"https://login.microsoftonline.com/{quote(tenant, safe='')}/oauth2/v2.0/token"


def store_connection(owner_email: str, connected_email: str, refresh_token: str) -> dict[str, Any]:
    owner = str(owner_email or "").strip().lower()
    connected = str(connected_email or "").strip().lower()
    token = str(refresh_token or "").strip()
    if not owner or not connected or not token:
        raise ValueError("owner email, connected email, and refresh token are required")
    encrypted_token = _fernet().encrypt(token.encode("utf-8")).decode("ascii")
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO microsoft_calendar_connections
                (user_email, connected_email, refresh_token_encrypted)
            VALUES (%s, %s, %s)
            ON CONFLICT (user_email) DO UPDATE
              SET connected_email = EXCLUDED.connected_email,
                  refresh_token_encrypted = EXCLUDED.refresh_token_encrypted,
                  updated_at = NOW(), last_error = NULL
            """,
            (owner, connected, encrypted_token),
        )
        conn.commit()
    return get_connection_status(owner)


def get_connection_status(owner_email: str) -> dict[str, Any]:
    owner = str(owner_email or "").strip().lower()
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT user_email, connected_email, selected_calendar_ids, connected_at,
                   updated_at, last_sync_at, last_reconciliation_at, last_error
            FROM microsoft_calendar_connections WHERE user_email = %s
            """,
            (owner,),
        )
        row = cur.fetchone()
    if not row:
        return {"connected": False, "selected_calendar_ids": []}
    return {
        "connected": True,
        "user_email": row["user_email"],
        "connected_email": row["connected_email"],
        "selected_calendar_ids": row.get("selected_calendar_ids") or [],
        "connected_at": _iso(row.get("connected_at")),
        "updated_at": _iso(row.get("updated_at")),
        "last_sync_at": _iso(row.get("last_sync_at")),
        "last_reconciliation_at": _iso(row.get("last_reconciliation_at")),
        "last_error": row.get("last_error"),
    }


def get_connection(owner_email: str) -> dict[str, Any] | None:
    owner = str(owner_email or "").strip().lower()
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT user_email, connected_email, refresh_token_encrypted, selected_calendar_ids "
            "FROM microsoft_calendar_connections WHERE user_email = %s",
            (owner,),
        )
        row = cur.fetchone()
    if not row:
        return None
    try:
        token = _fernet().decrypt(str(row["refresh_token_encrypted"]).encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeEncodeError) as exc:
        raise MicrosoftCalendarError("Stored Microsoft Calendar connection cannot be decrypted") from exc
    return {
        "user_email": row["user_email"],
        "connected_email": row["connected_email"],
        "refresh_token": token,
        "selected_calendar_ids": row.get("selected_calendar_ids") or [],
    }


def disconnect(owner_email: str) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "DELETE FROM microsoft_calendar_connections WHERE user_email = %s",
            (str(owner_email or "").strip().lower(),),
        )
        conn.commit()


def set_selected_calendars(owner_email: str, calendar_ids: list[str]) -> dict[str, Any]:
    owner = str(owner_email or "").strip().lower()
    cleaned = list(dict.fromkeys(str(value).strip() for value in calendar_ids if str(value).strip()))
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """UPDATE microsoft_calendar_connections
               SET selected_calendar_ids = %s, updated_at = NOW(), last_error = NULL
               WHERE user_email = %s RETURNING user_email""",
            (cleaned, owner),
        )
        if not cur.fetchone():
            raise ValueError("Microsoft Calendar is not connected")
        conn.commit()
    return get_connection_status(owner)


def list_calendars(owner_email: str) -> list[dict[str, Any]]:
    calendars: list[dict[str, Any]] = []
    url = f"{GRAPH_BASE}/me/calendars"
    params: dict[str, Any] | None = {"$select": "id,name,canEdit,owner"}
    while url:
        page = _graph_get(owner_email, url, params=params)
        calendars.extend(
            {
                "id": item.get("id"),
                "summary": item.get("name") or item.get("id"),
                "primary": False,
                "time_zone": None,
                "access_role": "writer" if item.get("canEdit") else "reader",
            }
            for item in page.get("value", [])
            if isinstance(item, dict) and item.get("id")
        )
        url = str(page.get("@odata.nextLink") or "")
        params = None
    return calendars


def list_events(
    owner_email: str,
    calendar_id: str,
    *,
    time_min: datetime,
    time_max: datetime,
) -> list[dict[str, Any]]:
    start = _graph_datetime(time_min)
    end = _graph_datetime(time_max)
    calendar_path = quote(str(calendar_id), safe="")
    url = f"{GRAPH_BASE}/me/calendars/{calendar_path}/calendarView"
    params: dict[str, Any] | None = {
        "startDateTime": start,
        "endDateTime": end,
        "$top": 1000,
        "$select": (
            "id,subject,body,start,end,location,attendees,isCancelled,lastModifiedDateTime,"
            "seriesMasterId,originalStart,webLink,type"
        ),
    }
    events: list[dict[str, Any]] = []
    while url:
        page = _graph_get(owner_email, url, params=params, immutable_ids=True)
        events.extend(_normalize_event(item) for item in page.get("value", []) if isinstance(item, dict))
        url = str(page.get("@odata.nextLink") or "")
        params = None
    return events


def get_event(owner_email: str, calendar_id: str, event_id: str) -> dict[str, Any] | None:
    url = (
        f"{GRAPH_BASE}/me/calendars/{quote(str(calendar_id), safe='')}/events/"
        f"{quote(str(event_id), safe='')}"
    )
    try:
        response = requests.get(
            url,
            headers=_headers(owner_email, immutable_ids=True),
            params={"$select": "id,subject,body,start,end,location,attendees,isCancelled,lastModifiedDateTime,seriesMasterId,originalStart,webLink,type"},
            timeout=GRAPH_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise MicrosoftCalendarError("Microsoft Graph could not be reached") from exc
    if response.status_code == 404:
        return None
    if not response.ok:
        raise MicrosoftCalendarError(f"Microsoft Graph returned HTTP {response.status_code}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise MicrosoftCalendarError("Microsoft Graph returned an invalid response") from exc
    return _normalize_event(payload) if isinstance(payload, dict) else None


def record_sync_result(owner_email: str, *, error: str | None = None, reconciled: bool = False) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """UPDATE microsoft_calendar_connections
               SET last_sync_at = CASE WHEN %s IS NULL THEN NOW() ELSE last_sync_at END,
                   last_reconciliation_at = CASE WHEN %s THEN NOW() ELSE last_reconciliation_at END,
                   last_error = %s WHERE user_email = %s""",
            (error, reconciled, str(error)[:500] if error else None, str(owner_email or "").strip().lower()),
        )
        conn.commit()


def list_connections() -> list[dict[str, Any]]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT user_email FROM microsoft_calendar_connections ORDER BY user_email")
        owners = [str(row["user_email"]) for row in cur.fetchall()]
    return [connection for owner in owners if (connection := get_connection(owner))]


def _headers(owner_email: str, *, immutable_ids: bool = False) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {_get_access_token(owner_email)}"}
    if immutable_ids:
        headers["Prefer"] = 'IdType="ImmutableId", outlook.timezone="UTC"'
    return headers


def _graph_get(
    owner_email: str,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    immutable_ids: bool = False,
) -> dict[str, Any]:
    try:
        response = requests.get(
            url,
            headers=_headers(owner_email, immutable_ids=immutable_ids),
            params=params,
            timeout=GRAPH_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise MicrosoftCalendarError("Microsoft Graph could not be reached") from exc
    if not response.ok:
        raise MicrosoftCalendarError(f"Microsoft Graph returned HTTP {response.status_code}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise MicrosoftCalendarError("Microsoft Graph returned an invalid response") from exc
    if not isinstance(payload, dict):
        raise MicrosoftCalendarError("Microsoft Graph returned an invalid response")
    return payload


def _get_access_token(owner_email: str) -> str:
    connection = get_connection(owner_email)
    if not connection:
        raise MicrosoftCalendarError("Microsoft Calendar is not connected")
    client_id, client_secret, tenant = _oauth_config()
    try:
        response = requests.post(
            _token_url(tenant),
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "grant_type": "refresh_token",
                "refresh_token": connection["refresh_token"],
                "scope": "openid profile email offline_access User.Read Calendars.Read",
            },
            timeout=GRAPH_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise MicrosoftCalendarError("Microsoft Calendar authorization could not be refreshed") from exc
    if not response.ok:
        raise MicrosoftCalendarError("Microsoft Calendar authorization expired; reconnect the account")
    try:
        payload = response.json()
    except ValueError as exc:
        raise MicrosoftCalendarError("Microsoft identity returned an invalid response") from exc
    access_token = payload.get("access_token") if isinstance(payload, dict) else None
    if not access_token:
        raise MicrosoftCalendarError("Microsoft identity response did not include an access token")
    refreshed = payload.get("refresh_token")
    if refreshed and refreshed != connection["refresh_token"]:
        encrypted = _fernet().encrypt(str(refreshed).encode("utf-8")).decode("ascii")
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE microsoft_calendar_connections SET refresh_token_encrypted = %s, updated_at = NOW() WHERE user_email = %s",
                (encrypted, str(owner_email).strip().lower()),
            )
            conn.commit()
    return str(access_token)


def _normalize_event(item: dict[str, Any]) -> dict[str, Any]:
    def date_value(value: Any) -> dict[str, str] | None:
        if not isinstance(value, dict) or not value.get("dateTime"):
            return None
        return {"dateTime": str(value["dateTime"]), "timeZone": "UTC"}

    attendees = [
        {"email": str((attendee.get("emailAddress") or {}).get("address") or "").strip()}
        for attendee in item.get("attendees", [])
        if isinstance(attendee, dict) and (attendee.get("emailAddress") or {}).get("address")
    ]
    body = item.get("body") or {}
    return {
        "id": item.get("id"),
        "summary": item.get("subject") or "",
        "description": _plain_text(body.get("content")),
        "start": date_value(item.get("start")),
        "end": date_value(item.get("end")),
        "location": (item.get("location") or {}).get("displayName"),
        "attendees": attendees,
        "updated": item.get("lastModifiedDateTime"),
        "status": "cancelled" if item.get("isCancelled") else "confirmed",
        "recurringEventId": item.get("seriesMasterId"),
        "originalStartTime": item.get("originalStart"),
        "htmlLink": item.get("webLink"),
    }


def _graph_datetime(value: datetime) -> str:
    normalized = value.isoformat() if value.tzinfo else f"{value.isoformat()}Z"
    return normalized.replace("+00:00", "Z")


def _iso(value: Any) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None
