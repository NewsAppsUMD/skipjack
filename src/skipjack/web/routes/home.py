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
from skipjack.web.maps import early_voting_map, locator_map, same_day_map, turnout_live_maps
from skipjack.web.routes.turnout import _tiles
from skipjack.web.routes.voters import county_registration
from skipjack.web.summary import highlights

router = APIRouter()


def _today() -> date:
    return date.today()


def _election_lead(conn: sqlite3.Connection, row: dict, today: date) -> dict:
    """The lead block while an election is under way: a row of headline numbers over both
    ways of voting before election day, then one tabbed panel each for the mail-in tracking,
    early voting and same-day registration -- tiles, a sentence and a county map linking to
    the county table (the same-day panel to its own page)."""
    code, slug = row["code"], row["slug"]
    headline = qt.headline(conn, code)
    sent = headline.get("sent-returned")
    requests_as_of = headline.get("requests", {}).get("as_of")
    sent_as_of = sent["as_of"] if sent else None
    early = qt.early_voting(conn, code)
    href_prefix = f"/turnout/{slug}/county/"
    rows = (
        qt.county_rows(
            conn,
            code,
            sent_as_of or requests_as_of,
            requests_as_of=requests_as_of,
            early_as_of=early["as_of"] if early else None,
        )
        if sent_as_of or requests_as_of
        else []
    )

    panels = []
    if sent_as_of or requests_as_of:
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
        panels.append(
            {
                "id": "mail",
                "label": "Mail-in ballots",
                "tiles": _tiles(headline),
                "sentences": [sentence] if sentence else [],
                "map": (
                    turnout_live_maps(rows, day_label(sent_as_of), href_prefix=href_prefix)[0]
                    if sent_as_of
                    else None
                ),
            }
        )
    if early:
        panels.append(
            {
                "id": "early",
                "label": "Early voting",
                "tiles": _early_tiles(early),
                "sentences": [_early_sentence(early)],
                "map": early_voting_map(rows, day_label(early["as_of"]), href_prefix=href_prefix)
                if rows
                else None,
            }
        )

    same_day = qt.same_day(conn, code)
    if same_day and same_day["total"]:
        # the share of early voters reads only when both reports count the same days
        early_counties = (
            {c["county"]: c["total"] for c in early["counties"]}
            if early and early["as_of"] == same_day["as_of"]
            else None
        )
        panels.append(
            {
                "id": "same-day",
                "label": "Same-day registration",
                "tiles": _same_day_tiles(same_day, early if early_counties else None),
                "sentences": [_same_day_sentence(code, same_day)],
                "map": (
                    same_day_map(
                        same_day["counties"],
                        early_counties,
                        day_label(same_day["as_of"]),
                        href_prefix=f"/turnout/{slug}/same-day/",
                    )
                    if early_counties
                    else None
                ),
                "link": (
                    f"/turnout/{slug}/same-day/",
                    "Same-day registration by day, party and county",
                ),
            }
        )

    # the headline numbers: ballots in hand before election day, when both families report
    figures = []
    received = sent.get("received") if sent else None
    if early and received is not None and early.get("total") is not None:
        voted = received + early["total"]
        figures.append(
            {
                "value": f"{voted:,}",
                "label": "already voted: mail-in ballots received back plus early voters",
            }
        )
        if early.get("eligible"):
            # approximate: the eligible actives are the early-voting report's own snapshot,
            # not today's register
            figures.append(
                {
                    "value": _approx_pct(voted / early["eligible"]),
                    "label": f"of the {early['eligible']:,} eligible active voters",
                }
            )
    days_left = (date.fromisoformat(ELECTIONS[code].election_day) - today).days
    if days_left > 0:
        figures.append(
            {
                "value": str(days_left),
                "label": "day to election day" if days_left == 1 else "days to election day",
            }
        )

    return {
        "mode": "election",
        "title": f"{ELECTIONS[code].name}: ballots so far",
        "as_of": headline["latest"],
        "figures": figures,
        "panels": panels,
        "link": (f"/turnout/{slug}/", "Daily tracking, by county and district"),
        "mail_as_of": sent_as_of or requests_as_of,
        "early_as_of": early["as_of"] if early else None,
    }


def _approx_pct(share: float) -> str:
    """A share as an approximate whole percentage ("~12%"), "<1%" below one percent."""
    pct = round(share * 100)
    return f"~{pct}%" if pct >= 1 else "<1%"


def _early_tiles(early: dict) -> list[dict]:
    """The early-voting panel's tiles: the voters so far, their share of the eligible
    actives, the latest day's count and the two big parties' shares of early voters."""
    tiles = [
        {
            "label": "early voters",
            "value": early["total"],
            "change": early.get("change"),
            "as_of": early["as_of"],
        }
    ]
    if early.get("share") is not None:
        tiles.append(
            {
                "label": "of eligible active voters",
                "value_text": _approx_pct(early["share"]),  # the report's own eligible snapshot
                "change": None,
                "as_of": early["as_of"],
            }
        )
    if early["days"]:
        last = early["days"][-1]
        when = f", {day_label(last['date'])}" if last.get("date") else ""
        tiles.append(
            {
                "label": f"on day {last['day']}{when}",
                "value": last["value"],
                "change": None,
                "as_of": early["as_of"],
            }
        )
    shares = {p["party"]: p["of_ballots"] for p in early["parties"]}
    if shares.get("DEM") is not None and shares.get("REP") is not None:
        tiles.append(
            {
                "label": "Democratic / Republican share of early voters",
                "value_text": f"{shares['DEM'] * 100:.0f}% / {shares['REP'] * 100:.0f}%",
                "change": None,
                "as_of": early["as_of"],
            }
        )
    return tiles


def _same_day_tiles(same_day: dict, early: dict | None) -> list[dict]:
    """The same-day panel's tiles: the registrants and address changes so far, each part,
    and -- when the early-voting report counts the same days -- their share of early voters."""
    as_of = same_day["as_of"]
    tiles = [
        {"label": "same-day registrations and address changes", "value": same_day["total"], "change": None, "as_of": as_of},
        {"label": "new registrants", "value": same_day["new"], "change": None, "as_of": as_of},
        {"label": "address changes", "value": same_day["addrchg"], "change": None, "as_of": as_of},
    ]  # fmt: skip
    if early and early.get("total"):
        tiles.append(
            {
                "label": "of early voters",
                "value_text": f"{same_day['total'] / early['total'] * 100:.1f}%",
                "change": None,
                "as_of": as_of,
            }
        )
    return tiles


def _same_day_sentence(code: str, same_day: dict) -> str:
    """One sentence on same-day registration: the count against the elections the page's
    mail-in pace compares with, over the same number of early-voting days."""
    days = same_day["through"]
    sentence = (
        f"{same_day['total']:,} Marylanders have registered or updated their address and "
        f"voted on the same day in {days} day{'s' if days != 1 else ''} of early voting"
    )
    by_code = {r["code"]: r for r in same_day["comparison"]}
    earlier = [by_code[c] for c in ELECTIONS[code].pace_comparisons if c in by_code]
    if earlier:
        sentence += ", against " + " and ".join(
            f"{r['through']:,} in the {r['name']}" for r in earlier
        )
        sentence += f" over their first {days}"
    return sentence + "."


def _early_sentence(early: dict) -> str:
    """One sentence on early voting: how many in how many days, and the leading county."""
    days = len(early["days"])
    sentence = f"{early['total']:,} Marylanders have voted early"
    if days:
        sentence += f" in {days} day{'s' if days != 1 else ''}"
    counties = [c for c in early["counties"] if c.get("share") is not None]
    if counties:
        top = max(counties, key=lambda c: c["share"])
        sentence += f"; {top['county']} leads the counties at {top['share'] * 100:.1f}% of its eligible active voters"
    return sentence + "."


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
        lead = _election_lead(conn, live, today)
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
        if lead.get("mail_as_of"):
            now.append(f"Mail-in ballots as of {day_label(lead['mail_as_of'])}")
        if lead.get("early_as_of"):
            now.append(f"Early voting as of {day_label(lead['early_as_of'])}")
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
