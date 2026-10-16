"""About page route."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Request

from skipjack.web.db import get_db
from skipjack.web.results import elections

router = APIRouter()


def results_index_url(year: int) -> str:
    """The State Board's election-data index page for a year."""
    if year >= 2026:
        return f"https://elections.maryland.gov/elections/{year}/election_data/index.html"
    return f"https://elections.maryland.gov/elections/archive/{year}/election_data/index.html"


def results_summary(conn) -> list[dict]:
    """Per election with results: its source-file count and the Board's index page."""
    try:
        found = elections(conn)
        counts = dict(
            conn.execute(
                "SELECT election, COUNT(DISTINCT source_id) FROM results_county GROUP BY election"
            ).fetchall()
        )
    except sqlite3.OperationalError:
        return []
    return [
        {
            "code": e["code"],
            "name": e["name"],
            "files": counts.get(e["code"], 0),
            "url": results_index_url(e["year"]),
        }
        for e in found
    ]


@router.get("/about")
async def about(request: Request):
    with get_db() as conn:
        # the 315 results files are summarized per election below, not listed one by one
        sources = conn.execute(
            "SELECT * FROM sources WHERE source_id NOT LIKE 'sbe-results-%' ORDER BY source_id"
        ).fetchall()
        results_sources = results_summary(conn)
        turnout_sources = conn.execute(
            "SELECT * FROM turnout_sources ORDER BY source_id"
        ).fetchall()

    return request.app.state.templates.TemplateResponse(
        request,
        "about.html",
        {
            "sources": sources,
            "turnout_sources": turnout_sources,
            "results_sources": results_sources,
        },
    )
