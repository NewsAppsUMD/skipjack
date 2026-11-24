"""Rendering: a JSON envelope of meta and rows, or the rows alone as CSV."""

from __future__ import annotations

import csv
import io
import json

# Official results name their tallies *_votes / *_against; unofficial results use the bare
# method names (early, election_day, ...) plus precinct counts and vote_for.
_COUNT_COLUMNS = {
    "active_voters",
    "value",
    "winner",
    "write_in",
    "early",
    "election_day",
    "mail_in",
    "provisional",
    "total",
    "precincts_reported",
    "precincts_total",
    "vote_for",
}
_COUNT_SUFFIXES = ("_votes", "_against")


def _is_count(column: str) -> bool:
    return column in _COUNT_COLUMNS or column.endswith(_COUNT_SUFFIXES)


def typed(rows: list[dict]) -> list[dict]:
    """CSV rows with their count columns as int (blank: None). Other columns are untouched,
    so a district "06" or a county code "01" stays text."""

    def convert(column: str, value):
        if not _is_count(column) or not isinstance(value, str):
            return value
        if value == "":
            return None
        try:
            return int(value)
        except ValueError:
            return value

    return [{k: convert(k, v) for k, v in row.items()} for row in rows]


def render(meta: dict, rows: list[dict], fmt: str) -> str:
    if fmt == "json":
        return json.dumps({"meta": meta, "rows": rows}, indent=1) + "\n"
    if not rows:
        return ""
    columns = list(dict.fromkeys(k for row in rows for k in row))
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)  # DictWriter writes None as an empty cell
    return out.getvalue()
