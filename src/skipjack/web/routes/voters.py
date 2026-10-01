"""Voter registration routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import RedirectResponse

from skipjack.web.db import get_db
from skipjack.web.urls import resolve_slug, slugify

router = APIRouter(prefix="/voters")


@router.get("/")
async def voters_index(request: Request, report_date: str | None = Query(None)):
    if report_date:  # an old query-string link
        return RedirectResponse(f"/voters/month/{report_date}/", status_code=307)
    return _month_page(request, None)


@router.get("/month/{report_date}/")
async def voters_month(request: Request, report_date: str):
    return _month_page(request, report_date)


def _month_page(request: Request, report_date: str | None):
    with get_db() as conn:
        dates = [
            r["report_date"]
            for r in conn.execute(
                "SELECT DISTINCT report_date FROM voter_registration ORDER BY report_date DESC"
            ).fetchall()
        ]

        if report_date is not None and report_date not in dates:
            raise HTTPException(404, f"No report for {report_date}")
        selected_date = report_date or (dates[0] if dates else None)

        PARTY_ORDER = [
            "DEM",
            "REP",
            "UNAF",
            "UNA",
            "LIB",
            "GRN",
            "CON",
            "IND",
            "AME",
            "BAR",
            "NLM",
            "WCP",
            "OTH",
        ]
        raw_parties = {
            r["party"]
            for r in conn.execute(
                "SELECT DISTINCT party FROM voter_registration WHERE report_date = ?",
                (selected_date,),
            ).fetchall()
        }
        parties_in_date = [p for p in PARTY_ORDER if p in raw_parties]

        rows = conn.execute(
            """SELECT county, party, party_name, active_voters
               FROM voter_registration
               WHERE report_date = ?
               ORDER BY county""",
            (selected_date,),
        ).fetchall()

        county_data = {}
        for row in rows:
            c = row["county"]
            if c not in county_data:
                county_data[c] = {"county": c, "total": 0, "parties": {}}
            county_data[c]["parties"][row["party"]] = row["active_voters"]
            county_data[c]["total"] += row["active_voters"]

        county_list = sorted(county_data.values(), key=lambda r: r["county"])

        statewide = {}
        statewide_total = 0
        for cd in county_list:
            statewide_total += cd["total"]
            for p, v in cd["parties"].items():
                statewide[p] = statewide.get(p, 0) + v

    return request.app.state.templates.TemplateResponse(
        request,
        "voters.html",
        {
            "dates": dates,
            "selected_date": selected_date,
            "parties": parties_in_date,
            "county_data": county_list,
            "statewide": statewide,
            "statewide_total": statewide_total,
        },
    )


# Series shown in the main chart. Everything else is drawn as a small multiple,
# because minor parties are too small to read on the same y-axis as these.
MAJOR_PARTIES = ["Democratic", "Republican", "Unaffiliated"]
STATEWIDE = "Maryland"


@router.get("/county/")
async def county_root(request: Request, county: str | None = Query(None)):
    if county and county != STATEWIDE:  # an old query-string link
        return RedirectResponse(f"/voters/county/{slugify(county)}/", status_code=307)
    return _county_page(request, None)


@router.get("/county/{slug}/")
async def county_detail(request: Request, slug: str):
    return _county_page(request, slug)


def _county_page(request: Request, slug: str | None):
    with get_db() as conn:
        counties = [
            r["county"]
            for r in conn.execute(
                "SELECT DISTINCT county FROM voter_registration ORDER BY county"
            ).fetchall()
        ]
        county = STATEWIDE if slug is None else resolve_slug(slug, counties)
        if county is None:
            raise HTTPException(404, f"No county called {slug}")

        # party_name merges codes that were renamed (UNAF -> UNA both are Unaffiliated).
        where, params = ("", ()) if county == STATEWIDE else ("WHERE county = ?", (county,))
        rows = conn.execute(
            f"""SELECT report_date, party_name, SUM(active_voters) AS voters
                FROM voter_registration {where}
                GROUP BY report_date, party_name
                ORDER BY report_date""",
            params,
        ).fetchall()
        activity_rows = conn.execute(
            f"""SELECT report_date,
                       SUM(CASE WHEN measure = 'inactive' THEN value END) AS inactive,
                       SUM(CASE WHEN measure = 'party_affiliation_changes_from'
                                THEN value END) AS party_switches
                FROM registration_activity {where}
                GROUP BY report_date""",
            params,
        ).fetchall()
        # New registrations and removals are only reported statewide.
        summary_rows = (
            conn.execute(
                """SELECT report_date,
                          SUM(CASE WHEN section = 'new_registration' AND party <> 'DUPS'
                                   THEN value END) AS new_registrations,
                          SUM(CASE WHEN section = 'removal' THEN value END) AS removals
                   FROM registration_summary
                   GROUP BY report_date"""
            ).fetchall()
            if county == STATEWIDE
            else []
        )

    dates = sorted({r["report_date"] for r in rows})
    index = {d: i for i, d in enumerate(dates)}
    by_party: dict[str, list[int | None]] = {}
    for r in rows:
        by_party.setdefault(r["party_name"], [None] * len(dates))[index[r["report_date"]]] = r[
            "voters"
        ]
    totals = [sum(v[i] or 0 for v in by_party.values()) for i in range(len(dates))]

    def by_date(rows, column: str) -> list[int | None]:
        values: list[int | None] = [None] * len(dates)
        for r in rows:
            if r["report_date"] in index:
                values[index[r["report_date"]]] = r[column]
        return values

    overall = {
        "active": totals,
        "inactive": by_date(activity_rows, "inactive"),
        "party_switches": by_date(activity_rows, "party_switches"),
        "new_registrations": by_date(summary_rows, "new_registrations") if summary_rows else None,
        "removals": by_date(summary_rows, "removals") if summary_rows else None,
    }

    # Minor parties: most recently active first, then by size at their last report.
    def recency(name: str) -> tuple[int, int]:
        vals = by_party[name]
        last = max(i for i, v in enumerate(vals) if v is not None)
        return (-last, -(vals[last] or 0))

    minor = sorted((p for p in by_party if p not in MAJOR_PARTIES), key=recency)
    series = [
        {"name": p, "values": by_party[p], "major": p in MAJOR_PARTIES}
        for p in [*[p for p in MAJOR_PARTIES if p in by_party], *minor]
    ]
    latest = len(dates) - 1

    return request.app.state.templates.TemplateResponse(
        request,
        "county.html",
        {
            "counties": counties,
            "county": county,
            "statewide_label": STATEWIDE,
            "dates": dates,
            "totals": totals,
            "series": series,
            "overall": overall,
            "latest_date": dates[latest] if dates else None,
            "latest_total": totals[latest] if dates else 0,
        },
    )
