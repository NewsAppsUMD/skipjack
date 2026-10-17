"""Home page route."""

from __future__ import annotations

import sqlite3
from datetime import date

from fastapi import APIRouter, Request

from skipjack.pipeline.turnout import ELECTIONS
from skipjack.web import trends
from skipjack.web import turnout as qt
from skipjack.web.dates import day_label, month_label
from skipjack.web.db import get_db
from skipjack.web.maps import locator_map, turnout_live_maps
from skipjack.web.routes.turnout import _tiles
from skipjack.web.routes.voters import county_registration
from skipjack.web.summary import highlights

router = APIRouter()


def _today() -> date:
    return date.today()


def _election_lead(conn: sqlite3.Connection, row: dict) -> dict:
    """The lead block while an election is under way: the mail-in tiles, a sentence and the
    return-rate map, each county linking to the election's county table."""
    code, slug = row["code"], row["slug"]
    headline = qt.headline(conn, code)
    sent = headline.get("sent-returned")
    requests_as_of = headline.get("requests", {}).get("as_of")
    sent_as_of = sent["as_of"] if sent else None
    sentence = None
    if sent and sent.get("sent") and sent.get("return_rate") is not None:
        sentence = (
            f"Of {sent['sent']:,} mail-in ballots sent, {sent['return_rate'] * 100:.0f}% "
            "have come back"
        )
        more = sent.get("change", {}).get("received")
        if more:
            sentence += f"; {more:,} more arrived since the previous report"
        sentence += "."
    county_map = None
    if sent_as_of:
        rows = qt.county_rows(conn, code, sent_as_of, requests_as_of=requests_as_of)
        county_map = turnout_live_maps(
            rows, day_label(sent_as_of), href_prefix=f"/turnout/{slug}/county/"
        )[0]
    return {
        "mode": "election",
        "title": f"{ELECTIONS[code].name}: mail-in ballots",
        "as_of": headline["latest"],
        "tiles": _tiles(headline),
        "sentences": [sentence] if sentence else [],
        "also": [],
        "link": (f"/turnout/{slug}/", "Daily tracking, by county and district"),
        "map": county_map,
    }


def _registration_lead(summary: dict, locator: dict | None, latest: str | None) -> dict:
    by_key = summary["by_key"]
    return {
        "mode": "registration",
        "title": f"{month_label(latest)} in registration" if latest else "Registration",
        "as_of": None,
        "tiles": summary["tiles"],
        "sentences": [by_key[k] for k in ("total", "record") if k in by_key],
        "also": [by_key[k] for k in ("inactive", "counties") if k in by_key],
        "link": ("/voters/county/", "More trends, by county"),
        "map": locator,
    }


def _flow_chart(conn: sqlite3.Connection) -> dict | None:
    """Rolling 12-month new registrations and removals, on the new registrations' months."""
    new, gone = trends.new_registrations(conn), trends.removals(conn)
    if not new.dates:
        return None
    return {
        "dates": new.dates,
        "new": new.total_rolling,
        "removed": trends.align(new.dates, gone.dates, gone.total_rolling),
    }


def home_context(conn: sqlite3.Connection, today: date | None = None) -> dict:
    """Everything the home page shows. ``today`` picks the lead (see ``qt.lead_election``)."""
    today = today or _today()
    latest = conn.execute(
        "SELECT report_date FROM voter_registration ORDER BY report_date DESC LIMIT 1"
    ).fetchone()
    latest_date = latest["report_date"] if latest else None
    summary = highlights(conn)

    live = qt.lead_election(conn, today)
    if live:
        lead = _election_lead(conn, live)
    else:
        locator = None
        if latest_date:
            _, county_list = county_registration(conn, latest_date)
            locator = locator_map(county_list, month_label(latest_date))
        lead = _registration_lead(summary, locator, latest_date)

    flow = _flow_chart(conn)

    now = []
    if latest_date:
        now.append(f"Registration through {month_label(latest_date)}")
    if live and lead["as_of"]:
        now.append(f"Mail-in ballots as of {day_label(lead['as_of'])}")
        now.append(f"Election day {day_label(live['election_day'])}")

    return {
        "lead": lead,
        "now": " · ".join(now),
        "summary": summary,
        "flow": flow,
        "flow_sentence": summary["by_key"].get("flow"),
        "party_sentence": summary["by_key"].get("shares"),
        "share_sentence": " ".join(summary["records"]),
        "chart_data": {
            "party": summary["chart"],
            "share": summary["share_chart"],
            "flow": flow,
        },
    }


@router.get("/")
async def home(request: Request):
    with get_db() as conn:
        ctx = home_context(conn)
    return request.app.state.templates.TemplateResponse(request, "home.html", ctx)
