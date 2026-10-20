"""Contact birthdays preserve an unknown year rather than inventing one."""

import re
from datetime import date


def parse_birthday(value):
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, str) and re.fullmatch(r"--\d{2}-\d{2}", value):
        date.fromisoformat("2000" + value[1:])  # Leap day is a valid recurring birthday.
        return value
    return date.fromisoformat(value)


def birthday_parts(value):
    parsed = parse_birthday(value)
    if isinstance(parsed, str):
        return None, int(parsed[2:4]), int(parsed[5:7])
    return parsed, None, None


def birthday_value(row):
    value = row.get("birthday")
    if value:
        return value.isoformat() if isinstance(value, date) else str(value)
    if row.get("birthday_month") and row.get("birthday_day"):
        return f"--{row['birthday_month']:02d}-{row['birthday_day']:02d}"
    return None


def birthday_label(value):
    parsed = parse_birthday(value)
    if isinstance(parsed, str):
        recurring = date.fromisoformat("2000" + parsed[1:])
        return f"{recurring:%B} {recurring.day}"
    return f"{parsed:%B} {parsed.day}, {parsed.year}"
