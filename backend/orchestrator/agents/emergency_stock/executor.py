"""
Executor for emergency stock checks.

Fetches spreadsheet rows, evaluates actions, and updates sheet columns.
"""

from __future__ import annotations

import os
import re
from typing import Any

from agents.emergency_stock.analyzer import (
    DEFAULT_COLUMN_MAP,
    MOVE_TO_CONSUMPTION_DAYS,
    StockAction,
    evaluate_stock,
    parse_sheet_rows,
)
from agents.emergency_stock.bring_client import add_item as bring_add_item
from agents.emergency_stock.bring_client import authenticate as bring_authenticate
from agents.emergency_stock.bring_client import get_list_items as bring_get_list_items
from agents.emergency_stock.bring_client import get_user_account as bring_get_user_account
from agents.emergency_stock.bring_client import list_lists as bring_list_lists
from agents.emergency_stock.sheets_client import (
    fetch_first_sheet_name,
    fetch_sheet_values,
    update_sheet_values,
)
from notifications import send_push_notification


def handle_emergency_stock_request() -> dict[str, Any]:
    """
    Run the emergency stock agent and update the sheet reorder columns.
    """
    sheet_id = os.getenv("EMERGENCY_STOCK_SHEET_ID", "").strip()
    sheet_range = os.getenv("EMERGENCY_STOCK_SHEET_RANGE", "").strip() or None
    column_map = DEFAULT_COLUMN_MAP
    move_to_consumption_days = int(
        os.getenv("EMERGENCY_STOCK_MOVE_TO_CONSUMPTION_DAYS", MOVE_TO_CONSUMPTION_DAYS)
    )
    if not sheet_id:
        return {
            "status": "error",
            "message": "Missing EMERGENCY_STOCK_SHEET_ID env var",
        }

    sheet_name = None
    try:
        sheet_name = fetch_first_sheet_name(sheet_id)
    except Exception as exc:
        return {
            "status": "error",
            "message": f"Failed to resolve sheet name: {exc}",
        }

    if not sheet_range:
        sheet_range = f"{sheet_name}!A1:Z"

    try:
        rows = fetch_sheet_values(sheet_id, sheet_range)
    except Exception as exc:
        return {"status": "error", "message": f"Failed to fetch sheet: {exc}"}

    items = parse_sheet_rows(rows, column_map=column_map)

    consume_actions = evaluate_stock(
        items,
        move_to_consumption_days=move_to_consumption_days,
    )
    header_info = _resolve_header_info(rows, column_map)
    buy_actions = _collect_buy_actions(rows, items, header_info)
    actions = consume_actions + buy_actions

    if actions:
        _notify_user_about_actions(actions, rows, header_info)

    updates = _build_sheet_updates(
        rows,
        actions,
        sheet_range=sheet_range,
        sheet_name_override=sheet_name,
        header_info=header_info,
    )

    update_result = {"updatedCells": 0}
    if updates:
        update_result = update_sheet_values(sheet_id, updates)

    return {
        "status": "success",
        "items_checked": len(items),
        "actions_found": len(actions),
        "sheet_updates": len(updates),
        "updated_cells": update_result.get("totalUpdatedCells")
        or update_result.get("updatedCells"),
        "actions": [
            {
                "action_type": action.action_type,
                "reason": action.reason,
                "row_number": action.item.row_number,
                "item": action.item.name,
            }
            for action in actions
        ],
    }


def _build_sheet_updates(
    rows: list[list[str]],
    actions: list[StockAction],
    sheet_range: str,
    sheet_name_override: str | None,
    header_info: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    if not rows:
        return []

    if not actions:
        return []

    sheet_name = sheet_name_override or _extract_sheet_name(sheet_range) or "Sheet1"
    sheet_name = _format_sheet_name(sheet_name)
    indices = header_info["indices"] if header_info else None
    if not indices:
        return []

    reorder_qty_idx = indices.get("reorder_quantity")
    move_to_consumption_idx = indices.get("move_to_consumption")
    quantity_idx = indices.get("quantity")

    updates: list[dict[str, Any]] = []
    bring_context = None
    if any(action.action_type in {"consume", "buy"} for action in actions):
        bring_context = _prepare_bring_context()

    for action in actions:
        row_number = action.item.row_number

        if action.action_type == "consume":
            if _is_already_marked(rows, row_number, move_to_consumption_idx):
                continue

            quantity = _get_row_cell(rows, row_number, quantity_idx) if quantity_idx is not None else ""
            note = _format_bring_note(quantity, action.item.item_number)
            _maybe_add_to_bring(bring_context, action.item.name, note)
            consume_updates = _build_cell_updates(
                sheet_name,
                row_number,
                {
                    move_to_consumption_idx: "YES",
                },
            )
            updates.extend(consume_updates)
        elif action.action_type == "buy":
            quantity = _get_row_cell(rows, row_number, reorder_qty_idx) if reorder_qty_idx is not None else ""
            note = _format_bring_note(quantity, action.item.item_number)
            _maybe_add_to_bring(bring_context, action.item.name, note)

    return updates


def _resolve_header_info(
    rows: list[list[str]],
    column_map: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not rows:
        return None

    header_row_index = None
    header: list[str] = []
    for idx, row in enumerate(rows):
        cleaned = [str(cell).strip() for cell in row]
        if any(cleaned):
            header_row_index = idx
            header = cleaned
            break

    if header_row_index is None:
        return None

    indices = _resolve_update_columns(header, column_map)
    if indices is None:
        return None

    return {
        "header_row_index": header_row_index,
        "header": header,
        "indices": indices,
    }


def _resolve_update_columns(
    header: list[str],
    column_map: dict[str, Any] | None,
) -> dict[str, int | None] | None:
    normalized = [_normalize_header_label(cell) for cell in header]

    flag_idx = _find_header_index(normalized, ["comprar?", "comprar", "reorder", "buy"])
    qty_idx = _find_header_index(
        normalized, ["qntd de compra", "quantidade de compra", "reorder qty", "purchase qty"]
    )
    date_idx = _find_header_index(
        normalized, ["comprar em", "data compra", "reorder date", "purchase date"]
    )
    move_idx = _find_header_index(
        normalized, ["mover para consumo", "move to consumption", "consumo"]
    )
    quantity_idx = _find_header_index(
        normalized, ["qntd", "quantidade", "quantity", "qty", "stock"]
    )
    item_number_idx = _find_header_index(
        normalized, ["item #", "item#", "item numero", "item number"]
    )

    if column_map:
        flag_idx = _override_index(normalized, flag_idx, column_map.get("reorder_flag"))
        qty_idx = _override_index(normalized, qty_idx, column_map.get("reorder_quantity"))
        date_idx = _override_index(normalized, date_idx, column_map.get("reorder_date"))
        move_idx = _override_index(normalized, move_idx, column_map.get("move_to_consumption"))
        quantity_idx = _override_index(normalized, quantity_idx, column_map.get("quantity"))
        item_number_idx = _override_index(normalized, item_number_idx, column_map.get("item_number"))

    if (
        flag_idx is None
        and qty_idx is None
        and date_idx is None
        and move_idx is None
        and quantity_idx is None
        and item_number_idx is None
    ):
        return None

    return {
        "reorder_flag": flag_idx,
        "reorder_quantity": qty_idx,
        "reorder_date": date_idx,
        "move_to_consumption": move_idx,
        "quantity": quantity_idx,
        "item_number": item_number_idx,
    }


def _override_index(
    normalized: list[str],
    current: int | None,
    override_value: Any,
) -> int | None:
    if override_value is None:
        return current
    if isinstance(override_value, int):
        return override_value
    if isinstance(override_value, str):
        try:
            normalized_value = _normalize_header_label(override_value)
            return normalized.index(normalized_value)
        except ValueError:
            return None
    return current


def _find_header_index(normalized: list[str], aliases: list[str]) -> int | None:
    for alias in aliases:
        alias_norm = _normalize_header_label(alias)
        if alias_norm in normalized:
            return normalized.index(alias_norm)
    return None


def _column_letter(index: int | None) -> str:
    if index is None:
        return "A"
    index += 1
    letters = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def _build_cell_updates(
    sheet_name: str,
    row_number: int,
    values_by_index: dict[int | None, str | None],
) -> list[dict[str, Any]]:
    updates = []
    for index, value in values_by_index.items():
        if index is None or value is None:
            continue
        cell = f"{sheet_name}!{_column_letter(index)}{row_number}"
        updates.append({"range": cell, "values": [[value]]})
    return updates


def _extract_sheet_name(sheet_range: str) -> str | None:
    if "!" not in sheet_range:
        return None
    return sheet_range.split("!", 1)[0].strip()


def _format_sheet_name(sheet_name: str) -> str:
    escaped = sheet_name.replace("'", "''")
    if " " in escaped or "-" in escaped:
        return f"'{escaped}'"
    return escaped


def _normalize_header_label(text: str) -> str:
    cleaned = text.replace("\ufeff", "").replace("\u00a0", " ").strip().lower()
    cleaned = _strip_accents(cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = re.sub(r"[^a-z0-9 ]", "", cleaned)
    return cleaned.strip()


_ACCENT_TRANSLATION = str.maketrans(
    {
        "\u00e1": "a",
        "\u00e0": "a",
        "\u00e2": "a",
        "\u00e3": "a",
        "\u00e9": "e",
        "\u00ea": "e",
        "\u00ed": "i",
        "\u00f3": "o",
        "\u00f4": "o",
        "\u00f5": "o",
        "\u00fa": "u",
        "\u00e7": "c",
    }
)


def _strip_accents(text: str) -> str:
    return text.translate(_ACCENT_TRANSLATION)


def _is_already_marked(
    rows: list[list[str]],
    row_number: int,
    column_index: int | None,
) -> bool:
    if column_index is None:
        return False
    cell_value = _get_row_cell(rows, row_number, column_index)
    if not cell_value:
        return False
    normalized = str(cell_value).strip().lower()
    return normalized in {"yes", "y", "true", "1", "sim"}


def _collect_buy_actions(
    rows: list[list[str]],
    items: list[Any],
    header_info: dict[str, Any] | None,
) -> list[StockAction]:
    if not header_info:
        return []
    indices = header_info.get("indices") or {}
    reorder_flag_idx = indices.get("reorder_flag")
    if reorder_flag_idx is None:
        return []

    actions: list[StockAction] = []
    for item in items:
        if _is_already_marked(rows, item.row_number, reorder_flag_idx):
            actions.append(
                StockAction(
                    action_type="buy",
                    item=item,
                    due_date=None,
                    reason="Marked to buy",
                )
            )
    return actions


def _get_row_cell(
    rows: list[list[str]],
    row_number: int,
    column_index: int,
) -> str:
    row_index = row_number - 1
    if row_index < 0 or row_index >= len(rows):
        return ""
    row = rows[row_index]
    if column_index < 0 or column_index >= len(row):
        return ""
    return str(row[column_index]).strip()


def _prepare_bring_context() -> dict[str, Any]:
    email = os.getenv("BRING_EMAIL", "").strip()
    password = os.getenv("BRING_PASSWORD", "").strip()
    list_name = os.getenv("BRING_LIST_NAME", "").strip()

    if not email or not password or not list_name:
        raise RuntimeError("Missing BRING_EMAIL, BRING_PASSWORD, or BRING_LIST_NAME env var")

    auth = bring_authenticate(email, password)
    access_token = auth.get("access_token")
    token_type = auth.get("token_type") or "Bearer"
    user_uuid = auth.get("uuid") or auth.get("userUuid") or auth.get("user_uuid")
    public_uuid = auth.get("publicUuid") or auth.get("public_uuid")

    if not access_token or not user_uuid:
        raise RuntimeError("Bring! authentication failed")

    user_account = bring_get_user_account(
        access_token=access_token,
        token_type=token_type,
        user_uuid=user_uuid,
        public_uuid=public_uuid,
    )
    user_locale = user_account.get("userLocale") or {}
    country = user_locale.get("country")

    lists_response = bring_list_lists(
        access_token=access_token,
        token_type=token_type,
        user_uuid=user_uuid,
        public_uuid=public_uuid,
        country=country,
    )
    lists = lists_response.get("lists") or []
    normalized_target = list_name.strip().lower()
    matching_list = next(
        (
            lst
            for lst in lists
            if isinstance(lst, dict)
            and str(lst.get("name", "")).strip().lower() == normalized_target
        ),
        None,
    )
    if not matching_list:
        available = [
            str(lst.get("name"))
            for lst in lists
            if isinstance(lst, dict) and lst.get("name")
        ]
        raise RuntimeError(
            "Bring list name not found. Available lists: "
            + (", ".join(available) if available else "none")
        )
    list_uuid = str(matching_list.get("listUuid", "")).strip()
    if not list_uuid:
        raise RuntimeError("Bring list UUID missing for matched list name")

    list_items = bring_get_list_items(
        list_uuid=list_uuid,
        access_token=access_token,
        token_type=token_type,
        user_uuid=user_uuid,
        public_uuid=public_uuid,
        country=country,
    )
    existing_items = {
        (_normalize_item_name(entry.get("itemId", "")), _normalize_item_spec(entry.get("specification", "")))
        for entry in list_items
        if entry.get("itemId")
    }
    print("existing_items", existing_items)

    return {
        "access_token": access_token,
        "token_type": token_type,
        "user_uuid": user_uuid,
        "public_uuid": public_uuid,
        "country": country,
        "list_uuid": list_uuid,
        "existing_items": existing_items,
    }


def _maybe_add_to_bring(
    bring_context: dict[str, Any] | None,
    item_name: str,
    note: str,
) -> None:
    if not bring_context:
        return
    normalized = (_normalize_item_name(item_name), _normalize_item_spec(note))
    if normalized in bring_context["existing_items"]:
        return

    bring_add_item(
        list_uuid=bring_context["list_uuid"],
        item_name=item_name,
        note=note,
        access_token=bring_context["access_token"],
        user_uuid=bring_context["user_uuid"],
        public_uuid=bring_context["public_uuid"],
        token_type=bring_context["token_type"],
        country=bring_context["country"],
    )
    bring_context["existing_items"].add(normalized)


def _notify_user_about_actions(
    actions: list[StockAction],
    rows: list[list[str]],
    header_info: dict[str, Any] | None,
) -> None:
    """
    TODO: Send notification to user when actions are required.
    """
    message = _build_notification_message(actions, rows, header_info)
    try:
        send_push_notification("Estoque de emergência", message)
    except Exception:
        return
    return


def _format_bring_note(quantity: str, item_number: str | None) -> str:
    parts = []
    if quantity:
        parts.append(quantity)
    if item_number:
        parts.append(f"item# {item_number}")
    return " - ".join(parts)


def _normalize_item_name(name: str) -> str:
    return _strip_accents(name).strip().lower()


def _normalize_item_spec(spec: str) -> str:
    return _strip_accents(spec).strip().lower()


def _build_notification_message(
    actions: list[StockAction],
    rows: list[list[str]],
    header_info: dict[str, Any] | None,
) -> str:
    consume_actions = [action for action in actions if action.action_type == "consume"]
    buy_actions = [action for action in actions if action.action_type == "buy"]

    consume_amount = len(consume_actions)
    total_amount_to_purchase = _sum_purchase_quantities(
        buy_actions,
        rows,
        header_info,
    )

    seen: set[str] = set()
    buy_items: list[str] = []
    for action in buy_actions:
        name = action.item.name
        if name not in seen:
            buy_items.append(name)
            seen.add(name)

    return (
        "Estoque de emergência precisa de alguns ajustes:\n"
        f"- Mover {consume_amount} items para consumo\n"
        f"- Comprar {total_amount_to_purchase} items\n"
        "\n"
        f"Items para comprar: {', '.join(buy_items)}\n"
    )


def _sum_purchase_quantities(
    buy_actions: list[StockAction],
    rows: list[list[str]],
    header_info: dict[str, Any] | None,
) -> int:
    if not header_info:
        return 0
    indices = header_info.get("indices") or {}
    reorder_qty_idx = indices.get("reorder_quantity")
    if reorder_qty_idx is None:
        return 0

    total = 0
    for action in buy_actions:
        raw = _get_row_cell(rows, action.item.row_number, reorder_qty_idx)
        total += _parse_int_value(raw)
    return total


def _parse_int_value(raw: str) -> int:
    if not raw:
        return 0
    match = re.search(r"-?\d+", raw.replace(",", ""))
    if not match:
        return 0
    try:
        return int(match.group(0))
    except ValueError:
        return 0
