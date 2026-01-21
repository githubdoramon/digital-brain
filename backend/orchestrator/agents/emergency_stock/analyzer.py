"""
Spreadsheet parsing and decision logic for emergency stock checks.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from dateutil import parser as date_parser

MOVE_TO_CONSUMPTION_DAYS = 60
DEFAULT_COLUMN_MAP = {
    "item_number": "ITEM #",
    "item": "NOME",
    "category": "CATEGORIA",
    "group": "GRUPO",
    "meal": "REFEIÇÃO",
    "preparation": "TIPO DE PREPARO",
    "quantity": "QNTD",
    "brand": "MARCA",
    "expiry_date": "VENCIMENTO",
    "move_to_consumption": "MOVER PARA CONSUMO",
    "reorder_flag": "COMPRAR?",
    "reorder_quantity": "QNTD DE COMPRA",
    "reorder_date": "COMPRAR EM",
}


@dataclass(frozen=True)
class StockItem:
    item_number: str | None
    name: str
    quantity: int
    min_quantity: int | None
    unit: str | None
    expiry_date: date | None
    category: str | None
    row_number: int


@dataclass(frozen=True)
class StockAction:
    action_type: str  # "consume" | "restock"
    item: StockItem
    due_date: date | None
    reason: str


def parse_sheet_rows(
    rows: list[list[str]],
    column_map: dict[str, Any] | None = None,
) -> list[StockItem]:
    if not rows:
        return []

    header_row_index = None
    header: list[str] = []
    for idx, row in enumerate(rows):
        cleaned = [str(cell).strip() for cell in row]
        if any(cleaned):
            header_row_index = idx
            header = cleaned
            break

    if header_row_index is None:
        return []

    mapping = _resolve_column_map(header, column_map or DEFAULT_COLUMN_MAP)
    items: list[StockItem] = []

    for index, row in enumerate(rows[header_row_index + 1 :], start=header_row_index + 2):
        item_number = _get_cell(row, mapping.get("item_number")) or None
        name = _get_cell(row, mapping.get("item"))
        if not name:
            continue

        quantity = _parse_int(_get_cell(row, mapping.get("quantity"))) or 0
        min_quantity = _parse_int(_get_cell(row, mapping.get("min_quantity")))
        unit = _get_cell(row, mapping.get("unit")) or None
        expiry = _parse_date(_get_cell(row, mapping.get("expiry_date")))
        category = _get_cell(row, mapping.get("category"))

        items.append(
            StockItem(
                item_number=item_number,
                name=name,
                quantity=quantity,
                min_quantity=min_quantity,
                unit=unit,
                expiry_date=expiry,
                category=category.lower() if category else None,
                row_number=index,
            )
        )

    return items


def evaluate_stock(
    items: list[StockItem],
    move_to_consumption_days: int = MOVE_TO_CONSUMPTION_DAYS,
    today: date | None = None,
) -> list[StockAction]:
    today = today or date.today()
    actions: list[StockAction] = []

    for item in items:
        if item.expiry_date:
            days_until_expiry = (item.expiry_date - today).days
            if move_to_consumption_days > 0 and days_until_expiry <= move_to_consumption_days:
                reason = f"Move to consumption (expires in {days_until_expiry} days)"
                actions.append(
                    StockAction(
                        action_type="consume",
                        item=item,
                        due_date=item.expiry_date,
                        reason=reason,
                    )
                )

    return actions


def _resolve_column_map(
    header: list[str],
    column_map: dict[str, Any],
) -> dict[str, int | None]:
    normalized = [_normalize_header_label(cell) for cell in header]
    mapping: dict[str, int | None] = {}

    for key, value in column_map.items():
        if isinstance(value, int):
            mapping[key] = value
            continue
        if isinstance(value, str):
            try:
                normalized_value = _normalize_header_label(value)
                mapping[key] = normalized.index(normalized_value)
            except ValueError:
                normalized_value = _normalize_header_label(value)
                match_idx = None
                for idx, header_value in enumerate(normalized):
                    if normalized_value == header_value:
                        match_idx = idx
                        break
                    if normalized_value in header_value or header_value in normalized_value:
                        match_idx = idx
                        break
                mapping[key] = match_idx

    return mapping


def _get_cell(row: list[str], index: int | None) -> str:
    if index is None:
        return ""
    if index >= len(row):
        return ""
    return str(row[index]).strip()


def _parse_int(raw: str) -> int | None:
    if not raw:
        return None
    match = re.search(r"-?\d+", raw.replace(",", ""))
    if not match:
        return None
    try:
        return int(match.group(0))
    except ValueError:
        return None


def _parse_date(raw: str) -> date | None:
    if not raw:
        return None
    normalized = _normalize_portuguese_date(raw)
    if normalized:
        return normalized

    try:
        parsed = date_parser.parse(raw, fuzzy=True)
    except (ValueError, OverflowError, TypeError):
        return None
    if not parsed:
        return None
    return parsed.date()


def _normalize_portuguese_date(raw: str) -> date | None:
    cleaned = _strip_accents(raw.strip().lower())
    if not cleaned or cleaned in {"#num!", "n/a", "na"}:
        return None

    match = re.match(r"^([a-z]+)\s*/\s*(\d{2,4})$", cleaned)
    if not match:
        return None

    month_token, year_token = match.groups()
    month = _PORTUGUESE_MONTHS.get(month_token)
    if not month:
        return None

    year = int(year_token)
    if year < 100:
        year += 2000

    # Use first day of month for conservative expiry handling.
    return date(year, month, 1)


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


def _normalize_header_label(text: str) -> str:
    cleaned = text.replace("\ufeff", "").replace("\u00a0", " ").strip().lower()
    cleaned = _strip_accents(cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = re.sub(r"[^a-z0-9 ]", "", cleaned)
    return cleaned.strip()


_PORTUGUESE_MONTHS = {
    "janeiro": 1,
    "fevereiro": 2,
    "marco": 3,
    "abril": 4,
    "maio": 5,
    "junho": 6,
    "julho": 7,
    "agosto": 8,
    "setembro": 9,
    "outubro": 10,
    "novembro": 11,
    "dezembro": 12,
}
