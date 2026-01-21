"""
Google Sheets client for the Emergency Stock agent.

Uses a service account JSON to fetch rows from a spreadsheet.
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx
from google.auth.transport.requests import Request
from google.oauth2 import service_account

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def _load_service_account_info() -> dict[str, Any]:
    json_path = os.getenv("EMERGENCY_STOCK_SERVICE_ACCOUNT_JSON", "/app/keys/digital-brain-service-account.json").strip()

    if json_path and os.path.exists(json_path):
        with open(json_path) as f:
            return json.load(f)

    raise ValueError(
        "Missing Google Sheets credentials. Set EMERGENCY_STOCK_SERVICE_ACCOUNT_JSON."
    )


def fetch_sheet_values(sheet_id: str, sheet_range: str) -> list[list[str]]:
    """
    Fetch rows from a Google Sheet range.

    Args:
        sheet_id: Spreadsheet ID (not the full URL).
        sheet_range: A1 notation range (e.g., "Stock!A1:Z").

    Returns:
        List of rows, each row a list of strings.
    """
    info = _load_service_account_info()
    credentials = service_account.Credentials.from_service_account_info(
        info, scopes=SCOPES
    )
    credentials.refresh(Request())

    url = f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}/values/{sheet_range}"
    headers = {"Authorization": f"Bearer {credentials.token}"}

    with httpx.Client(timeout=10) as client:
        response = client.get(url, headers=headers)
        response.raise_for_status()

    payload = response.json()
    return payload.get("values", [])


def fetch_first_sheet_name(sheet_id: str) -> str:
    """
    Fetch the first sheet title for a spreadsheet.
    """
    info = _load_service_account_info()
    credentials = service_account.Credentials.from_service_account_info(
        info, scopes=SCOPES
    )
    credentials.refresh(Request())

    url = f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}"
    headers = {"Authorization": f"Bearer {credentials.token}"}
    params = {"fields": "sheets.properties.title"}

    with httpx.Client(timeout=10) as client:
        response = client.get(url, headers=headers, params=params)
        response.raise_for_status()

    payload = response.json()
    sheets = payload.get("sheets", [])
    if not sheets:
        raise ValueError("Spreadsheet has no sheets")

    return sheets[0]["properties"]["title"]


def update_sheet_values(
    sheet_id: str,
    updates: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Batch update spreadsheet values.

    Args:
        sheet_id: Spreadsheet ID.
        updates: List of {range: "Sheet!A1", values: [[...]]}.

    Returns:
        API response payload.
    """
    if not updates:
        return {"updatedCells": 0}

    info = _load_service_account_info()
    credentials = service_account.Credentials.from_service_account_info(
        info, scopes=SCOPES
    )
    credentials.refresh(Request())

    url = f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}/values:batchUpdate"
    headers = {"Authorization": f"Bearer {credentials.token}"}
    body = {
        "valueInputOption": "RAW",
        "data": updates,
    }

    with httpx.Client(timeout=10) as client:
        response = client.post(url, headers=headers, json=body)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = response.text
            try:
                detail = response.json()
            except ValueError:
                pass
            raise RuntimeError(f"Sheet update failed: {exc.response.status_code} {detail}") from exc

    return response.json()
