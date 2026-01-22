"""
Bring! client helpers for adding items to a shopping list.
"""

from __future__ import annotations

import os
from typing import Any, Final

import requests

API_BASE_URL: Final = "https://api.getbring.com/rest/"
DEFAULT_HEADERS: Final = {
    "Authorization": "Bearer",
    "X-BRING-API-KEY": "cof4Nc6D8saplXjE3h3HXqHH8m7VU2i1Gs0g85Sp",
    "X-BRING-CLIENT": "android",
    "X-BRING-APPLICATION": "bring",
    "X-BRING-COUNTRY": os.getenv("BRING_COUNTRY", "DE"),
    "X-BRING-USER-UUID": "",
    "X-BRING-PUBLIC-USER-UUID": "",
}


def authenticate(email: str, password: str) -> dict[str, Any]:
    payload = {"email": email, "password": password}
    response = requests.post(
        f"{API_BASE_URL}v2/bringauth",
        data=payload,
        headers=DEFAULT_HEADERS,
        timeout=15,
    )
    if response.status_code == 401:
        raise RuntimeError("Bring auth failed: invalid credentials")
    if response.status_code == 400:
        raise RuntimeError("Bring auth failed: invalid email")
    if response.status_code >= 400:
        raise RuntimeError(f"Bring auth failed: {response.status_code} {response.text}")
    return response.json()


def get_user_account(
    *,
    access_token: str,
    token_type: str,
    user_uuid: str,
    public_uuid: str | None = None,
) -> dict[str, Any]:
    headers = dict(DEFAULT_HEADERS)
    headers["Authorization"] = f"{token_type} {access_token}"
    headers["X-BRING-USER-UUID"] = user_uuid
    if public_uuid:
        headers["X-BRING-PUBLIC-USER-UUID"] = public_uuid

    url = f"{API_BASE_URL}v2/bringusers/{user_uuid}"
    response = requests.get(url, headers=headers, timeout=15)
    if response.status_code >= 400:
        raise RuntimeError(f"Bring user fetch failed: {response.status_code} {response.text}")
    return response.json()


def add_item(
    *,
    list_uuid: str,
    item_name: str,
    note: str,
    access_token: str,
    user_uuid: str,
    public_uuid: str | None = None,
    token_type: str = "Bearer",
    country: str | None = None,
) -> dict[str, Any]:
    headers = dict(DEFAULT_HEADERS)
    headers["Authorization"] = f"{token_type} {access_token}"
    headers["X-BRING-USER-UUID"] = user_uuid
    if public_uuid:
        headers["X-BRING-PUBLIC-USER-UUID"] = public_uuid
    if country:
        headers["X-BRING-COUNTRY"] = country
    headers["Content-Type"] = "application/json"

    payload = {
        "changes": [
            {
                "accuracy": "0.0",
                "altitude": "0.0",
                "latitude": "0.0",
                "longitude": "0.0",
                "itemId": item_name,
                "spec": note or "",
                "operation": "TO_PURCHASE",
            }
        ],
        "sender": "",
    }

    url = f"{API_BASE_URL}v2/bringlists/{list_uuid}/items"
    response = requests.put(url, json=payload, headers=headers, timeout=15)
    if response.status_code >= 400:
        raise RuntimeError(f"Bring add item failed: {response.status_code} {response.text}")
    if not response.content:
        return {}
    try:
        return response.json()
    except ValueError:
        return {}


def get_list_items(
    *,
    list_uuid: str,
    access_token: str,
    token_type: str,
    user_uuid: str,
    public_uuid: str | None = None,
    country: str | None = None,
) -> list[dict[str, str]]:
    headers = dict(DEFAULT_HEADERS)
    headers["Authorization"] = f"{token_type} {access_token}"
    headers["X-BRING-USER-UUID"] = user_uuid
    if public_uuid:
        headers["X-BRING-PUBLIC-USER-UUID"] = public_uuid
    if country:
        headers["X-BRING-COUNTRY"] = country

    url = f"{API_BASE_URL}v2/bringlists/{list_uuid}"
    response = requests.get(url, headers=headers, timeout=15)
    if response.status_code >= 400:
        raise RuntimeError(f"Bring list fetch failed: {response.status_code} {response.text}")
    data = response.json()
    items = data.get("items") or {}
    purchase = items.get("purchase") or []
    entries: list[dict[str, str]] = []
    for entry in purchase:
        item_id = entry.get("itemId")
        if not item_id:
            continue
        entries.append(
            {
                "itemId": str(item_id),
                "specification": str(entry.get("specification") or ""),
            }
        )
    return entries


def list_lists(
    *,
    access_token: str,
    token_type: str,
    user_uuid: str,
    public_uuid: str | None = None,
    country: str | None = None,
) -> dict[str, Any]:
    headers = dict(DEFAULT_HEADERS)
    headers["Authorization"] = f"{token_type} {access_token}"
    headers["X-BRING-USER-UUID"] = user_uuid
    if public_uuid:
        headers["X-BRING-PUBLIC-USER-UUID"] = public_uuid
    if country:
        headers["X-BRING-COUNTRY"] = country

    url = f"{API_BASE_URL}bringusers/{user_uuid}/lists"
    response = requests.get(url, headers=headers, timeout=15)
    if response.status_code >= 400:
        raise RuntimeError(f"Bring list fetch failed: {response.status_code} {response.text}")
    return response.json()
