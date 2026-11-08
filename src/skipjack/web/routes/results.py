"""The /results/ section: county-level results from the State Board's results CSVs.

Route order matters: ``/county/`` and ``/county/{county_slug}/`` (a county's results over
time, ``results_trends``) are registered before ``/{slug}/``, since ``_code`` 404s a slug that
is not an election; and ``/{slug}/county/...`` before ``/{slug}/{contest_slug}/`` so the
county pages are never read as a contest. No contest slug can be "county" either (they all
end in a party-file suffix, see ``results.contest_slug``).
"""

from __future__ import annotations

import re
import sqlite3

from fastapi import APIRouter, HTTPException, Request

from skipjack.pipeline.results import COUNTY_CODES, RESULTS_ELECTIONS
from skipjack.web import results as qr
from skipjack.web import results_trends, results_trends_plot
from skipjack.web import results_unofficial as qu
from skipjack.web import ticket
from skipjack.web.db import get_db
from skipjack.web.maps import question_map, results_map, trends_shift_map

router = APIRouter(prefix="/results")

_SOURCE_ID = re.compile(r"-county-(\d\d)-(\w+)$")
_UNOFFICIAL_SOURCE_ID = re.compile(r"_by_county_(\d+)$")
_SOURCE_ELECTION = re.compile(r"^sbe-results-(?:unofficial-)?([A-Z]{2}\d{2})-")


def _code(conn: sqlite3.Connection, slug: str) -> str:
    """The election code for a slug, or a 404 when it has no results rows."""
    code = next((e["code"] for e in qu.site_elections(conn) if e["slug"] == slug), None)
    if code is None:
        raise HTTPException(status_code=404)
    return code


def _election_label(sid: str) -> str | None:
    """The short name of the election a source id belongs to ("2018 general"), or None."""
    m = _SOURCE_ELECTION.match(sid)
    e = RESULTS_ELECTIONS.get(m.group(1)) if m else None
    return f"{e.year} {e.kind}" if e else None


def source_links(
    conn: sqlite3.Connection, source_ids: list[str], with_election: bool = False
) -> list[dict]:
    """The SBE CSV behind each source id, labelled by county and party file, in input order;
    with ``with_election``, as a page spanning elections needs, the election leads the label
    ("2018 general, Montgomery, Democratic")."""
    ids = list(dict.fromkeys(source_ids))
    if not ids:
        return []
    marks = ",".join("?" * len(ids))
    urls = dict(
        conn.execute(
            f"SELECT source_id, source_url FROM sources WHERE source_id IN ({marks})", ids
        ).fetchall()
    )
    links = []
    for sid in ids:
        if sid not in urls:
            continue
        label = sid
        if m := _UNOFFICIAL_SOURCE_ID.search(sid):
            label = COUNTY_CODES.get(f"{int(m.group(1)):02d}", sid)
        elif m := _SOURCE_ID.search(sid):
            county = COUNTY_CODES.get(m.group(1), m.group(1))
            party = qr.PARTY_LABELS.get(m.group(2), "")
            label = f"{county}, {party}" if party else county
        if with_election and (election := _election_label(sid)):
            label = f"{election}, {label}"
        links.append({"label": label, "url": urls[sid]})
    return links


def _queries(code: str):
    """The query module for an election: the unofficial count while it is live, else the
    certified results."""
    return qu if qu.is_unofficial(code) else qr


def _page_context(conn: sqlite3.Connection, code: str) -> dict:
    """What every results page needs besides its own figures: where the numbers come from and,
    for an election being counted, the unofficial banner's facts."""
    if qu.is_unofficial(code):
        unofficial = qu.unofficial_context(conn, code)
        return {
            "unofficial": unofficial,
            "data_page": unofficial["board_url"] if unofficial else "",
        }
    return {"unofficial": None, "data_page": RESULTS_ELECTIONS[code].base_url}


def _trends_context(conn: sqlite3.Connection, elections: list[dict]) -> dict:
    """The page context for a page spanning several elections: the unofficial banner's facts
    when one of them is being counted, else the latest certified election's data page."""
    live = next((e for e in elections if e["unofficial"]), None)
    if live is not None:
        return _page_context(conn, live["code"])
    certified = [e for e in elections if not e["unofficial"]]
    data_page = RESULTS_ELECTIONS[certified[-1]["code"]].base_url if certified else ""
    return {"unofficial": None, "data_page": data_page}


def _election_sources(conn: sqlite3.Connection, code: str) -> list[dict]:
    if qu.is_unofficial(code):
        return source_links(conn, qu.election_source_ids(conn, code))
    ids = [
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT source_id FROM results_county WHERE election = ? ORDER BY source_id",
            (code,),
        )
    ]
    return source_links(conn, ids)


def mail_pair(vote_types: list[dict]) -> tuple[str, str] | None:
    """Labels of the two mail columns when an election's files have two (Mail-In Ballot 1 and
    2, By Mail and By Mail 2), else None. The one place the pages decide to explain them."""
    by_stem = {v["stem"]: v["label"] for v in vote_types}
    for first, second in (("mail_in_1", "mail_in_2"), ("by_mail", "by_mail_2")):
        if first in by_stem and second in by_stem:
            return by_stem[first], by_stem[second]
    return None


def _render(request: Request, template: str, context: dict):
    return request.app.state.templates.TemplateResponse(request, template, context)


@router.get("/")
async def index(request: Request):
    with get_db() as conn:
        elections = qu.site_elections(conn)
    years: dict[int, dict] = {}
    for e in elections:  # newest first already
        years.setdefault(e["year"], {"year": e["year"], "primary": None, "general": None})
        years[e["year"]][e["kind"]] = e
    certified = [e for e in elections if not e.get("unofficial")]
    data_page = RESULTS_ELECTIONS[certified[0]["code"]].base_url if certified else ""
    return _render(
        request, "results_index.html", {"years": list(years.values()), "data_page": data_page}
    )


@router.get("/county/")
async def trends_index(request: Request):
    with get_db() as conn:
        rows = results_trends.counties_index(conn)
        latest = [
            {"code": r["latest"]["election"], "unofficial": r["latest"]["unofficial"]}
            for r in rows
            if r["latest"]
        ]
        context = _trends_context(conn, latest)
    return _render(
        request,
        "results_county_trends_index.html",
        {"rows": rows, "county_maps": [trends_shift_map(rows)], **context},
    )


@router.get("/county/{county_slug}/")
async def trends_county(request: Request, county_slug: str):
    with get_db() as conn:
        trends = results_trends.county_trends(conn, county_slug)
        if trends is None:
            raise HTTPException(status_code=404)
        sources = source_links(conn, trends["source_ids"], with_election=True)
        context = _trends_context(conn, trends["generals"])
    plots = results_trends_plot.county_plots(trends)
    return _render(
        request,
        "results_county_trends.html",
        {"trends": trends, "plots": plots, "sources": sources, **context},
    )


@router.get("/{slug}/")
async def election(request: Request, slug: str):
    with get_db() as conn:
        code = _code(conn, slug)
        q = _queries(code)
        data = q.hub(conn, code)
        if data is None:
            raise HTTPException(status_code=404)
        sources = _election_sources(conn, code)
        context = _page_context(conn, code)
        outran = None
        if code in ticket.HUB_PLOT_ELECTIONS:
            pages = [q.county_page(conn, code, s) for s in q.county_slugs(conn, code)]
            outran = ticket.statewide([p for p in pages if p])
    return _render(
        request,
        "results_election.html",
        {
            "election": data["election"],
            "groups": data["groups"],
            "questions": data.get("questions", []),
            "outran": outran,
            "sources": sources,
            **context,
        },
    )


@router.get("/{slug}/county/")
async def county_index(request: Request, slug: str):
    with get_db() as conn:
        code = _code(conn, slug)
        data = _queries(code).county_list(conn, code)
        if data is None:
            raise HTTPException(status_code=404)
        sources = _election_sources(conn, code)
        context = _page_context(conn, code)
    return _render(
        request,
        "results_county_index.html",
        {
            "election": data["election"],
            "counties": data["counties"],
            "party_labels": qr.PARTY_LABELS,
            "sources": sources,
            **context,
        },
    )


@router.get("/{slug}/county/{county_slug}/")
async def county(request: Request, slug: str, county_slug: str):
    with get_db() as conn:
        code = _code(conn, slug)
        data = _queries(code).county_page(conn, code, county_slug)
        if data is None:
            raise HTTPException(status_code=404)
        sources = source_links(conn, data["source_ids"])
        context = _page_context(conn, code)
    return _render(
        request,
        "results_county.html",
        {
            "election": data["election"],
            "county": data["county"],
            "vote_types": data["vote_types"],
            "mail_pair": mail_pair(data["vote_types"]),
            "groups": data["groups"],
            "questions": data.get("questions", []),
            "outran": ticket.outran(data),
            "sources": sources,
            **context,
        },
    )


@router.get("/{slug}/{contest_slug}/")
async def contest(request: Request, slug: str, contest_slug: str):
    with get_db() as conn:
        code = _code(conn, slug)
        q = _queries(code)
        if q is qu and contest_slug.startswith("question-"):
            question = qu.question(conn, code, contest_slug)
            if question is None:
                raise HTTPException(status_code=404)
            sources = source_links(conn, question["source_ids"])
            context = _page_context(conn, code)
            return _render(
                request,
                "results_question.html",
                {
                    "question": question,
                    "election": question["election"],
                    "county_maps": question_map(question),
                    "sources": sources,
                    **context,
                },
            )
        data = q.contest(conn, code, contest_slug) if contest_slug != "county" else None
        if data is None:
            raise HTTPException(status_code=404)
        sources = source_links(conn, data["source_ids"])
        context = _page_context(conn, code)
    return _render(
        request,
        "results_contest.html",
        {
            "contest": data,
            "election": data["election"],
            "county_maps": results_map(data),
            "sources": sources,
            "mail_pair": mail_pair(data["vote_types"]),
            **context,
        },
    )
