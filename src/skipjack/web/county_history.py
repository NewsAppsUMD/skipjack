"""A county's whole record in one document, published as downloads/county/<slug>.json.

Registration by month, the newest publishable turnout snapshot of each report family, the
certified results rows, and the results-over-time analysis the county page shows.
"""

from __future__ import annotations

import sqlite3

from skipjack.pipeline.results import COUNTY_CODES
from skipjack.pipeline.turnout import may_publish
from skipjack.web import results_trends
from skipjack.web.downloads import results_published
from skipjack.web.urls import resolve_slug

_REGISTRATION = ["report_date", "county", "party", "party_name", "active_voters"]
_TURNOUT = [
    "as_of", "election", "family", "geography", "county", "district",
    "category", "party", "measure", "value",
]  # fmt: skip
_RESULTS = [
    "election", "county", "party_file", "office", "district", "candidate",
    "party", "winner", "write_in", "total_votes", "total_against",
]  # fmt: skip


def _rows(conn: sqlite3.Connection, sql: str, columns: list[str], params: tuple) -> list[dict]:
    return [dict(zip(columns, row, strict=True)) for row in conn.execute(sql, params)]


def county_document(conn: sqlite3.Connection, slug: str, cache: dict | None = None) -> dict | None:
    """The county's document, or None for a slug that is not a Maryland county. ``cache`` is
    passed through to county_trends so a build of all 24 sums each statewide contest once."""
    county = resolve_slug(slug, COUNTY_CODES.values())
    if county is None:
        return None
    registration = _rows(
        conn,
        f"SELECT {', '.join(_REGISTRATION)} FROM voter_registration "  # noqa: S608
        "WHERE county = ? ORDER BY report_date, party",
        _REGISTRATION,
        (county,),
    )
    turnout = [
        row
        for row in _rows(
            conn,
            f"SELECT {', '.join(_TURNOUT)} FROM turnout_counts t "  # noqa: S608
            "WHERE geography = 'county' AND county = ? AND as_of = ("
            "  SELECT MAX(as_of) FROM turnout_counts"
            "  WHERE election = t.election AND family = t.family AND geography = 'county')"
            " ORDER BY election, family, category, party, measure, district, id",
            _TURNOUT,
            (county,),
        )
        if may_publish(row["election"], row["family"])
    ]
    results = [
        row
        for row in _rows(
            conn,
            f"SELECT {', '.join(_RESULTS)} FROM results_county "  # noqa: S608
            "WHERE county = ? ORDER BY election, party_file, office, district, id",
            _RESULTS,
            (county,),
        )
        if results_published(row["election"])
    ]
    return {
        "county": county,
        "slug": slug,
        "registration": registration,
        "turnout": turnout,
        "results": results,
        "results_over_time": results_trends.county_trends(conn, slug, cache),
    }
