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
        if code is None:
            raise HTTPException(status_code=404)
        headline = qt.headline(conn, code)
        rows = qt.county_rows(conn, code, headline["latest"])
        chart = _chart(conn, code)
        sources = qt.snapshot_sources(conn, code, headline["latest"])
    config = ELECTIONS.get(code)
    return request.app.state.templates.TemplateResponse(
        request,
        "turnout_election.html",
        {
            "election": config,
            "headline": headline,
            "rows": rows,
            "chart": chart,
            "sources": sources,
        },
    )


def _chart(conn, code: str) -> dict | None:
    """Chart data for the election page: the trends of both families on one axis.

    The date axis is every snapshot day that has any figures; each series stops where
    its family has no figures that day.
    """
    all_dates = sorted(
        {d for f in ("requests", "sent-returned") for d in qt.series(conn, code, f)["dates"]}
    )
    if not all_dates:
        return None

    def aligned(family: str, measure: str):
        trend = qt.series(conn, code, family)
        if not trend["dates"]:
            return None
        date_index = {d: i for i, d in enumerate(trend["dates"])}
        values = trend["series"].get(measure, [None] * len(trend["dates"]))
        return [values[date_index[d]] if d in date_index else None for d in all_dates]

    wanted = [
        ("requests", "total", "Requests"),
        ("sent-returned", "sent", "Sent"),
        ("sent-returned", "received", "Received"),
        ("sent-returned", "accepted", "Accepted"),
    ]
    series = []
    for family, measure, name in wanted:
        values = aligned(family, measure)
        if values is not None and any(v for v in values):
            series.append({"name": name, "values": values})
    return {"dates": all_dates, "series": series} if series else None
