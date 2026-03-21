"""Venmo CSV parser — normalizes Venmo exports to unified transaction schema."""

from __future__ import annotations

import csv
import json
import re
from datetime import datetime
from pathlib import Path


class VenmoParseError(Exception):
    """Raised when Venmo CSV parsing fails."""


VENMO_ACCOUNT_ID = "venmo_default"


def parse_venmo_csv(file_path: str) -> list[dict]:
    """Parse a Venmo CSV export and return normalized transactions."""
    path = Path(file_path)
    if not path.exists():
        raise VenmoParseError(f"File not found: {file_path}")

    transactions = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row_num, row in enumerate(reader, start=2):
            try:
                txn = _parse_row(row)
                if txn is not None:
                    transactions.append(txn)
            except Exception as e:
                raise VenmoParseError(
                    f"Error parsing row {row_num}: {e}"
                ) from e

    return transactions


def _parse_row(row: dict) -> dict | None:
    """Parse a single CSV row. Returns None for empty/header rows."""
    txn_id = (row.get("ID") or "").strip()
    if not txn_id:
        return None

    amount_str = (row.get("Amount (total)") or "").strip()
    if not amount_str:
        return None

    amount = _parse_amount(amount_str)
    date_str = (row.get("Datetime") or "").strip()
    date = _parse_date(date_str)

    txn_type = (row.get("Type") or "").strip()
    from_user = (row.get("From") or "").strip()
    to_user = (row.get("To") or "").strip()

    # Determine counterparty: if sending money, counterparty is To; if receiving, From
    if amount < 0:
        counterparty = to_user
    else:
        counterparty = from_user

    return {
        "id": txn_id,
        "account_id": VENMO_ACCOUNT_ID,
        "amount": amount,
        "date": date,
        "description": (row.get("Note") or "").strip(),
        "category": "uncategorized",
        "type": txn_type,
        "status": (row.get("Status") or "").strip().lower(),
        "counterparty": counterparty or None,
        "source": "venmo",
        "raw_data": json.dumps(dict(row)),
    }


def _parse_amount(amount_str: str) -> float:
    """Parse Venmo amount string like '- $1,200.00' or '+ $25.00'."""
    cleaned = amount_str.replace("$", "").replace(",", "").replace(" ", "")
    match = re.match(r"^([+-]?)(\d+\.?\d*)$", cleaned)
    if not match:
        raise VenmoParseError(f"Cannot parse amount: {amount_str!r}")
    sign = -1 if match.group(1) == "-" else 1
    return sign * float(match.group(2))


def _parse_date(date_str: str) -> str:
    """Parse Venmo datetime to YYYY-MM-DD."""
    if not date_str:
        return ""
    # Venmo uses ISO-ish format: 2026-01-15T10:30:00
    try:
        dt = datetime.fromisoformat(date_str)
        return dt.strftime("%Y-%m-%d")
    except ValueError:
        # Try other common formats
        for fmt in ["%m/%d/%Y", "%Y-%m-%d"]:
            try:
                dt = datetime.strptime(date_str, fmt)
                return dt.strftime("%Y-%m-%d")
            except ValueError:
                continue
        raise VenmoParseError(f"Cannot parse date: {date_str!r}")
