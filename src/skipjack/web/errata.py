"""The State Board's own arithmetic errors, read from the committed discrepancy file."""

from __future__ import annotations

import csv

from skipjack.web.paths import DATA_DIR

DISCREPANCY_FILE = DATA_DIR / "voter_registration" / "source_discrepancies.csv"


def load_discrepancies() -> list[dict]:
    """Every recorded discrepancy, oldest month first. Empty if the file does not exist."""
    if not DISCREPANCY_FILE.exists():
        return []
    with DISCREPANCY_FILE.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return sorted(rows, key=lambda r: (r["report_date"], r["message"]))


def by_month() -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for row in load_discrepancies():
        grouped.setdefault(row["report_date"], []).append(row)
    return grouped
