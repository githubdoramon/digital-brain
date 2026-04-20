#!/usr/bin/env python3
"""
Import contacts from a JSON file and merge into existing records.

Usage:
    python scripts/import_contacts.py path/to/contacts.json [--dry-run]

Notes:
- Matches contacts by email/phone first, then fuzzy name matching.
- Only fills missing fields; existing data is preserved.
- Writes a JSON report with uncertain matches.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import uuid4

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from contacts import get_contact, ingest_contact, list_contacts, normalize_email
from schemas import ContactIn


@dataclass
class MatchCandidate:
    contact_id: str
    display_name: str
    score: int
    reason: str


def _split_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        items = [str(item).strip() for item in value]
    else:
        items = [item.strip() for item in str(value).split(",")]
    return [item for item in items if item]


def _normalize_phone_digits(phone: str) -> str:
    return "".join(ch for ch in phone if ch.isdigit())


def _parse_birthday(value: Any) -> date | None:
    if not value:
        return None
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text.split("T", 1)[0])
    except ValueError:
        return None


def _slugify_name(name: str) -> str:
    safe = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return safe or uuid4().hex[:8]


def _display_name_from_email(email: str) -> str:
    local_part = email.split("@", 1)[0] if "@" in email else email
    pieces = [piece for piece in re.split(r"[._+]+", local_part) if piece]
    if not pieces:
        return email
    return " ".join(piece.capitalize() for piece in pieces)


def _load_contacts(path: str) -> list[dict[str, Any]]:
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        if isinstance(data.get("contacts"), list):
            return [item for item in data["contacts"] if isinstance(item, dict)]
        return [data]
    raise ValueError("Unsupported JSON format; expected list or dict")


def _build_indexes(
    contacts: list[dict[str, Any]],
) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    email_index: dict[str, set[str]] = {}
    phone_index: dict[str, set[str]] = {}
    for contact in contacts:
        contact_id = contact.get("contact_id")
        if not contact_id:
            continue
        for email in contact.get("emails") or []:
            normalized = normalize_email(email)
            if not normalized:
                continue
            email_index.setdefault(normalized, set()).add(contact_id)
        for phone in contact.get("phones") or []:
            digits = _normalize_phone_digits(phone)
            if not digits:
                continue
            phone_index.setdefault(digits, set()).add(contact_id)
    return email_index, phone_index


def _merge_unique(existing: list[str], incoming: list[str]) -> list[str]:
    merged = list(existing)
    existing_set = {item.lower(): item for item in existing if item}
    for item in incoming:
        key = item.lower()
        if key not in existing_set:
            merged.append(item)
            existing_set[key] = item
    return merged


def _fuzzy_match_contact(
    name_candidates: list[str],
    contacts: list[dict[str, Any]],
    threshold: int,
    margin: int,
) -> tuple[str | None, list[MatchCandidate]]:
    if not name_candidates:
        return None, []

    from rapidfuzz import fuzz

    scores: list[MatchCandidate] = []
    for contact in contacts:
        display_name = str(contact.get("display_name") or "")
        aliases = contact.get("aliases") or []
        contact_names = [display_name, *aliases]
        best_score = -1
        best_reason = ""
        for candidate in name_candidates:
            candidate_clean = candidate.strip()
            if not candidate_clean:
                continue
            candidate_tokens = {
                token for token in re.split(r"\s+", candidate_clean.lower()) if len(token) >= 3
            }
            for name in contact_names:
                name_clean = str(name).strip()
                if not name_clean:
                    continue
                name_tokens = {
                    token for token in re.split(r"\s+", name_clean.lower()) if len(token) >= 3
                }
                shared_tokens = candidate_tokens & name_tokens
                if not shared_tokens:
                    continue
                if " " in candidate_clean.strip() and " " in name_clean.strip():
                    candidate_last = candidate_clean.strip().split()[-1].lower()
                    name_last = name_clean.strip().split()[-1].lower()
                    if candidate_last != name_last:
                        continue
                candidate_first = candidate_clean[0].lower()
                name_first = name_clean[0].lower()
                if candidate_first != name_first:
                    continue
                score = round(fuzz.token_sort_ratio(candidate_clean, name_clean))
                if score > best_score:
                    best_score = score
                    best_reason = f"name match: {candidate_clean} -> {name_clean}"
        if best_score >= 0:
            scores.append(
                MatchCandidate(
                    contact_id=contact["contact_id"],
                    display_name=display_name,
                    score=best_score,
                    reason=best_reason,
                )
            )

    scores.sort(key=lambda candidate: (-candidate.score, candidate.display_name))
    if not scores:
        return None, []

    top = scores[0]
    runner_up = scores[1] if len(scores) > 1 else None
    if top.score < threshold:
        return None, scores[:5]
    if runner_up and (top.score - runner_up.score) < margin:
        return None, scores[:5]
    return top.contact_id, scores[:5]


def _build_contact_input(
    contact_id: str,
    existing: dict[str, Any] | None,
    payload: dict[str, Any],
) -> ContactIn:
    display_name = (
        (existing or {}).get("display_name")
        or payload.get("name")
        or payload.get("nickname")
        or "Unknown"
    )
    aliases = list((existing or {}).get("aliases") or [])
    nickname = payload.get("nickname")
    if nickname:
        aliases = _merge_unique(aliases, [str(nickname).strip()])

    birthday = (existing or {}).get("birthday")
    if not birthday:
        birthday = _parse_birthday(payload.get("birthday"))

    emails = list((existing or {}).get("emails") or [])
    incoming_emails = [normalize_email(email) for email in _split_values(payload.get("emails"))]
    incoming_emails = [email for email in incoming_emails if email]
    emails = _merge_unique(emails, incoming_emails)

    phones = list((existing or {}).get("phones") or [])
    incoming_phones = _split_values(payload.get("phones"))
    phones = _merge_unique(phones, incoming_phones)

    comments = (existing or {}).get("comments")
    if not comments:
        comments = payload.get("comments")

    return ContactIn(
        contact_id=contact_id,
        display_name=display_name,
        aliases=aliases,
        birthday=birthday,
        emails=emails,
        phones=phones,
        links=list((existing or {}).get("links") or []),
        tags=list((existing or {}).get("tags") or []),
        comments=comments,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Import contacts from a JSON file")
    parser.add_argument("json_path", help="Path to JSON file with contacts")
    parser.add_argument("--dry-run", action="store_true", help="Do not write changes")
    parser.add_argument(
        "--fuzzy-threshold",
        type=int,
        default=92,
        help="Minimum fuzzy score to auto-match names (default: 92)",
    )
    parser.add_argument(
        "--fuzzy-margin",
        type=int,
        default=4,
        help="Minimum score gap between top matches (default: 4)",
    )
    parser.add_argument(
        "--report",
        default="contact_import_report.json",
        help="Path to write JSON report (default: contact_import_report.json)",
    )
    args = parser.parse_args()

    records = _load_contacts(args.json_path)
    existing_contacts = list_contacts()
    contacts_by_id = {contact["contact_id"]: contact for contact in existing_contacts}
    email_index, phone_index = _build_indexes(existing_contacts)

    summary = {
        "total": len(records),
        "created": 0,
        "updated": 0,
        "skipped": 0,
        "uncertain": 0,
    }
    uncertain: list[dict[str, Any]] = []

    for record in records:
        name = (record.get("name") or "").strip()
        nickname = (record.get("nickname") or "").strip()
        emails = [normalize_email(email) for email in _split_values(record.get("emails"))]
        emails = [email for email in emails if email]
        phones = _split_values(record.get("phones"))
        phone_digits = [_normalize_phone_digits(phone) for phone in phones]
        phone_digits = [digits for digits in phone_digits if digits]

        candidate_ids: set[str] = set()
        candidate_reasons: list[str] = []
        for email in emails:
            for contact_id in email_index.get(email, set()):
                candidate_ids.add(contact_id)
                candidate_reasons.append(f"email match: {email}")
        for digits in phone_digits:
            for contact_id in phone_index.get(digits, set()):
                candidate_ids.add(contact_id)
                candidate_reasons.append(f"phone match: {digits}")

        contact_id: str | None = None
        fuzzy_candidates: list[MatchCandidate] = []

        if len(candidate_ids) == 1:
            contact_id = next(iter(candidate_ids))
        elif len(candidate_ids) > 1:
            uncertain.append(
                {
                    "record": record,
                    "reason": "multiple email/phone matches",
                    "candidates": [
                        {
                            "contact_id": cid,
                            "display_name": contacts_by_id.get(cid, {}).get("display_name"),
                        }
                        for cid in sorted(candidate_ids)
                    ],
                    "match_reasons": candidate_reasons,
                }
            )
            summary["uncertain"] += 1
            continue

        if not contact_id:
            name_candidates = [value for value in [name, nickname] if value]
            contact_id, fuzzy_candidates = _fuzzy_match_contact(
                name_candidates,
                existing_contacts,
                args.fuzzy_threshold,
                args.fuzzy_margin,
            )
            if not contact_id and fuzzy_candidates:
                uncertain.append(
                    {
                        "record": record,
                        "reason": "ambiguous name match",
                        "candidates": [candidate.__dict__ for candidate in fuzzy_candidates],
                    }
                )
                summary["uncertain"] += 1
                continue

        if contact_id:
            existing = contacts_by_id.get(contact_id) or get_contact(contact_id)
            contact_in = _build_contact_input(contact_id, existing, record)
            if args.dry_run:
                print(f"[dry-run] Update {contact_id} ({contact_in.display_name})")
            else:
                ingest_contact(contact_in)
                updated = get_contact(contact_id)
                if updated:
                    contacts_by_id[contact_id] = updated
                    for email in updated.get("emails") or []:
                        normalized = normalize_email(email)
                        if normalized:
                            email_index.setdefault(normalized, set()).add(contact_id)
                    for phone in updated.get("phones") or []:
                        digits = _normalize_phone_digits(phone)
                        if digits:
                            phone_index.setdefault(digits, set()).add(contact_id)
            summary["updated"] += 1
            continue

        if not name and not emails:
            summary["skipped"] += 1
            uncertain.append(
                {
                    "record": record,
                    "reason": "missing name/email; cannot create contact",
                }
            )
            summary["uncertain"] += 1
            continue

        if not name:
            name = _display_name_from_email(emails[0])

        new_contact_id = f"contact:{_slugify_name(name)}-{uuid4().hex[:6]}"
        contact_in = _build_contact_input(new_contact_id, None, record)
        if args.dry_run:
            print(f"[dry-run] Create {new_contact_id} ({contact_in.display_name})")
        else:
            ingest_contact(contact_in)
            created = get_contact(new_contact_id)
            if created:
                existing_contacts.append(created)
                contacts_by_id[new_contact_id] = created
                for email in created.get("emails") or []:
                    normalized = normalize_email(email)
                    if normalized:
                        email_index.setdefault(normalized, set()).add(new_contact_id)
                for phone in created.get("phones") or []:
                    digits = _normalize_phone_digits(phone)
                    if digits:
                        phone_index.setdefault(digits, set()).add(new_contact_id)
        summary["created"] += 1

    report = {
        "summary": summary,
        "uncertain": uncertain,
    }

    with open(args.report, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, ensure_ascii=True)

    print("\nContact import complete")
    print(json.dumps(summary, indent=2, ensure_ascii=True))
    print(f"Report written to {args.report}")


if __name__ == "__main__":
    main()
