from __future__ import annotations

import os
import re
from datetime import datetime
from typing import Any
from urllib.parse import quote

import requests
from cryptography.fernet import Fernet, InvalidToken

from db import get_conn

GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_API_BASE = "https://www.googleapis.com/calendar/v3"
GOOGLE_TIMEOUT_SECONDS = 20


class GoogleCalendarError(RuntimeError):
    pass


def _fernet() -> Fernet:
    key = os.getenv("GOOGLE_CALENDAR_TOKEN_ENCRYPTION_KEY", "").strip()
    if not key:
        raise GoogleCalendarError("Google Calendar token encryption is not configured")
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise GoogleCalendarError("Google Calendar token encryption key is invalid") from exc


def _oauth_client() -> tuple[str, str]:
    client_id = (os.getenv("GOOGLE_CALENDAR_CLIENT_ID") or os.getenv("GOOGLE_CLIENT_ID") or "").strip()
    client_secret = (os.getenv("GOOGLE_CALENDAR_CLIENT_SECRET") or os.getenv("GOOGLE_CLIENT_SECRET") or "").strip()
    if not client_id or not client_secret:
        raise GoogleCalendarError("Google Calendar OAuth client is not configured")
    return client_id, client_secret


def store_connection(user_email: str, refresh_token: str) -> dict[str, Any]:
    normalized_email = str(user_email or "").strip().lower()
    normalized_token = str(refresh_token or "").strip()
    if not normalized_email or not normalized_token:
        raise ValueError("user email and refresh token are required")
    encrypted_token = _fernet().encrypt(normalized_token.encode("utf-8")).decode("ascii")
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO google_calendar_connections (user_email, refresh_token_encrypted)
            VALUES (%s, %s)
            ON CONFLICT (user_email) DO UPDATE
              SET refresh_token_encrypted = EXCLUDED.refresh_token_encrypted,
                  updated_at = NOW(),
                  last_error = NULL
            """,
            (normalized_email, encrypted_token),
        )
        conn.commit()
    return get_connection_status(normalized_email)


def get_connection_status(user_email: str) -> dict[str, Any]:
    normalized_email = str(user_email or "").strip().lower()
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT user_email, selected_calendar_ids, connected_at, updated_at,
                   last_sync_at, last_reconciliation_at, last_error
            FROM google_calendar_connections
            WHERE user_email = %s
            """,
            (normalized_email,),
        )
        row = cur.fetchone()
    if not row:
        return {"connected": False, "selected_calendar_ids": []}
    return {
        "connected": True,
        "user_email": row["user_email"],
        "selected_calendar_ids": row.get("selected_calendar_ids") or [],
        "connected_at": _iso(row.get("connected_at")),
        "updated_at": _iso(row.get("updated_at")),
        "last_sync_at": _iso(row.get("last_sync_at")),
        "last_reconciliation_at": _iso(row.get("last_reconciliation_at")),
        "last_error": row.get("last_error"),
    }


def get_connection(user_email: str) -> dict[str, Any] | None:
    normalized_email = str(user_email or "").strip().lower()
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT user_email, refresh_token_encrypted, selected_calendar_ids
            FROM google_calendar_connections
            WHERE user_email = %s
            """,
            (normalized_email,),
        )
        row = cur.fetchone()
    if not row:
        return None
    try:
        refresh_token = _fernet().decrypt(
            str(row["refresh_token_encrypted"]).encode("ascii")
        ).decode("utf-8")
    except (InvalidToken, UnicodeEncodeError) as exc:
        raise GoogleCalendarError("Stored Google Calendar connection cannot be decrypted") from exc
    return {
        "user_email": row["user_email"],
        "refresh_token": refresh_token,
        "selected_calendar_ids": row.get("selected_calendar_ids") or [],
    }


def disconnect(user_email: str) -> None:
    normalized_email = str(user_email or "").strip().lower()
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "DELETE FROM google_calendar_connections WHERE user_email = %s",
            (normalized_email,),
        )
        conn.commit()


def set_selected_calendars(user_email: str, calendar_ids: list[str]) -> dict[str, Any]:
    normalized_email = str(user_email or "").strip().lower()
    cleaned_ids = list(dict.fromkeys(str(value).strip() for value in calendar_ids if str(value).strip()))
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE google_calendar_connections
            SET selected_calendar_ids = %s, updated_at = NOW(), last_error = NULL
            WHERE user_email = %s
            RETURNING user_email
            """,
            (cleaned_ids, normalized_email),
        )
        if not cur.fetchone():
            raise ValueError("Google Calendar is not connected")
        conn.commit()
    return get_connection_status(normalized_email)


def list_calendars(user_email: str) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"maxResults": 250}
    calendars: list[dict[str, Any]] = []
    while True:
        response = _google_get(user_email, "/users/me/calendarList", params=params)
        calendars.extend(
            {
                "id": item.get("id"),
                "summary": item.get("summary") or item.get("id"),
                "primary": bool(item.get("primary")),
                "time_zone": item.get("timeZone"),
                "access_role": item.get("accessRole"),
            }
            for item in response.get("items", [])
            if isinstance(item, dict) and item.get("id")
        )
        page_token = response.get("nextPageToken")
        if not page_token:
            return calendars
        params["pageToken"] = page_token


def list_events(
    user_email: str,
    calendar_id: str,
    *,
    time_min: datetime,
    time_max: datetime,
) -> list[dict[str, Any]]:
    encoded_calendar_id = quote(calendar_id, safe="")
    path = f"/calendars/{encoded_calendar_id}/events"
    params: dict[str, Any] = {
        "timeMin": _google_datetime(time_min),
        "timeMax": _google_datetime(time_max),
        "singleEvents": "true",
        "showDeleted": "true",
        "maxResults": 2500,
        "orderBy": "startTime",
    }
    events: list[dict[str, Any]] = []
    while True:
        page = _google_get(user_email, path, params=params)
        events.extend(item for item in page.get("items", []) if isinstance(item, dict))
        page_token = page.get("nextPageToken")
        if not page_token:
            return events
        params["pageToken"] = page_token


def get_event(user_email: str, calendar_id: str, event_id: str) -> dict[str, Any] | None:
    token = _get_access_token(user_email)
    path = (
        f"{GOOGLE_API_BASE}/calendars/{quote(calendar_id, safe='')}/events/"
        f"{quote(event_id, safe='')}"
    )
    try:
        response = requests.get(
            path,
            headers={"Authorization": f"Bearer {token}"},
            timeout=GOOGLE_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise GoogleCalendarError("Google Calendar could not be reached") from exc
    if response.status_code == 404:
        return None
    if not response.ok:
        raise GoogleCalendarError(f"Google Calendar API returned HTTP {response.status_code}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise GoogleCalendarError("Google Calendar returned an invalid response") from exc
    return payload if isinstance(payload, dict) else None


def record_sync_result(user_email: str, *, error: str | None = None, reconciled: bool = False) -> None:
    normalized_email = str(user_email or "").strip().lower()
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE google_calendar_connections
            SET last_sync_at = CASE WHEN %s::text IS NULL THEN NOW() ELSE last_sync_at END,
                last_reconciliation_at = CASE WHEN %s THEN NOW() ELSE last_reconciliation_at END,
                last_error = %s
            WHERE user_email = %s
            """,
            (error, reconciled, str(error)[:500] if error else None, normalized_email),
        )
        conn.commit()


def list_connections() -> list[dict[str, Any]]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT user_email FROM google_calendar_connections ORDER BY user_email"
        )
        rows = cur.fetchall()
    return [connection for row in rows if (connection := get_connection(row["user_email"]))]


def _google_get(user_email: str, path: str, *, params: dict[str, Any]) -> dict[str, Any]:
    token = _get_access_token(user_email)
    try:
        response = requests.get(
            f"{GOOGLE_API_BASE}{path}",
            headers={"Authorization": f"Bearer {token}"},
            params=params,
            timeout=GOOGLE_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise GoogleCalendarError("Google Calendar could not be reached") from exc
    if not response.ok:
        error_code = _provider_error_code(response)
        suffix = f" ({error_code})" if error_code else ""
        raise GoogleCalendarError(
            f"Google Calendar API returned HTTP {response.status_code}{suffix}"
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise GoogleCalendarError("Google Calendar returned an invalid response") from exc
    if not isinstance(payload, dict):
        raise GoogleCalendarError("Google Calendar returned an invalid response")
    return payload


def _get_access_token(user_email: str) -> str:
    connection = get_connection(user_email)
    if not connection:
        raise GoogleCalendarError("Google Calendar is not connected")
    client_id, client_secret = _oauth_client()
    try:
        response = requests.post(
            GOOGLE_TOKEN_URL,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "grant_type": "refresh_token",
                "refresh_token": connection["refresh_token"],
            },
            timeout=GOOGLE_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise GoogleCalendarError("Google Calendar authorization could not be refreshed") from exc
    if not response.ok:
        error_code = _provider_error_code(response)
        suffix = f" ({error_code})" if error_code else ""
        raise GoogleCalendarError(
            "Google Calendar authorization refresh failed"
            f"{suffix}; reconnect the account if the problem persists"
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise GoogleCalendarError("Google OAuth returned an invalid response") from exc
    access_token = payload.get("access_token") if isinstance(payload, dict) else None
    if not access_token:
        raise GoogleCalendarError("Google OAuth response did not include an access token")
    refreshed_token = payload.get("refresh_token")
    if refreshed_token and refreshed_token != connection["refresh_token"]:
        _replace_refresh_token(user_email, refreshed_token)
    return str(access_token)


_SAFE_PROVIDER_ERROR_CODE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")


def _provider_error_code(response: requests.Response) -> str | None:
    """Extract a short machine-readable provider code without exposing response bodies."""
    try:
        payload = response.json()
    except (ValueError, AttributeError):
        return None
    if not isinstance(payload, dict):
        return None

    error = payload.get("error")
    candidates: list[Any] = []
    if isinstance(error, str):
        candidates.append(error)
    elif isinstance(error, dict):
        details = error.get("errors")
        if isinstance(details, list):
            candidates.extend(
                item.get("reason")
                for item in details
                if isinstance(item, dict)
            )
        candidates.extend((error.get("status"), error.get("code")))

    for candidate in candidates:
        if isinstance(candidate, str) and _SAFE_PROVIDER_ERROR_CODE.fullmatch(candidate):
            return candidate
    return None


def _replace_refresh_token(user_email: str, refresh_token: str) -> None:
    encrypted = _fernet().encrypt(refresh_token.encode("utf-8")).decode("ascii")
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE google_calendar_connections
            SET refresh_token_encrypted = %s, updated_at = NOW()
            WHERE user_email = %s
            """,
            (encrypted, str(user_email).strip().lower()),
        )
        conn.commit()


def _google_datetime(value: datetime) -> str:
    from datetime import timezone

    normalized = value.astimezone(timezone.utc).isoformat() if value.tzinfo else value.isoformat() + "Z"
    return normalized.replace("+00:00", "Z")


def _iso(value: Any) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None
