"""The /turnout/ section: each election's mail-in figures from the press-room reports."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from skipjack.pipeline.turnout import ELECTIONS
from skipjack.web import turnout as qt
from skipjack.web.db import get_db

router = APIRouter(prefix="/turnout")


@router.get("/")
async def index(request: Request):
    with get_db() as conn:
        elections = qt.elections(conn)
    return request.app.state.templates.TemplateResponse(
        request, "turnout_index.html", {"elections": elections}
    )


@router.get("/{slug}/")
async def election(request: Request, slug: str):
    with get_db() as conn:
        available = {e["slug"]: e["code"] for e in qt.elections(conn)}
        code = available.get(slug)
        if code is None or code not in ELECTIONS:
            raise HTTPException(status_code=404)
        headline = qt.headline(conn, code)
        # the two files refresh on their own schedules: read each family at its own
        # latest snapshot so a day with only one new file keeps the other family's columns
        sent_as_of = headline.get("sent-returned", {}).get("as_of")
        requests_as_of = headline.get("requests", {}).get("as_of")
        rows = qt.county_rows(
            conn,
            code,
            sent_as_of or requests_as_of,
            requests_as_of=requests_as_of,
        )
        sources = qt.snapshot_sources(conn, code)
        chart = qt.chart_data(conn, code)
    tiles = _tiles(headline)
    return request.app.state.templates.TemplateResponse(
        request,
        "turnout_election.html",
        {
            "election": ELECTIONS[code],
            "headline": headline,
            "tiles": tiles,
            "rows": rows,
            "chart": chart,
            "sources": sources,
        },
    )


def _tiles(headline: dict) -> list[dict]:
    """The headline figures, in the order the page shows them: each tile carries its
    family's own as-of day and the change from that family's previous snapshot."""
    tiles: list[dict] = []
    if "requests" in headline:
        entry = headline["requests"]
        tiles.append(
            {
                "label": "mail-in ballot requests",
                "value": entry.get("requests_total"),
                "change": entry.get("change", {}).get("total"),
                "as_of": entry["as_of"],
            }
        )
    if "sent-returned" in headline:
        entry = headline["sent-returned"]
        change = entry.get("change", {})
        tiles += [
            {
                "label": "ballots sent",
                "value": entry.get("sent"),
                "change": change.get("sent"),
                "as_of": entry["as_of"],
            },
            {
                "label": "received back",
                "value": entry.get("received"),
                "change": change.get("received"),
                "sub": (
                    f"{entry['return_rate'] * 100:.0f}% of sent"
                    if entry.get("return_rate")
                    else None
                ),
                "as_of": entry["as_of"],
            },
            {
                "label": "accepted in the canvass",
                "value": entry.get("accepted"),
                "change": change.get("accepted"),
                "as_of": entry["as_of"],
            },
        ]
    return tiles
