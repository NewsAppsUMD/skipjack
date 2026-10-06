"""Whole-history CSV files, built from the database on request.

The monthly CSVs under data/ are the source of truth. These combine every month into one
file per table so a reader does not have to fetch 200 files. Each row carries the id of the
report it came from, so the combined file keeps the provenance the monthly files have.
"""

from __future__ import annotations

import csv
import io
import sqlite3

# file name -> (table, columns). Every table has report_date; source_id is derived from it.
BULK_FILES = {
    "registration_monthly_all.csv": (
        "voter_registration",
        ["report_date", "county", "party", "party_name", "active_voters"],
        "county, party",
    ),
    "registration_activity_all.csv": (
        "registration_activity",
        ["report_date", "county", "measure", "party", "value"],
        "county, measure, party",
    ),
    "registration_summary_all.csv": (
        "registration_summary",
        ["report_date", "section", "category", "party", "value"],
        "section, category, party",
    ),
}

DESCRIPTIONS = {
    "registration_monthly_all.csv": "Active voters by county and party, every month.",
    "registration_activity_all.csv": "County activity (inactive voters, address and name changes, "
    "party switches, confirmation mailings), every month.",
    "registration_summary_all.csv": "Statewide new registrations by method and removals by "
    "reason, every month.",
}


def bulk_csv(conn: sqlite3.Connection, name: str) -> str:
    """The combined file called ``name`` as CSV text. Raises KeyError for an unknown name."""
    table, columns, order = BULK_FILES[name]
    rows = conn.execute(
        f"SELECT {', '.join(columns)} FROM {table} ORDER BY report_date, {order}"  # noqa: S608
    ).fetchall()
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow([*columns, "source_id"])
    for row in rows:
        values = ["" if v is None else v for v in tuple(row)]
        writer.writerow([*values, f"sbe-vrar-{row[0]}"])
    return out.getvalue()
